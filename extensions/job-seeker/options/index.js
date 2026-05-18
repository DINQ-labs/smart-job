/*
 * options/index.js — 设置页：后端地址（portal-api / api-gw / agent-gw 各自独立填写）+ 账号状态
 *
 * 三个后端地址各用一个输入框单独设置，存到 chrome.storage.local：
 *   portalApiUrl  账号 / 鉴权     (job-portal-api,    默认 https://api.smartjob.top/portal)
 *   apiGwUrl      命令网关 / WS   (job-api-gateway,   默认 https://api.smartjob.top)
 *   agentGwUrl    Agent 对话/录制 (job-agent-gateway, 默认 https://api.smartjob.top/agent-gw)
 *
 * 各消费方（sidepanel/shared/api.js、auth-api.js、background/index.js、
 * lib/recorder/recorder-bg.js、lib/ext-core/core/task-monitor.js、
 * lib/autofill/autofill-bg.js）优先读这三个 key；为空时回退到旧的 gatewayHost 推导，
 * 对老安装向下兼容。
 */
(function () {
  'use strict';

  // 预设：一键填好三个地址
  const PRESETS = {
    localhost: {
      portalApiUrl: 'http://127.0.0.1:8771',
      apiGwUrl:     'http://127.0.0.1:8767',
      agentGwUrl:   'http://127.0.0.1:8769',
    },
    // 演示站:部署在 smartjob.top —— 三个网关同在 api.smartjob.top 下,按路径前缀分发
    demo: {
      portalApiUrl: 'https://api.smartjob.top/portal',
      apiGwUrl:     'https://api.smartjob.top',
      agentGwUrl:   'https://api.smartjob.top/agent-gw',
    },
  };

  const els = {
    presetRow:  document.getElementById('presetRow'),
    portalApi:  document.getElementById('portalApiUrl'),
    apiGw:      document.getElementById('apiGwUrl'),
    agentGw:    document.getElementById('agentGwUrl'),
    saveBtn:    document.getElementById('saveBtn'),
    pingBtn:    document.getElementById('pingBtn'),
    savedTip:   document.getElementById('savedTip'),
    healthRows: document.getElementById('healthRows'),
    accountRow: document.getElementById('accountRow'),
    connDot:        document.getElementById('connDot'),
    connText:       document.getElementById('connText'),
    connMeta:       document.getElementById('connMeta'),
    connRefreshBtn: document.getElementById('connRefreshBtn'),
  };

  const norm = (s) => (s || '').trim().replace(/\/+$/, '');

  // ── 老安装迁移：没存过显式 URL 时，按旧的 gatewayHost 推导一次 ─────────
  function isLocal(host) { return /^(127\.0\.0\.1|localhost)/.test(host); }
  function hostNoPort(host) { return (host || '').split(':')[0]; }
  function derivePortalApi(host) {
    return isLocal(host) ? `http://${hostNoPort(host)}:8771` : PRESETS.demo.portalApiUrl;
  }
  function deriveApiGw(host) {
    return isLocal(host) ? `http://${hostNoPort(host)}:8767` : PRESETS.demo.apiGwUrl;
  }
  function deriveAgentGw(host) {
    return isLocal(host) ? `http://${hostNoPort(host)}:8769` : PRESETS.demo.agentGwUrl;
  }

  function syncPresetActive() {
    const cur = {
      portalApiUrl: norm(els.portalApi.value),
      apiGwUrl:     norm(els.apiGw.value),
      agentGwUrl:   norm(els.agentGw.value),
    };
    let matched = 'custom';
    for (const key of ['localhost', 'demo']) {
      const p = PRESETS[key];
      if (p.portalApiUrl === cur.portalApiUrl &&
          p.apiGwUrl === cur.apiGwUrl &&
          p.agentGwUrl === cur.agentGwUrl) { matched = key; break; }
    }
    els.presetRow.querySelectorAll('button').forEach((b) => {
      b.classList.toggle('active', b.dataset.preset === matched);
    });
  }

  // ── 加载 / 保存 ─────────────────────────────────────────────────
  async function load() {
    const r = await chrome.storage.local.get(['portalApiUrl', 'apiGwUrl', 'agentGwUrl', 'gatewayHost']);
    const host = r.gatewayHost || '127.0.0.1';
    els.portalApi.value = r.portalApiUrl || derivePortalApi(host);
    els.apiGw.value     = r.apiGwUrl     || deriveApiGw(host);
    els.agentGw.value   = r.agentGwUrl   || deriveAgentGw(host);
    syncPresetActive();
    await renderAccount();
    renderConnStatus();
  }

  // ── 扩展连接状态（后台 Service Worker ↔ api-gw WebSocket）─────────
  // 向 background 发 getStatus 取 ws 实时状态;后台 connectionState 广播时实时刷新。
  async function renderConnStatus() {
    if (!els.connDot) return;
    let st = null;
    try { st = await chrome.runtime.sendMessage({ type: 'getStatus' }); }
    catch (_) { st = null; }
    if (!st) {
      els.connDot.className = 'health-dot';
      els.connText.textContent = t('options.conn.unknown');
      els.connMeta.textContent = '';
      return;
    }
    const online = !!st.connected;
    els.connDot.className = 'health-dot ' + (online ? 'up' : 'down');
    els.connText.textContent = t(online ? 'options.conn.online' : 'options.conn.offline');
    const bits = [];
    if (st.gatewayUrl) bits.push(st.gatewayUrl);
    if (online && st.sessionId) bits.push('session=' + String(st.sessionId).slice(0, 12));
    els.connMeta.textContent = bits.join('  ·  ');
  }

  async function save() {
    const portalApiUrl = norm(els.portalApi.value) || PRESETS.demo.portalApiUrl;
    const apiGwUrl     = norm(els.apiGw.value)     || PRESETS.demo.apiGwUrl;
    const agentGwUrl   = norm(els.agentGw.value)   || PRESETS.demo.agentGwUrl;
    // 后端地址变了：旧 token 是上一个后端发的，对新后端无效 —— 清掉本地登录态强制
    // 重登，避免拿旧 token 撞出 refresh_revoked / invalid_refresh 之类的死胡同。
    const prev = await chrome.storage.local.get(['portalApiUrl', 'apiGwUrl', 'agentGwUrl']);
    const hadExplicit = !!(prev.portalApiUrl || prev.apiGwUrl || prev.agentGwUrl);
    const backendChanged = hadExplicit && (
      (prev.portalApiUrl || '') !== portalApiUrl ||
      (prev.apiGwUrl     || '') !== apiGwUrl ||
      (prev.agentGwUrl   || '') !== agentGwUrl
    );
    // gatewayHost：从 api-gw URL 抽出主机名，供未迁移的消费方判断"本地与否"（向下兼容）
    let gatewayHost = '127.0.0.1';
    try { gatewayHost = new URL(apiGwUrl).hostname || '127.0.0.1'; } catch (_) {}
    await chrome.storage.local.set({ portalApiUrl, apiGwUrl, agentGwUrl, gatewayHost });
    if (backendChanged) {
      await window.DQ?.authApi?.clearAuth?.();
      // 通知后台 Service Worker 重读网关地址并重连 WS —— 否则后台仍连旧网关。
      try { await chrome.runtime.sendMessage({ type: 'reconnectWithNewUrl' }); } catch (_) {}
      setTimeout(renderConnStatus, 1200);
    }
    els.portalApi.value = portalApiUrl;
    els.apiGw.value = apiGwUrl;
    els.agentGw.value = agentGwUrl;
    syncPresetActive();
    if (backendChanged) await renderAccount();
    flash(t(backendChanged ? 'options.flash.savedSwitched' : 'options.flash.saved'));
  }

  function flash(msg) {
    els.savedTip.textContent = msg;
    els.savedTip.classList.remove('hidden');
    clearTimeout(flash._t);
    flash._t = setTimeout(() => els.savedTip.classList.add('hidden'), 2200);
  }

  // ── 测试三个后端连通性 ──────────────────────────────────────────
  // agent-gw 无 /health,用 /resume/status 做存活探测。
  const GATEWAYS = [
    { name: 'portal-api', el: () => els.portalApi, path: '/health',
      ok: (b) => !!b.ok,
      label: (b) => `${b.service || ''} v${b.version || '?'} · db=${b.db || '?'}` },
    { name: 'api-gw', el: () => els.apiGw, path: '/health',
      ok: (b) => b.status === 'ok' || !!b.ok,
      label: (b) => b.service || 'ok' },
    { name: 'agent-gw', el: () => els.agentGw, path: '/resume/status?user_id=__probe__',
      ok: (b) => !!b.ok,
      label: () => t('options.agent.ok') },
  ];

  async function pingAll() {
    els.healthRows.hidden = false;
    els.healthRows.innerHTML = GATEWAYS.map((g, i) =>
      `<div class="health-row">
         <span class="health-dot" id="hd${i}"></span>
         <span id="hm${i}">${g.name} ${t('options.ping.testing')}</span>
       </div>`).join('');
    await Promise.all(GATEWAYS.map(async (g, i) => {
      const dot = document.getElementById('hd' + i);
      const msg = document.getElementById('hm' + i);
      const base = norm(g.el().value);
      if (!base) {
        dot.className = 'health-dot down';
        msg.textContent = `${g.name} ${t('options.ping.empty')}`;
        return;
      }
      const url = `${base}${g.path}`;
      try {
        const t0 = Date.now();
        const resp = await fetch(url, { method: 'GET' });
        const ms = Date.now() - t0;
        const body = await resp.json().catch(() => ({}));
        const ok = resp.ok && g.ok(body);
        dot.className = 'health-dot ' + (ok ? 'up' : 'down');
        msg.textContent = ok
          ? `${g.name} ✓ ${resp.status} · ${g.label(body)} · ${ms}ms`
          : `${g.name} ✗ ${resp.status} · ${JSON.stringify(body).slice(0, 80)}`;
      } catch (e) {
        dot.className = 'health-dot down';
        msg.textContent = `${g.name} ✗ ${e.message || e}`;
      }
    }));
  }

  // ── 账号状态（依赖 sidepanel/shared/auth-api.js 已经 importScripts）──
  async function renderAccount() {
    const authApi = window.DQ?.authApi;
    if (!authApi) {
      els.accountRow.innerHTML = `<div class="info"><span class="placeholder">${t('options.account.notLoaded')}</span></div>`;
      return;
    }
    const a = await authApi.loadAuth();
    if (!a || !a.accessToken) {
      els.accountRow.innerHTML = `
        <div class="info"><span class="placeholder">${t('options.account.notLogin')}</span></div>
        <button class="btn-secondary" id="openSpBtn" type="button">${t('options.btn.openSp')}</button>
      `;
      document.getElementById('openSpBtn')?.addEventListener('click', () => {
        // 不能直接打开 sidepanel UI（chrome API 限制），引导用户点击工具栏图标
        alert(t('options.alert.openSp'));
      });
      return;
    }

    const u = a.user || {};
    const expSec = (a.accessExpiresAt || 0) - Math.floor(Date.now() / 1000);
    const expHint = expSec > 60
      ? t('options.expiry.ok', { min: Math.floor(expSec / 60) })
      : expSec > 0
      ? t('options.expiry.soon', { sec: expSec })
      : t('options.expiry.expired');

    els.accountRow.innerHTML = `
      <div class="info">
        <span class="email">${escapeHtml(u.email || '(no email)')}</span>
        <span class="uid">id=${escapeHtml(u.id || '')} · ${expHint}</span>
      </div>
      <div style="display:flex;gap:var(--sp-2)">
        <button class="btn-secondary" id="refreshNowBtn" type="button" title="${t('options.btn.refreshToken.title')}">${t('options.btn.refreshToken')}</button>
        <button class="btn-danger" id="logoutBtn" type="button">${t('options.btn.logout')}</button>
      </div>
    `;
    document.getElementById('refreshNowBtn')?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true;
      try {
        await authApi.refresh();
        flash(t('options.flash.refreshed'));
      } catch (err) {
        // sessionExpired：refresh 已不可恢复，auth-api 已清本地会话 → 提示去重新登录。
        flash(err && err.sessionExpired
          ? t('options.flash.sessionExpired')
          : t('options.flash.refreshFail') + (err.message || err));
      } finally {
        await renderAccount();
      }
    });
    document.getElementById('logoutBtn')?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true;
      try {
        await authApi.logout({ all_devices: false });
        flash(t('options.flash.loggedOut'));
      } catch (err) {
        flash(t('options.flash.logoutFail') + (err.message || err));
      } finally {
        await renderAccount();
      }
    });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── 事件 ────────────────────────────────────────────────────────
  [els.portalApi, els.apiGw, els.agentGw].forEach((inp) => {
    inp.addEventListener('input', syncPresetActive);
  });
  els.presetRow.querySelectorAll('button').forEach((b) => {
    b.addEventListener('click', () => {
      const key = b.dataset.preset;
      if (key === 'custom') { els.portalApi.focus(); return; }
      const p = PRESETS[key];
      if (!p) return;
      els.portalApi.value = p.portalApiUrl;
      els.apiGw.value = p.apiGwUrl;
      els.agentGw.value = p.agentGwUrl;
      syncPresetActive();
    });
  });
  els.saveBtn.addEventListener('click', save);
  els.pingBtn.addEventListener('click', pingAll);
  els.connRefreshBtn?.addEventListener('click', async () => {
    // 主动催后台连一次(已连则 connect() 直接返回),再刷新状态显示
    try { await chrome.runtime.sendMessage({ type: 'connect' }); } catch (_) {}
    setTimeout(renderConnStatus, 600);
  });

  // 后台 WS 连接状态变化（notifyPopup 广播 connectionState）时实时刷新
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === 'connectionState') renderConnStatus();
  });
  // 切回本页时重新查一次（Service Worker 可能已休眠/重启，状态已变）
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) renderConnStatus();
  });

  // 当 sidepanel 完成登录/登出后 storage 变了，options 页里同步刷新
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.auth) renderAccount();
    if (changes.portalApiUrl || changes.apiGwUrl || changes.agentGwUrl) load();
  });

  window.DQI18N.ready.then(function () { load(); });
})();
