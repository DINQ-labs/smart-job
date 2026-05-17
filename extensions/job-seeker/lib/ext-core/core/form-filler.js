/**
 * form-filler.js — 通用表单操作原语库
 *
 * 提供一组可注入 MAIN world 的纯函数，用于 React/Vue 兼容的表单填写。
 * 每个函数都可作为 chrome.scripting.executeScript 的 func 参数单独使用。
 *
 * 设计原则：
 *   - 无闭包、无外部依赖、无 eval — MV3 合规
 *   - React/Vue 兼容：使用原生 setter + 事件派发
 *   - 所有函数通过 executeSiteFormAction() 在 Worker Tab 或前台标签页执行
 */

// ── React/Vue 兼容的文本输入 ─────────────────────────────────────────────

/**
 * 填写 <input> 元素（React/Vue 兼容）。
 * @param {string} selector - CSS selector
 * @param {string} value - 填写值
 * @returns {{ ok: boolean, error?: string }}
 */
function formFillInput(selector, value) {
  try {
    const el = document.querySelector(selector);
    if (!el) return { ok: false, error: `元素未找到: ${selector}` };
    el.focus();
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value'
    ).set;
    nativeSetter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/**
 * 填写 <textarea> 元素（React/Vue 兼容）。
 * @param {string} selector - CSS selector
 * @param {string} value - 填写值
 * @returns {{ ok: boolean, error?: string }}
 */
function formFillTextarea(selector, value) {
  try {
    const el = document.querySelector(selector);
    if (!el) return { ok: false, error: `元素未找到: ${selector}` };
    el.focus();
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, 'value'
    ).set;
    nativeSetter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ── 选择类控件 ───────────────────────────────────────────────────────────

/**
 * 选择原生 <select> 的选项。
 * @param {string} selector - CSS selector
 * @param {string} value - option value 或 text
 * @returns {{ ok: boolean, error?: string }}
 */
function formSelectNative(selector, value) {
  try {
    const el = document.querySelector(selector);
    if (!el || el.tagName !== 'SELECT') return { ok: false, error: `非 <select> 元素: ${selector}` };

    // 优先 value 匹配，其次 text 匹配
    let found = false;
    for (const opt of el.options) {
      if (opt.value === value || opt.textContent.trim() === value) {
        el.value = opt.value;
        found = true;
        break;
      }
    }
    if (!found) return { ok: false, error: `选项未找到: ${value}` };

    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/**
 * 操作自定义下拉菜单（如 LinkedIn artdeco-dropdown）。
 * 点击触发器 → 等待列表 → 按文本匹配点击选项。
 * @param {string} triggerSelector - 触发下拉的元素 selector
 * @param {string} optionText - 要选择的选项文本
 * @param {string} [listSelector] - 下拉列表容器 selector（可选，默认自动检测）
 * @returns {Promise<{ ok: boolean, error?: string }>}
 */
async function formClickCustomDropdown(triggerSelector, optionText, listSelector) {
  try {
    const trigger = document.querySelector(triggerSelector);
    if (!trigger) return { ok: false, error: `触发器未找到: ${triggerSelector}` };
    trigger.click();

    // 等待下拉列表出现
    const listSel = listSelector
      || '[role="listbox"]'
      + ', [role="menu"]'
      + ', .artdeco-dropdown__content'
      + ', .select__options';

    let list = null;
    for (let i = 0; i < 20; i++) {
      list = document.querySelector(listSel);
      if (list && list.offsetParent !== null) break;
      await new Promise(r => setTimeout(r, 100));
    }
    if (!list) return { ok: false, error: '下拉列表未出现' };

    // 在列表中找匹配文本的选项
    const optionTextLower = optionText.toLowerCase().trim();
    const candidates = list.querySelectorAll('[role="option"], [role="menuitem"], li, [data-value]');
    for (const opt of candidates) {
      if (opt.textContent.trim().toLowerCase() === optionTextLower) {
        opt.click();
        return { ok: true };
      }
    }
    // 模糊匹配（contains）
    for (const opt of candidates) {
      if (opt.textContent.trim().toLowerCase().includes(optionTextLower)) {
        opt.click();
        return { ok: true };
      }
    }
    return { ok: false, error: `选项未找到: ${optionText}` };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/**
 * 选择 radio 或 checkbox。
 * @param {string} selector - CSS selector 或 name 属性
 * @param {string} value - 要选择的值
 * @returns {{ ok: boolean, error?: string }}
 */
function formCheckRadio(selector, value) {
  try {
    // 优先按 selector 找
    let el = document.querySelector(selector);
    if (!el) {
      // 按 name + value 找
      el = document.querySelector(`input[name="${selector}"][value="${value}"]`);
    }
    if (!el) {
      // 按 label 文本找
      const labels = document.querySelectorAll('label');
      for (const label of labels) {
        if (label.textContent.trim().toLowerCase() === value.toLowerCase()) {
          const input = label.querySelector('input') || document.getElementById(label.htmlFor);
          if (input) { el = input; break; }
        }
      }
    }
    if (!el) return { ok: false, error: `Radio/Checkbox 未找到: ${selector} = ${value}` };

    if (!el.checked) {
      el.checked = true;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('click', { bubbles: true }));
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ── 点击与等待 ───────────────────────────────────────────────────────────

/**
 * 点击元素（带等待重试）。
 * @param {string} selector - CSS selector
 * @param {number} [timeoutMs=5000] - 等待超时
 * @returns {Promise<{ ok: boolean, error?: string }>}
 */
async function formClickElement(selector, timeoutMs = 5000) {
  try {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const el = document.querySelector(selector);
      if (el && el.offsetParent !== null) {
        el.click();
        return { ok: true };
      }
      await new Promise(r => setTimeout(r, 200));
    }
    return { ok: false, error: `点击超时: ${selector}` };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/**
 * 等待元素出现。
 * @param {string} selector - CSS selector
 * @param {number} [timeoutMs=10000] - 等待超时
 * @returns {Promise<{ ok: boolean, found: boolean, error?: string }>}
 */
async function formWaitForElement(selector, timeoutMs = 10000) {
  try {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const el = document.querySelector(selector);
      if (el) return { ok: true, found: true };
      await new Promise(r => setTimeout(r, 200));
    }
    return { ok: false, found: false, error: `等待超时: ${selector}` };
  } catch (e) {
    return { ok: false, found: false, error: String(e) };
  }
}

// ── 文件上传 ─────────────────────────────────────────────────────────────

/**
 * 通过 DataTransfer API 上传文件到 <input type="file">。
 * @param {string} selector - file input 的 CSS selector
 * @param {string} base64Data - base64 编码的文件内容（不含 data: 前缀）
 * @param {string} fileName - 文件名
 * @param {string} [mimeType='application/pdf'] - MIME 类型
 * @returns {{ ok: boolean, error?: string }}
 */
function formUploadFile(selector, base64Data, fileName, mimeType = 'application/pdf') {
  try {
    const el = document.querySelector(selector);
    if (!el) return { ok: false, error: `文件输入未找到: ${selector}` };

    const binaryStr = atob(base64Data);
    const bytes = new Uint8Array(binaryStr.length);
    for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);

    const file = new File([bytes], fileName, { type: mimeType });
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

// ── 表单结构提取 ─────────────────────────────────────────────────────────

/**
 * 生成元素的唯一 CSS selector。
 * @param {Element} el
 * @returns {string}
 */
function _buildSelector(el) {
  if (el.id) return `#${CSS.escape(el.id)}`;
  // name 属性
  if (el.name) {
    const byName = document.querySelectorAll(`[name="${CSS.escape(el.name)}"]`);
    if (byName.length === 1) return `[name="${CSS.escape(el.name)}"]`;
  }
  // 用 nth-child 路径构建
  const parts = [];
  let cur = el;
  while (cur && cur !== document.body) {
    let tag = cur.tagName.toLowerCase();
    const parent = cur.parentElement;
    if (parent) {
      const siblings = [...parent.children].filter(c => c.tagName === cur.tagName);
      if (siblings.length > 1) {
        const idx = siblings.indexOf(cur) + 1;
        tag += `:nth-of-type(${idx})`;
      }
    }
    parts.unshift(tag);
    cur = parent;
  }
  return parts.join(' > ');
}

/**
 * 获取与 input 元素关联的 label 文本。
 * @param {Element} el
 * @returns {string}
 */
function _getLabel(el) {
  // 1. <label for="id">
  if (el.id) {
    const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (label) return label.textContent.trim();
  }
  // 2. 祖先 <label>
  const parentLabel = el.closest('label');
  if (parentLabel) return parentLabel.textContent.trim();
  // 3. aria-label / aria-labelledby
  if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy) {
    const ref = document.getElementById(labelledBy);
    if (ref) return ref.textContent.trim();
  }
  // 4. placeholder
  if (el.placeholder) return el.placeholder;
  // 5. 前一个兄弟 label 或 span
  const prev = el.previousElementSibling;
  if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'SPAN')) {
    return prev.textContent.trim();
  }
  // 6. 父容器内的 label/legend
  const container = el.closest('.form-group, .form-field, fieldset, [class*="field"]');
  if (container) {
    const lbl = container.querySelector('label, legend, .field-label, [class*="label"]');
    if (lbl) return lbl.textContent.trim();
  }
  return '';
}

/**
 * 提取表单容器内所有可交互元素的元数据。
 * @param {string} [containerSelector='form'] - 表单容器 selector
 * @returns {{ fields: Array, buttons: object }}
 */
function formGetStructure(containerSelector = 'form') {
  try {
    // 尝试多个容器 selector
    const selectors = containerSelector === 'form'
      ? ['form', '[role="dialog"]', '.modal', '.application-form', 'main']
      : [containerSelector];

    let container = null;
    for (const sel of selectors) {
      container = document.querySelector(sel);
      if (container) break;
    }
    if (!container) container = document.body;

    const fields = [];
    const seen = new Set();

    // 收集 input, textarea, select
    const inputs = container.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), '
      + 'textarea, select, '
      + '[role="combobox"], [role="listbox"], [contenteditable="true"]'
    );

    for (const el of inputs) {
      const sel = _buildSelector(el);
      if (seen.has(sel)) continue;
      seen.add(sel);

      const tagName = el.tagName.toLowerCase();
      let type = el.type || tagName;
      let options = null;

      if (tagName === 'select') {
        type = 'select';
        options = [...el.options].map(o => ({ value: o.value, text: o.textContent.trim() }));
      } else if (el.getAttribute('role') === 'combobox' || el.getAttribute('role') === 'listbox') {
        type = 'custom-dropdown';
      } else if (type === 'radio' || type === 'checkbox') {
        // 对 radio group，收集所有同 name 选项
        if (el.name && type === 'radio') {
          const group = container.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`);
          options = [...group].map(r => ({
            value: r.value,
            text: (_getLabel(r) || r.value),
          }));
        }
      }

      fields.push({
        selector: sel,
        label: _getLabel(el),
        type,
        required: el.required || el.getAttribute('aria-required') === 'true',
        options,
        currentValue: el.value || '',
        ariaLabel: el.getAttribute('aria-label') || '',
        name: el.name || '',
      });
    }

    // 检测导航按钮
    const buttons = {};
    const btnSelectors = {
      next: [
        'button[aria-label*="Continue"]', 'button[aria-label*="Next"]',
        'button[aria-label*="继续"]', 'button[data-easy-apply-next-button]',
        'button:has(span:contains("Next"))',
      ],
      submit: [
        'button[aria-label*="Submit"]', 'button[aria-label*="提交"]',
        'button[type="submit"]', 'button[data-easy-apply-submit-button]',
      ],
      review: [
        'button[aria-label*="Review"]', 'button[aria-label*="审核"]',
      ],
    };

    for (const [key, sels] of Object.entries(btnSelectors)) {
      for (const sel of sels) {
        try {
          const btn = container.querySelector(sel);
          if (btn && btn.offsetParent !== null) {
            buttons[key] = _buildSelector(btn);
            break;
          }
        } catch (_) {}
      }
    }

    // 兜底：通过按钮文本匹配
    if (!buttons.next || !buttons.submit) {
      const allBtns = container.querySelectorAll('button, [role="button"]');
      for (const btn of allBtns) {
        if (btn.offsetParent === null) continue;
        const text = btn.textContent.trim().toLowerCase();
        if (!buttons.next && (text === 'next' || text === 'continue' || text === '下一步' || text === '继续')) {
          buttons.next = _buildSelector(btn);
        }
        if (!buttons.submit && (text.includes('submit') || text === '提交' || text === '投递')) {
          buttons.submit = _buildSelector(btn);
        }
        if (!buttons.review && (text === 'review' || text === '审核')) {
          buttons.review = _buildSelector(btn);
        }
      }
    }

    return { fields, buttons };
  } catch (e) {
    return { fields: [], buttons: {}, error: String(e) };
  }
}

// ── 批量填写 ─────────────────────────────────────────────────────────────

/**
 * 批量执行填写指令。供 fill_fields 命令使用。
 * @param {Array<{ selector: string, value: string, type?: string }>} actions
 * @returns {{ ok: boolean, filled: number, failed: Array }}
 */
function formFillByActions(actions) {
  const failed = [];
  let filled = 0;

  for (const action of actions) {
    const { selector, value, type } = action;
    try {
      const el = document.querySelector(selector);
      if (!el) {
        failed.push({ selector, error: '元素未找到' });
        continue;
      }

      const tagName = el.tagName.toLowerCase();
      const inputType = type || el.type || tagName;

      if (tagName === 'select') {
        // 原生 select
        let found = false;
        for (const opt of el.options) {
          if (opt.value === value || opt.textContent.trim() === value) {
            el.value = opt.value;
            found = true;
            break;
          }
        }
        if (!found) { failed.push({ selector, error: `选项未找到: ${value}` }); continue; }
        el.dispatchEvent(new Event('change', { bubbles: true }));
      } else if (tagName === 'textarea') {
        el.focus();
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLTextAreaElement.prototype, 'value'
        ).set;
        setter.call(el, value);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      } else if (inputType === 'radio' || inputType === 'checkbox') {
        const shouldCheck = value === 'true' || value === '1' || value === el.value;
        if (el.checked !== shouldCheck) {
          el.checked = shouldCheck;
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }
      } else if (inputType === 'file') {
        // file 类型跳过，需用 formUploadFile 单独处理
        failed.push({ selector, error: '文件上传请用 formUploadFile' });
        continue;
      } else {
        // text / number / email / tel 等
        el.focus();
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(el, value);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }
      filled++;
    } catch (e) {
      failed.push({ selector, error: String(e) });
    }
  }

  return { ok: failed.length === 0, filled, failed };
}


// ── DOM 视觉 + 点击（v1.6 新增）─────────────────────────────────────────────
//
// 给 agent 看页面 + 决策 + 执行的 3 个原语：
//   formGetClickables(root?, opts?)   → 列出所有可点击元素 [{idx, tag, text, selector, rect, ...}]
//   formGetDomSnapshot(root?, opts?)  → 返回完整（截断的）DOM 树
//   formClickByText(text, opts?)      → 按文本匹配点击（selector 漂移时备选）
//
// **关键约束**：这些函数通过 chrome.scripting.executeScript({func, world:'MAIN'})
// 注入到页面跑。chrome 只 .toString() 序列化函数体本身，外部模块级符号
// （_buildSelector / 常量数组等）在 MAIN world 解析失败 ReferenceError。
//
// 因此每个原语都是 **完全自包含**：所有用到的常量、helper 函数全部 inline
// 在函数体内，零外部依赖。代码有重复但是必要的。
//
// 拿不到 tokenStore / chrome.* API。snapshot_id 缓存 + idx 解析放在
// service worker 端 handler 层（commands/dom.js）做。


/**
 * 列出当前页面所有可点击元素，给 agent 看用。
 * @param {string} [rootSelector='body']
 * @param {{ includeHidden?: boolean, maxItems?: number }} [opts]
 * @returns {{ ok: boolean, count?: number, clickables?: Array, error?: string }}
 *
 * 每项包含 idx / tag / text / aria_label / title / role / type / disabled / selector / rect。
 * idx 从 0 严格递增；text 截 100 字符。
 *
 * 注意：完全自包含 — 所有 helper 都在函数内定义，能跨 chrome.scripting injection 边界。
 */
function formGetClickables(rootSelector, opts) {
  try {
    // ── inline helpers（不能引用外部，会跨注入边界 ReferenceError）──
    const SELECTORS = [
      'button', 'a[href]',
      '[role="button"]', '[role="link"]', '[role="tab"]',
      '[role="menuitem"]', '[role="option"]',
      'input[type="button"]', 'input[type="submit"]', 'input[type="reset"]',
      '[onclick]', 'summary', 'label[for]',
    ].join(',');

    function buildSelector(el) {
      if (el.id) return '#' + (typeof CSS !== 'undefined' ? CSS.escape(el.id) : el.id);
      if (el.name) {
        const byName = document.querySelectorAll('[name="' + el.name + '"]');
        if (byName.length === 1) return '[name="' + el.name + '"]';
      }
      const parts = [];
      let cur = el;
      while (cur && cur !== document.body && cur.tagName) {
        let tag = cur.tagName.toLowerCase();
        const parent = cur.parentElement;
        if (parent) {
          const sibs = [...parent.children].filter(c => c.tagName === cur.tagName);
          if (sibs.length > 1) tag += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
        }
        parts.unshift(tag);
        cur = parent;
      }
      return parts.join(' > ');
    }
    // ── 核心 ──

    const root = document.querySelector(rootSelector || 'body');
    if (!root) return { ok: false, error: 'root 未找到: ' + rootSelector };
    const includeHidden = !!(opts && opts.includeHidden);
    const maxItems = (opts && opts.maxItems) || 200;

    const els = root.querySelectorAll(SELECTORS);
    const items = [];
    let idx = 0;
    for (const el of els) {
      if (idx >= maxItems) break;
      const rect = el.getBoundingClientRect();
      if (!includeHidden) {
        if (el.offsetParent === null && el.tagName !== 'BODY') continue;
        if (rect.width === 0 || rect.height === 0) continue;
      }
      const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100);
      items.push({
        idx: idx++,
        tag: el.tagName.toLowerCase(),
        text,
        aria_label: el.getAttribute('aria-label') || '',
        title: el.getAttribute('title') || '',
        role: el.getAttribute('role') || '',
        type: el.type || '',
        disabled: !!el.disabled,
        selector: buildSelector(el),
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          w: Math.round(rect.width),
          h: Math.round(rect.height),
        },
      });
    }
    return { ok: true, count: items.length, clickables: items };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}


/**
 * 返回完整（截断的）DOM 树，给 agent 深度看页面用。
 * 比 formGetClickables 更详细，但 token 消耗大。
 * @param {string} [rootSelector='body']
 * @param {{ maxDepth?: number, maxNodes?: number, includeText?: boolean }} [opts]
 * @returns {{ ok: boolean, node_count?: number, root?: object, error?: string }}
 *
 * 完全自包含。
 */
function formGetDomSnapshot(rootSelector, opts) {
  try {
    // inline 常量（不能 const 在外面，跨边界丢）
    const SKIP_TAGS = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1 };
    const KEEP_ATTRS = [
      'id', 'class', 'role', 'aria-label', 'aria-labelledby', 'aria-describedby',
      'href', 'name', 'type', 'value', 'placeholder', 'title',
      'data-testid', 'data-cy', 'data-qa',
    ];

    const root = document.querySelector(rootSelector || 'body');
    if (!root) return { ok: false, error: 'root 未找到: ' + rootSelector };
    const maxDepth = (opts && opts.maxDepth) || 6;
    const maxNodes = (opts && opts.maxNodes) || 500;
    const includeText = !(opts && opts.includeText === false);

    let count = 0;

    function walk(el, depth) {
      if (count >= maxNodes) return null;
      if (depth > maxDepth) return null;
      if (el.nodeType !== 1) return null;            // 1 = ELEMENT_NODE（避免引用 Node.ELEMENT_NODE）
      if (SKIP_TAGS[el.tagName]) return null;

      if (depth > 0 && el.offsetParent === null && el.tagName !== 'BODY') {
        return null;
      }

      count++;
      const node = {
        tag: el.tagName.toLowerCase(),
        attrs: {},
        children: [],
      };
      for (let i = 0; i < KEEP_ATTRS.length; i++) {
        const a = KEEP_ATTRS[i];
        const v = el.getAttribute && el.getAttribute(a);
        if (v) node.attrs[a] = String(v).slice(0, 120);
      }
      if (includeText) {
        const ownText = [];
        for (const child of el.childNodes) {
          if (child.nodeType === 3) {                // 3 = TEXT_NODE
            const t = (child.textContent || '').trim();
            if (t) ownText.push(t);
          }
        }
        const joined = ownText.join(' ').trim();
        if (joined) node.text = joined.slice(0, 80);
      }
      for (const child of el.children) {
        if (count >= maxNodes) break;
        const c = walk(child, depth + 1);
        if (c) node.children.push(c);
      }
      return node;
    }

    const tree = walk(root, 0);
    return { ok: true, node_count: count, root: tree };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}


/**
 * 按可见文本匹配点击（selector 漂移时备选；多匹配选 nth 个）。
 * @param {string} text 要匹配的文本
 * @param {{
 *   tag?: string,
 *   exact?: boolean,
 *   rootSelector?: string,
 *   timeoutMs?: number,
 *   nth?: number
 * }} [opts]
 * @returns {Promise<{ ok: boolean, matched_count?: number, error?: string }>}
 *
 * 完全自包含。
 */
async function formClickByText(text, opts) {
  try {
    if (!text || typeof text !== 'string') {
      return { ok: false, error: 'text 必填且为字符串' };
    }
    const SELECTORS = [
      'button', 'a[href]',
      '[role="button"]', '[role="link"]', '[role="tab"]',
      '[role="menuitem"]', '[role="option"]',
      'input[type="button"]', 'input[type="submit"]', 'input[type="reset"]',
      '[onclick]', 'summary', 'label[for]',
    ].join(',');

    const tag = (opts && opts.tag) || '';
    const exact = !!(opts && opts.exact);
    const rootSelector = (opts && opts.rootSelector) || 'body';
    const timeoutMs = (opts && opts.timeoutMs) || 5000;
    const nth = (opts && opts.nth) || 0;

    const targetText = text.trim();
    const targetLower = targetText.toLowerCase();
    const start = Date.now();

    while (Date.now() - start < timeoutMs) {
      const root = document.querySelector(rootSelector);
      if (!root) {
        await new Promise(r => setTimeout(r, 200));
        continue;
      }
      const sel = tag ? tag : SELECTORS;
      const els = root.querySelectorAll(sel);
      const matches = [];
      for (const el of els) {
        if (el.offsetParent === null) continue;
        const t = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
        if (t === targetText) matches.push(el);
      }
      if (matches.length === 0 && !exact) {
        for (const el of els) {
          if (el.offsetParent === null) continue;
          const t = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').toLowerCase();
          if (t.includes(targetLower)) matches.push(el);
        }
      }
      if (matches.length > nth) {
        matches[nth].click();
        return { ok: true, matched_count: matches.length };
      }
      await new Promise(r => setTimeout(r, 200));
    }
    return { ok: false, error: '等待超时，未找到匹配文本: "' + text + '"' };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}


// ────────────────────────────────────────────────────────────────────────
// E4 / Sprint 4 — 人类化变体：依赖 window.DQ_Human（由 site-executor 在
// executeSiteFormAction 前注入 ext_shared/core/human-simulator.js 提供）。
// LinkedIn / Indeed Apply 表单这类高敏环节调用 *Human 变体，常规填表用上面
// 的瞬时版本。Human 变体全部 async，调用方需 await。
// ────────────────────────────────────────────────────────────────────────

async function formHumanFillInput(selector, value) {
  try {
    const el = document.querySelector(selector);
    if (!el) return { ok: false, error: `元素未找到: ${selector}` };
    if (!window.DQ_Human) {
      // 兜底：human 模块未注入 → 退化为瞬时填写
      return formFillInput(selector, value);
    }
    await window.DQ_Human.type(el, String(value), { clearFirst: true });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function formHumanFillTextarea(selector, value) {
  try {
    const el = document.querySelector(selector);
    if (!el) return { ok: false, error: `元素未找到: ${selector}` };
    if (!window.DQ_Human) return formFillTextarea(selector, value);
    await window.DQ_Human.type(el, String(value), { clearFirst: true });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function formHumanClick(selector, timeoutMs) {
  try {
    timeoutMs = typeof timeoutMs === 'number' ? timeoutMs : 5000;
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const el = document.querySelector(selector);
      if (el && el.offsetParent !== null) {
        if (!window.DQ_Human) {
          el.click();
          return { ok: true, mode: 'fallback' };
        }
        await window.DQ_Human.click(el);
        return { ok: true, mode: 'human' };
      }
      await new Promise(r => setTimeout(r, 150));
    }
    return { ok: false, error: `等待超时: ${selector}` };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

/**
 * Human 版批量填写。actions 同 formFillByActions 格式 + 可选 _meta.skipPause。
 * 每个 action 之间夹 800-2000ms 阅读停顿，字段填写用 humanType / humanClick。
 *
 * @param {Array<{ selector, value, type? }>} actions
 * @param {{ pauseMin?: number, pauseMax?: number }} [opts]
 */
async function formFillByActionsHuman(actions, opts) {
  opts = opts || {};
  const pauseMin = typeof opts.pauseMin === 'number' ? opts.pauseMin : 800;
  const pauseMax = typeof opts.pauseMax === 'number' ? opts.pauseMax : 2000;
  const failed = [];
  let filled = 0;
  const hasHuman = !!window.DQ_Human;

  for (let i = 0; i < actions.length; i++) {
    const action = actions[i];
    const { selector, value, type } = action;
    try {
      const el = document.querySelector(selector);
      if (!el) { failed.push({ selector, error: '元素未找到' }); continue; }

      const tag = el.tagName.toLowerCase();
      const inputType = type || el.type || tag;

      if (tag === 'select') {
        if (hasHuman) {
          // 用 native setter + change/blur，select 用户键盘选择也走这条
          const opt = Array.from(el.options).find(
            (o) => o.value === value || o.textContent.trim() === value);
          if (!opt) { failed.push({ selector, error: `选项未找到: ${value}` }); continue; }
          window.DQ_Human.selectOption(el, opt.value);
        } else {
          el.value = value;
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }
      } else if (tag === 'textarea') {
        if (hasHuman) await window.DQ_Human.type(el, String(value), { clearFirst: true });
        else formFillTextarea(selector, value);
      } else if (inputType === 'radio' || inputType === 'checkbox') {
        const shouldCheck = value === 'true' || value === '1' || value === el.value;
        if (el.checked !== shouldCheck) {
          if (hasHuman) await window.DQ_Human.click(el);
          else { el.checked = shouldCheck; el.dispatchEvent(new Event('change', { bubbles: true })); }
        }
      } else if (inputType === 'file') {
        failed.push({ selector, error: '文件上传请用 formUploadFile' });
        continue;
      } else {
        if (hasHuman) await window.DQ_Human.type(el, String(value), { clearFirst: true });
        else formFillInput(selector, value);
      }
      filled++;
      // 字段之间夹阅读停顿（最后一个不加）
      if (i < actions.length - 1 && hasHuman) {
        await window.DQ_Human.readingPause(pauseMin, pauseMax);
      }
    } catch (e) {
      failed.push({ selector, error: String(e) });
    }
  }
  return { ok: failed.length === 0, filled, failed, mode: hasHuman ? 'human' : 'fallback' };
}
