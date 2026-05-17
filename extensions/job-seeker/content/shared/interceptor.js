/**
 * interceptor.js — world: ISOLATED, run_at: document_idle
 * 监听来自 MAIN world 的 postMessage，转发给 background Service Worker。
 * 支持 zhipin（__bossApiExt）和通用站点（__dinqExt）两种消息协议。
 */
(function () {
  'use strict';

  function safeSend(msg) {
    try {
      chrome.runtime.sendMessage(msg, () => {
        void chrome.runtime.lastError;
      });
    } catch (e) {}
  }

  window.addEventListener('message', (event) => {
    // 审核补丁 #R6：只信任**同一 window 自己发**的 message。MAIN world 与
    // ISOLATED world 共用同一个 window 对象（postMessage 跨 world 的正常
    // 路径），所以 event.source === window 能在接受 MAIN → ISOLATED 合法
    // 消息的同时，过滤掉来自 iframe / popup / 其他窗口的伪造消息。
    // event.origin 在扩展 content script 上下文下有时是 "null"（比如 worker
    // tab 里注入），所以不强制校验 origin，转而用 __bossApiExt / __dinqExt
    // 这两个魔字段做协议级 sanity。
    if (event.source !== window) return;
    const d = event.data;
    if (!d || typeof d !== 'object') return;

    if (d.__bossApiExt) {
      // zhipin 原有协议
      const { type, jobs, data, fp, zpStoken, token, zp_token } = d;
      switch (type) {
        case 'JOB_LIST_TOKENS':  safeSend({ type: 'JOB_LIST_TOKENS', jobs }); break;
        case 'TOKEN_UPDATE':     safeSend({ type: 'TOKEN_UPDATE', data });    break;
        case 'FINGERPRINT':      safeSend({ type: 'FINGERPRINT', fp });       break;
        case 'ZP_STOKEN_ROTATE': safeSend({ type: 'ZP_STOKEN_ROTATE', zpStoken }); break;
        case 'REQUEST_TOKENS':   safeSend({ type: 'REQUEST_TOKENS', token, zp_token }); break;
      }
    } else if (d.__dinqExt) {
      // 通用站点 token 消息（linkedin, indeed 等）
      safeSend({ type: 'SITE_TOKEN', site: d.site, tokenType: d.type, payload: d });
    }
  });

  // 定期重新读取 zhipin 指纹（仅在 zhipin 域名下有效）
  if (location.hostname.endsWith('zhipin.com')) {
    setTimeout(() => {
      try {
        const fp = {
          deviceId: localStorage.getItem('pc_device_id') || '',
          guid: localStorage.getItem('guid') || '',
        };
        if (fp.deviceId || fp.guid) safeSend({ type: 'FINGERPRINT', fp });
      } catch (e) {}
    }, 3000);
  }
})();
