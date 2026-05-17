/**
 * core/voice-bridge.js — sidepanel ↔ offscreen 桥接(语音录音协调,跑在 service worker)
 *
 * 为什么要桥:
 *   - sidepanel 在 chrome-extension:// origin 下默认拒绝 getUserMedia
 *   - offscreen document(reasons:['USER_MEDIA'])是 Chrome 文档明确支持的麦克风入口
 *   - service worker 是唯一能调 chrome.offscreen.* API 的上下文
 *
 * 协议:监听 chrome.runtime.onMessage,只处理 target === 'voice-bridge'。
 *   { type: 'start' }                              → ensureOffscreen + 转发 → { ok }
 *   { type: 'stop',  payload: { asrUrl, userId } } → 转发 → { ok, text? }
 *   { type: 'abort' }                              → 转发 → { ok }
 *
 * offscreen 同一时刻只能存在一个,这里用 hasDocument 检测复用。
 */
(function () {
  'use strict';

  const OFFSCREEN_URL = 'lib/ext-core/offscreen/recorder.html';

  async function hasOffscreen() {
    // Chrome 116+: chrome.offscreen.hasDocument()
    if (typeof chrome.offscreen?.hasDocument === 'function') {
      try { return await chrome.offscreen.hasDocument(); } catch {}
    }
    // 兜底:用 getContexts 查
    if (typeof chrome.runtime?.getContexts === 'function') {
      try {
        const matches = await chrome.runtime.getContexts({
          contextTypes: ['OFFSCREEN_DOCUMENT'],
        });
        return matches.length > 0;
      } catch {}
    }
    return false;
  }

  let creating = null; // 防并发创建
  async function ensureOffscreen() {
    if (await hasOffscreen()) return;
    if (creating) return creating;
    creating = chrome.offscreen.createDocument({
      url: OFFSCREEN_URL,
      reasons: ['USER_MEDIA'],
      justification: '语音输入需要麦克风录音(讯飞 IAT)',
    });
    try { await creating; } finally { creating = null; }
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg?.target !== 'voice-bridge') return false;
    (async () => {
      try {
        await ensureOffscreen();
        const result = await chrome.runtime.sendMessage({
          target: 'offscreen-voice',
          type: msg.type,
          payload: msg.payload,
        });
        sendResponse(result || { ok: false, error: 'no response from offscreen' });
      } catch (e) {
        console.warn('[voice-bridge]', msg?.type, 'failed:', e);
        sendResponse({ ok: false, error: 'bridge: ' + (e?.message || e) });
      }
    })();
    return true; // async sendResponse
  });

  console.info('[voice-bridge] ready');
})();
