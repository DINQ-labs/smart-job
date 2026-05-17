/**
 * Session / 认证 命令
 *
 * 包含：check_login, init_session, navigate_login, login_with_qr,
 *       generate_qrcode, refresh_qrcode, capture_qrcode,
 *       get_session_status, tokens
 *
 * 依赖（全局，importScripts 顺序保证）：
 *   tokenStore, apiGetUserInfo, apiInitSession, apiGetQrId, apiGetQrCodeImage
 *   navigateWorkerTab, ensureWorkerTab, waitForTabLoad
 *   extOk, _ensureQRTabVisible, _captureQRFromTab（后两者在 background.js 中定义）
 */

// ── 检查登录状态 ───────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/check_login',
  description: '检查当前 Cookie 是否已登录，返回 identity（1=招聘方 2=求职者）',
  handler: async () => {
    const raw = await apiGetUserInfo();
    const loggedIn = raw && raw.code === 0 && raw.zpData && !!raw.zpData.userId;
    if (loggedIn) {
      const { userId, encryptUserId, name, recruit, agentRecruit, doubleIdentity } = raw.zpData;
      const newUserId = String(userId || '');
      // recruit=true → 招聘方(boss/HR), false → 求职者(geek)
      const isRecruiter = !!(recruit || agentRecruit);

      // 账号切换 → 清旧 token
      if (tokenStore.session.userId && tokenStore.session.userId !== newUserId) {
        console.warn('[boss-api-ext] 检测到账号切换:', tokenStore.session.userId, '→', newUserId, '，清除旧 token');
        tokenStore.jobs = {};
        tokenStore.geeks = {};
      }
      // 身份切换（求职↔招聘）→ 同样清旧 token，避免环境污染
      if (tokenStore.session.userId === newUserId &&
          tokenStore.session.identity !== undefined &&
          tokenStore.session.identity !== isRecruiter) {
        console.warn('[boss-api-ext] 检测到身份切换: 招聘方=', tokenStore.session.identity, '→', isRecruiter, '，清除旧 token');
        tokenStore.jobs = {};
        tokenStore.geeks = {};
      }

      tokenStore.updateSession({
        userId: newUserId,
        encryptUserId: encryptUserId || '',
        name: name || '',
        identity: isRecruiter,       // true=招聘方, false=求职者
        doubleIdentity: !!doubleIdentity,
      });
      // 上报 site_status 给网关，与 LinkedIn/Indeed 对齐（便于按 app_user_id 定位会话）
      try {
        if (typeof reportSiteStatus === 'function') {
          reportSiteStatus('boss', newUserId);
        }
      } catch (_) {}
      return extOk({
        logged_in: true,
        userId: newUserId,
        encryptUserId,
        name,
        is_recruiter: isRecruiter,   // true=招聘方, false=求职者
        double_identity: !!doubleIdentity,
      });
    }
    return extOk({ logged_in: false, code: raw && raw.code, message: raw && raw.message });
  },
});

// ── 初始化会话（wt2 + 用户信息） ─────────────────────────────────────────

defineCommand({
  path: 'boss/init_session',
  description: '初始化会话，获取 wt2 和用户信息',
  handler: async () => {
    const data = await apiInitSession();
    if (data && data.zpData && data.zpData.wt2) {
      tokenStore.updateSession({ wt2: data.zpData.wt2 });
    }
    const userInfo = await apiGetUserInfo();
    if (userInfo && userInfo.zpData) {
      const { userId, encryptUserId, name } = userInfo.zpData;
      const newUserId = String(userId || '');
      if (tokenStore.session.userId && tokenStore.session.userId !== newUserId) {
        console.warn('[boss-api-ext] 检测到账号切换:', tokenStore.session.userId, '→', newUserId, '，清除旧 token');
        tokenStore.jobs = {};
        tokenStore.geeks = {};
      }
      tokenStore.updateSession({ userId: newUserId, encryptUserId: encryptUserId || '', name });
    }
    return extOk({ raw: { wt: data, user: userInfo }, session: tokenStore.session });
  },
});

// ── 导航到登录页 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/navigate_login',
  description: '在 Worker Tab 打开登录页',
  handler: async () => {
    const tab = await navigateWorkerTab('https://www.zhipin.com/web/user/?intent=0&ka=header-login');
    return extOk({ tabId: tab.id, url: tab.url });
  },
});

// ── QR 码登录（打开登录页 + 截图二维码） ─────────────────────────────────

defineCommand({
  path: 'boss/login_with_qr',
  description: '打开登录页并截取二维码图片',
  handler: async () => {
    const LOGIN_URL = 'https://www.zhipin.com/web/user/?intent=0&ka=header-login';
    const tab = await navigateWorkerTab(LOGIN_URL);
    await waitForTabLoad(tab.id, 8000);
    await new Promise(r => setTimeout(r, 1500));

    let fullTab;
    try {
      fullTab = await chrome.tabs.get(tab.id);
    } catch (e) {
      throw new Error('登录 tab 已关闭，请重试');
    }

    const tabState = await _ensureQRTabVisible(fullTab.id);
    console.log('[boss-api-ext] login tab state:', tabState);
    if (tabState === 'switched') {
      await new Promise(r => setTimeout(r, 1500));
    } else if (tabState === 'qr_expired') {
      throw new Error('二维码已过期，请刷新登录页后重试');
    }

    const { qrcode_dataurl, source } = await _captureQRFromTab(fullTab);
    return extOk({ tabId: fullTab.id, qrcode_dataurl, tab_state: tabState, source });
  },
});

// ── API 直接生成二维码（不依赖页面截图）──────────────────────────────────

const _handleGenerateQRCode = async () => {
  const tab = await ensureWorkerTab();
  if (!tab || !tab.url || !tab.url.startsWith('https://www.zhipin.com')) {
    await navigateWorkerTab('https://www.zhipin.com/web/geek/job');
  }
  const randResp = await apiGetQrId();
  const zpData = randResp?.zpData;
  const qrId = zpData?.qrId;
  if (!qrId) {
    throw new Error('获取 qrId 失败，接口返回: ' + JSON.stringify(randResp));
  }
  const qrDataUrl = await apiGetQrCodeImage(qrId);
  return extOk({
    qr_id: qrId,
    rand_key: zpData.randKey || '',
    secret_key: zpData.secretKey || '',
    short_rand_key: zpData.shortRandKey || '',
    qrcode_dataurl: qrDataUrl,
  });
};

defineCommand({
  path: 'boss/refresh_qrcode',
  description: '刷新登录二维码（同 generate_qrcode）',
  handler: _handleGenerateQRCode,
});

// ── 截图方式获取二维码 ────────────────────────────────────────────────────

defineCommand({
  path: 'boss/capture_qrcode',
  description: '从已打开的登录页截图获取二维码',
  handler: async () => {
    const loginTabs = await chrome.tabs.query({ url: 'https://www.zhipin.com/web/user/*' });
    if (loginTabs.length === 0) throw new Error('未找到登录页，请先调用 boss/navigate_login');
    const tab = loginTabs[0];
    const tabState = await _ensureQRTabVisible(tab.id);
    console.log('[boss-api-ext] capture_qrcode tab state:', tabState);
    if (tabState === 'switched') {
      await new Promise(r => setTimeout(r, 1500));
    } else if (tabState === 'qr_expired') {
      throw new Error('二维码已过期，请刷新登录页后重试');
    }
    const { qrcode_dataurl, source } = await _captureQRFromTab(tab);
    return extOk({ qrcode_dataurl, tab_state: tabState, source });
  },
});

// ── 会话状态快照 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/get_session_status',
  description: '返回当前登录状态、token 统计和 WS 连接状态',
  handler: async () => {
    const userInfo = await apiGetUserInfo();
    const loggedIn = userInfo && userInfo.code === 0 && userInfo.zpData && !!userInfo.zpData.userId;
    return extOk({
      logged_in: loggedIn,
      is_recruiter: loggedIn ? !!(userInfo.zpData.recruit || userInfo.zpData.agentRecruit) : null,
      double_identity: loggedIn ? !!userInfo.zpData.doubleIdentity : false,
      session: tokenStore.session,
      job_count: Object.keys(tokenStore.jobs).length,
      ws_connected: !!(ws && ws.readyState === WebSocket.OPEN),
      session_id: currentSessionId,
    });
  },
});

// ── Token 快照 ────────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/tokens',
  description: '返回 tokenStore 完整快照',
  handler: async () => extOk(tokenStore.snapshot()),
});
