/*!
 * DingQ i18n 语言切换组件
 * 任何页面在元素上加 data-i18n-switch 属性,本组件即自动:
 *   - 把元素文字设为「切到另一语言」的标签(中 / EN)
 *   - 点击后切换语言并 reload 当前页面,确保 JS 动态渲染的文案也整体刷新
 * 用法:<button class="header-lang" data-i18n-switch type="button"></button>
 */
;(function () {
  'use strict';
  if (typeof document === 'undefined' || !window.DQI18N) return;

  function otherLabel() {
    return window.DQI18N.getLocale() === 'zh' ? 'EN' : '中';
  }

  function wire() {
    var els = document.querySelectorAll('[data-i18n-switch]');
    for (var i = 0; i < els.length; i++) {
      (function (el) {
        if (el.__i18nSwitchWired) return;
        el.__i18nSwitchWired = true;
        el.textContent = otherLabel();
        el.setAttribute('title',
          window.DQI18N.getLocale() === 'zh' ? 'Switch to English' : '切换为中文');
        el.addEventListener('click', function () {
          var next = window.DQI18N.getLocale() === 'zh' ? 'en' : 'zh';
          window.DQI18N.setLocale(next).then(function () {
            try { location.reload(); } catch (e) {}
          });
        });
      })(els[i]);
    }
  }

  if (window.DQI18N.ready && window.DQI18N.ready.then) window.DQI18N.ready.then(wire);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }
})();
