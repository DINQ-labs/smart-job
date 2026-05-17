/**
 * offscreen/recorder.js — 麦克风录音 + 流式 WebSocket 上传讯飞代理
 *
 * 由 background service worker 通过
 *   chrome.offscreen.createDocument({reasons:['USER_MEDIA']})
 * 启动。所有 getUserMedia 必须在这里做 — sidepanel 在 chrome-extension://
 * origin 下默认拒绝麦克风,只有 offscreen+USER_MEDIA 上下文 Chrome 会弹原生权限弹窗。
 *
 * 协议(被 voice-bridge.js 调用,跑在本扩展 chrome.runtime 频道里):
 *   { target:'offscreen-voice', type:'start', payload:{wsUrl, userId} }  → {ok}
 *   { target:'offscreen-voice', type:'stop'  }                           → {ok}
 *   { target:'offscreen-voice', type:'abort' }                           → {ok}
 *
 * 实时识别结果转发(主动推):
 *   chrome.runtime.sendMessage({ target:'sidepanel-voice', type:'partial', text, sn, is_final, is_seg_end })
 *   chrome.runtime.sendMessage({ target:'sidepanel-voice', type:'error',   error })
 *   chrome.runtime.sendMessage({ target:'sidepanel-voice', type:'closed' })
 */
(function () {
  'use strict';

  let stream = null;
  let audioCtx = null;
  let source = null;
  let processor = null;
  let ws = null;
  let recording = false;

  function floatTo16(f32) {
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return i16;
  }

  function teardownAudio() {
    try { processor?.disconnect(); } catch {}
    try { source?.disconnect(); } catch {}
    try { audioCtx?.close(); } catch {}
    stream?.getTracks().forEach((t) => { try { t.stop(); } catch {} });
    processor = source = audioCtx = stream = null;
  }

  function closeWs(code = 1000, reason = '') {
    if (!ws) return;
    try { if (ws.readyState <= 1) ws.close(code, reason); } catch {}
    ws = null;
  }

  function pushToSidepanel(payload) {
    // sidepanel 监听 target='sidepanel-voice' → 实时更新 UI
    chrome.runtime.sendMessage({ target: 'sidepanel-voice', ...payload }, () => {
      // 没监听时 lastError 会有值,忽略即可
      void chrome.runtime.lastError;
    });
  }

  async function startRecording(payload) {
    if (recording) return { ok: true };
    const { wsUrl, userId } = payload || {};
    if (!wsUrl) return { ok: false, error: 'missing wsUrl' };

    // 1. 拿麦克风(首次会弹 Chrome 原生授权弹窗)
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      console.warn('[recorder] getUserMedia failed', err?.name, err?.message);
      return {
        ok: false,
        errorName: err?.name || 'Error',
        error: (err?.name || 'Error') + ': ' + (err?.message || ''),
      };
    }

    // 2. 开 WebSocket 到后端
    const fullUrl = wsUrl + (userId ? `?user_id=${encodeURIComponent(userId)}` : '');
    try {
      ws = new WebSocket(fullUrl);
      ws.binaryType = 'arraybuffer';
    } catch (err) {
      teardownAudio();
      return { ok: false, error: 'ws ctor failed: ' + (err?.message || err) };
    }

    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.type === 'partial' || msg.type === 'error') {
        pushToSidepanel(msg);
      }
    };
    ws.onerror = (e) => {
      console.warn('[recorder] ws error', e);
      pushToSidepanel({ type: 'error', error: 'websocket error' });
    };
    ws.onclose = () => {
      pushToSidepanel({ type: 'closed' });
    };

    // 等 WS open(讯飞鉴权一旦失败,onclose 立即触发)
    try {
      await new Promise((resolve, reject) => {
        const t = setTimeout(() => reject(new Error('ws open timeout')), 8000);
        ws.addEventListener('open',  () => { clearTimeout(t); resolve(); }, { once: true });
        ws.addEventListener('error', () => { clearTimeout(t); reject(new Error('ws connect error')); }, { once: true });
      });
    } catch (err) {
      teardownAudio();
      closeWs();
      return { ok: false, error: err.message || String(err) };
    }

    // 3. 接 audio graph,onaudioprocess 直接 send PCM 给 server
    const AC = window.AudioContext || window.webkitAudioContext;
    try { audioCtx = new AC({ sampleRate: 16000 }); }
    catch { audioCtx = new AC(); }
    source = audioCtx.createMediaStreamSource(stream);
    processor = audioCtx.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = (e) => {
      if (!recording || !ws || ws.readyState !== WebSocket.OPEN) return;
      const f32 = e.inputBuffer.getChannelData(0);
      const i16 = floatTo16(f32);
      try { ws.send(i16.buffer); } catch {}
    };
    source.connect(processor);
    processor.connect(audioCtx.destination);

    recording = true;
    return { ok: true };
  }

  async function stopRecording() {
    if (!recording) return { ok: true };
    recording = false;
    // 通知后端发讯飞 last 帧;后端会继续推 partial(直到 status=2),sidepanel 收
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ type: 'end' })); } catch {}
    }
    teardownAudio();
    // ws 不立即关 — 等 server 推完 final 后 close → onclose 通知 sidepanel
    return { ok: true };
  }

  function abortRecording() {
    recording = false;
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ type: 'abort' })); } catch {}
    }
    teardownAudio();
    closeWs();
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg?.target !== 'offscreen-voice') return false;
    (async () => {
      let result;
      try {
        if (msg.type === 'start') {
          result = await startRecording(msg.payload);
        } else if (msg.type === 'stop') {
          result = await stopRecording();
        } else if (msg.type === 'abort') {
          result = abortRecording();
        } else {
          result = { ok: false, error: 'unknown type: ' + msg.type };
        }
      } catch (e) {
        result = { ok: false, error: 'exception: ' + (e?.message || e) };
      }
      sendResponse(result);
    })();
    return true;
  });

  console.info('[recorder] offscreen ready (streaming mode)');
})();
