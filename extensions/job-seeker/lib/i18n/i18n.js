/*!
 * DingQ i18n 引擎 —— 中 / 英双语
 * ────────────────────────────────────────────────────────────────
 * API(window.DQI18N):
 *   t(key, vars)        取译文;vars 支持 {name} 占位插值
 *   getLocale()         当前语言 'zh' | 'en'
 *   setLocale(loc)      切换语言(持久化到 chrome.storage + 通知)→ Promise
 *   onChange(fn)        订阅语言变更,返回取消订阅函数
 *   apply(root)         扫描 root 下 data-i18n* 属性并写入译文
 *   register({zh,en})   注册一个词典命名空间(各 dict/*.js 调用)
 *   ready               Promise,init 完成后 resolve
 * 全局便捷:window.t === DQI18N.t
 *
 * HTML 标注方式:
 *   <span data-i18n="tabs.tasks">任务</span>             → textContent
 *   <input data-i18n-attr="placeholder:chat.ph;title:x">  → 指定属性
 *   <div  data-i18n-html="login.desc"></div>              → innerHTML
 *
 * Service Worker 无 DOM —— 引擎对 document / navigator 做存在性保护,
 * background 里只用 t() / getLocale() / setLocale() 即可。
 */
;(function (global) {
  'use strict';

  var STORAGE_KEY = 'dq_locale';
  var SUPPORTED   = ['zh', 'en'];
  var hasDOM      = typeof document !== 'undefined';
  var hasStore    = (typeof chrome !== 'undefined') && chrome.storage && chrome.storage.local;

  var dict      = { zh: {}, en: {} };
  var locale    = 'zh';
  var inited    = false;
  var listeners = [];

  function detectLocale() {
    var lang = '';
    try { lang = ((global.navigator && global.navigator.language) || '').toLowerCase(); }
    catch (e) {}
    if (!lang) return 'zh';
    return lang.indexOf('zh') === 0 ? 'zh' : 'en';
  }

  function register(ns) {
    if (!ns) return;
    for (var li = 0; li < SUPPORTED.length; li++) {
      var L = SUPPORTED[li];
      if (!ns[L]) continue;
      for (var k in ns[L]) if (ns[L].hasOwnProperty(k)) dict[L][k] = ns[L][k];
    }
    if (inited && hasDOM) apply(document);
  }

  function t(key, vars) {
    var s = dict[locale] && dict[locale][key];
    if (s == null) s = dict.zh && dict.zh[key];   // 缺译回退中文
    if (s == null) s = key;                        // 仍缺则显示 key 本身
    if (vars) s = String(s).replace(/\{(\w+)\}/g, function (m, n) {
      return vars[n] != null ? vars[n] : m;
    });
    return s;
  }

  function apply(root) {
    if (!hasDOM) return;
    root = root || document;
    var els, i, j;
    els = root.querySelectorAll('[data-i18n]');
    for (i = 0; i < els.length; i++) els[i].textContent = t(els[i].getAttribute('data-i18n'));
    els = root.querySelectorAll('[data-i18n-html]');
    for (i = 0; i < els.length; i++) els[i].innerHTML = t(els[i].getAttribute('data-i18n-html'));
    els = root.querySelectorAll('[data-i18n-attr]');
    for (i = 0; i < els.length; i++) {
      var spec = els[i].getAttribute('data-i18n-attr').split(';');
      for (j = 0; j < spec.length; j++) {
        var pair = spec[j].split(':');
        var attr = (pair[0] || '').trim();
        var key  = (pair[1] || '').trim();
        if (attr && key) els[i].setAttribute(attr, t(key));
      }
    }
    // 仅在扩展自己的页面改 <html lang>;content script 注入到网站页面时不动宿主页
    if (root === document && document.documentElement &&
        typeof location !== 'undefined' && location.protocol === 'chrome-extension:') {
      document.documentElement.lang = (locale === 'zh') ? 'zh-CN' : 'en';
    }
  }

  function emit() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](locale); } catch (e) {}
    }
  }

  function getLocale() { return locale; }

  function setLocale(loc) {
    if (SUPPORTED.indexOf(loc) < 0 || loc === locale) return Promise.resolve(locale);
    locale = loc;
    var done = Promise.resolve();
    if (hasStore) {
      done = new Promise(function (res) {
        var o = {}; o[STORAGE_KEY] = loc;
        chrome.storage.local.set(o, function () { res(); });
      });
    }
    return done.then(function () {
      if (hasDOM) apply(document);
      emit();
      return locale;
    });
  }

  function onChange(fn) {
    listeners.push(fn);
    return function () {
      var i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    };
  }

  function init() {
    var get = Promise.resolve(null);
    if (hasStore) {
      get = new Promise(function (res) {
        chrome.storage.local.get(STORAGE_KEY, function (r) { res(r && r[STORAGE_KEY]); });
      });
    }
    return get.then(function (saved) {
      locale = (SUPPORTED.indexOf(saved) >= 0) ? saved : detectLocale();
      inited = true;
      if (hasDOM) apply(document);
      emit();
      return locale;
    });
  }

  var API = {
    t: t, register: register, apply: apply,
    getLocale: getLocale, setLocale: setLocale, onChange: onChange,
    SUPPORTED: SUPPORTED, init: init, ready: null
  };
  global.DQI18N = API;
  global.t = t;

  // 引擎加载前若已有 dict 排队(防御:正常顺序下 i18n.js 总是先加载),先吸收
  if (global.__DQ_I18N__ && global.__DQ_I18N__.length) {
    for (var qi = 0; qi < global.__DQ_I18N__.length; qi++) register(global.__DQ_I18N__[qi]);
    global.__DQ_I18N__ = [];
  }

  if (hasDOM && document.readyState === 'loading') {
    API.ready = new Promise(function (res) {
      document.addEventListener('DOMContentLoaded', function () { init().then(res); });
    });
  } else {
    API.ready = init();
  }
})(typeof self !== 'undefined' ? self : this);
