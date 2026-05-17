/*
 * sidepanel/shared/api.js — 与 background 通信的桥接层
 *
 * sidepanel 不直接 fetch；所有后端/页面操作都经 background 中转。
 * 暴露 window.AF.api。
 */
(function () {
  'use strict';
  window.AF = window.AF || {};

  function send(type, payload) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type, payload }, (resp) => {
          if (chrome.runtime.lastError) {
            resolve({ ok: false, error: chrome.runtime.lastError.message });
          } else {
            resolve(resp || { ok: false, error: t('autofill.noResponse') });
          }
        });
      } catch (e) {
        resolve({ ok: false, error: String(e) });
      }
    });
  }

  AF.api = {
    detect:      ()  => send('AF_DETECT'),
    match:       (p) => send('AF_MATCH', p),
    fill:        (p) => send('AF_FILL', p),
    capture:     ()  => send('AF_CAPTURE'),
    ocr:         (p) => send('AF_OCR', p),
    ocrDetect:   (p) => send('AF_OCR_DETECT', p),
    getProfile:  ()  => send('AF_GET_PROFILE'),
    saveProfile: (p) => send('AF_SAVE_PROFILE', p),
    highlight:   (p) => send('AF_HIGHLIGHT', p),
    recordTemplate: (p) => send('AF_RECORD_TEMPLATE', p),
    captureBegin:    () => send('AF_CAPTURE_BEGIN'),
    captureFinalize: () => send('AF_CAPTURE_FINALIZE'),
    captureFlushNow: () => send('AF_CAPTURE_FLUSH'),
    clickNav:       (p) => send('AF_CLICK_NAV', p),
    uploadResume:   (p) => send('AF_UPLOAD_RESUME', p),
  };
})();
