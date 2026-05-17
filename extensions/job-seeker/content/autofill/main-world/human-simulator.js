/*
 * content/main-world/human-simulator.js — DOM 操作"人类化"原语（MAIN world）
 *
 * 移植自 job-seeker-ext-v3 lib/ext-core/core/human-simulator.js。
 * 暴露 window.AF_Human：type / click / selectOption / readingPause / scrollTo
 *
 * 反风控要点：
 *   ❌ el.click() 0ms 完成     → ✅ hover + mousedown/up + click 序列
 *   ❌ el.value = 'text'      → ✅ 逐字符 keydown/keypress/input/keyup
 *   ❌ 0ms 字段间转移          → ✅ 阅读停顿
 */
(function () {
  'use strict';

  function _randMs(min, max) { return Math.floor(min + Math.random() * (max - min)); }
  function _sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  function _keyCode(ch) {
    if (/[a-zA-Z]/.test(ch)) return 'Key' + ch.toUpperCase();
    if (/[0-9]/.test(ch)) return 'Digit' + ch;
    return '';
  }

  async function type(el, text, opts) {
    if (!el || typeof text !== 'string') return { ok: false, error: 'invalid_args' };
    opts = opts || {};
    const interval = opts.intervalMs || [80, 140];
    const clearFirst = opts.clearFirst !== false;
    const proto = el.tagName === 'TEXTAREA'
      ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;

    el.focus();
    if (clearFirst && 'value' in el) {
      setter.call(el, '');
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    await _sleep(_randMs(80, 200));

    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      el.dispatchEvent(new KeyboardEvent('keydown', { key: ch, code: _keyCode(ch), bubbles: true, cancelable: true }));
      el.dispatchEvent(new KeyboardEvent('keypress', { key: ch, bubbles: true, cancelable: true }));
      setter.call(el, (el.value || '') + ch);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, data: ch, inputType: 'insertText' }));
      el.dispatchEvent(new KeyboardEvent('keyup', { key: ch, code: _keyCode(ch), bubbles: true }));
      let delay = _randMs(interval[0], interval[1]);
      if (Math.random() < 0.05) delay += _randMs(200, 600);
      await _sleep(delay);
    }
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return { ok: true, typed: text.length };
  }

  async function click(el, opts) {
    if (!el || typeof el.dispatchEvent !== 'function') return { ok: false, error: 'invalid_element' };
    opts = opts || {};
    const hoverRange = opts.hoverMs || [200, 400];
    const settleRange = opts.settleMs || [80, 180];

    const rect = el.getBoundingClientRect();
    if (!(rect.top >= 0 && rect.bottom <= window.innerHeight)) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      await _sleep(_randMs(300, 600));
    }
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const mk = (t) => new MouseEvent(t, {
      bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy, button: 0,
    });
    el.dispatchEvent(mk('mouseenter'));
    el.dispatchEvent(mk('mouseover'));
    el.dispatchEvent(mk('mousemove'));
    await _sleep(_randMs(hoverRange[0], hoverRange[1]));
    el.dispatchEvent(mk('mousedown'));
    await _sleep(_randMs(40, 80));
    el.dispatchEvent(mk('mouseup'));
    el.dispatchEvent(mk('click'));
    await _sleep(_randMs(settleRange[0], settleRange[1]));
    return { ok: true };
  }

  function selectOption(el, value) {
    if (!el || el.tagName !== 'SELECT') return { ok: false, error: 'not_select' };
    el.focus();
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return { ok: true };
  }

  async function readingPause(min, max) {
    await _sleep(_randMs(typeof min === 'number' ? min : 800, typeof max === 'number' ? max : 2000));
  }

  async function scrollTo(el, opts) {
    if (!el) return { ok: false };
    opts = opts || {};
    el.scrollIntoView({ behavior: 'smooth', block: opts.block || 'center' });
    await _sleep(_randMs(opts.minMs || 250, opts.maxMs || 600));
    return { ok: true };
  }

  window.AF_Human = { type, click, selectOption, readingPause, scrollTo, _randMs, _sleep };
})();
