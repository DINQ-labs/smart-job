/*
 * lib/autofill/autofill-bg.js — 表单自动填写子系统 background（合并自 autofill-ext）
 *
 * 由 capture.js + api.js + index.js 三文件合并而成，整体包在 IIFE 内，
 * 避免与 v3 SW 其它全局符号冲突。消息以 { type: 'AF_*' } 区分，监听器
 * 只处理 AF_ 前缀消息，其余放行给 v3 主监听器。
 *
 * 后端：agent-gw /autofill/*、/profile；portal-api /auth/refresh。
 * 地址按 gatewayHost 本地优先推导。
 */
(function () {
  'use strict';

  // ===========================================================================
  // capture.js — 可见区域截图（OCR 兜底用）
  // ===========================================================================
  const AF_capture = {
    async captureActiveTab() {
      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) return { ok: false, error: '找不到活动标签页' };
        const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
        return { ok: true, dataUrl, viewport: { width: tab.width || 0, height: tab.height || 0 } };
      } catch (e) {
        return { ok: false, error: String((e && e.message) || e) };
      }
    },
  };

  // ===========================================================================
  // api.js — 后端 gateway client
  // ===========================================================================
  const AF_DEFAULT_HOST = '127.0.0.1';  // 本地优先

  async function _gwHost() {
    try {
      const r = await chrome.storage.local.get(['gatewayHost']);
      return r.gatewayHost || AF_DEFAULT_HOST;
    } catch { return AF_DEFAULT_HOST; }
  }

  // options 设置页可显式指定后端 URL；为空时回退到 gatewayHost 推导。
  async function _override(key) {
    try {
      const r = await chrome.storage.local.get([key]);
      const v = (r[key] || '').trim();
      return v ? v.replace(/\/+$/, '') : '';
    } catch { return ''; }
  }

  async function _gwBase() {
    const ov = await _override('agentGwUrl');
    if (ov) return ov;
    const host = await _gwHost();
    if (/^(127\.0\.0\.1|localhost|0\.0\.0\.0|192\.168\.|10\.)/.test(host)) {
      return 'http://' + (host.includes(':') ? host : host + ':8769');
    }
    return 'https://agent.job.joyhouse.chat';
  }

  async function _portalBase() {
    const ov = await _override('portalApiUrl');
    if (ov) return ov;
    const host = await _gwHost();
    if (/^(127\.0\.0\.1|localhost|0\.0\.0\.0|192\.168\.|10\.)/.test(host)) {
      return 'http://' + host.split(':')[0] + ':8771';
    }
    return 'https://api.job.joyhouse.chat';
  }

  async function _token() {
    try {
      const r = await chrome.storage.local.get(['auth']);
      return (r.auth && r.auth.accessToken) || '';
    } catch { return ''; }
  }

  // 兼容 v3：登录身份单一真相是 storage.local.auth.user.id，userId 仅做兜底
  async function _userId() {
    try {
      const r = await chrome.storage.local.get(['userId', 'auth']);
      return r.userId || (r.auth && r.auth.user && r.auth.user.id) || '';
    } catch { return ''; }
  }

  let _refreshInflight = null;
  async function _refreshAuth() {
    if (_refreshInflight) return _refreshInflight;
    _refreshInflight = (async () => {
      try {
        const r = await chrome.storage.local.get(['auth']);
        const rt = r.auth && r.auth.refreshToken;
        if (!rt) return false;
        const resp = await fetch((await _portalBase()) + '/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: rt }),
        });
        if (!resp.ok) return false;
        const data = await resp.json().catch(() => null);
        const tok = data && data.tokens;
        if (!tok || !tok.access_token) return false;
        const auth = Object.assign({}, r.auth, {
          accessToken: tok.access_token,
          refreshToken: tok.refresh_token || rt,
          accessExpiresAt: tok.access_expires_at,
          refreshExpiresAt: tok.refresh_expires_at,
          user: (data && data.user) || (r.auth && r.auth.user),
        });
        const set = { auth };
        if (data && data.user && data.user.id) set.userId = data.user.id;
        await chrome.storage.local.set(set);
        return true;
      } catch (e) {
        return false;
      }
    })();
    try {
      return await _refreshInflight;
    } finally {
      _refreshInflight = null;
    }
  }

  async function _req(path, opts = {}, _retried) {
    try {
      const base = await _gwBase();
      const token = await _token();
      const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
      if (token) headers['Authorization'] = 'Bearer ' + token;
      const resp = await fetch(base + path, Object.assign({}, opts, { headers }));
      const text = await resp.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
      if (resp.status === 401 && !_retried) {
        if (await _refreshAuth()) return _req(path, opts, true);
      }
      if (!resp.ok) {
        return { ok: false, status: resp.status, error: data.error || data.message || text };
      }
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  }

  // 本地启发式兜底匹配（后端不可用时）
  const AF_LOCAL_KW = [
    [/(first.?name|given name|名$)/i, 'firstName'],
    [/(last.?name|surname|family name|姓$)/i, 'lastName'],
    [/(full.?name|姓名|your name|^name$)/i, 'fullName'],
    [/(e-?mail|邮箱|邮件)/i, 'email'],
    [/(phone|mobile|tel\b|电话|手机)/i, 'phone'],
    [/(city|城市)/i, 'city'],
    [/(address|地址)/i, 'address'],
    [/(zip|postal|邮编)/i, 'zipCode'],
    [/(state|province|省份?)/i, 'state'],
    [/(country|国家)/i, 'country'],
    [/(linkedin)/i, 'linkedin'],
    [/(github)/i, 'github'],
    [/(website|portfolio|个人网站|主页)/i, 'website'],
    [/(company|公司|当前雇主|employer)/i, 'currentCompany'],
    [/(job ?title|职位|position)/i, 'currentTitle'],
    [/(years.*experience|工作年限|经验年限)/i, 'experienceYears'],
    [/(salary|期望薪|薪资)/i, 'expectedSalary'],
  ];

  function AF_localMatch(payload) {
    const fields = (payload && payload.fields) || [];
    const profile = (payload && payload.profileSnapshot) || {};
    const mappings = [];
    const unresolved = [];
    for (const f of fields) {
      const text = [f.label, f.placeholder, f.name, f.ariaLabel, f.autocomplete]
        .filter(Boolean).join(' ');
      let hit = null;
      for (const [re, key] of AF_LOCAL_KW) {
        if (re.test(text) && profile[key] != null && profile[key] !== '') {
          hit = { fid: f.fid, value: String(profile[key]), confidence: 0.7, reason: '本地关键词匹配 → ' + key };
          break;
        }
      }
      if (hit) mappings.push(hit);
      else unresolved.push({ fid: f.fid, label: f.label, type: f.type, required: f.required });
    }
    return { mappings, unresolved };
  }

  function _stripLocal(obj) {
    const out = {};
    for (const k of Object.keys(obj || {})) {
      if (!k.startsWith('__')) out[k] = obj[k];
    }
    return out;
  }

  const AF_api = {
    async match(payload) {
      payload = payload || {};
      const body = {
        user_id: await _userId(),
        page_url: payload.pageUrl || '',
        fields: payload.fields || [],
        profile_snapshot: _stripLocal(payload.profileSnapshot),
      };
      const r = await _req('/autofill/match', { method: 'POST', body: JSON.stringify(body) });
      if (r.ok && r.data && r.data.ok) return Object.assign({ ok: true }, r.data);
      return Object.assign({ ok: true, fallback: true }, AF_localMatch(payload));
    },

    async ocr(payload) {
      const r = await _req('/autofill/ocr', { method: 'POST', body: JSON.stringify(payload || {}) });
      return (r.ok && r.data && r.data.ok)
        ? Object.assign({ ok: true }, r.data)
        : { ok: false, error: r.error || (r.data && r.data.error) || 'OCR 失败' };
    },

    async getProfile() {
      const uid = await _userId();
      const r = await _req('/profile?user_id=' + encodeURIComponent(uid), { method: 'GET' });
      if (r.ok && r.data && r.data.ok) {
        await chrome.storage.local.set({ profileCache: r.data.profile || {} });
        return { ok: true, profile: r.data.profile || {} };
      }
      const c = await chrome.storage.local.get(['profileCache']);
      return { ok: true, fallback: true, profile: c.profileCache || {} };
    },

    async saveProfile(payload) {
      await chrome.storage.local.set({ profileCache: payload || {} });
      const body = { user_id: await _userId(), fields: _stripLocal(payload) };
      const r = await _req('/profile', { method: 'PUT', body: JSON.stringify(body) });
      return (r.ok && r.data && r.data.ok) ? { ok: true } : { ok: true, fallback: true };
    },

    async recordTemplate(payload) {
      const body = Object.assign({ user_id: await _userId() }, payload || {});
      const r = await _req('/autofill/template/record', { method: 'POST', body: JSON.stringify(body) });
      return (r.ok && r.data && r.data.ok)
        ? Object.assign({ ok: true }, r.data) : { ok: false, error: r.error };
    },

    async uploadCapture(payload) {
      const body = Object.assign({ user_id: await _userId() }, payload || {});
      const r = await _req('/autofill/template/capture', { method: 'POST', body: JSON.stringify(body) });
      return (r.ok && r.data && r.data.ok)
        ? Object.assign({ ok: true }, r.data) : { ok: false, error: r.error };
    },
  };

  // ===========================================================================
  // index.js — 消息路由 + 帧感知检测/填写
  // ===========================================================================
  const PRIMITIVE_FILES = [
    'content/autofill/main-world/human-simulator.js',
    'content/autofill/main-world/form-primitives.js',
  ];
  const NET_INTERCEPTOR = ['content/autofill/main-world/net-interceptor.js'];
  const DETECTOR_FILE = 'content/autofill/detector.js';

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || typeof msg.type !== 'string' || !msg.type.startsWith('AF_')) return; // 放行
    handle(msg, sender)
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
    return true;
  });

  async function handle(msg, sender) {
    const { type, payload } = msg || {};
    switch (type) {
      case 'AF_DETECT':            return detect();
      case 'AF_FILL':              return fill(payload);
      case 'AF_MATCH':             return AF_api.match(payload);
      case 'AF_CAPTURE':           return AF_capture.captureActiveTab();
      case 'AF_OCR':               return AF_api.ocr(payload);
      case 'AF_OCR_DETECT':        return ocrDetect(payload);
      case 'AF_GET_PROFILE':       return AF_api.getProfile();
      case 'AF_SAVE_PROFILE':      return AF_api.saveProfile(payload);
      case 'AF_HIGHLIGHT':         return highlight(payload);
      case 'AF_CLICK_NAV':         return clickNav(payload);
      case 'AF_UPLOAD_RESUME':     return uploadResume(payload);
      case 'AF_RECORD_TEMPLATE':   return AF_api.recordTemplate(payload);
      case 'AF_CAPTURE_BEGIN':     return captureBegin();
      case 'AF_CAPTURE_FINALIZE':  return captureFinalize();
      case 'AF_CAPTURE_FLUSH':     return _finishCapture();
      case 'AF_CAPTURE_PUSH':      return capturePush(msg, sender);
      default:                     return { ok: false, error: '未知消息类型: ' + type };
    }
  }

  async function activeTab() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || tab.id == null) throw new Error('找不到活动标签页');
    if (/^(chrome|edge|about|chrome-extension):/.test(tab.url || '')) {
      throw new Error('当前页面不支持检测（浏览器内部页面）');
    }
    return tab;
  }
  async function activeTabId() { return (await activeTab()).id; }

  async function listFrames(tabId) {
    let frames = [];
    try {
      frames = (await chrome.webNavigation.getAllFrames({ tabId })) || [];
    } catch (e) { frames = []; }
    frames = frames.filter((f) => f.frameId === 0 || /^https?:/.test(f.url || ''));
    if (!frames.some((f) => f.frameId === 0)) frames.unshift({ frameId: 0, url: '' });
    return frames;
  }

  async function detect() {
    const tabId = await activeTabId();
    const frames = await listFrames(tabId);
    const allFields = [];
    let pageUrl = '', pageTitle = '';
    const navByFrame = {};
    for (const fr of frames) {
      const resp = await detectInFrame(tabId, fr.frameId);
      if (!resp || !resp.ok) continue;
      if (fr.frameId === 0) { pageUrl = resp.pageUrl || ''; pageTitle = resp.pageTitle || ''; }
      for (const f of (resp.fields || [])) {
        f.frameId = fr.frameId;
        allFields.push(f);
      }
      if (resp.nav && (resp.nav.next || resp.nav.submit || resp.nav.review)) {
        navByFrame[fr.frameId] = { nav: resp.nav, count: (resp.fields || []).length };
      }
    }
    let nav = {}, navFrameId = 0, best = -1;
    for (const fidStr of Object.keys(navByFrame)) {
      const info = navByFrame[fidStr];
      if (info.count > best) { best = info.count; nav = info.nav; navFrameId = Number(fidStr); }
    }
    for (const k of ['next', 'submit', 'review']) {
      if (nav[k]) nav[k].frameId = navFrameId;
    }
    return {
      ok: true, fields: allFields, nav,
      pageUrl: pageUrl || ((frames[0] && frames[0].url) || ''),
      pageTitle,
    };
  }

  async function detectInFrame(tabId, frameId) {
    try {
      return await chrome.tabs.sendMessage(tabId, { type: 'AF_DETECT' }, { frameId });
    } catch (e) {
      try {
        await chrome.scripting.executeScript({
          target: { tabId, frameIds: [frameId] },
          files: [DETECTOR_FILE],
        });
        return await chrome.tabs.sendMessage(tabId, { type: 'AF_DETECT' }, { frameId });
      } catch (e2) {
        return null;
      }
    }
  }

  async function fill(payload) {
    const tabId = await activeTabId();
    const actions = (payload && payload.actions) || [];
    if (!actions.length) return { ok: true, result: { filled: [], failed: [] } };
    const byFrame = {};
    for (const a of actions) {
      const fr = a.frameId || 0;
      (byFrame[fr] = byFrame[fr] || []).push(a);
    }
    const filled = [], failed = [];
    for (const frStr of Object.keys(byFrame)) {
      const frameId = Number(frStr);
      const acts = byFrame[frStr];
      try {
        await chrome.scripting.executeScript({
          target: { tabId, frameIds: [frameId] }, files: PRIMITIVE_FILES, world: 'MAIN',
        });
        const results = await chrome.scripting.executeScript({
          target: { tabId, frameIds: [frameId] }, world: 'MAIN',
          func: (a2) => window.AF_Form.fillByActions(a2), args: [acts],
        });
        const res = (results[0] && results[0].result) || {};
        (res.filled || []).forEach((x) => filled.push(x));
        (res.failed || []).forEach((x) => failed.push(x));
      } catch (e) {
        acts.forEach((a) => failed.push({ fid: a.fid, error: String((e && e.message) || e) }));
      }
    }
    return { ok: true, result: { filled, failed } };
  }

  async function highlight(payload) {
    const tabId = await activeTabId();
    const items = (payload && payload.items) || [];
    const byFrame = {};
    for (const it of items) {
      const fr = it.frameId || 0;
      (byFrame[fr] = byFrame[fr] || []).push({ fid: it.fid, status: it.status });
    }
    for (const frStr of Object.keys(byFrame)) {
      try {
        await chrome.tabs.sendMessage(tabId,
          { type: 'AF_HIGHLIGHT', payload: { items: byFrame[frStr] } },
          { frameId: Number(frStr) });
      } catch (e) { /* 帧不在 */ }
    }
    return { ok: true };
  }

  async function clickNav(payload) {
    const tabId = await activeTabId();
    const loc = payload && payload.locator;
    if (!loc || (!loc.selector && !loc.shadowPath)) return { ok: false, error: '无导航按钮' };
    const frameId = loc.frameId || 0;
    await chrome.scripting.executeScript({
      target: { tabId, frameIds: [frameId] }, files: PRIMITIVE_FILES, world: 'MAIN',
    });
    const results = await chrome.scripting.executeScript({
      target: { tabId, frameIds: [frameId] }, world: 'MAIN',
      func: (l) => window.AF_Form.clickLocator(l), args: [loc],
    });
    return { ok: true, result: (results[0] && results[0].result) || {} };
  }

  async function uploadResume(payload) {
    const tabId = await activeTabId();
    const loc = payload && payload.locator;
    if (!loc) return { ok: false, error: '无文件字段' };
    const c = await chrome.storage.local.get(['profileCache']);
    const resume = c.profileCache && c.profileCache.__resume__;
    if (!resume || !resume.base64) return { ok: false, error: '未上传简历' };
    const frameId = loc.frameId || 0;
    await chrome.scripting.executeScript({
      target: { tabId, frameIds: [frameId] }, files: PRIMITIVE_FILES, world: 'MAIN',
    });
    const results = await chrome.scripting.executeScript({
      target: { tabId, frameIds: [frameId] }, world: 'MAIN',
      func: (l, b, n, m) => window.AF_Form.uploadFile(l, b, n, m),
      args: [loc, resume.base64, resume.fileName || 'resume.pdf', resume.mimeType || 'application/pdf'],
    });
    return { ok: true, result: (results[0] && results[0].result) || {} };
  }

  async function ocrDetect(payload) {
    const cap = await AF_capture.captureActiveTab();
    if (!cap.ok) return { ok: false, error: cap.error || '截图失败' };
    const ocr = await AF_api.ocr({
      screenshot: cap.dataUrl,
      page_url: (payload && payload.pageUrl) || '',
    });
    if (!ocr.ok) return { ok: false, error: ocr.error || 'OCR 识别失败' };
    const tabId = await activeTabId();
    let resolved = null;
    try {
      resolved = await chrome.tabs.sendMessage(tabId,
        { type: 'AF_RESOLVE_BBOX', payload: { fields: ocr.fields || [] } },
        { frameId: 0 });
    } catch (e) {
      return { ok: false, error: 'OCR 字段定位失败：' + String(e) };
    }
    const fields = (resolved && resolved.fields) || [];
    fields.forEach((f) => { f.frameId = 0; });
    return { ok: true, fields, detected: (ocr.fields || []).length, model: ocr.model };
  }

  // ── HTTP 抓包会话 ────────────────────────────────────────────────────────
  const CAP_KEY = 'afCapture';
  const CAP_MAX = 60;
  const CAP_ALARM = 'afCaptureFlush';
  const CAP_FALLBACK_MIN = 5;
  let _capFlushing = false;

  async function _capGet() {
    try {
      const r = await chrome.storage.session.get([CAP_KEY]);
      return r[CAP_KEY] || null;
    } catch { return null; }
  }
  async function _capSet(s) {
    try { await chrome.storage.session.set({ [CAP_KEY]: s }); } catch (e) {}
  }
  async function _capClear() {
    try { await chrome.storage.session.remove(CAP_KEY); } catch (e) {}
  }

  async function _injectInterceptor(tabId) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId }, files: NET_INTERCEPTOR, world: 'MAIN',
      });
    } catch (e) { /* 部分页面注入失败，忽略 */ }
  }

  async function _armFlushAlarm() {
    try { await chrome.alarms.create(CAP_ALARM, { delayInMinutes: CAP_FALLBACK_MIN }); } catch (e) {}
  }
  async function _clearFlushAlarm() {
    try { await chrome.alarms.clear(CAP_ALARM); } catch (e) {}
  }

  async function captureBegin() {
    await _finishCapture();
    const tab = await activeTab();
    await _capSet({ tabId: tab.id, pageUrl: tab.url || '', phase: 'filling', buffer: [] });
    await _injectInterceptor(tab.id);
    return { ok: true };
  }

  async function captureFinalize() {
    const s = await _capGet();
    if (!s) return { ok: true };
    s.phase = 'awaiting';
    await _capSet(s);
    await _armFlushAlarm();
    return { ok: true };
  }

  async function capturePush(msg, sender) {
    const s = await _capGet();
    if (!s) return { ok: false };
    const tabId = sender && sender.tab && sender.tab.id;
    if (tabId !== s.tabId || !msg || !msg.rec) return { ok: false };
    s.buffer.push(msg.rec);
    if (s.buffer.length > CAP_MAX) s.buffer.shift();
    await _capSet(s);
    return { ok: true };
  }

  async function _finishCapture() {
    if (_capFlushing) return { ok: true, busy: true };
    _capFlushing = true;
    try {
      const s = await _capGet();
      if (!s) return { ok: true, count: 0, empty: true };
      const requests = s.buffer || [];
      if (!requests.length) {
        await _capClear();
        await _clearFlushAlarm();
        return { ok: true, count: 0, empty: true };
      }
      const scrubbed = await _scrubCaptures(requests);
      const up = await AF_api.uploadCapture({ page_url: s.pageUrl || '', requests: scrubbed });
      if (up && up.ok) {
        await _capClear();
        await _clearFlushAlarm();
        return { ok: true, count: requests.length };
      }
      return { ok: false, count: requests.length, error: (up && up.error) || 'upload_failed' };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    } finally {
      _capFlushing = false;
    }
  }

  chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
    const s = await _capGet();
    if (!s || s.tabId !== tabId) return;
    if (s.phase === 'filling' && changeInfo.status === 'loading') {
      await _injectInterceptor(tabId);
    } else if (s.phase === 'awaiting' && (changeInfo.status === 'complete' || changeInfo.url)) {
      await new Promise((r) => setTimeout(r, 800));
      await _finishCapture();
    }
  });

  chrome.tabs.onRemoved.addListener(async (tabId) => {
    const s = await _capGet();
    if (s && s.tabId === tabId) await _finishCapture();
  });

  chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm && alarm.name === CAP_ALARM) await _finishCapture();
  });

  async function _scrubCaptures(requests) {
    let values = [];
    try {
      const c = await chrome.storage.local.get(['profileCache']);
      const p = c.profileCache || {};
      values = Object.keys(p)
        .filter((k) => !k.startsWith('__'))
        .map((k) => String(p[k] == null ? '' : p[k]))
        .filter((v) => v.length >= 4);
    } catch (e) {}
    const scrub = (t) => {
      if (typeof t !== 'string' || !t) return t;
      let out = t;
      for (const v of values) out = out.split(v).join('«AF»');
      return out;
    };
    return requests.map((req) => Object.assign({}, req, {
      req_body: scrub(req.req_body),
      resp_body: scrub(req.resp_body),
    }));
  }

  console.log('[autofill] 表单自动填写子系统已加载');
})();
