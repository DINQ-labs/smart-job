/*! SmartJob i18n 词典 —— background 通知 */
;(function () {
  var ns = {
    zh: {
      'bg.notLoggedIn': '未登录，请先在扩展侧栏完成登录'
    },
    en: {
      'bg.notLoggedIn': 'Not signed in. Please sign in via the extension side panel first.'
    }
  };
  if (typeof self !== 'undefined' && self.DQI18N) self.DQI18N.register(ns);
  else {
    var g = typeof self !== 'undefined' ? self : (typeof globalThis !== 'undefined' ? globalThis : {});
    (g.__DQ_I18N__ = g.__DQ_I18N__ || []).push(ns);
  }
})();
