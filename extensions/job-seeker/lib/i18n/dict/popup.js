/*! DingQ i18n 词典 —— popup 弹窗 */
;(function () {
  var ns = {
    zh: {
      'popup.title':       'DingQ v3',
      'popup.h2':          'DingQ v3',
      'popup.btn.panel':   '打开侧边栏',
      'popup.btn.options': '设置'
    },
    en: {
      'popup.title':       'DingQ v3',
      'popup.h2':          'DingQ v3',
      'popup.btn.panel':   'Open Side Panel',
      'popup.btn.options': 'Settings'
    }
  };
  if (window.DQI18N) window.DQI18N.register(ns);
  else (window.__DQ_I18N__ = window.__DQ_I18N__ || []).push(ns);
})();
