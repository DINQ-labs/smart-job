/*
 * shared/auth-api.js — job-portal-api（独立身份服务，port 8771）的 HTTP 客户端
 *
 * 与 shared/api.js 的关系：
 *   shared/api.js   → 调 agent-gw（8769）+ api-gw（8767）的业务接口，自带 x-user-id 头
 *   shared/auth-api → 调 portal-api（8771）的身份接口，自带 Authorization: Bearer
 *
 * 现状：扩展走纯 JWT；token 持久化在 chrome.storage.local.auth。
 *
 * 暴露：window.DQ.authApi = {
 *   register({ email, password })           → { verification_id, email, expires_at, dev_hint? }
 *   verifyEmail({ verification_id, code })  → { user, tokens }；成功自动 saveTokens
 *   resendCode({ email })                   → { sent, verification_id?, dev_hint? }
 *   login({ email, password })              → { user, tokens }；成功自动 saveTokens
 *   refresh()                                → { user, tokens }；成功自动 saveTokens
 *   logout({ all_devices? })                → 撤销 refresh + clearTokens
 *   me()                                     → { id, email, ... }
 *
 *   requestPasswordReset({ email })          → { sent, verification_id?, expires_at?, dev_hint? }
 *   verifyPasswordResetCode({ verification_id, code }) → { ok, verified }
 *   confirmPasswordReset({ verification_id, code, new_password }) → { ok }
 *
 *   loadAuth() → { accessToken, refreshToken, accessExpiresAt, refreshExpiresAt, user } | null
 *   saveAuth(payload), clearAuth()
 *   portalBase() → 'http://localhost:8771' / 'https://api.job.joyhouse.chat'
 * }
 *
 * 错误形状：fetch 失败抛 Error，err.status / err.data 可读出 { error, ... } body。
 */
(function () {
  'use strict';

  const STORAGE_GW_KEY  = 'gatewayHost';     // 复用 shared/api.js 的存储位（同一来源切环境）
  const STORAGE_AUTH    = 'auth';
  const STORAGE_USER_ID = 'userId';          // back-compat：shared/api.js 仍然按 userId 注头
  const PROD_PORTAL     = 'https://api.job.joyhouse.chat';
  const DEV_PORTAL_PORT = 8771;

  // ── 选址 ────────────────────────────────────────────────
  async function portalBase() {
    let host = '', override = '';
    try {
      const r = await chrome.storage.local.get(['portalApiUrl', STORAGE_GW_KEY]);
      override = (r.portalApiUrl || '').trim();
      host = r[STORAGE_GW_KEY] || '127.0.0.1';
    } catch (_) { host = '127.0.0.1'; }
    // options 设置页显式指定的 portal-api URL 优先；为空才回退到 gatewayHost 推导。
    if (override) return override.replace(/\/+$/, '');
    // gatewayHost 是给老 8767/8769 用的；portal-api 跟着同一选址逻辑：
    // - localhost / 127.0.0.1 / 任意 ip → 走 http://<host(无端口)>:8771
    // - 否则 → https://api.job.joyhouse.chat
    if (/^(127\.0\.0\.1|localhost|0\.0\.0\.0|192\.168\.|10\.)/.test(host)) {
      const justHost = host.split(':')[0];
      return `http://${justHost}:${DEV_PORTAL_PORT}`;
    }
    return PROD_PORTAL;
  }

  // ── token 存储 ──────────────────────────────────────────
  async function loadAuth() {
    try {
      const r = await chrome.storage.local.get([STORAGE_AUTH]);
      return r[STORAGE_AUTH] || null;
    } catch { return null; }
  }

  async function saveAuth(payload) {
    if (!payload || !payload.user || !payload.tokens) {
      throw new Error('saveAuth: invalid payload');
    }
    const auth = {
      accessToken:        payload.tokens.access_token,
      refreshToken:       payload.tokens.refresh_token,
      accessExpiresAt:    payload.tokens.access_expires_at,
      refreshExpiresAt:   payload.tokens.refresh_expires_at,
      user:               payload.user,
      issuedAt:           Math.floor(Date.now() / 1000),
    };
    await chrome.storage.local.set({
      [STORAGE_AUTH]: auth,
      // back-compat：shared/api.js getUserId() 仍读这个 key
      [STORAGE_USER_ID]: payload.user.id,
    });
    return auth;
  }

  async function clearAuth() {
    try {
      await chrome.storage.local.remove([STORAGE_AUTH, STORAGE_USER_ID]);
    } catch (_) {}
  }

  // ── 通用 fetch ──────────────────────────────────────────
  async function _req(path, opts = {}) {
    const base = await portalBase();
    const url  = `${base}${path}`;
    const headers = {
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    };
    if (opts.auth) {
      const a = await loadAuth();
      if (a && a.accessToken) headers['Authorization'] = `Bearer ${a.accessToken}`;
    }
    const init = {
      method:  opts.method || 'GET',
      headers,
      signal:  opts.signal,
    };
    if (opts.body != null) {
      init.body = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body);
    }
    let resp;
    try {
      resp = await fetch(url, init);
    } catch (e) {
      const err = new Error(`network_error: ${e.message || e}`);
      err.status = 0;
      err.data = { error: 'network_error' };
      throw err;
    }
    const text = await resp.text();
    let data; try { data = text ? JSON.parse(text) : null; } catch { data = { _raw: text }; }
    if (!resp.ok) {
      const errKey = (data && data.error) || `http_${resp.status}`;
      const err = new Error(errKey);
      err.status = resp.status;
      err.data   = data || {};
      throw err;
    }
    return data;
  }

  // ── 接口封装 ────────────────────────────────────────────
  const api = {
    // 现网选址（调试 / UI 显示用）
    portalBase,

    // 注册：发送验证码
    register({ email, password, client = 'extension' }) {
      return _req('/auth/register', { method: 'POST', body: { email, password, client } });
    },

    // 验证邮箱码：成功后自动 saveAuth
    async verifyEmail({ verification_id, code }) {
      const r = await _req('/auth/verify-email', {
        method: 'POST',
        body: { verification_id, code },
      });
      await saveAuth(r);
      return r;
    },

    // 重发验证码
    resendCode({ email }) {
      return _req('/auth/resend-code', { method: 'POST', body: { email } });
    },

    // 邮密登录：成功后自动 saveAuth
    async login({ email, password, client = 'extension' }) {
      const r = await _req('/auth/login', {
        method: 'POST',
        body: { email, password, client },
      });
      await saveAuth(r);
      return r;
    },

    // refresh：拿当前 refresh 换新对。
    // /auth/refresh 返回 401 = refresh 凭证被拒（已吊销 / 重用检测 / 过期 / 无效），
    // 会话不可恢复 —— 清掉本地登录态并打 sessionExpired 标记，让各界面回落到登录引导。
    // 网络错误 / 5xx 等暂时性故障不清 auth，留待重试。
    async refresh() {
      const a = await loadAuth();
      if (!a || !a.refreshToken) {
        await clearAuth();
        const err = new Error('no_refresh_token');
        err.status = 401; err.sessionExpired = true;
        throw err;
      }
      let r;
      try {
        r = await _req('/auth/refresh', {
          method: 'POST',
          body: { refresh_token: a.refreshToken },
        });
      } catch (err) {
        if (err && err.status === 401) {
          await clearAuth();
          err.sessionExpired = true;
        }
        throw err;
      }
      await saveAuth(r);
      return r;
    },

    // 登出：撤销 refresh + 清本地
    async logout({ all_devices = false } = {}) {
      const a = await loadAuth();
      try {
        await _req('/auth/logout', {
          method: 'POST',
          auth: true,
          body: {
            refresh_token: a?.refreshToken,
            all_devices,
          },
        });
      } catch (_) {
        // 即便服务端撤销失败也要清本地，避免登出按钮失效
      }
      await clearAuth();
    },

    // 当前用户
    me() { return _req('/auth/me', { auth: true }); },

    // ── 密码重置 ──────────────────────────────────────────
    // 1) 请求重置:发送验证码到邮箱。
    //    防枚举:邮箱不存在/无密码也返回 200,但 sent=false 且无 verification_id。
    //    冷却期内重发 → 抛 status=429,err.data.retry_after_seconds。
    requestPasswordReset({ email }) {
      return _req('/auth/password-reset', { method: 'POST', body: { email } });
    },

    // 2) 校验验证码(可选 UX 步骤,不消耗验证码)
    verifyPasswordResetCode({ verification_id, code }) {
      return _req('/auth/password-reset/verify', {
        method: 'POST',
        body: { verification_id, code },
      });
    },

    // 3) 确认重置:验证码 + 新密码(8-72 位)。成功后服务端会撤销该用户所有 token。
    confirmPasswordReset({ verification_id, code, new_password }) {
      return _req('/auth/password-reset/confirm', {
        method: 'POST',
        body: { verification_id, code, new_password },
      });
    },

    // 存取
    loadAuth, saveAuth, clearAuth,
  };

  window.DQ = window.DQ || {};
  window.DQ.authApi = api;
})();
