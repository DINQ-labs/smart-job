/**
 * 杂项命令：logout, set_proxy
 *
 * 依赖（全局）：tokenStore, applyProxy, resetWorkerTab,
 *   chrome.cookies, notifyPopup, _clearCurrentSessionId, extOk
 */

const AUTH_COOKIES = new Set(['wt2', 'wt3', 'uid', 'log_data', 'partner_id']);
// 与 popup.js / options.js / commands/session.js 保持一致的 Boss 登录入口
const BOSS_LOGIN_URL = 'https://www.zhipin.com/web/user/?intent=0&ka=header-login';

// ── 登出 ───────────────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/logout',
  description: '清除认证 Cookie，重置 tokenStore，断开工作 Tab；默认同时打开登录页供重新登录',
  handler: async ({ open_login_page = true } = {}) => {
    const cookies = await chrome.cookies.getAll({ domain: '.zhipin.com' });
    for (const c of cookies) {
      if (AUTH_COOKIES.has(c.name)) {
        await chrome.cookies.remove({
          url: `https://www.zhipin.com${c.path}`,
          name: c.name,
        });
      }
    }

    tokenStore.updateSession({ wt2: '', userId: '', encryptUserId: '', name: '' });
    tokenStore.jobs = {};
    tokenStore.geeks = {};
    await tokenStore._save();

    resetWorkerTab();

    _clearCurrentSessionId();
    notifyPopup({ type: 'loggedOut' });

    // 登出后自动打开 Boss 登录页（与 popup 刷新登录状态未登录时的 UX 一致）。
    // 调用方传 open_login_page: false 可禁用（批量脚本登出等场景）。
    let login_tab_opened = false;
    if (open_login_page) {
      try {
        await chrome.tabs.create({ url: BOSS_LOGIN_URL });
        login_tab_opened = true;
      } catch (e) {
        console.warn('[boss/logout] 打开登录页失败（cookie 已清）:', e);
      }
    }

    return extOk({ message: '已退出登录', login_tab_opened });
  },
});

// ── 设置代理 ──────────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/set_proxy',
  description: '设置或清除 Chrome 代理（支持 http/https/socks4/socks5）',
  handler: async ({ proxy_url } = {}) => {
    const proxyUrl = (proxy_url || '').trim();
    await applyProxy(proxyUrl);
    notifyPopup({ type: 'proxyChanged', proxyUrl });
    return extOk({
      proxy_url: proxyUrl,
      message: proxyUrl ? `代理已设置: ${proxyUrl}` : '代理已清除（直连）',
    });
  },
});
