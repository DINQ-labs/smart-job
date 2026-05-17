/*
 * sidepanel/modules/recorder-view.js — API 录制视图（工具 tab 二级菜单项）
 *
 * UI 端，经 chrome.runtime.sendMessage({ action }) 与 lib/recorder/recorder-bg.js
 * 通信。CDP 隐身抓取当前标签页的 API 流量并送往本地 agent-gateway。
 *
 * 暴露 window.DQ.recorderView = { render, activate, deactivate }。
 */
(function () {
  'use strict';
  window.DQ = window.DQ || {};

  const esc = (s) => String(s == null ? '' : s)
    .replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  let tabId = null;
  let recording = false;
  let pollTimer = null;

  function host() { return document.getElementById('view-recorder'); }

  function send(action, data) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(Object.assign({ action }, data || {}), (resp) => {
          void chrome.runtime.lastError;
          resolve(resp || null);
        });
      } catch (e) { resolve(null); }
    });
  }

  async function ensureTab() {
    if (tabId != null) return tabId;
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      tabId = tab && tab.id != null ? tab.id : null;
    } catch (e) { tabId = null; }
    return tabId;
  }

  function render() {
    const el = host();
    if (!el) return;
    el.innerHTML = `
      <div class="card">
        <div class="card-title">
          ${t('recorder.cardTitle')} <span class="rec-dot" id="recDot"></span>
        </div>
        <div class="hint">${t('recorder.hint')}</div>
        <div class="rec-controls">
          <button class="btn rec-btn rec-rec" id="recRecord">${t('recorder.startBtn')}</button>
          <button class="btn rec-btn rec-stop" id="recStop" disabled>${t('recorder.stopBtn')}</button>
          <button class="btn rec-btn rec-clear" id="recClear">${t('recorder.clearBtn')}</button>
          <button class="btn rec-btn rec-flush" id="recFlush">${t('recorder.flushBtn')}</button>
        </div>
        <div class="rec-namebar hidden" id="recNameBar">
          <input class="input" id="recNameInput" type="text" placeholder="${t('recorder.namePlaceholder')}">
          <button class="btn btn-primary" id="recNameStart">${t('recorder.nameStartBtn')}</button>
          <button class="btn" id="recNameCancel">${t('recorder.nameCancelBtn')}</button>
        </div>
        <div class="rec-status">
          <span id="recStatusText">${t('recorder.statusIdle')}</span>
          <span id="recCountText">0${t('recorder.countSuffix')}</span>
        </div>
        <div class="rec-filter">
          <input class="input" id="recDomainInput" type="text"
            placeholder="${t('recorder.domainPlaceholder')}">
          <button class="btn" id="recSetDomain">${t('recorder.domainApplyBtn')}</button>
        </div>
      </div>
      <div class="card">
        <div class="card-title">${t('recorder.requestsTitle')}</div>
        <div class="rec-list" id="recList">
          <div class="empty">${t('recorder.emptyInit')}</div>
        </div>
      </div>
    `;
    el.querySelector('#recRecord').addEventListener('click', onRecord);
    el.querySelector('#recStop').addEventListener('click', onStop);
    el.querySelector('#recClear').addEventListener('click', onClear);
    el.querySelector('#recFlush').addEventListener('click', onFlush);
    el.querySelector('#recNameStart').addEventListener('click', onNameStart);
    el.querySelector('#recNameCancel').addEventListener('click', () => {
      el.querySelector('#recNameBar').classList.add('hidden');
    });
    el.querySelector('#recNameInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') onNameStart();
    });
    el.querySelector('#recSetDomain').addEventListener('click', onSetDomain);
    ensureTab().then(refresh);
  }

  function onRecord() {
    const bar = host()?.querySelector('#recNameBar');
    const inp = host()?.querySelector('#recNameInput');
    if (bar) bar.classList.remove('hidden');
    if (inp) { inp.value = ''; inp.focus(); }
  }

  async function onNameStart() {
    const el = host();
    if (!el) return;
    await ensureTab();
    if (tabId == null) { (window.AF && AF.toast) ? AF.toast(t('recorder.noTabError'), 'error') : alert(t('recorder.noTabError')); return; }
    const sessionName = el.querySelector('#recNameInput').value.trim();
    el.querySelector('#recNameBar').classList.add('hidden');
    const resp = await send('startRecording', { tabId, sessionName });
    if (resp && resp.success) {
      recording = true;
      refresh();
    } else {
      const msg = t('recorder.startFail') + ((resp && resp.error) || t('recorder.unknownErr'));
      (window.AF && AF.toast) ? AF.toast(msg, 'error') : alert(msg);
    }
  }

  async function onStop() {
    await ensureTab();
    if (tabId == null) return;
    await send('stopRecording', { tabId });
    recording = false;
    refresh();
  }

  async function onClear() {
    await ensureTab();
    if (tabId == null) return;
    await send('clearRequests', { tabId });
    refresh();
  }

  async function onFlush() {
    const btn = host()?.querySelector('#recFlush');
    await ensureTab();
    if (tabId == null) return;
    if (btn) { btn.disabled = true; btn.textContent = t('recorder.flushing'); }
    const resp = await send('flushToServer', { tabId });
    if (btn) { btn.disabled = false; btn.textContent = t('recorder.flushBtn'); }
    const msg = (resp && resp.sent !== undefined)
      ? t('recorder.flushOk', { n: resp.sent })
      : (t('recorder.flushFail') + ((resp && resp.error) || t('recorder.flushUnreachable')));
    (window.AF && AF.toast) ? AF.toast(msg, resp && resp.sent !== undefined ? 'success' : 'error') : alert(msg);
  }

  async function onSetDomain() {
    const el = host();
    const raw = el.querySelector('#recDomainInput').value.trim();
    const domains = raw ? raw.split(',').map((d) => d.trim()).filter(Boolean) : [];
    await send('updateSettings', { settings: { domainFilter: domains } });
    (window.AF && AF.toast) ? AF.toast(t('recorder.domainUpdated'), 'success') : null;
  }

  function methodClass(m) {
    return 'rec-m-' + String(m || 'GET').toUpperCase();
  }
  function shortUrl(url) {
    try { const u = new URL(url); return u.pathname + u.search; }
    catch (e) { return String(url || '').slice(0, 80); }
  }

  function renderRequests(requests) {
    const box = host()?.querySelector('#recList');
    if (!box) return;
    if (!requests || !requests.length) {
      box.innerHTML = `<div class="empty">${t('recorder.emptyList')}</div>`;
      return;
    }
    box.innerHTML = requests.map((req) => {
      const method = esc((req.request && req.request.method) || 'GET');
      const status = req.response ? req.response.status : '…';
      const sClass = (status >= 200 && status < 400) ? 'ok' : 'err';
      return `<div class="rec-item" title="${esc((req.request && req.request.url) || '')}">
        <span class="rec-method ${methodClass(method)}">${method}</span>
        <span class="rec-st ${sClass}">${esc(status)}</span>
        <span class="rec-url">${esc(shortUrl(req.request && req.request.url))}</span>
        <span class="rec-rt">${esc(req.resourceType || '')}</span>
      </div>`;
    }).join('');
  }

  async function refresh() {
    const el = host();
    if (!el || tabId == null) return;
    const status = await send('getStatus', { tabId });
    if (status) {
      recording = !!status.recording;
      const st = el.querySelector('#recStatusText');
      const ct = el.querySelector('#recCountText');
      if (ct) ct.textContent = (status.completedCount || 0) + t('recorder.countSuffix');
      if (st) {
        if (recording) { st.textContent = t('recorder.statusRecording'); st.className = 'rec-on'; }
        else if (status.detachReason) { st.textContent = t('recorder.statusStopped', { reason: status.detachReason }); st.className = 'rec-idle'; }
        else { st.textContent = t('recorder.statusIdle'); st.className = 'rec-idle'; }
      }
      el.querySelector('#recRecord').disabled = recording;
      el.querySelector('#recStop').disabled = !recording;
    }
    const health = await send('checkServer');
    const dot = el.querySelector('#recDot');
    if (dot) dot.className = 'rec-dot ' + (health && health.online ? 'online' : 'offline');

    const domainInput = el.querySelector('#recDomainInput');
    if (document.activeElement !== domainInput) {
      const s = await send('getSettings');
      if (s && s.settings && s.settings.domainFilter) {
        domainInput.value = s.settings.domainFilter.join(', ');
      }
    }
    const reqs = await send('getRequests', { tabId, limit: 40 });
    if (reqs && reqs.requests) renderRequests(reqs.requests);
  }

  // 录制子系统在每条请求完成时广播 captureUpdate
  chrome.runtime.onMessage.addListener((message) => {
    if (message && message.action === 'captureUpdate' && message.tabId === tabId) {
      if (host()) refresh();
    }
  });

  function activate() {
    render();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => { if (host()) refresh(); }, 5000);
  }
  function deactivate() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  window.DQ.recorderView = { render, activate, deactivate };
})();
