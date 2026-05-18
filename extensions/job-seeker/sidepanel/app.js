/*
 * sidepanel/app.js — 启动 + 顶级 state + Tab router.
 *
 * 职责:
 *   - 装配 window.DQ.state(单一真相)
 *   - Tab 切换(顶部 .pt-tab,active class)
 *   - 顶栏元素(platform / credit / notif)的拉取与渲染
 *   - ContextBar 渲染(resume / prefs / page)
 *   - background runtime.onMessage 路由(PageContext / Auth / Notification 等)
 *
 * 不在这里做:具体业务(chat/tasks/bookmarks/profile)由 modules/*.js 自管.
 *
 * 严守 v3 类名(DESIGN.md §6).
 */
(function () {
  'use strict';

  const { NAMES, emit } = window.DQ.events;

  // ── 单一 state(暴露到 window.DQ.state) ───────────────────
  const state = {
    user:        { loggedIn: false, id: '', email: '', name: '', avatarUrl: '' },
    role:        'jobseeker',       // 'jobseeker' | 'recruiter'
    platform:    'boss',            // 'boss' | 'linkedin' | 'indeed'
    activeTab:   'chat',            // 'chat' | 'tasks' | 'bookmarks' | 'profile'
    credit:      { balance: 0, lowWarning: false },
    notifCount:  0,
    resume:      { uploaded: false, summary: '' },
    prefs:       { summary: '' },
    pageContext: {
      url: '', title: '', kind: '',
      page_type: '',                // PRD §10.3: list/detail/chat/off_platform/other
      page_item_count: 0,
    },
    platformsStatus: {
      boss:     { online: false, name: '' },
      linkedin: { online: false, name: '' },
      indeed:   { online: false, name: '' },
    },
  };

  window.DQ = window.DQ || {};
  window.DQ.state = state;
  window.DQ.config = window.DQ.config || {};
  Object.assign(window.DQ.config, { role: state.role, platform: state.platform });

  // ── DOM 引用 ──────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const els = {
    headerHome:          $('headerHome'),
    headerSettings:      $('headerSettings'),
    headerMode:          $('headerMode'),
    headerModeLabel:     $('headerModeLabel'),
    headerModeDot:       $('headerModeDot'),

    ctxBar:        $('ctxBar'),
    ctxResume:     $('ctxResume'), ctxResumeText: $('ctxResumeText'),
    ctxPrefs:      $('ctxPrefs'),  ctxPrefsText:  $('ctxPrefsText'),
    ctxPage:       $('ctxPage'),   ctxPageText:   $('ctxPageText'), ctxPageIcon: $('ctxPageIcon'),

    ptTabs:        $('ptTabs'),
    tabsBadge:     $('tabTasksBadge'),
    tabPanes:      document.querySelectorAll('.tab-pane'),

    loginGate:     $('loginGate'),
    gateOpenLogin: $('gateOpenLogin'),
    gateForgotPwd: $('gateForgotPwd'),

    kickBanner:      $('kickBanner'),
    kickBannerText:  $('kickBannerText'),
    kickBannerClose: $('kickBannerClose'),
  };

  // ── Tab router ────────────────────────────────────────────
  function switchTab(tab) {
    if (!tab || tab === state.activeTab) return;
    state.activeTab = tab;
    document.querySelectorAll('.pt-tab').forEach(b => {
      b.classList.toggle('active', b.dataset.tab === tab);
    });
    els.tabPanes.forEach(p => {
      p.classList.toggle('active', p.id === `tab-${tab}`);
    });
    emit(NAMES.TAB_CHANGED, { tab });
  }

  els.ptTabs?.addEventListener('click', (e) => {
    const btn = e.target.closest('.pt-tab[data-tab]');
    if (btn) switchTab(btn.dataset.tab);
  });

  // ── 顶栏品牌区「🏠 首页」→ 回首页 ──────────────────────────
  // 已登录回 AI助手 tab;未登录回登录页。整个扩展任意位置都能随时回首页。
  function goHome() {
    if (state.user.loggedIn) {
      switchTab('chat');
    } else {
      try { window.DQ.onboarding?.openLogin?.(); } catch (_) {}
    }
  }
  els.headerHome?.addEventListener('click', goHome);

  // ── 顶栏「⚙️ 设置」→ 打开扩展设置页(配置 portal / api-gw / agent-gw 网关)──
  // 任意位置(含登录前)都能改网关地址 —— 网关配错会导致登录失败,故须常驻可达。
  function openSettings() {
    try { chrome.runtime.openOptionsPage(); } catch (_) {}
  }
  els.headerSettings?.addEventListener('click', openSettings);

  // ── 顶栏身份 pill → 角色 / 平台切换 ────────────────────────
  // 「Boss · 求职」胶囊显示当前平台 · 角色,点它即开切换向导(onboarding 监听)。
  function openRoleSwitcher() {
    window.dispatchEvent(new CustomEvent('dq:open-role-switcher'));
  }
  els.headerMode?.addEventListener('click', openRoleSwitcher);
  els.headerMode?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRoleSwitcher(); }
  });

  // ── JobSeeker 工作台显示控制 ───────────────────────────────
  function showJobSeekerWorkspace(show) {
    const container = document.getElementById('jobseekerContainer');
    if (show) {
      container?.classList.add('open');
    } else {
      container?.classList.remove('open');
    }
  }

  // 暴露给外部调用
  window.DQ.app = { switchTab, renderHeader, renderContext, renderLoginGate, showJobSeekerWorkspace };

  // 注意:求职者工作台不再随角色变化自动弹出。求职引导(onboarding Step 5a/5b/5c)
  // 已接管简历/偏好采集,引导结束直接落到 AI 对话 tab。工作台仅经
  // 'dq:jobseeker-open'(「我的」tab 入口)手动打开。

  // ── 顶栏渲染 ──────────────────────────────────────────────
  function renderHeader() {
    // 顶栏身份 pill = 平台 · 角色;在线状态用绿点表示。
    // credit / 通知 已移入「我的」tab(profile.js),顶栏不再展示。
    const p = state.platformsStatus[state.platform] || { online: false };
    const platLabel = ({ boss: 'Boss', linkedin: 'LinkedIn', indeed: 'Indeed' })[state.platform] || t('app.platUnknown');
    const roleLabel = state.role === 'recruiter' ? t('app.roleRecruiter') : t('app.roleJobseeker');
    if (els.headerModeLabel) els.headerModeLabel.textContent = `${platLabel} · ${roleLabel}`;
    if (els.headerModeDot) els.headerModeDot.classList.toggle('online', !!p.online);
  }

  function renderContext() {
    // Resume 行不再在上下文条提示 —— 简历采集已由 onboarding Step 5a 接管,
    // 这里恒隐藏,避免与引导重复。
    els.ctxResume.hidden = true;
    // Prefs
    if (state.prefs.summary) {
      els.ctxPrefs.hidden = false;
      els.ctxPrefsText.textContent = state.prefs.summary;
    } else {
      els.ctxPrefs.hidden = true;
    }
    // Page
    if (state.pageContext.page_type && state.pageContext.title) {
      els.ctxPage.hidden = false;
      els.ctxPageText.textContent = state.pageContext.title;
      els.ctxPageIcon.textContent = ({
        list: '📑', detail: '📌', chat: '💬', off_platform: '🌐', other: '🗒',
      })[state.pageContext.page_type] || '📌';
    } else {
      els.ctxPage.hidden = true;
    }
    // 三行都隐藏时收起整条 ctx-bar,避免留一条空白条
    const anyRow = !els.ctxResume.hidden || !els.ctxPrefs.hidden || !els.ctxPage.hidden;
    els.ctxBar.hidden = !anyRow;
  }

  function renderLoginGate() {
    els.loginGate.classList.toggle('open', !state.user.loggedIn);
  }

  // ── 连接异常横幅 ──────────────────────────────────────────
  // 被网关踢出(扩展版本过旧 / 异地登录等)时,顶栏下方常驻红色提示,
  // 直到重连成功(background 收到 registered)或用户手动关闭。
  function showKickBanner(reason) {
    if (!els.kickBanner) return;
    els.kickBannerText.textContent = reason || '扩展与服务端的连接已断开。';
    els.kickBanner.hidden = false;
  }
  function hideKickBanner() {
    if (els.kickBanner) els.kickBanner.hidden = true;
  }
  els.kickBannerClose?.addEventListener('click', () => {
    hideKickBanner();
    try { chrome.storage.local.remove('extKick'); } catch (_) {}
  });

  // ── ContextBar 链接(jobseeker 提示"上传简历"等)─────────
  document.addEventListener('click', (e) => {
    const link = e.target.closest('[data-tab-link]');
    if (link) {
      e.preventDefault();
      switchTab(link.dataset.tabLink);
    }
  });

  // 把 auth(auth-api.js 登录/验证后落盘的 chrome.storage.local.auth)
  // 映射到 state.user —— 这是登录账号的单一真相。
  function applyAuthUser(auth) {
    const a = auth || {};
    const au = a.user || {};
    if (a.accessToken) {
      state.user.loggedIn = true;
      state.user.id        = au.id || state.user.id || '';
      state.user.email     = au.email || '';
      state.user.name      = au.name || au.nickname || '';
      state.user.avatarUrl = au.avatar_url || au.avatarUrl || '';
    } else {
      state.user.loggedIn = false;
      state.user.id = ''; state.user.email = ''; state.user.name = '';
    }
  }

  // ── 加载用户偏好(role/platform 从 user;账号从 auth)─────────
  async function loadUserPrefs() {
    try {
      const stored = await chrome.storage.local.get(['user', 'userId', 'auth']);
      const u = (stored && stored.user) || {};
      const prevRole = state.role;
      if (u.role)     state.role = u.role;
      if (u.platform) state.platform = u.platform;
      // 登录账号:auth 是单一真相;没有 auth 时退回旧 userId
      if (stored.auth && stored.auth.accessToken) {
        applyAuthUser(stored.auth);
      } else if (stored.userId) {
        state.user.id = stored.userId;
        state.user.loggedIn = true;
      }
      window.DQ.config.role = state.role;
      window.DQ.config.platform = state.platform;
      document.body.dataset.role = u.onboarded ? state.role : '';

      // 角色发生变化时发送事件
      if (prevRole !== state.role && u.onboarded) {
        emit(NAMES.ROLE_CHANGED, { role: state.role });
      }
    } catch (_) {}
  }

  // 会话期内登录/登出/换 token → auth 变更,实时刷新账号显示。
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !changes.auth) return;
    applyAuthUser(changes.auth.newValue || null);
    renderHeader();
    renderContext();
    renderLoginGate();
    emit(NAMES.AUTH_CHANGED, { loggedIn: state.user.loggedIn });
  });

  // ── 主动拉一次当前 active tab 的 PageContext ──────────────
  function pullPageContext() {
    try {
      chrome.runtime?.sendMessage?.({ type: 'DQ_GET_PAGE_CONTEXT' }, (resp) => {
        void chrome.runtime.lastError;
        const ctx = resp && resp.ok && resp.context;
        if (!ctx) return;
        applyPageContext(ctx);
      });
    } catch (_) {}
  }

  function applyPageContext(ctx) {
    state.pageContext = {
      url:             ctx.url || '',
      title:           ctx.title || '',
      kind:            ctx.kind || '',
      page_type:       ctx.page_type || '',
      page_item_count: Number(ctx.page_item_count || 0) || 0,
    };
    renderContext();
    emit(NAMES.PAGE_CONTEXT_CHANGED, state.pageContext);
  }

  // ── runtime.onMessage(background 推送)────────────────────
  function setupRuntimeListener() {
    if (!chrome?.runtime?.onMessage) return;
    chrome.runtime.onMessage.addListener((msg) => {
      if (!msg || typeof msg !== 'object') return;
      switch (msg.type) {
        case 'DQ_PAGE_CONTEXT_UPDATE':
          applyPageContext(msg.context || {}); break;
        case 'NOTIFICATION_COUNT':
          state.notifCount = msg.count || 0; renderHeader(); break;
        case 'RESUME_UPDATED':
          state.resume = msg.resume || state.resume; renderContext(); break;
        case 'CREDIT_UPDATED':
          state.credit.balance = msg.balance || 0;
          state.credit.lowWarning = msg.balance < 20;
          renderHeader();
          emit(NAMES.CREDIT_UPDATED, state.credit);
          break;
        case 'kicked':
          // 被网关踢出(版本过旧 / 异地登录等)→ 顶部弹常驻提示横幅
          showKickBanner(msg.reason);
          loadPlatformStatus().then(renderHeader);
          break;
        case 'registered':
          // 重新接入成功 → 收起提示横幅
          hideKickBanner();
          loadPlatformStatus().then(renderHeader);
          break;
        case 'connectionState':
          if (msg.connected) hideKickBanner();
          loadPlatformStatus().then(renderHeader);
          break;
        case 'loggedOut':
          loadPlatformStatus().then(renderHeader);
          break;
        case 'DQ_PREFILL_CHAT':
          // 平台页浮动「AI 分析」按钮 → 切到 AI Tab + 把 prompt 填进输入框
          switchTab('chat');
          if (msg.prompt) window.DQ.chat?.prefill?.(msg.prompt);
          break;
        case 'DQ_NAV_PROFILE':
          switchTab('profile');
          break;
        case 'DQ_EVAL_RESULT':
          // 平台页「查看详情」→ 切到 AI Tab + 呈现评估结果
          switchTab('chat');
          window.DQ.chat?.showEvalResult?.(msg.evaluated, msg.job_snapshot);
          break;
      }
    });
  }

  // 启动时拉一次待发 chat prompt(冷启动时 DQ_PREFILL_CHAT 可能在 sidepanel
  // 加载前就发出而丢失,这里兜底)。
  function pullPendingPrefill() {
    try {
      chrome.runtime?.sendMessage?.({ type: 'DQ_GET_PENDING_PREFILL' }, (resp) => {
        void chrome.runtime.lastError;
        if (resp && resp.prompt) {
          switchTab('chat');
          window.DQ.chat?.prefill?.(resp.prompt);
        }
      });
    } catch (_) {}
  }

  // ── 拉远端状态 ────────────────────────────────────────────
  async function loadPlatformStatus() {
    try {
      const resp = await new Promise(res =>
        chrome.runtime.sendMessage({ type: 'DQ_GET_PLATFORM_STATUS' }, res));
      if (resp && resp.ok && resp.platforms) {
        Object.assign(state.platformsStatus, resp.platforms);
      }
    } catch (_) {}
  }

  async function loadCredit() {
    try {
      const r = await window.DQ.api.apiGw.creditBalance();
      state.credit.balance = Number(r?.balance || 0);
      state.credit.lowWarning = state.credit.balance < 20;
      renderHeader();
    } catch (_) { /* fail-open */ }
  }

  // ── 登录 gate 行为 ────────────────────────────────────────
  // 直接在扩展内登录:打开 onboarding 向导(内含邮箱登录/注册 UI),
  // 不再跳 dinq.me 外部页。登录成功 → auth 落盘 → storage.onChanged
  // 监听自动隐藏 gate。
  els.gateOpenLogin?.addEventListener('click', () => {
    window.DQ.onboarding?.openLogin?.();
  });
  els.gateForgotPwd?.addEventListener('click', () => {
    window.DQ.forgotPassword?.open();
  });

  // ── 启动序列 ──────────────────────────────────────────────
  (async function init() {
    // 确保语言在首屏渲染前已从 storage 读出
    if (window.DQI18N && window.DQI18N.ready) {
      await window.DQI18N.ready;
    }
    await loadUserPrefs();
    renderHeader(); renderContext(); renderLoginGate();
    setupRuntimeListener();

    // 冷启动:若上次连接被踢出且尚未恢复,恢复提示横幅
    try {
      const k = await chrome.storage.local.get('extKick');
      if (k && k.extKick && k.extKick.reason) showKickBanner(k.extKick.reason);
    } catch (_) {}

    // 并发拉远端状态
    loadPlatformStatus().then(renderHeader);
    loadCredit();
    pullPageContext();
    pullPendingPrefill();

    emit(NAMES.AUTH_CHANGED, { loggedIn: state.user.loggedIn });

    // 发送角色变化事件（触发求职者工作台显示）
    const stored = await chrome.storage.local.get(['user']);
    if (stored.user?.onboarded) {
      emit(NAMES.ROLE_CHANGED, { role: state.role });
    }

    // 首屏路由:未登录 → 直接展示「登录 / 注册」表单(login-first);
    // 已登录但未走完引导 → 续跑角色 / 平台 / 简历引导。
    if (!state.user.loggedIn) {
      try { await window.DQ.onboarding?.openLogin?.(); } catch (_) {}
    } else if (!stored.user?.onboarded) {
      try { await window.DQ.onboarding?.startOnboarding?.(); } catch (_) {}
    }

    console.log('[app] ready · role=%s platform=%s', state.role, state.platform);
  })();
})();
