/**
 * ext_shared/core/human-simulator.js — DOM 操作"人类化"原语 (E3 / Sprint 4 / PRD §102-103)
 *
 * 运行于 MAIN world（由 site-executor.js 在 executeSiteFormAction 前注入）。
 * 暴露 window.DQ_Human：
 *   - type(el, text, opts)              逐字符 dispatchEvent + 80-140ms 抖动（绕 keystroke 风控）
 *   - click(el, opts)                   hover 200-300ms + mousedown/up + click（绕"瞬时点击"风控）
 *   - scrollTo(el, opts)                scrollIntoView('smooth') + 200-600ms 等待
 *   - readingPause(min, max)            阅读停顿 800-2000ms 随机
 *   - selectOption(selectEl, value)     <select> 选择 + change/blur 序列
 *
 * 设计取舍（方案 A MVP）：
 *   - 不做贝塞尔鼠标轨迹（方案 B 加入），仅在元素中心点 click
 *   - 不做"误点击纠正"（方案 C 加入）
 *   - 时序范围参 PRD §9 反作弊（操作间隔 0.8-2.3s + keystroke 真人节奏）
 *
 * 风控规避面（这些是 Bot 的常见破绽）：
 *   ❌ el.click() 0ms 完成        → ✅ hover + mousedown + mouseup + click 4 事件序列
 *   ❌ el.value = 'long text'    → ✅ 逐字符 keydown/keypress/input/keyup
 *   ❌ 0ms 字段间转移            → ✅ 800-2000ms 阅读停顿
 */
(function () {
  'use strict';

  // 随机 [min, max] 闭区间整数
  function _randMs(min, max) {
    return Math.floor(min + Math.random() * (max - min));
  }

  function _sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  /**
   * 逐字符模拟键入。每字符触发 keydown/keypress/input/keyup 完整序列。
   * 默认 keystroke 间隔 80-140ms（参考真人 60-80wpm，约 200ms/word, 50ms/char + 抖动）。
   * 数字键盘上字符（IME 中文输入）速度较慢，可通过 opts.intervalMs 调。
   *
   * @param {HTMLElement} el        目标 input/textarea
   * @param {string} text           要键入的文本
   * @param {object} opts           { intervalMs?: [min, max], clearFirst?: bool }
   */
  async function type(el, text, opts) {
    if (!el || typeof text !== 'string') return { ok: false, error: 'invalid_args' };
    opts = opts || {};
    const interval = opts.intervalMs || [80, 140];
    const clearFirst = opts.clearFirst !== false;

    el.focus();
    if (clearFirst && 'value' in el) {
      // 清空时用 native setter 确保 React/Vue 监听到 value 变化
      const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
                                                : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, '');
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }

    // 真实手感：先短暂停顿（似乎是"想了一下"），再开始打字
    await _sleep(_randMs(80, 200));

    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      // keydown
      el.dispatchEvent(new KeyboardEvent('keydown', {
        key: ch, code: _keyCode(ch), bubbles: true, cancelable: true,
      }));
      // keypress（部分 React 监听）
      el.dispatchEvent(new KeyboardEvent('keypress', {
        key: ch, bubbles: true, cancelable: true,
      }));
      // 写入：用 native setter + input event
      const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
                                                : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, (el.value || '') + ch);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, data: ch, inputType: 'insertText' }));
      // keyup
      el.dispatchEvent(new KeyboardEvent('keyup', {
        key: ch, code: _keyCode(ch), bubbles: true,
      }));

      // 字符间随机间隔（含偶发"思考"长停顿 5% 概率）
      let delay = _randMs(interval[0], interval[1]);
      if (Math.random() < 0.05) delay += _randMs(200, 600);
      await _sleep(delay);
    }

    // 完成后 change + blur
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return { ok: true, typed: text.length };
  }

  function _keyCode(ch) {
    // 简化映射：字母 → KeyA/KeyB/... 数字 → Digit0/Digit1/... 其它 → 空
    if (/[a-zA-Z]/.test(ch)) return 'Key' + ch.toUpperCase();
    if (/[0-9]/.test(ch)) return 'Digit' + ch;
    return '';
  }

  /**
   * 人类化点击：mouseenter → mouseover → mousedown → mouseup → click 完整序列。
   * 中间夹随机 hover 时长 + 提交前 80-180ms"决定犹豫"。
   *
   * @param {HTMLElement} el
   * @param {object} opts  { hoverMs?: [min,max], settleMs?: [min,max] }
   */
  async function click(el, opts) {
    if (!el || typeof el.dispatchEvent !== 'function') return { ok: false, error: 'invalid_element' };
    opts = opts || {};
    const hoverRange = opts.hoverMs || [200, 400];
    const settleRange = opts.settleMs || [80, 180];

    // 先滚动到视野内（如果不在）
    const rect = el.getBoundingClientRect();
    const inView = rect.top >= 0 && rect.bottom <= window.innerHeight;
    if (!inView) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      await _sleep(_randMs(300, 600));
    }

    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const mkMouse = (type) => new MouseEvent(type, {
      bubbles: true, cancelable: true, view: window,
      clientX: cx, clientY: cy, button: 0,
    });

    // 1. 鼠标进入
    el.dispatchEvent(mkMouse('mouseenter'));
    el.dispatchEvent(mkMouse('mouseover'));
    el.dispatchEvent(mkMouse('mousemove'));
    // 2. hover 一段时间（人类决定要不要点）
    await _sleep(_randMs(hoverRange[0], hoverRange[1]));
    // 3. 按下
    el.dispatchEvent(mkMouse('mousedown'));
    await _sleep(_randMs(40, 80));
    // 4. 抬起
    el.dispatchEvent(mkMouse('mouseup'));
    // 5. 触发 click（多数 framework 这里才响应）
    el.dispatchEvent(mkMouse('click'));
    // 6. 给页面渲染一个 frame 反应
    await _sleep(_randMs(settleRange[0], settleRange[1]));
    return { ok: true };
  }

  /**
   * 选 <select> 选项 + 完整 change/input/blur 事件序列。
   * 不模拟下拉点击（原生 <select> 用户用键盘也可以选，无需点击轨迹）。
   */
  function selectOption(el, value) {
    if (!el || el.tagName !== 'SELECT') return { ok: false, error: 'not_select' };
    el.focus();
    const proto = window.HTMLSelectElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return { ok: true };
  }

  /**
   * 阅读停顿：模拟用户读完一个字段/卡片再继续。
   * PRD §103 阅读行为模拟。
   */
  async function readingPause(min, max) {
    min = typeof min === 'number' ? min : 800;
    max = typeof max === 'number' ? max : 2000;
    await _sleep(_randMs(min, max));
  }

  /**
   * 平滑滚动到元素 + 等渲染完成。
   */
  async function scrollTo(el, opts) {
    if (!el) return { ok: false };
    opts = opts || {};
    el.scrollIntoView({ behavior: 'smooth', block: opts.block || 'center' });
    await _sleep(_randMs(opts.minMs || 250, opts.maxMs || 600));
    return { ok: true };
  }

  // ── 暴露 ────────────────────────────────────────────────────────────────
  window.DQ_Human = {
    type, click, selectOption, readingPause, scrollTo,
    _randMs, _sleep,    // 暴露给其它注入脚本复用
  };
})();
