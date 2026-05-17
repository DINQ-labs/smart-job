/*
 * modules/log.js — 填写记录
 *
 * 每次自动填写后追加一条，存 chrome.storage.local 'fillLog'（最多 50 条）。
 */
(function () {
  'use strict';
  window.AF = window.AF || {};
  const esc = (s) => AF.esc(s);

  let entries = [];

  function host() { return document.getElementById('view-log'); }

  async function init() {
    try {
      const r = await chrome.storage.local.get(['fillLog']);
      entries = r.fillLog || [];
    } catch { entries = []; }
  }

  async function add(e) {
    entries.unshift(Object.assign({ time: Date.now() }, e));
    entries = entries.slice(0, 50);
    try { await chrome.storage.local.set({ fillLog: entries }); } catch (_) {}
  }

  function render() {
    const el = host();
    if (!el) return;
    if (!entries.length) {
      el.innerHTML = '<div class="empty">' + t('autofill.logEmpty') + '</div>';
      return;
    }
    el.innerHTML = entries.map((e) => `
      <div class="card">
        <div class="log-title">${esc(e.title || e.url || t('autofill.logUnknownPage'))}</div>
        <div class="hint">${new Date(e.time).toLocaleString()}</div>
        <div class="log-stats">
          <span class="chip chip-green">${t('autofill.logFilled', { n: e.filled || 0 })}</span>
          ${e.failed ? '<span class="chip chip-red">' + t('autofill.logFailed', { n: e.failed }) + '</span>' : ''}
          ${e.unresolved ? '<span class="chip chip-amber">' + t('autofill.logPending', { n: e.unresolved }) + '</span>' : ''}
        </div>
      </div>
    `).join('');
  }

  AF.log = { init, add, render };
})();
