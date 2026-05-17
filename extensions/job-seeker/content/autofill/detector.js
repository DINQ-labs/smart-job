/*
 * content/detector.js — 通用表单字段检测（ISOLATED world，每个 frame 各跑一份）
 *
 * 消息：
 *   AF_DETECT       扫描 DOM（含 open shadow DOM 深度遍历）→ { fields, nav }
 *   AF_HIGHLIGHT    按 fid 高亮字段
 *   AF_RESOLVE_BBOX OCR bbox → DOM 元素对齐
 *
 * HTTP 抓包：net-interceptor 的请求经 window.postMessage 到达本脚本，
 * 即时转发给 background（AF_CAPTURE_PUSH），不在 detector 缓冲。
 *
 * frameId 由 background 统一标注；fid 用每次加载的随机前缀保证跨帧唯一。
 * 跨 shadow 边界的字段产出 shadowPath（分段 selector 路径）。
 */
(function () {
  'use strict';
  if (window.__AF_DETECTOR__) return;
  window.__AF_DETECTOR__ = true;

  // 本 detector 实例的随机前缀 —— 让 fid 跨 frame 唯一，无需 background 改写
  const INST = Math.random().toString(36).slice(2, 7);

  let lastMap = new Map();   // fid → element（高亮复用）

  // ── HTTP 抓包（K4）：net-interceptor 的请求即时转发给 background ──────────
  // background 持有抓包会话（chrome.storage.session），跨页面跳转存活，窗口覆盖
  // 到用户真正手动提交 —— detector 只做无状态转发。
  window.addEventListener('message', (e) => {
    if (e.source !== window) return;
    const d = e.data;
    if (!d || !d.__afCapture || !d.rec) return;
    try { chrome.runtime.sendMessage({ type: 'AF_CAPTURE_PUSH', rec: d.rec }); }
    catch (_) { /* 扩展上下文失效，忽略 */ }
  });

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || !msg.type) return;
    if (msg.type === 'AF_DETECT') {
      try {
        sendResponse({
          ok: true,
          fields: detectFields(),
          nav: detectNav(),
          pageUrl: location.href,
          pageTitle: document.title,
        });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
      return;
    }
    if (msg.type === 'AF_HIGHLIGHT') {
      try { highlight((msg.payload && msg.payload.items) || []); sendResponse({ ok: true }); }
      catch (e) { sendResponse({ ok: false, error: String(e) }); }
      return;
    }
    if (msg.type === 'AF_RESOLVE_BBOX') {
      try {
        sendResponse({
          ok: true,
          fields: resolveBboxFields((msg.payload && msg.payload.fields) || []),
        });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
      return;
    }
    if (msg.type === 'AF_PING') { sendResponse({ ok: true }); return; }
  });

  // ── 字段扫描 ────────────────────────────────────────────────────────────
  const FIELD_SELECTOR = [
    'input:not([type="hidden"]):not([type="submit"]):not([type="button"])' +
      ':not([type="reset"]):not([type="image"])',
    'textarea', 'select',
    '[contenteditable="true"]', '[contenteditable=""]',
    '[role="combobox"]', '[role="radiogroup"]',
  ].join(',');

  // 深度遍历：document + 所有 open shadow root 内的字段元素
  function collectFieldEls() {
    const out = [];
    function walk(root) {
      let els;
      try { els = root.querySelectorAll(FIELD_SELECTOR); } catch (e) { els = []; }
      for (const el of els) out.push(el);
      let all;
      try { all = root.querySelectorAll('*'); } catch (e) { all = []; }
      for (const el of all) {
        if (el.shadowRoot) { try { walk(el.shadowRoot); } catch (e) {} }
      }
    }
    walk(document);
    return out;
  }

  function detectFields() {
    lastMap = new Map();
    const fields = [];
    const radioGroups = new Set();
    let idx = 0;

    for (const el of collectFieldEls()) {
      const tag = el.tagName.toLowerCase();
      let type = el.getAttribute('type') || tag;
      const role = (el.getAttribute('role') || '').toLowerCase();

      // role=radiogroup 若包着原生 radio，交给原生 radio 处理
      if (role === 'radiogroup' && el.querySelector('input[type="radio"]')) continue;

      const isFile = type === 'file';
      // file input 常被样式按钮隐藏 —— 隐藏也要收
      if (!isFile && !isVisible(el)) continue;

      // 原生 radio：每个 name 组只产出一个字段
      if (type === 'radio' && el.name) {
        if (radioGroups.has(el.name)) continue;
        radioGroups.add(el.name);
      }

      const scope = scopeOf(el);
      const widget = classifyWidget(el, type, role);
      if (widget === 'aria-radio') type = 'radio';
      else if (widget === 'combobox') type = 'combobox';

      let options = null;
      if (tag === 'select') {
        options = [...el.options].map((o) => ({ value: o.value, text: (o.textContent || '').trim() }));
      } else if (role === 'radiogroup') {
        options = [...el.querySelectorAll('[role="radio"]')].map((r) => {
          const t = r.getAttribute('aria-label') || clean(r.textContent) || r.getAttribute('value') || '';
          return { value: t, text: t };
        });
      } else if (type === 'radio' && el.name) {
        const group = scope.querySelectorAll('input[name="' + cssEsc(el.name) + '"]');
        options = [...group].map((r) => ({ value: r.value, text: getLabel(r, scope) || r.value }));
      }

      const loc = buildLocator(el);
      const label = getLabel(el, scope);
      const fid = INST + '_f' + (idx++);
      const rect = el.getBoundingClientRect();

      fields.push({
        fid,
        selector: loc.selector,
        shadowPath: loc.shadowPath,
        type,
        widget,
        label,
        placeholder: el.getAttribute('placeholder') || '',
        name: el.name || '',
        autocomplete: el.getAttribute('autocomplete') || '',
        required: el.required || el.getAttribute('aria-required') === 'true',
        ariaLabel: el.getAttribute('aria-label') || '',
        options,
        currentValue: (el.value || '').slice(0, 200),
        bbox: {
          x: Math.round(rect.x), y: Math.round(rect.y),
          w: Math.round(rect.width), h: Math.round(rect.height),
        },
        relocate: {
          name: el.name || '',
          ariaLabel: el.getAttribute('aria-label') || '',
          dataTestid: el.getAttribute('data-testid') || el.getAttribute('data-test')
            || el.getAttribute('data-qa') || '',
          labelText: label,
          type,
        },
        source: 'dom',
      });
      lastMap.set(fid, el);
    }
    return fields;
  }

  // ── 控件分类（P5.3）────────────────────────────────────────────────────
  function classifyWidget(el, type, role) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') return 'native-select';
    if (tag === 'textarea') return 'textarea';
    if (el.isContentEditable) return 'contenteditable';
    if (role === 'radiogroup') return 'aria-radio';
    if (role === 'combobox' || el.getAttribute('aria-haspopup') === 'listbox') return 'combobox';
    const ac = el.getAttribute('aria-autocomplete');
    if (ac && ac !== 'none') return 'typeahead';
    if (type === 'file') return 'file';
    if (type === 'checkbox') return 'checkbox';
    if (type === 'radio') return 'radio';
    if (type === 'date' || type === 'datetime-local' || type === 'month' || type === 'week') {
      return 'date-native';
    }
    if (type === 'text' || type === 'tel' || type === 'search' || type === 'div'
        || type === 'span' || !type) {
      const hint = ((el.getAttribute('placeholder') || '') + ' '
        + (el.getAttribute('aria-label') || '')).toLowerCase();
      if (/date|dd\s*\/|mm\s*\/|yyyy|出生|日期/.test(hint)) return 'date-text';
    }
    return 'text';
  }

  // ── 导航按钮检测（P5.2）────────────────────────────────────────────────
  function detectNav() {
    const nav = {};
    let els;
    try {
      els = document.querySelectorAll('button, [role="button"], input[type="submit"], a[role="button"]');
    } catch (e) { return nav; }
    for (const b of els) {
      if (!navVisible(b)) continue;
      const t = ((b.innerText || b.textContent || '') + ' '
        + (b.getAttribute('aria-label') || '') + ' ' + (b.value || ''))
        .replace(/\s+/g, ' ').trim().toLowerCase();
      if (!t || t.length > 40) continue;
      if (!nav.submit && /submit|提交|投递|finish|完成|send application/.test(t)
          && !/upload|browse|选择文件/.test(t)) {
        nav.submit = { selector: buildSelector(b, document), shadowPath: null };
      } else if (!nav.next && /\bnext\b|continue|继续|下一步|save and continue/.test(t)) {
        nav.next = { selector: buildSelector(b, document), shadowPath: null };
      } else if (!nav.review && /review|审核|预览|preview/.test(t)) {
        nav.review = { selector: buildSelector(b, document), shadowPath: null };
      }
    }
    return nav;
  }

  function navVisible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && !el.disabled;
  }

  function isVisible(el) {
    if (el.offsetParent === null && getComputedStyle(el).position !== 'fixed') {
      // shadow DOM 里 offsetParent 可能为 null 但仍可见 —— 用尺寸兜底
      const r0 = el.getBoundingClientRect();
      if (r0.width === 0 && r0.height === 0) return false;
    }
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  }

  function cssEsc(s) {
    return (typeof CSS !== 'undefined' && CSS.escape)
      ? CSS.escape(s) : String(s).replace(/"/g, '\\"');
  }

  function clean(s) {
    return (s || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  }

  // ── shadow DOM 寻址（P5.4）──────────────────────────────────────────────
  // 元素所在的 root（document 或 shadowRoot）
  function scopeOf(el) {
    const r = el.getRootNode && el.getRootNode();
    return (r && r.host) ? r : document;
  }

  // 元素到 document 的 shadow 宿主链 [host1, host2, ...]
  function hostChainOf(el) {
    const chain = [];
    let root = el.getRootNode && el.getRootNode();
    let guard = 0;
    while (root && root.host && guard++ < 12) {
      chain.unshift(root.host);
      root = root.host.getRootNode && root.host.getRootNode();
    }
    return chain;
  }

  // 产出 locator：轻 DOM → {selector}；跨 shadow → {selector, shadowPath:[...]}
  function buildLocator(el) {
    const chain = hostChainOf(el);
    if (!chain.length) {
      return { selector: buildSelector(el, document), shadowPath: null };
    }
    const path = [];
    let scope = document;
    for (const host of chain) {
      path.push(buildSelector(host, scope));
      scope = host.shadowRoot;
    }
    path.push(buildSelector(el, scope));
    return { selector: path[path.length - 1], shadowPath: path };
  }

  // 在指定 scope（document / shadowRoot）内构建唯一 selector
  function buildSelector(el, scope) {
    scope = scope || document;
    if (el.id) {
      try {
        if (scope.querySelectorAll('#' + cssEsc(el.id)).length === 1) {
          return '#' + cssEsc(el.id);
        }
      } catch (e) {}
    }
    if (el.name) {
      try {
        if (scope.querySelectorAll('[name="' + cssEsc(el.name) + '"]').length === 1) {
          return '[name="' + cssEsc(el.name) + '"]';
        }
      } catch (e) {}
    }
    const parts = [];
    let cur = el;
    while (cur && cur.tagName) {
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const sibs = [...parent.children].filter((c) => c.tagName === cur.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      if (!parent || parent === document.body || parent === document.documentElement) break;
      cur = parent;
    }
    return parts.join(' > ');
  }

  // ── label 推断 ──────────────────────────────────────────────────────────
  function getLabel(el, scope) {
    scope = scope || document;
    if (el.id) {
      try {
        const lbl = scope.querySelector('label[for="' + cssEsc(el.id) + '"]');
        if (lbl) return clean(lbl.textContent);
      } catch (e) {}
    }
    const parentLabel = el.closest && el.closest('label');
    if (parentLabel) return clean(parentLabel.textContent);
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy && scope.getElementById) {
      const ref = scope.getElementById(labelledBy);
      if (ref) return clean(ref.textContent);
    }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    let prev = el.previousElementSibling;
    while (prev) {
      if (['LABEL', 'SPAN', 'DIV', 'P'].includes(prev.tagName)) {
        const t = clean(prev.textContent);
        if (t && t.length < 80) return t;
      }
      prev = prev.previousElementSibling;
    }
    const container = el.closest
      && el.closest('.form-group, .form-field, .field, fieldset, [class*="field"]');
    if (container) {
      const lbl = container.querySelector('label, legend, [class*="label"]');
      if (lbl) return clean(lbl.textContent);
    }
    if (el.getAttribute('placeholder')) return clean(el.getAttribute('placeholder'));
    return '';
  }

  // ── OCR bbox → DOM 元素对齐（P4，含 shadow 下钻）─────────────────────────
  function deepElementFromPoint(x, y) {
    let el = document.elementFromPoint(x, y);
    let guard = 0;
    while (el && el.shadowRoot && guard++ < 12) {
      const inner = el.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === el) break;
      el = inner;
    }
    return el;
  }

  function resolveFillable(el) {
    if (!el) return null;
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable) {
      return el;
    }
    if (tag === 'LABEL') {
      const inp = el.querySelector('input, textarea, select')
        || (el.htmlFor && scopeOf(el).getElementById && scopeOf(el).getElementById(el.htmlFor));
      if (inp) return inp;
    }
    if (el.querySelector) {
      const inner = el.querySelector('input, textarea, select, [contenteditable="true"]');
      if (inner) return inner;
    }
    return null;
  }

  function resolveBboxFields(ocrFields) {
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const out = [];
    let idx = 0;
    for (const ofld of ocrFields) {
      const bb = ofld.bbox || {};
      const x = Number(bb.x), y = Number(bb.y), w = Number(bb.w), h = Number(bb.h);
      if (![x, y, w, h].every((n) => isFinite(n))) continue;
      const cx = (x + w / 2) * vw;
      const cy = (y + h / 2) * vh;
      if (cx < 0 || cy < 0 || cx > vw || cy > vh) continue;

      const el = resolveFillable(deepElementFromPoint(cx, cy));
      if (!el) continue;

      const tag = el.tagName.toLowerCase();
      let type = el.getAttribute('type') || tag;
      if (tag === 'select') type = 'select';
      const role = (el.getAttribute('role') || '').toLowerCase();
      const widget = classifyWidget(el, type, role);
      const loc = buildLocator(el);
      const fid = INST + '_o' + (idx++);

      out.push({
        fid,
        selector: loc.selector,
        shadowPath: loc.shadowPath,
        type: ofld.type || type,
        widget,
        label: ofld.label || getLabel(el, scopeOf(el)) || '',
        placeholder: el.getAttribute('placeholder') || '',
        name: el.name || '',
        autocomplete: el.getAttribute('autocomplete') || '',
        required: !!ofld.required || !!el.required,
        ariaLabel: el.getAttribute('aria-label') || '',
        options: ofld.options || null,
        currentValue: (el.value || '').slice(0, 200),
        bbox: { x, y, w, h },
        relocate: {
          name: el.name || '', ariaLabel: el.getAttribute('aria-label') || '',
          dataTestid: el.getAttribute('data-testid') || '', labelText: ofld.label || '', type,
        },
        source: 'ocr',
      });
      lastMap.set(fid, el);
    }
    return out;
  }

  // ── 高亮 ────────────────────────────────────────────────────────────────
  const COLORS = { filled: '#16a34a', pending: '#d97706', failed: '#dc2626' };

  function highlight(items) {
    for (const el of lastMap.values()) {
      if (el && el.dataset && el.dataset.afHl) {
        el.style.outline = '';
        el.style.outlineOffset = '';
        delete el.dataset.afHl;
      }
    }
    for (const it of items) {
      const el = lastMap.get(it.fid);
      if (!el || !el.style) continue;
      el.style.outline = '2px solid ' + (COLORS[it.status] || COLORS.pending);
      el.style.outlineOffset = '1px';
      if (el.dataset) el.dataset.afHl = '1';
    }
  }
})();
