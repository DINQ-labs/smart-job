/*
 * content/main-world/form-primitives.js — 表单填写原语（MAIN world）
 *
 * 暴露 window.AF_Form：fillByActions / uploadFile / clickElement / clickLocator
 *
 * 寻址：locator = { selector, shadowPath?, relocate? }
 *   - shadowPath 存在 → 逐段穿 open shadow root
 *   - selector 落空 → 按 relocate 线索重定位（P5.5）
 * 填写按 action.widget 派发策略（P5.3）。优先走 window.AF_Human 人类化变体。
 */
(function () {
  'use strict';

  function css(s) {
    return (typeof CSS !== 'undefined' && CSS.escape)
      ? CSS.escape(s) : String(s).replace(/"/g, '\\"');
  }

  // ── locator 解析 ────────────────────────────────────────────────────────
  function resolveLocator(loc) {
    if (!loc) return null;
    // 1. shadowPath：逐段穿 shadow root
    if (loc.shadowPath && loc.shadowPath.length) {
      let root = document;
      const path = loc.shadowPath;
      for (let i = 0; i < path.length; i++) {
        let el;
        try { el = root.querySelector(path[i]); } catch (e) { el = null; }
        if (!el) break;
        if (i === path.length - 1) return el;
        if (!el.shadowRoot) break;
        root = el.shadowRoot;
      }
      // shadowPath 失效 → 落到 relocate
    } else if (loc.selector) {
      let el;
      try { el = document.querySelector(loc.selector); } catch (e) { el = null; }
      if (el) return el;
    }
    // 2. relocate 兜底重定位（选择器漂移）
    return relocate(loc.relocate);
  }

  function relocate(hint) {
    if (!hint) return null;
    if (hint.dataTestid) {
      const e = qsAny([
        '[data-testid="' + css(hint.dataTestid) + '"]',
        '[data-test="' + css(hint.dataTestid) + '"]',
        '[data-qa="' + css(hint.dataTestid) + '"]',
      ]);
      if (e) return e;
    }
    if (hint.name) {
      const e = qs('[name="' + css(hint.name) + '"]');
      if (e) return e;
    }
    if (hint.ariaLabel) {
      const e = qs('[aria-label="' + css(hint.ariaLabel) + '"]');
      if (e) return e;
    }
    if (hint.labelText) {
      const want = hint.labelText.trim();
      const labels = document.querySelectorAll('label');
      for (const lbl of labels) {
        if ((lbl.textContent || '').replace(/\s+/g, ' ').trim() === want) {
          const inp = lbl.querySelector('input, textarea, select')
            || (lbl.htmlFor && document.getElementById(lbl.htmlFor));
          if (inp) return inp;
        }
      }
    }
    return null;
  }

  function qs(sel) { try { return document.querySelector(sel); } catch (e) { return null; } }
  function qsAny(sels) {
    for (const s of sels) { const e = qs(s); if (e) return e; }
    return null;
  }

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  function nativeSet(el, value) {
    const proto = el.tagName === 'TEXTAREA'
      ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    el.focus();
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  }

  async function typeInto(el, value) {
    if (window.AF_Human) await window.AF_Human.type(el, String(value), { clearFirst: true });
    else nativeSet(el, String(value));
  }

  // ── 填写策略表（按 widget 派发）────────────────────────────────────────
  async function fillOne(el, action) {
    const widget = action.widget || action.type || 'text';
    const value = action.value;
    const tag = el.tagName.toLowerCase();

    if (widget === 'native-select' || tag === 'select') {
      const opt = [...el.options].find(
        (o) => o.value === value || (o.textContent || '').trim() === value);
      if (!opt) return { ok: false, error: '选项未找到: ' + value };
      if (window.AF_Human) window.AF_Human.selectOption(el, opt.value);
      else { el.value = opt.value; el.dispatchEvent(new Event('change', { bubbles: true })); }
      return { ok: true };
    }

    if (widget === 'checkbox' || widget === 'radio'
        || (el.tagName === 'INPUT' && /^(checkbox|radio)$/.test(el.type))) {
      const want = value === true || value === 'true' || value === '1' || value === el.value;
      if (el.checked !== want) {
        if (window.AF_Human) await window.AF_Human.click(el);
        else { el.checked = want; el.dispatchEvent(new Event('change', { bubbles: true })); }
      }
      return { ok: true };
    }

    if (widget === 'aria-radio') {
      // el 是 [role=radiogroup]，找文本匹配的 [role=radio] 点击
      const opt = matchOption(el.querySelectorAll('[role="radio"]'), value);
      if (!opt) return { ok: false, error: '选项未找到: ' + value };
      if (window.AF_Human) await window.AF_Human.click(opt);
      else opt.click();
      return { ok: true };
    }

    if (widget === 'combobox' || widget === 'typeahead') {
      return await fillCombobox(el, value);
    }

    if (widget === 'contenteditable' || el.isContentEditable) {
      fillContentEditable(el, String(value));
      return { ok: true };
    }

    if (widget === 'date-native') {
      // 日期控件不接受逐字符键入 —— 直接 set value（期望 value 已是 YYYY-MM-DD）
      nativeSet(el, String(value));
      return { ok: true };
    }

    if (widget === 'file') {
      return { ok: false, error: '文件字段请用 uploadFile' };
    }

    // text / textarea / tel / date-text / 其它
    await typeInto(el, value);
    return { ok: true };
  }

  function matchOption(nodeList, value) {
    const want = String(value || '').trim().toLowerCase();
    const arr = [...nodeList];
    for (const o of arr) {
      const t = (o.getAttribute('aria-label') || o.textContent || o.getAttribute('value') || '')
        .replace(/\s+/g, ' ').trim().toLowerCase();
      if (t === want) return o;
    }
    for (const o of arr) {
      const t = (o.getAttribute('aria-label') || o.textContent || '')
        .replace(/\s+/g, ' ').trim().toLowerCase();
      if (t && (t.includes(want) || want.includes(t))) return o;
    }
    return null;
  }

  async function fillCombobox(el, value) {
    // 打开下拉
    if (window.AF_Human) await window.AF_Human.click(el);
    else el.click();
    // 等列表出现
    let listbox = null;
    for (let i = 0; i < 25; i++) {
      listbox = findVisibleListbox();
      if (listbox) break;
      await sleep(120);
    }
    if (!listbox) {
      // 可编辑型 combobox：直接键入
      if (el.tagName === 'INPUT') { await typeInto(el, value); return { ok: true, note: 'typed' }; }
      return { ok: false, error: '下拉列表未出现' };
    }
    // typeahead：先键入再等结果收敛
    if (el.tagName === 'INPUT' && el.getAttribute('aria-autocomplete')) {
      await typeInto(el, value);
      await sleep(700);
      listbox = findVisibleListbox() || listbox;
    }
    const opt = matchOption(listbox.querySelectorAll('[role="option"], li, [data-value]'), value);
    if (!opt) return { ok: false, error: '选项未找到: ' + value };
    if (window.AF_Human) await window.AF_Human.click(opt);
    else opt.click();
    return { ok: true };
  }

  function findVisibleListbox() {
    const cands = document.querySelectorAll(
      '[role="listbox"], [role="menu"], .select__menu, ul[class*="option"]');
    for (const c of cands) {
      const r = c.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && getComputedStyle(c).visibility !== 'hidden') return c;
    }
    return null;
  }

  function fillContentEditable(el, value) {
    el.focus();
    try {
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(el);
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (e) {}
    let ok = false;
    try { ok = document.execCommand('insertText', false, value); } catch (e) {}
    if (!ok) el.textContent = value;
    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  }

  // ── 批量填写 ────────────────────────────────────────────────────────────
  async function fillByActions(actions) {
    const human = !!window.AF_Human;
    const filled = [];
    const failed = [];
    for (let i = 0; i < actions.length; i++) {
      const a = actions[i];
      try {
        const el = resolveLocator(a);
        if (!el) { failed.push({ fid: a.fid, error: '元素未找到' }); continue; }
        if (human && window.AF_Human.scrollTo) {
          try { await window.AF_Human.scrollTo(el); } catch (e) {}
        }
        const r = await fillOne(el, a);
        if (r.ok) filled.push(a.fid);
        else failed.push({ fid: a.fid, error: r.error || '填写失败' });
        if (i < actions.length - 1 && human) {
          await window.AF_Human.readingPause(500, 1400);
        }
      } catch (e) {
        failed.push({ fid: a.fid, error: String(e) });
      }
    }
    return { ok: failed.length === 0, filled, failed };
  }

  // ── 文件上传 ────────────────────────────────────────────────────────────
  function uploadFile(locator, base64Data, fileName, mimeType) {
    try {
      const el = resolveLocator(locator);
      if (!el) return { ok: false, error: '文件输入未找到' };
      const bin = atob(base64Data);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const file = new File([bytes], fileName || 'resume.pdf',
        { type: mimeType || 'application/pdf' });
      const dt = new DataTransfer();
      dt.items.add(file);
      el.files = dt.files;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  }

  // ── 点击 ────────────────────────────────────────────────────────────────
  async function clickLocator(locator) {
    const el = resolveLocator(locator);
    if (!el) return { ok: false, error: '元素未找到' };
    if (window.AF_Human) await window.AF_Human.click(el);
    else el.click();
    return { ok: true };
  }

  async function clickElement(selector, timeoutMs) {
    timeoutMs = timeoutMs || 5000;
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const el = qs(selector);
      if (el && el.offsetParent !== null) {
        if (window.AF_Human) await window.AF_Human.click(el);
        else el.click();
        return { ok: true };
      }
      await sleep(200);
    }
    return { ok: false, error: '点击超时: ' + selector };
  }

  window.AF_Form = { fillByActions, uploadFile, clickElement, clickLocator };
})();
