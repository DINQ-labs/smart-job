/*
 * content/main-world/net-interceptor.js — HTTP 请求拦截（MAIN world，K4）
 *
 * 覆写 fetch 与 XMLHttpRequest，把**第一方** XHR/fetch 的请求/响应摘要经
 * window.postMessage 发给 ISOLATED world 的 detector.js 缓冲。
 *
 * 隐私：只抓同源请求；不抓任何请求头（避免 Authorization / Cookie 外泄）；
 * body 截断；值脱敏由 background 在上传前再做一道。
 *
 * 由 background 在「抓包开关」开启且用户点检测时按需注入。
 */
(function () {
  'use strict';
  if (window.__AF_NET__) return;
  window.__AF_NET__ = true;

  var ORIGIN = location.origin;
  var MAX_BODY = 16384;
  var BODY_MIME = /json|text|xml|form|javascript/i;

  function clip(s) {
    if (typeof s !== 'string') return '';
    return s.length > MAX_BODY ? s.slice(0, MAX_BODY) : s;
  }

  function sameOrigin(url) {
    try { return new URL(url, location.href).origin === ORIGIN; }
    catch (e) { return false; }
  }

  function post(rec) {
    try { window.postMessage({ __afCapture: true, rec: rec }, ORIGIN); }
    catch (e) {}
  }

  // ── fetch ────────────────────────────────────────────────────────────────
  var origFetch = window.fetch;
  if (typeof origFetch === 'function') {
    window.fetch = async function () {
      var args = arguments;
      var a0 = args[0];
      var url = typeof a0 === 'string' ? a0 : (a0 && a0.url) || '';
      var init = args[1] || {};
      var method = (init.method || (a0 && a0.method) || 'GET').toUpperCase();
      var reqBody = typeof init.body === 'string' ? init.body : '';
      var resp = await origFetch.apply(this, args);
      try {
        if (sameOrigin(url)) {
          var clone = resp.clone();
          var ct = clone.headers.get('content-type') || '';
          var respBody = BODY_MIME.test(ct) ? await clone.text() : '';
          post({
            method: method, url: url, resource_type: 'fetch',
            timestamp: Date.now(),
            req_body: clip(reqBody),
            status: resp.status, resp_mime: ct,
            resp_body: clip(respBody),
          });
        }
      } catch (e) {}
      return resp;
    };
  }

  // ── XMLHttpRequest ───────────────────────────────────────────────────────
  var X = window.XMLHttpRequest;
  if (X && X.prototype) {
    var origOpen = X.prototype.open;
    var origSend = X.prototype.send;
    X.prototype.open = function (method, url) {
      this.__af = { method: (method || 'GET').toUpperCase(), url: url || '' };
      return origOpen.apply(this, arguments);
    };
    X.prototype.send = function (body) {
      var meta = this.__af;
      if (meta && sameOrigin(meta.url)) {
        var xhr = this;
        this.addEventListener('load', function () {
          try {
            var ct = xhr.getResponseHeader('content-type') || '';
            var rb = '';
            try { rb = BODY_MIME.test(ct) ? (xhr.responseText || '') : ''; }
            catch (e) {}
            post({
              method: meta.method, url: meta.url, resource_type: 'xhr',
              timestamp: Date.now(),
              req_body: clip(typeof body === 'string' ? body : ''),
              status: xhr.status, resp_mime: ct,
              resp_body: clip(rb),
            });
          } catch (e) {}
        });
      }
      return origSend.apply(this, arguments);
    };
  }
})();
