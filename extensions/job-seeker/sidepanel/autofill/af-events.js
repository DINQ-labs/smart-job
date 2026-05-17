/*
 * sidepanel/shared/events.js — UI 反馈工具（toast）+ 共享 helper
 *
 * 暴露 window.AF.toast / window.AF.esc。
 */
(function () {
  'use strict';
  window.AF = window.AF || {};

  let timer = null;
  AF.toast = function (msg, kind) {
    const el = document.getElementById('af-toast');
    if (!el) return;
    el.textContent = msg;
    el.className = 'toast toast-' + (kind || 'info');
    clearTimeout(timer);
    timer = setTimeout(() => { el.className = 'toast hidden'; }, 3000);
  };

  // HTML 转义 —— 所有 innerHTML 拼接前必须过一遍
  AF.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
    ));
  };
})();
