/**
 * Stealth script — world: MAIN, run_at: document_start
 * 直接在页面主世界执行，无需注入 <script> 标签，不受 CSP 限制。
 */
(function () {
  try {
    var nav = typeof navigator !== 'undefined' ? navigator : null;
    if (!nav) return;

    // 隐藏 webdriver 标志
    var desc = Object.getOwnPropertyDescriptor(nav, 'webdriver');
    if (desc && desc.configurable) {
      Object.defineProperty(nav, 'webdriver', {
        get: function () { return false; },
        configurable: true,
        enumerable: true,
      });
    } else {
      try {
        Object.defineProperty(nav, 'webdriver', {
          get: function () { return false; },
          configurable: true,
          enumerable: true,
        });
      } catch (e) {}
    }
    try { delete nav.webdriver; } catch (e) {}
    if (nav.webdriver !== false) { try { nav.webdriver = false; } catch (e) {} }

    var proto = Object.getPrototypeOf && Object.getPrototypeOf(nav);
    if (proto && typeof proto.webdriver !== 'undefined') {
      try { proto.webdriver = false; } catch (e) {}
    }
    if (nav.__proto__) { try { nav.__proto__.webdriver = false; } catch (e) {} }

    // 标记扩展已注入
    var d = document;
    if (d.documentElement && !d.documentElement.hasAttribute('data-boss-api-ext')) {
      d.documentElement.setAttribute('data-boss-api-ext', '1');
    }
  } catch (e) {}
})();
