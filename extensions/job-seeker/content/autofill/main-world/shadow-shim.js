/*
 * content/main-world/shadow-shim.js — closed shadow DOM 垫片（MAIN world, document_start）
 *
 * 把 attachShadow 的 mode:'closed' 强制改成 'open'，让 detector 能穿透遍历。
 * 必须早于页面调用 attachShadow，故 manifest 声明 run_at: document_start。
 *
 * 仅注入到已知 ATS 域名（manifest content_scripts.matches 限定），不全网篡改。
 */
(function () {
  'use strict';
  if (window.__AF_SHADOW_SHIM__) return;
  window.__AF_SHADOW_SHIM__ = true;

  try {
    const orig = Element.prototype.attachShadow;
    if (typeof orig !== 'function') return;
    Element.prototype.attachShadow = function (init) {
      const opts = Object.assign({}, init || {}, { mode: 'open' });
      return orig.call(this, opts);
    };
  } catch (e) { /* 某些环境禁止改原型，静默放弃 */ }
})();
