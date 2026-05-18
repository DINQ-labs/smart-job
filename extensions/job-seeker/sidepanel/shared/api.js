/*
 * shared/api.js — 集中 HTTP gateway client.
 *
 * 替代 v2 散落在 10+ 模块的 fetch.集中以后:
 *   - 路径前缀切换(prod/dev)一处改完
 *   - 统一错误处理 + 自动 user_id 注入
 *   - 单元测试只 mock 此模块
 *
 * 后端两个 gateway:
 *   - api-gw   (port 8767): 老 MCP gateway, /bookmarks /billing /jobs/evaluate-by-id
 *   - agent-gw (port 8769): 新 agent gateway, /sse/* /tasks/* /agent/cell/chips /events/*
 *
 * 暴露:window.DQ.api = { agentGw, apiGw, getUserId, baseUrls }
 */
(function () {
  'use strict';

  const STORAGE_GW_KEY = 'gatewayHost';

  async function getStoredHost() {
    try {
      const r = await chrome.storage.local.get([STORAGE_GW_KEY]);
      return r[STORAGE_GW_KEY] || '127.0.0.1';
    } catch { return '127.0.0.1'; }
  }

  // options 设置页可显式指定后端 URL；为空时回退到 gatewayHost 推导。
  async function _override(key) {
    try {
      const r = await chrome.storage.local.get([key]);
      const v = (r[key] || '').trim();
      return v ? v.replace(/\/+$/, '') : '';
    } catch { return ''; }
  }

  async function agentGwBase() {
    const ov = await _override('agentGwUrl');
    if (ov) return ov;
    const host = await getStoredHost();
    if (/^(127\.0\.0\.1|localhost)/.test(host)) {
      return `http://${host.includes(':') ? host : host + ':8769'}`;
    }
    return 'https://agent.job.joyhouse.chat';
  }

  async function apiGwBase() {
    const ov = await _override('apiGwUrl');
    if (ov) return ov;
    const host = await getStoredHost();
    if (/^(127\.0\.0\.1|localhost)/.test(host)) {
      return `http://${host.includes(':') ? host : host + ':8767'}`;
    }
    return 'https://control.job.joyhouse.chat';
  }

  async function getUserId() {
    try {
      // 登录身份单一真相 = storage.local.auth.user.id(与 lib/autofill/autofill-bg.js
      // 口径统一);顶层 userId 仅作兜底 —— 避免 SSE/MCP 侧与 autofill 拿到不一致的 id。
      const r = await chrome.storage.local.get(['userId', 'auth']);
      return (r.auth && r.auth.user && r.auth.user.id) || r.userId || '';
    } catch { return ''; }
  }

  // ── 读 access token（Bearer 头用）────────────────────────────
  // 不调 DQ.authApi.loadAuth() 以避免引入加载顺序耦合（auth-api 在 shared 同级
  // 通常已先加载，但读 storage 是最稳的）。
  async function _getAccessToken() {
    try {
      const r = await chrome.storage.local.get(['auth']);
      return r.auth?.accessToken || '';
    } catch { return ''; }
  }

  // 用 authApi.refresh() 做一次性 401 续期 + 重试。
  // 不让两个并发 401 都各自 refresh：用 module 级 inflight Promise 串行。
  let _refreshInflight = null;
  async function _tryRefresh() {
    if (_refreshInflight) return _refreshInflight;
    const authApi = window.DQ?.authApi;
    if (!authApi) return Promise.reject(new Error('auth_api_missing'));
    _refreshInflight = (async () => {
      try { return await authApi.refresh(); }
      finally { _refreshInflight = null; }
    })();
    return _refreshInflight;
  }

  // ── 通用 request（Bearer + 401 自动 refresh-retry 一次）────
  async function _req(base, path, opts = {}) {
    const url = `${base}${path}`;
    const headers = {
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    };

    // Bearer（如果当前已登录）。x-user-id 头不再发送 —— gateway 业务路由
    // 已经从 query string 取 user_id（每个方法 qs.set('user_id', uid)）。
    // gateway 后续加 JWT 中间件后会读 Authorization；现在它会忽略，向下兼容。
    const token = await _getAccessToken();
    if (token && !opts.noAuth) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const init = {
      method: opts.method || 'GET',
      credentials: 'include',
      headers,
      signal: opts.signal,
    };
    if (opts.body != null) {
      init.body = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body);
    }

    let resp = await fetch(url, init);

    // 401 + 还没重试过 + 有 refresh token + 不是匿名请求 → 续一次再发
    if (
      resp.status === 401 &&
      !opts._retried &&
      !opts.noAuth &&
      token
    ) {
      try {
        await _tryRefresh();
        const newTok = await _getAccessToken();
        if (newTok && newTok !== token) {
          headers['Authorization'] = `Bearer ${newTok}`;
          resp = await fetch(url, { ...init, headers });
        }
      } catch (_) {
        // refresh 失败：auth-api 内部会处理；这里继续把原始 401 抛上去
      }
    }

    const text = await resp.text();
    let data; try { data = text ? JSON.parse(text) : null; } catch { data = { _raw: text }; }
    if (!resp.ok) {
      const err = new Error(`HTTP ${resp.status}: ${(data && data.error) || text.slice(0, 120)}`);
      err.status = resp.status; err.data = data;
      throw err;
    }
    return data;
  }

  async function agentGet(path, opts = {}) {
    return _req(await agentGwBase(), path, { ...opts, method: 'GET' });
  }
  async function agentPost(path, body, opts = {}) {
    return _req(await agentGwBase(), path, { ...opts, method: 'POST', body });
  }
  async function apiGet(path, opts = {}) {
    return _req(await apiGwBase(), path, { ...opts, method: 'GET' });
  }
  async function apiPost(path, body, opts = {}) {
    return _req(await apiGwBase(), path, { ...opts, method: 'POST', body });
  }

  // ── multipart 上传原语（resume 上传用）────────────────────────
  // _req 强制 application/json，无法传 FormData；这里裸 fetch：
  // 不设 Content-Type（浏览器自动带 multipart boundary），手动加 Bearer，
  // 401 时续期重试一次。
  async function agentUpload(path, formData, opts = {}) {
    const base = await agentGwBase();
    const url = `${base}${path}`;
    const token = await _getAccessToken();
    const headers = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const doFetch = (h) => fetch(url, {
      method: 'POST', credentials: 'include', headers: h, body: formData,
    });

    let resp = await doFetch(headers);
    if (resp.status === 401 && !opts._retried && token) {
      try {
        await _tryRefresh();
        const newTok = await _getAccessToken();
        if (newTok && newTok !== token) {
          resp = await doFetch({ Authorization: `Bearer ${newTok}` });
        }
      } catch (_) { /* 续期失败：原始 401 继续抛 */ }
    }
    const text = await resp.text();
    let data; try { data = text ? JSON.parse(text) : null; } catch { data = { _raw: text }; }
    if (!resp.ok) {
      const err = new Error(`HTTP ${resp.status}: ${(data && data.error) || text.slice(0, 120)}`);
      err.status = resp.status; err.data = data;
      throw err;
    }
    return data;
  }

  // ── 业务封装(模块只调下面这些,不直接 _req)────────────────
  const agentGw = {
    /** PRD §10.3 chips(page_type/page_item_count 可选)*/
    async chips({ role, platform, lang = 'zh', page_type, page_item_count } = {}) {
      const qs = new URLSearchParams({ role, platform, lang });
      const uid = await getUserId(); if (uid) qs.set('user_id', uid);
      if (page_type) qs.set('page_type', page_type);
      if (page_item_count) qs.set('page_item_count', String(page_item_count));
      return agentGet(`/agent/cell/chips?${qs}`);
    },
    /** Tasks list */
    async tasks({ role, platform, scope = 'current', limit = 50 } = {}) {
      const uid = await getUserId();
      const qs = new URLSearchParams({ role, limit: String(limit) });
      if (uid) qs.set('user_id', uid);
      if (scope === 'current' && platform) qs.set('platform', platform);
      return agentGet(`/tasks?${qs}`);
    },
    async taskDetail(taskId) {
      const uid = await getUserId();
      const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
      return agentGet(`/tasks/${taskId}${qs}`);
    },
    async taskRun(payload)    { return agentPost(`/tasks/run`, payload); },
    async taskCancel(taskId)  {
      const uid = await getUserId();
      const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
      return agentPost(`/tasks/${taskId}/cancel${qs}`, null);
    },
    async taskResume(taskId)  {
      const uid = await getUserId();
      const qs = uid ? `?user_id=${encodeURIComponent(uid)}` : '';
      return agentPost(`/tasks/${taskId}/resume${qs}`, null);
    },
    async templates({ role, platform } = {}) {
      const qs = new URLSearchParams();
      if (role) qs.set('role', role);
      if (platform) qs.set('platform', platform);
      return agentGet(`/tasks/templates?${qs}`);
    },
    async chipsVersion() { return agentGet(`/agent/cell/chips/version`); },
    async reportChipClick({ chip_key, chip_label, role, platform, lang = 'zh' }) {
      const uid = await getUserId();
      // fire-and-forget,调用方不需要 await
      return agentPost(`/events/chip_click`, {
        chip_key, chip_label, user_id: uid, role, platform, lang,
      });
    },
    async userPreferences(platform) {
      const uid = await getUserId();
      if (!uid) return { ok: false, data: null };
      return agentGet(`/user-preferences/${encodeURIComponent(uid)}?platform=${platform}`);
    },

    // ── 简历 / 求职偏好（onboarding Step 5a/5b/5c）─────────────
    /** 上传简历文件 → { ok, resume_id, parse_status:'pending' } (HTTP 202) */
    async resumeUpload(file, title = '') {
      const uid = await getUserId();
      if (!uid) throw new Error('no_user_id');
      const fd = new FormData();
      fd.append('user_id', uid);
      fd.append('file', file, file.name);
      if (title) fd.append('title', title);
      return agentUpload(`/resume/upload`, fd);
    },
    /** 查解析状态 → { ok, has_resume, resume_id?, parse_status? } */
    async resumeStatus() {
      const uid = await getUserId();
      if (!uid) return { ok: false, has_resume: false };
      return agentGet(`/resume/status?user_id=${encodeURIComponent(uid)}`);
    },
    /** 取解析结果 → { ok, parse_status, parsed:{ skills[], ... } } */
    async resumeParsed() {
      const uid = await getUserId();
      if (!uid) return { ok: false };
      return agentGet(`/resume/${encodeURIComponent(uid)}`);
    },
    /** 解析失败后重试 → { ok, parse_status:'pending' } */
    async resumeRetry() {
      const uid = await getUserId();
      if (!uid) throw new Error('no_user_id');
      return agentPost(`/resume/${encodeURIComponent(uid)}/retry`, null);
    },
    /** AI 分析：据简历推荐求职偏好 → { ok, suggestion:{job_role,city,salary_range,notes} }
     *  resume 仍在解析时后端返回 409 —— 调用方据 err.status 继续轮询。 */
    async suggestPreferences(platform = 'boss') {
      const uid = await getUserId();
      if (!uid) throw new Error('no_user_id');
      return agentGet(`/user-preferences/${encodeURIComponent(uid)}/suggest?platform=${encodeURIComponent(platform)}`);
    },
    /** 保存确认后的求职偏好 → { ok } */
    async savePreferences(platform, body) {
      const uid = await getUserId();
      if (!uid) throw new Error('no_user_id');
      return agentPost(`/user-preferences/${encodeURIComponent(uid)}`, { platform, ...body });
    },
  };

  const apiGw = {
    async bookmarksList({ platform, role } = {}) {
      const uid = await getUserId();
      if (!uid) return { bookmarks: [] };
      const qs = new URLSearchParams({ app_user_id: uid });
      if (platform) qs.set('platform', platform);
      if (role) qs.set('role', role);
      return apiGet(`/bookmarks?${qs}`);
    },
    async bookmarkAdd(payload) {
      const uid = await getUserId();
      return apiPost(`/bookmarks`, { app_user_id: uid, ...payload });
    },
    async bookmarkDelete(id) {
      const uid = await getUserId();
      const qs = uid ? `?app_user_id=${encodeURIComponent(uid)}` : '';
      return _req(await apiGwBase(), `/bookmarks/${id}${qs}`, { method: 'DELETE' });
    },
    /** 订阅 + Credit 概览。后端 /billing/status 返回 credit_balance 等字段;
     *  这里补一个 balance 别名,兼容只读 .balance 的调用方(app.js / profile.js)。*/
    async creditBalance() {
      const uid = await getUserId();
      if (!uid) return { balance: 0 };
      const r = await apiGet(`/billing/status?app_user_id=${encodeURIComponent(uid)}`);
      return { ...r, balance: Number(r?.credit_balance || 0) };
    },
    async checkout(payload) {
      const uid = await getUserId();
      return apiPost(`/billing/checkout`, { app_user_id: uid, ...payload });
    },
    async portal() {
      const uid = await getUserId();
      return apiPost(`/billing/portal`, { app_user_id: uid });
    },
    async reanalyzeBookmark(id) {
      const uid = await getUserId();
      return apiPost(`/jobs/evaluate-by-id`, { user_id: uid, bookmark_id: id });
    },
  };

  window.DQ = window.DQ || {};
  window.DQ.api = {
    getUserId,
    baseUrls: { agentGwBase, apiGwBase },
    agentGw,
    apiGw,
    // 低级原语(模块自己拼路径时用,但优先走上面的命名方法)
    raw: { agentGet, agentPost, apiGet, apiPost },
  };
})();
