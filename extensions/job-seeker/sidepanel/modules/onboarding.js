/*
 * modules/onboarding.js — 通用 onboarding wizard(DESIGN.md §6.2 ob-*).
 *
 * SmartJob 账号登录是前置门禁,不计入 onboarding 步骤。未登录时由 app.js
 * 调 openLogin() 先完成登录;登录成功后再进入下面的使用场景 / 平台流程。
 *
 * 可见 onboarding:
 *   1 · 选择身份     jobseeker / recruiter,点即选(无确定按钮)
 *   2 · 选择平台     Boss / LinkedIn / Indeed,auto-detect 当前 tab 给高亮
 *   3 · 登录平台     检测平台登录态;未登录 / 异常 → 登录引导 + 手动检查
 *   4 · 初始化资料   求职者首次使用时补简历 / 偏好;招聘者直接完成
 *
 * 顶栏切换模式时,前两步由弹窗完成,然后直接进入 Step 3 平台登录检测。
 */
(function () {
  'use strict';
  const { NAMES, emit } = window.DQ.events;
  const api = window.DQ.api;

  // 各平台薪资档位（与后端 preferences_router.py 的校验集一致）
  const SALARY_OPTIONS = {
    boss:     ['3k以下', '3-5k', '5-10k', '10-20k', '20-30k', '30-50k', '50k+'],
    linkedin: ['<$60k/yr', '$60k-100k/yr', '$100k-150k/yr', '$150k-200k/yr', '$200k+/yr', 'Unspecified'],
    indeed:   ['<$60k/yr', '$60k-100k/yr', '$100k-150k/yr', '$150k-200k/yr', '$200k+/yr', 'Unspecified'],
  };

  const PLATFORMS = [
    { key: 'boss',     label: 'Boss 直聘',  subKey: 'onboarding.step2.bossSub' },
    { key: 'linkedin', label: 'LinkedIn',  subKey: 'onboarding.step2.linkedinSub' },
    { key: 'indeed',   label: 'Indeed',    subKey: 'onboarding.step2.indeedSub' },
  ];

  // ── 各平台 step 4 指引(URL / 登录方式 / 常见问题提示)─────────
  // 三个平台登录方式差异很大:Boss 主要扫码,LinkedIn 是邮箱+密码,Indeed 是
  // 邮箱 + magic link.每个平台有自己专属的步骤说明 / FAQ 链接 / 排查提示.
  function getPlatformGuides() {
    return {
      boss: {
        label: 'Boss 直聘',
        loginUrl: 'https://www.zhipin.com/web/user/?ka=header-login',
        homepage: 'https://www.zhipin.com/',
        steps: [
          { title: t('onboarding.guide.boss.step1.title'), sub: t('onboarding.guide.boss.step1.sub') },
          { title: t('onboarding.guide.boss.step2.title'), sub: t('onboarding.guide.boss.step2.sub') },
          { title: t('onboarding.guide.boss.step3.title'), sub: t('onboarding.guide.boss.step3.sub') },
        ],
        faqHint: t('onboarding.guide.boss.faq'),
      },
      linkedin: {
        label: 'LinkedIn',
        loginUrl: 'https://www.linkedin.com/login',
        homepage: 'https://www.linkedin.com/',
        steps: [
          { title: t('onboarding.guide.linkedin.step1.title'), sub: t('onboarding.guide.linkedin.step1.sub') },
          { title: t('onboarding.guide.linkedin.step2.title'), sub: t('onboarding.guide.linkedin.step2.sub') },
          { title: t('onboarding.guide.linkedin.step3.title'), sub: t('onboarding.guide.linkedin.step3.sub') },
        ],
        faqHint: t('onboarding.guide.linkedin.faq'),
      },
      indeed: {
        label: 'Indeed',
        loginUrl: 'https://secure.indeed.com/account/login',
        homepage: 'https://www.indeed.com/',
        steps: [
          { title: t('onboarding.guide.indeed.step1.title'), sub: t('onboarding.guide.indeed.step1.sub') },
          { title: t('onboarding.guide.indeed.step2.title'), sub: t('onboarding.guide.indeed.step2.sub') },
          { title: t('onboarding.guide.indeed.step3.title'), sub: t('onboarding.guide.indeed.step3.sub') },
        ],
        faqHint: t('onboarding.guide.indeed.faq'),
      },
    };
  }

  // wizard 状态机
  const state = {
    step: 1,          // 1 / 2 / 3 / '3c' / 4 / '5a' / '5b' / '5c'
    role: '',         // 'jobseeker' | 'recruiter'
    platform: '',     // 'boss' | 'linkedin' | 'indeed'
    accountMethod: '',// 'email'
    accountEmail: '',
    accountOk: false, // SmartJob 登录门禁完成?
    preAuthed: false, // 进 wizard 时已登录 → 直接走可见 onboarding
    loginFirst: false,// 由 openLogin() 进入:前置登录页(无返回 / 无进度条)
    modeSwitchFlow: false, // 顶栏切换模式进入:选择平台后只做平台登录检测,成功后直接回 AI 助手
    alreadyOnboarded: false, // 之前已完成过引导 → 跳过资料初始化,不重复弹
    platOk: false,    // 平台已登录?
    detectTimer: null,
    // 邮箱注册/登录表单
    mode: 'signup',         // 'signup' | 'signin'，控制 step 3 表单 CTA
    verificationId: '',     // step 3 → 3c 传递的 verification_id
    devCode: '',            // 开发态固定验证码(后端 dev_hint 回的 6 位码)→ 注册面板直显
    error: '',              // 表单错误（空即隐藏）
    info: '',               // 表单提示（重发成功 / dev 模式提醒）
    busy: false,            // 提交进行中，禁用 CTA
    cooldownEnds: 0,        // 重发倒计时基线（ms timestamp）
    cooldownTimer: null,    // 1Hz 重绘 #obResend 用的 setInterval
    // 资料初始化 — 简历获取 / AI 解析 / 分析结果
    bgMethod: '',           // 'upload' | 'manual'
    resumeFile: null,       // 待上传的简历 File
    resumeParseStatus: '',  // 'uploading'|'parsing'|'analyzing'|'failed'|'timeout'
    suggestion: null,       // { job_role, city, salary_range, notes, skills[] }
    prefsEditing: false,    // 偏好编辑子状态
    confirmedPrefs: null,   // 已确认偏好(finish 传给 chat 开场白)
    pollTimer: null,        // resume 解析轮询 setInterval
    pollDeadline: 0,        // 轮询超时基线
  };

  let mask = null;

  function escapeText(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── 子渲染 ────────────────────────────────────────────────────
  function renderProgress(active) {
    // SmartJob 登录已前置;可见 onboarding 只展示 4 个阶段:
    // 1 选择场景 / 2 选择平台 / 3 平台登录检测 / 4 求职偏好初始化
    return `
      <div class="ob-progress-bar">
        ${[1,2,3,4].map(n => {
          let cls = '';
          if (n < active) cls = 'done';
          else if (n === active) cls = 'active';
          return `<span class="ob-progress-seg ${cls}"></span>`;
        }).join('')}
      </div>`;
  }

  function renderBack() {
    return `<button type="button" class="ob-back" id="obBack">${t('onboarding.back')}</button>`;
  }

  function renderHood() {
    // 品牌区 = 「首页」按钮:与主顶栏一致,向导内也能随时回首页
    return `
      <header class="ob-hood">
        <button type="button" class="ob-home" id="obHome" title="${t('header.homeTitle')}">
          <span class="ob-bot" aria-hidden="true">S</span>
          <span class="ob-brand">${t('onboarding.home')}</span>
        </button>
        <button type="button" class="ob-settings" id="obSettings" title="${t('header.settingsTitle')}">ST</button>
      </header>`;
  }

  function renderFooter(name, hint) {
    return `
      <footer class="ob-footer">
        <div class="step-name">${escapeText(name)}</div>
        <div class="step-hint">${hint || ''}</div>
      </footer>`;
  }

  // ── Step 1:角色 ──────────────────────────────────────────────
  function renderStep1() {
    const cards = [
      { v: 'jobseeker', ico: 'JS', tk: 'onboarding.step1.roleJobseeker', dk: 'onboarding.step1.roleJobseekerDesc' },
      { v: 'recruiter', ico: 'HR', tk: 'onboarding.step1.roleRecruiter', dk: 'onboarding.step1.roleRecruiterDesc' },
    ];
    const html = cards.map(c => `
      <div class="ob-option-card ${state.role === c.v ? 'selected' : ''}" data-role="${c.v}">
        <span class="ob-option-ico" aria-hidden="true">${c.ico}</span>
        <div class="ob-option-text">
          <div class="ob-option-title">${t(c.tk)}</div>
          <div class="ob-option-desc">${t(c.dk)}</div>
        </div>
        <span class="ob-option-radio">✓</span>
      </div>`).join('');

    return `
      ${renderHood()}
      ${renderProgress(1)}
      <div class="ob-body">
        <h2 class="ob-title">${t('onboarding.step1.title')}</h2>
        <p class="ob-desc">${t('onboarding.step1.desc')}</p>
        <div style="display:flex;flex-direction:column;gap:var(--sp-2)">${html}</div>
      </div>
      ${renderFooter(t('onboarding.step1.footer'), t('onboarding.step1.footerHint'))}`;
  }

  // ── Step 2:平台 ──────────────────────────────────────────────
  function renderStep2() {
    const detected = detectCurrentPlatform();   // 异步 + 估算
    const html = PLATFORMS.map(p => `
      <div class="ob-option-card ${state.platform === p.key ? 'selected' : ''}" data-plat="${p.key}">
        <span class="ob-option-ico"><span class="ob-platform-dot ${p.key}" aria-hidden="true"></span></span>
        <div class="ob-option-text">
          <div class="ob-option-title">${escapeText(p.label)}</div>
          <div class="ob-option-desc">${t(p.subKey)}</div>
        </div>
        <span class="ob-option-radio">✓</span>
      </div>`).join('');
    const detectMsg = detected
      ? t('onboarding.step2.descDetected', { platform: escapeText(({ boss: 'Boss 直聘', linkedin: 'LinkedIn', indeed: 'Indeed' })[detected] || '') })
      : t('onboarding.step2.descDefault');
    return `
      ${renderHood()}
      ${renderBack()}
      ${renderProgress(2)}
      <div class="ob-body">
        <h2 class="ob-title">${t('onboarding.step2.title')}</h2>
        <p class="ob-desc">${detectMsg}</p>
        <div style="display:flex;flex-direction:column;gap:var(--sp-2)">${html}</div>
      </div>
      ${renderFooter(t('onboarding.step2.footer'), t('onboarding.step2.footerHint'))}`;
  }

  // ── SmartJob 前置登录页（注册 / 登录 双模式,不计入 onboarding 步骤）──
  function renderStep3() {
    const mode = state.mode === 'signin' ? 'signin' : 'signup';
    const primaryLabel = mode === 'signup' ? t('onboarding.step3.ctaSignup') : t('onboarding.step3.ctaSignin');
    const desc = mode === 'signup'
      ? t('onboarding.step3.descSignup')
      : t('onboarding.step3.descSignin');
    return `
      ${renderHood()}
      ${state.loginFirst ? '' : renderBack()}
      ${state.loginFirst ? '' : renderProgress(2)}
      <div class="ob-body">
        <h2 class="ob-title">${t('onboarding.step3.title')}</h2>
        <p class="ob-desc">${desc}</p>

        <div class="ob-mode-toggle" role="tablist">
          <button type="button" data-mode="signin" class="${mode === 'signin' ? 'active' : ''}">${t('onboarding.step3.tabSignin')}</button>
          <button type="button" data-mode="signup" class="${mode === 'signup' ? 'active' : ''}">${t('onboarding.step3.tabSignup')}</button>
        </div>

        <form id="obEmailForm" novalidate>
          <div class="ob-field-label" style="margin-top:var(--sp-2\\.5)">${t('onboarding.step3.fieldEmail')}</div>
          <input type="email" class="ob-input" id="obEmail" placeholder="you@example.com"
                 autocomplete="email" required
                 value="${escapeText(state.accountEmail)}">

          <div class="ob-field-label" style="margin-top:var(--sp-2)">${t('onboarding.step3.fieldPassword')}</div>
          <div class="ob-input-row">
            <input type="password" class="ob-input" id="obPassword"
                   placeholder="${mode === 'signup' ? t('onboarding.step3.placeholderPwdSignup') : t('onboarding.step3.placeholderPwdSignin')}"
                   autocomplete="${mode === 'signup' ? 'new-password' : 'current-password'}"
                   minlength="8" maxlength="72" required>
            <button type="button" class="ob-eye-btn" id="obEyeBtn" tabindex="-1">${t('onboarding.step3.pwdShow')}</button>
          </div>
          ${mode === 'signup' ? `<div class="ob-hint" style="margin-top:var(--sp-1)">${t('onboarding.step3.pwdHint')}</div>` : ''}

          ${state.error ? `<div class="ob-form-error" style="margin-top:var(--sp-2)">${escapeText(state.error)}</div>` : ''}

          <button type="submit" class="ob-cta" id="obSubmit" style="margin-top:var(--sp-2\\.5)"
                  ${state.busy ? 'disabled' : ''}>
            ${state.busy ? t('onboarding.step3.ctaBusy') : primaryLabel}
          </button>
        </form>

        <p style="font-size:var(--text-xs);color:var(--muted);text-align:center;margin-top:var(--sp-2)">
          ${mode === 'signup' ? t('onboarding.step3.termsSignup') : t('onboarding.step3.termsSignin')}《<a href="https://job.joyhouse.chat/legal/terms" target="_blank" rel="noopener">${t('onboarding.step3.terms')}</a>》
          与《<a href="https://job.joyhouse.chat/legal/privacy" target="_blank" rel="noopener">${t('onboarding.step3.privacy')}</a>》
        </p>
      </div>
      ${renderFooter(t('onboarding.step3.footer'), mode === 'signup' ? t('onboarding.step3.footerSignup') : t('onboarding.step3.footerSignin'))}`;
  }

  // ── SmartJob 前置登录验证码页 ─────────────────────────────────
  function renderStep3c() {
    const target = state.accountEmail ? maskEmail(state.accountEmail) : t('onboarding.step3c.desc').replace(' {email}', '');
    const cooldownLeft = Math.max(0, Math.ceil((state.cooldownEnds - Date.now()) / 1000));
    return `
      ${renderHood()}
      ${renderBack()}
      ${state.loginFirst ? '' : renderProgress(2)}
      <div class="ob-body">
        <h2 class="ob-title">${t('onboarding.step3c.title')}</h2>
        <p class="ob-desc">${t('onboarding.step3c.desc', { email: escapeText(target) })}</p>

        ${state.devCode ? `<div class="ob-form-info" style="margin-top:var(--sp-2)">${t('onboarding.step3c.devCodeHint', { code: escapeText(state.devCode) })}</div>` : ''}

        <form id="obCodeForm" novalidate>
          <input type="text" class="ob-code-input" id="obCode"
                 inputmode="numeric" pattern="\\d{6}" maxlength="6"
                 placeholder="······" autocomplete="one-time-code"
                 autofocus>

          ${state.info  ? `<div class="ob-form-info"  style="margin-top:var(--sp-2)">${escapeText(state.info)}</div>` : ''}
          ${state.error ? `<div class="ob-form-error" style="margin-top:var(--sp-2)">${escapeText(state.error)}</div>` : ''}

          <button type="submit" class="ob-cta" id="obVerifySubmit" style="margin-top:var(--sp-2\\.5)"
                  ${state.busy ? 'disabled' : ''}>
            ${state.busy ? t('onboarding.step3c.ctaBusy') : t('onboarding.step3c.ctaVerify')}
          </button>
        </form>

        <div class="ob-link-row" style="margin-top:var(--sp-2)">
          <span class="label">${t('onboarding.step3c.noRecv')}</span>
          <button type="button" class="action" id="obResend" ${(cooldownLeft > 0 || state.busy) ? 'disabled' : ''}>
            ${cooldownLeft > 0 ? t('onboarding.step3c.resendCooldown', { n: cooldownLeft }) : t('onboarding.step3c.resend')}
          </button>
        </div>
      </div>
      ${renderFooter(t('onboarding.step3c.footer'), t('onboarding.step3c.footerHint'))}`;
  }

  // ── Step 3:登录目标平台(按 getPlatformGuides()[state.platform] 渲染)───
  function renderStep4() {
    const PLATFORM_GUIDES = getPlatformGuides();
    const guide = PLATFORM_GUIDES[state.platform] || PLATFORM_GUIDES.boss;
    const platLabel = guide.label;
    const dotCls = state.platform;
    const detected = state.platOk;
    const stepsHtml = guide.steps.map((s, i) => `
      <div class="ob-step-row ${(!detected && i === 2) ? 'pending' : ''}">
        <span class="ob-step-num">${i + 1}</span>
        <div class="ob-step-text">
          <div class="ob-step-title">${escapeText(s.title)}</div>
          <div class="ob-step-sub">${escapeText(s.sub)}</div>
        </div>
      </div>`).join('');

    return `
      ${renderHood()}
      ${renderBack()}
      ${renderProgress(3)}
      <div class="ob-body">
        <h2 class="ob-title">${t('onboarding.step4.title', { platform: escapeText(platLabel) })}</h2>
        <p class="ob-desc">${t('onboarding.step4.desc', { platform: escapeText(platLabel) })}</p>

        ${detected ? `
          <div class="ob-alert-card" style="background:var(--green-50);border-color:var(--green-200)">
            <div class="ob-alert-title" style="color:var(--green-600)">${t('onboarding.step4.detectedTitle', { platform: escapeText(platLabel) })}</div>
            <div class="ob-alert-body" style="color:var(--ink-2)">${t('onboarding.step4.detectedBody')}</div>
          </div>
        ` : `
          <div class="ob-alert-card">
            <div class="ob-alert-title">${t('onboarding.step4.notDetectedTitle', { platform: escapeText(platLabel) })}</div>
            <div class="ob-alert-body">${t('onboarding.step4.notDetectedBody', { platform: escapeText(platLabel) })}</div>
          </div>
        `}

        <div class="ob-step-list">${stepsHtml}</div>

        ${guide.faqHint ? `
          <div style="font-size:var(--text-xs);color:var(--muted);line-height:var(--leading-relaxed);padding:var(--sp-2);background:var(--surface-alt);border-radius:var(--rd-md)">
            ${escapeText(guide.faqHint)}
          </div>
        ` : ''}
      </div>
      <div class="ob-cta-wrap">
        <button type="button" class="ob-cta" id="obGoPlat">
          <span class="ob-platform-dot ${dotCls}" aria-hidden="true"
                style="width:10px;height:10px"></span>
          <span>${t('onboarding.step4.goPlatBtn', { platform: escapeText(platLabel) })}</span>
        </button>
        ${detected ? `
          <button type="button" class="ob-cta" id="obFinish" style="background:var(--green-600)">
            ${t('onboarding.step4.finishBtn')}
          </button>
        ` : `
          <div class="ob-detecting">
            <span class="dots"><span></span><span></span><span></span></span>
            <span>${t('onboarding.step4.detecting')}</span>
          </div>
          <button type="button" class="ob-cta ob-cta-google" id="obManualCheck">
            ${t('onboarding.step4.manualCheck')}
          </button>
        `}
      </div>
      ${renderFooter(t('onboarding.step4.footer', { platform: escapeText(platLabel) }), detected ? t('onboarding.step4.footerDetected') : t('onboarding.step4.footerPending'))}`;
  }

  // ── Step 4a:了解你的背景(三种简历/偏好获取方式)──────────────
  function renderStep5a() {
    return `
      ${renderHood()}
      ${renderBack()}
      ${renderProgress(4)}
      <div class="ob-body">
        <span class="ob-mode-chip">${t('onboarding.step5a.modeChip')}</span>
        <h2 class="ob-title">${t('onboarding.step5a.title')}</h2>
        <p class="ob-desc">${t('onboarding.step5a.desc')}</p>
        <input type="file" id="obResumeInput" accept=".pdf,.doc,.docx" hidden>
        <div style="display:flex;flex-direction:column;gap:var(--sp-2)">
          <div class="ob-option-card" data-bg="upload">
            <span class="ob-option-ico" aria-hidden="true">CV</span>
            <div class="ob-option-text">
              <div class="ob-option-title">${t('onboarding.step5a.uploadTitle')}</div>
              <div class="ob-option-desc">${t('onboarding.step5a.uploadDesc')}</div>
            </div>
            <span style="color:var(--muted);font-size:var(--text-lg)" aria-hidden="true">→</span>
          </div>
          <div class="ob-option-card disabled" data-bg="boss">
            <span class="ob-option-ico" aria-hidden="true">AI</span>
            <div class="ob-option-text">
              <div class="ob-option-title">${t('onboarding.step5a.bossTitle')}</div>
              <div class="ob-option-desc">${t('onboarding.step5a.bossDesc')}</div>
            </div>
            <span class="ob-option-badge">${t('onboarding.step5a.bossBadge')}</span>
          </div>
          <div class="ob-option-card" data-bg="manual">
            <span class="ob-option-ico" aria-hidden="true">ED</span>
            <div class="ob-option-text">
              <div class="ob-option-title">${t('onboarding.step5a.manualTitle')}</div>
              <div class="ob-option-desc">${t('onboarding.step5a.manualDesc')}</div>
            </div>
            <span style="color:var(--muted);font-size:var(--text-lg)" aria-hidden="true">→</span>
          </div>
        </div>
        <button type="button" class="ob-skip-link" id="obSkipBg">${t('onboarding.step5a.skip')}</button>
      </div>
      ${renderFooter(t('onboarding.step5a.footer'), t('onboarding.step5a.footerHint'))}`;
  }

  // ── Step 4b:上传 + 解析进度 ──────────────────────────────────
  function renderStep5b() {
    const st = state.resumeParseStatus;
    let body;
    if (st === 'failed') {
      body = `
        <div class="ob-alert-card" style="background:var(--red-50);border-color:var(--red-200)">
          <div class="ob-alert-title" style="color:var(--red-600)">${t('onboarding.step5b.failedTitle')}</div>
          <div class="ob-alert-body">${escapeText(state.error || t('onboarding.step5b.failedDefault'))}</div>
        </div>`;
    } else if (st === 'timeout') {
      body = `
        <div class="ob-alert-card">
          <div class="ob-alert-title">${t('onboarding.step5b.timeoutTitle')}</div>
          <div class="ob-alert-body">${t('onboarding.step5b.timeoutBody')}</div>
        </div>`;
    } else {
      const label = st === 'uploading' ? t('onboarding.step5b.uploading')
                  : st === 'analyzing' ? t('onboarding.step5b.analyzing')
                  : t('onboarding.step5b.parsing');
      body = `
        <div class="ob-detecting" style="padding:var(--sp-4) 0">
          <span class="dots"><span></span><span></span><span></span></span>
          <span>${label}</span>
        </div>
        ${state.resumeFile ? `<p class="ob-desc" style="text-align:center">${escapeText(state.resumeFile.name)}</p>` : ''}`;
    }
    const showActions = st === 'failed' || st === 'timeout';
    return `
      ${renderHood()}
      ${renderBack()}
      ${renderProgress(4)}
      <div class="ob-body">
        <span class="ob-mode-chip">${t('onboarding.step5b.modeChip')}</span>
        <h2 class="ob-title">${t('onboarding.step5b.title')}</h2>
        <p class="ob-desc">${t('onboarding.step5b.desc')}</p>
        ${body}
      </div>
      ${showActions ? `
        <div class="ob-cta-wrap">
          ${st === 'timeout'
            ? `<button type="button" class="ob-cta" id="obKeepWaiting">${t('onboarding.step5b.keepWaiting')}</button>`
            : `<button type="button" class="ob-cta" id="obResumeRetry">${t('onboarding.step5b.retry')}</button>`}
          <button type="button" class="ob-cta ob-cta-google" id="obToManual">${t('onboarding.step5b.toManual')}</button>
        </div>
      ` : ''}
      ${renderFooter(t('onboarding.step5b.footer'), t('onboarding.step5b.footerHint'))}`;
  }

  // ── Step 4c:AI 分析结果(summary)/ 偏好编辑(form)────────────
  function renderStep5c() {
    return state.prefsEditing ? renderStep5cForm() : renderStep5cSummary();
  }

  function renderStep5cSummary() {
    const s = state.suggestion || {};
    const box = (v) => v
      ? `<div class="ob-result-box">${escapeText(v)}</div>`
      : `<div class="ob-result-box empty">${t('onboarding.step5c.emptyField')}</div>`;
    const skills = Array.isArray(s.skills) ? s.skills : [];
    return `
      ${renderHood()}
      ${renderBack()}
      ${renderProgress(4)}
      <div class="ob-body">
        <span class="ob-mode-chip">${t('onboarding.step5c.modeChip')}</span>
        <h2 class="ob-title">${t('onboarding.step5c.summaryTitle')}</h2>
        <p class="ob-desc">${t('onboarding.step5c.summaryDesc')}</p>
        <div class="ob-result-card">
          <div class="ob-result-head">${t('onboarding.step5c.aiLabel')}</div>
          <div class="ob-result-field">
            <div class="ob-field-label">${t('onboarding.step5c.fieldRole')}</div>
            ${box(s.job_role)}
          </div>
          <div class="ob-result-field">
            <div class="ob-field-label">${t('onboarding.step5c.fieldCity')}</div>
            ${box(s.city)}
          </div>
          <div class="ob-result-field">
            <div class="ob-field-label">${t('onboarding.step5c.fieldSalary')}</div>
            ${box(s.salary_range)}
          </div>
          ${skills.length ? `
            <div class="ob-result-field">
              <div class="ob-field-label">${t('onboarding.step5c.fieldSkills')}</div>
              <div class="ob-skill-row">
                ${skills.map(k => `<span class="ob-skill-chip">${escapeText(k)}</span>`).join('')}
              </div>
            </div>
          ` : ''}
        </div>
        ${state.error ? `<div class="ob-form-error">${escapeText(state.error)}</div>` : ''}
      </div>
      <div class="ob-cta-wrap">
        <button type="button" class="ob-cta" id="obConfirmPrefs" ${state.busy ? 'disabled' : ''}>
          ${state.busy ? t('onboarding.step5c.savingBtn') : t('onboarding.step5c.confirmBtn')}
        </button>
        <button type="button" class="ob-cta ob-cta-google" id="obEditPrefs" ${state.busy ? 'disabled' : ''}>${t('onboarding.step5c.editBtn')}</button>
      </div>
      ${renderFooter(t('onboarding.step5c.summaryFooter'), t('onboarding.step5c.summaryFooterHint'))}`;
  }

  function renderStep5cForm() {
    const s = state.suggestion || {};
    const opts = SALARY_OPTIONS[state.platform] || SALARY_OPTIONS.boss;
    const salaryOpts = [`<option value="">${t('onboarding.step5c.salaryNoLimit')}</option>`]
      .concat(opts.map(o =>
        `<option value="${escapeText(o)}" ${s.salary_range === o ? 'selected' : ''}>${escapeText(o)}</option>`))
      .join('');
    const isManual = state.bgMethod === 'manual';
    return `
      ${renderHood()}
      ${renderBack()}
      ${renderProgress(4)}
      <div class="ob-body">
        <span class="ob-mode-chip">${t('onboarding.step5c.modeChip')}</span>
        <h2 class="ob-title">${isManual ? t('onboarding.step5c.formManualTitle') : t('onboarding.step5c.formEditTitle')}</h2>
        <p class="ob-desc">${isManual ? t('onboarding.step5c.formManualDesc') : t('onboarding.step5c.formEditDesc')}</p>
        <form id="obPrefsForm" novalidate>
          <div class="ob-field-label">${t('onboarding.step5c.fieldFormRole')}</div>
          <input type="text" class="ob-input" id="obPrefRole" placeholder="${t('onboarding.step5c.placeholderRole')}"
                 value="${escapeText(s.job_role || '')}">
          <div class="ob-field-label" style="margin-top:var(--sp-2)">${t('onboarding.step5c.fieldFormCity')}</div>
          <input type="text" class="ob-input" id="obPrefCity" placeholder="${t('onboarding.step5c.placeholderCity')}"
                 value="${escapeText(s.city || '')}">
          <div class="ob-field-label" style="margin-top:var(--sp-2)">${t('onboarding.step5c.fieldFormSalary')}</div>
          <select class="ob-input" id="obPrefSalary">${salaryOpts}</select>
          <div class="ob-field-label" style="margin-top:var(--sp-2)">${t('onboarding.step5c.fieldFormNotes')}</div>
          <input type="text" class="ob-input" id="obPrefNotes" placeholder="${t('onboarding.step5c.placeholderNotes')}"
                 value="${escapeText(s.notes || '')}">
          ${state.error ? `<div class="ob-form-error" style="margin-top:var(--sp-2)">${escapeText(state.error)}</div>` : ''}
        </form>
      </div>
      <div class="ob-cta-wrap">
        <button type="button" class="ob-cta" id="obSavePrefs" ${state.busy ? 'disabled' : ''}>
          ${state.busy ? t('onboarding.step5c.savingBtn') : t('onboarding.step5c.saveBtn')}
        </button>
        ${isManual ? '' : `<button type="button" class="ob-cta ob-cta-google" id="obCancelEdit" ${state.busy ? 'disabled' : ''}>${t('onboarding.step5c.cancelBtn')}</button>`}
      </div>
      ${renderFooter(t('onboarding.step5c.formFooter'), t('onboarding.step5c.formFooterHint'))}`;
  }

  // ── Step 4 处理逻辑 ───────────────────────────────────────────
  // Step 3 平台登录完成:仅首次引导的求职者走资料初始化。
  // 招聘者、或之前已完成过引导的求职者(如「切换角色」重进)直接结束 ——
  // 资料初始化只在首次引导出现,不再每次重复弹。
  function completeStep4() {
    if (state.detectTimer) { clearInterval(state.detectTimer); state.detectTimer = null; }
    if (state.modeSwitchFlow) {
      finish();
    } else if (state.role === 'jobseeker' && !state.alreadyOnboarded) {
      state.step = '5a';
      state.error = '';
      render();
    } else {
      finish();
    }
  }

  function chooseBackground(method) {
    if (method === 'boss') {
      alert(t('onboarding.step5a.bossComing'));
      return;
    }
    if (method === 'upload') {
      mask.querySelector('#obResumeInput')?.click();
      return;
    }
    if (method === 'manual') {
      state.bgMethod = 'manual';
      state.suggestion = { skills: [] };
      state.prefsEditing = true;
      state.error = '';
      state.step = '5c';
      render();
    }
  }

  function startResumeUpload(file) {
    if (file.size > 10 * 1024 * 1024) {
      alert(t('onboarding.step5b.tooBig'));
      return;
    }
    if (!/\.(pdf|doc|docx)$/i.test(file.name)) {
      alert(t('onboarding.step5b.wrongType'));
      return;
    }
    state.bgMethod = 'upload';
    state.resumeFile = file;
    state.error = '';
    state.resumeParseStatus = 'uploading';
    state.step = '5b';
    render();
    uploadAndPoll();
  }

  function skipBackground() {
    stopPoll();
    state.confirmedPrefs = null;
    finish();
  }

  function stopPoll() {
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  }

  async function uploadAndPoll() {
    try {
      await api.agentGw.resumeUpload(state.resumeFile);
      state.resumeParseStatus = 'parsing';
      if (state.step === '5b') render();
      state.pollDeadline = Date.now() + 75000; // ~75s 上限
      pollResumeStatus();
    } catch (err) {
      state.resumeParseStatus = 'failed';
      state.error = t('onboarding.step5b.uploadFail') + (err.message || err);
      if (state.step === '5b') render();
    }
  }

  function pollResumeStatus() {
    if (state.pollTimer) return;
    const tick = async () => {
      if (state.step !== '5b') { stopPoll(); return; }
      try {
        const r = await api.agentGw.resumeStatus();
        const st = r && r.parse_status;
        if (st === 'done') {
          stopPoll();
          loadSuggestionAndGo();
          return;
        }
        if (st === 'failed') {
          stopPoll();
          state.resumeParseStatus = 'failed';
          state.error = t('onboarding.step5b.parseFail');
          render();
          return;
        }
        // pending / parsing / 暂无 → 继续,直到超时
        if (Date.now() > state.pollDeadline) {
          stopPoll();
          state.resumeParseStatus = 'timeout';
          render();
        }
      } catch (err) {
        // 单次网络抖动忽略;持续失败到超时再报错
        if (Date.now() > state.pollDeadline) {
          stopPoll();
          state.resumeParseStatus = 'failed';
          state.error = t('onboarding.step5b.statusFail') + (err.message || err);
          render();
        }
      }
    };
    tick();
    state.pollTimer = setInterval(tick, 2500);
  }

  async function loadSuggestionAndGo() {
    state.resumeParseStatus = 'analyzing';
    if (state.step === '5b') render();
    try {
      const [sugResp, parsedResp] = await Promise.all([
        api.agentGw.suggestPreferences(state.platform).catch(() => null),
        api.agentGw.resumeParsed().catch(() => null),
      ]);
      const sug = (sugResp && sugResp.suggestion) || {};
      const parsed = (parsedResp && parsedResp.parsed) || {};
      state.suggestion = {
        job_role: sug.job_role || (parsed.target_positions && parsed.target_positions[0]) || '',
        city: sug.city || (parsed.target_cities && parsed.target_cities[0]) || '',
        salary_range: sug.salary_range || '',
        notes: sug.notes || '',
        skills: Array.isArray(parsed.skills) ? parsed.skills.slice(0, 12) : [],
      };
      state.prefsEditing = false;
      state.error = '';
      state.step = '5c';
      render();
    } catch (err) {
      state.resumeParseStatus = 'failed';
      state.error = t('onboarding.step5b.analyzeFail') + (err.message || err);
      if (state.step === '5b') render();
    }
  }

  async function retryResume() {
    state.error = '';
    state.resumeParseStatus = 'parsing';
    render();
    try {
      await api.agentGw.resumeRetry();
      state.pollDeadline = Date.now() + 75000;
      pollResumeStatus();
    } catch (err) {
      state.resumeParseStatus = 'failed';
      state.error = t('onboarding.step5b.retryFail') + (err.message || err);
      render();
    }
  }

  function keepWaiting() {
    state.error = '';
    state.resumeParseStatus = 'parsing';
    state.pollDeadline = Date.now() + 75000;
    render();
    pollResumeStatus();
  }

  function toManualFromStep5b() {
    stopPoll();
    state.bgMethod = 'manual';
    state.suggestion = { skills: [] };
    state.prefsEditing = true;
    state.error = '';
    state.step = '5c';
    render();
  }

  // 保存求职偏好并结束引导
  async function persistPrefs(body) {
    state.busy = true;
    state.error = '';
    render();
    try {
      await api.agentGw.savePreferences(state.platform, body);
      state.confirmedPrefs = body;
      state.busy = false;
      finish();
    } catch (err) {
      state.busy = false;
      state.error = t('onboarding.step5c.saveFail') + (err.message || err);
      render();
    }
  }

  function confirmPrefs() {
    const s = state.suggestion || {};
    persistPrefs({
      job_role: s.job_role || '',
      city: s.city || '',
      salary_range: s.salary_range || '',
      notes: s.notes || '',
    });
  }

  function savePrefsForm() {
    const role   = (mask.querySelector('#obPrefRole')?.value || '').trim();
    const city   = (mask.querySelector('#obPrefCity')?.value || '').trim();
    const salary = mask.querySelector('#obPrefSalary')?.value || '';
    const notes  = (mask.querySelector('#obPrefNotes')?.value || '').trim();
    // 先把已输入值写回 state.suggestion,避免校验失败重渲染丢输入
    state.suggestion = {
      ...(state.suggestion || {}),
      job_role: role, city, salary_range: salary, notes,
    };
    if (!role) {
      state.error = t('onboarding.step5c.errRequireRole');
      render();
      return;
    }
    persistPrefs({ job_role: role, city, salary_range: salary, notes });
  }

  // ── 平台检测(检查 active tab URL 命中哪个平台)──────────────
  function detectCurrentPlatform() {
    // 同步返回 cache 里的 platform(异步在 init 时拉一次写入 state._lastDetected)
    return state._lastDetected || '';
  }
  async function refreshDetected() {
    try {
      const [tab] = await new Promise(r => chrome.tabs.query({ active: true, currentWindow: true }, r));
      const url = (tab && tab.url) || '';
      if (/zhipin\.com/.test(url))    state._lastDetected = 'boss';
      else if (/linkedin\.com/.test(url)) state._lastDetected = 'linkedin';
      else if (/indeed\.com/.test(url))   state._lastDetected = 'indeed';
      else state._lastDetected = '';
      // 没选过平台 + 检测到了 → 自动给个默认高亮(用户还能改)
      if (!state.platform && state._lastDetected) state.platform = state._lastDetected;
    } catch (_) { state._lastDetected = ''; }
  }

  // ── 主路由 ────────────────────────────────────────────────────
  function render() {
    if (!mask) return;
    const fn = ({
      1: renderStep1, 2: renderStep2, 3: renderStep3,
      '3c': renderStep3c, 4: renderStep4,
      '5a': renderStep5a, '5b': renderStep5b, '5c': renderStep5c,
    })[state.step];
    mask.innerHTML = `<article class="ob-card">${fn()}</article>`;
    bind();
  }

  function bind() {
    mask.querySelector('#obBack')?.addEventListener('click', back);
    mask.querySelector('#obHome')?.addEventListener('click', goHomeFromWizard);
    mask.querySelector('#obSettings')?.addEventListener('click', () => {
      try { chrome.runtime.openOptionsPage(); } catch (_) {}
    });
    // step 1
    mask.querySelectorAll('[data-role]').forEach(el => {
      el.addEventListener('click', () => {
        state.role = el.dataset.role;
        state.step = 2;
        render();
      });
    });
    // step 2
    mask.querySelectorAll('[data-plat]').forEach(el => {
      el.addEventListener('click', () => {
        state.platform = el.dataset.plat;
        // 顶栏切换已经发生在主界面内,必须进入平台检测;首次引导才需要兜底回前置登录页。
        state.step = (state.modeSwitchFlow || state.preAuthed) ? 4 : 3;
        if (state.step === 4) {
          state.platOk = false;
          if (state.detectTimer) { clearInterval(state.detectTimer); state.detectTimer = null; }
        }
        render();
        if (state.step === 4) startPlatformDetect();
      });
    });
    // SmartJob 前置登录 — 模式切换 [注册|登录]
    mask.querySelectorAll('.ob-mode-toggle [data-mode]').forEach(el => {
      el.addEventListener('click', () => {
        const next = el.dataset.mode === 'signin' ? 'signin' : 'signup';
        if (state.mode === next) return;
        state.mode = next;
        state.error = '';
        render();
      });
    });
    // SmartJob 前置登录 — 邮箱表单提交
    const emailForm = mask.querySelector('#obEmailForm');
    if (emailForm) {
      emailForm.addEventListener('submit', (e) => {
        e.preventDefault();
        submitEmailForm();
      });
      mask.querySelector('#obEyeBtn')?.addEventListener('click', toggleShowPassword);
    }
    // SmartJob 前置登录 — 验证码表单
    const codeForm = mask.querySelector('#obCodeForm');
    if (codeForm) {
      const codeInput = mask.querySelector('#obCode');
      codeInput?.addEventListener('input', () => {
        codeInput.value = codeInput.value.replace(/\D/g, '').slice(0, 6);
      });
      codeForm.addEventListener('submit', (e) => {
        e.preventDefault();
        submitCodeForm();
      });
      mask.querySelector('#obResend')?.addEventListener('click', resendCode);
      // 重进 step 3c 时如果还在冷却，重启 1Hz 倒计时
      if (state.cooldownEnds > Date.now() && !state.cooldownTimer) {
        tickCooldown();
      }
    }
    // step 3 — 平台登录检测
    mask.querySelector('#obGoPlat')?.addEventListener('click', () => {
      const guide = getPlatformGuides()[state.platform];
      const url = guide?.loginUrl || guide?.homepage;
      if (url) chrome.tabs?.create?.({ url });
    });
    mask.querySelector('#obManualCheck')?.addEventListener('click', manualCheck);
    mask.querySelector('#obFinish')?.addEventListener('click', completeStep4);
    // step 4a — 背景获取方式
    mask.querySelectorAll('[data-bg]').forEach(el => {
      el.addEventListener('click', () => chooseBackground(el.dataset.bg));
    });
    mask.querySelector('#obResumeInput')?.addEventListener('change', (e) => {
      const f = e.target.files && e.target.files[0];
      if (f) startResumeUpload(f);
    });
    mask.querySelector('#obSkipBg')?.addEventListener('click', skipBackground);
    // step 4b — 解析失败 / 超时出口
    mask.querySelector('#obResumeRetry')?.addEventListener('click', retryResume);
    mask.querySelector('#obKeepWaiting')?.addEventListener('click', keepWaiting);
    mask.querySelector('#obToManual')?.addEventListener('click', toManualFromStep5b);
    // step 4c — 确认 / 编辑 / 保存
    mask.querySelector('#obConfirmPrefs')?.addEventListener('click', confirmPrefs);
    mask.querySelector('#obEditPrefs')?.addEventListener('click', () => {
      state.prefsEditing = true;
      state.error = '';
      render();
    });
    mask.querySelector('#obCancelEdit')?.addEventListener('click', () => {
      state.prefsEditing = false;
      state.error = '';
      render();
    });
    const prefsForm = mask.querySelector('#obPrefsForm');
    if (prefsForm) {
      prefsForm.addEventListener('submit', (e) => { e.preventDefault(); savePrefsForm(); });
    }
    mask.querySelector('#obSavePrefs')?.addEventListener('click', savePrefsForm);
  }

  // hood「首页」按钮:已登录 → 关向导回 AI助手 tab;未登录 → 回前置登录页。
  async function goHomeFromWizard() {
    if (await isLoggedIn()) {
      close();
      try { window.DQ.app?.switchTab?.('chat'); } catch (_) {}
    } else {
      openLogin();
    }
  }

  function back() {
    if (state.step === 2) state.step = 1;
    else if (state.step === 3) state.step = 2;
    else if (state.step === '3c') state.step = 3;
    else if (state.step === 4) state.step = (state.modeSwitchFlow || state.preAuthed) ? 2 : 3;
    else if (state.step === '5a') state.step = 4;
    else if (state.step === '5b') state.step = '5a';
    else if (state.step === '5c') state.step = '5a';
    // 离开 5b 一律停掉解析轮询
    stopPoll();
    if (state.step !== 4 && state.detectTimer) {
      clearInterval(state.detectTimer); state.detectTimer = null;
    }
    if (state.step !== '3c' && state.cooldownTimer) {
      clearInterval(state.cooldownTimer); state.cooldownTimer = null;
    }
    state.error = '';
    state.info = '';
    render();
  }

  // SmartJob 账号登录 / 注册成功后的去向:
  //   - 老用户(已完成过引导)→ 关向导,直接进 AI 助手;
  //   - 新用户 → 续跑可见 onboarding。账号已就绪(preAuthed),Step 2 选完平台直接跳平台登录。
  //     已选过角色 / 平台的 → 直接到平台登录,否则从 Step 1 角色选择起。
  function afterAccountReady() {
    state.loginFirst = false;
    state.modeSwitchFlow = false;
    if (state.alreadyOnboarded) {
      close();
      try { window.DQ.app?.switchTab?.('chat'); } catch (_) {}
      return;
    }
    if (state.role && state.platform) {
      state.step = 4;
      startPlatformDetect();
    } else {
      state.step = 1;
    }
    render();
  }

  // ── SmartJob 邮箱表单：注册 / 登录 ─────────────────────────────
  async function submitEmailForm() {
    const emailEl = mask.querySelector('#obEmail');
    const pwEl    = mask.querySelector('#obPassword');
    const email = (emailEl?.value || '').trim().toLowerCase();
    const password = pwEl?.value || '';

    state.error = '';
    state.info = '';

    // 客户端预校验（服务端会再校一遍）
    if (!email || !/.+@.+\..+/.test(email)) { state.error = t('onboarding.err.emailInvalid'); render(); return; }
    if (!password) { state.error = t('onboarding.err.pwdRequired'); render(); return; }
    if (state.mode === 'signup') {
      if (password.length < 8 || password.length > 72) { state.error = t('onboarding.err.pwdLength'); render(); return; }
      if (!/[A-Za-z]/.test(password) || !/\d/.test(password)) {
        state.error = t('onboarding.err.pwdFormat'); render(); return;
      }
    }

    state.accountEmail = email;
    state.busy = true;
    render();

    try {
      const api = window.DQ?.authApi;
      if (!api) throw new Error('auth_api_missing');

      if (state.mode === 'signup') {
        const r = await api.register({ email, password });
        state.verificationId = r.verification_id;
        // dev_hint 是 6 位数字 → 开发态固定验证码,注册面板直接展示;
        // 'code_in_server_console' → 随机码,提示去 portal-api 日志里看。
        state.devCode = /^\d{6}$/.test(r.dev_hint || '') ? r.dev_hint : '';
        if (!state.devCode && r.dev_hint === 'code_in_server_console') {
          state.info = t('onboarding.info.devCode');
        }
        state.step = '3c';
        state.busy = false;
        startResendCooldown(60);
        render();
      } else {
        await api.login({ email, password });
        state.accountMethod = 'email';
        state.accountOk = true;
        state.preAuthed = true;
        state.busy = false;
        afterAccountReady();
      }
    } catch (err) {
      state.busy = false;
      // 已注册但未验证 → 自动转到验证码页（已有 cooldown 不重发）
      if (err && err.data && err.data.error === 'email_not_verified') {
        state.mode = 'signin';
        state.error = t('onboarding.err.notVerifiedResend');
      } else {
        state.error = formatAuthError(err);
      }
      render();
    }
  }

  // ── SmartJob 验证码表单 ───────────────────────────────────────
  async function submitCodeForm() {
    const codeInput = mask.querySelector('#obCode');
    const code = (codeInput?.value || '').trim();
    if (!/^\d{6}$/.test(code)) {
      state.error = t('onboarding.err.codeInvalid');
      render();
      return;
    }
    state.error = '';
    state.info  = '';
    state.busy  = true;
    render();
    try {
      const api = window.DQ.authApi;
      await api.verifyEmail({ verification_id: state.verificationId, code });
      // 成功：保存了 tokens
      state.busy = false;
      state.accountMethod = 'email';
      state.accountOk = true;
      state.preAuthed = true;
      afterAccountReady();
    } catch (err) {
      state.busy = false;
      state.error = formatAuthError(err);
      render();
    }
  }

  async function resendCode() {
    if (!state.accountEmail) return;
    if (Date.now() < state.cooldownEnds) return;
    state.error = '';
    state.info = '';
    state.busy = true;
    render();
    try {
      const api = window.DQ.authApi;
      const r = await api.resendCode({ email: state.accountEmail });
      if (r.sent && r.verification_id) {
        state.verificationId = r.verification_id;
        if (/^\d{6}$/.test(r.dev_hint || '')) state.devCode = r.dev_hint;
        state.info = r.dev_hint === 'code_in_server_console'
          ? t('onboarding.info.devCodeResent')
          : t('onboarding.info.resendOk');
      } else {
        state.info = t('onboarding.info.resendMaybe');
      }
      startResendCooldown(60);
    } catch (err) {
      state.error = formatAuthError(err);
    } finally {
      state.busy = false;
      render();
    }
  }

  // ── 工具：密码显隐 / 邮箱遮罩 / 错误文案 / 重发倒计时 ─────────
  function toggleShowPassword() {
    const pw = mask.querySelector('#obPassword');
    const btn = mask.querySelector('#obEyeBtn');
    if (!pw || !btn) return;
    if (pw.type === 'password') { pw.type = 'text';     btn.textContent = t('onboarding.step3.pwdHide'); }
    else                        { pw.type = 'password'; btn.textContent = t('onboarding.step3.pwdShow'); }
  }

  function maskEmail(email) {
    const at = String(email).indexOf('@');
    if (at < 1) return email || '';
    const local = email.slice(0, at);
    const domain = email.slice(at);
    const visible = local.slice(0, Math.min(2, local.length));
    return visible + '*'.repeat(Math.max(1, local.length - visible.length)) + domain;
  }

  function formatAuthError(err) {
    const data = (err && err.data) || {};
    const key = data.error || (err && err.message) || 'unknown';
    const map = {
      invalid_input:            () => (data.issues && data.issues[0] && data.issues[0].msg) || t('onboarding.err.invalidInput'),
      email_already_registered: t('onboarding.err.emailExists'),
      cooldown:                 () => t('onboarding.err.cooldown', { n: data.retry_after_seconds || 60 }),
      email_send_failed:        t('onboarding.err.sendFailed'),
      invalid_credentials:      t('onboarding.err.invalidCreds'),
      email_not_verified:       t('onboarding.err.notVerified'),
      wrong_code:               () => data.attempts_left != null ? t('onboarding.err.wrongCode', { n: data.attempts_left }) : t('onboarding.err.wrongCodeSimple'),
      expired:                  t('onboarding.err.expired'),
      too_many_attempts:        t('onboarding.err.tooManyAttempts'),
      already_used:             t('onboarding.err.alreadyUsed'),
      not_found:                t('onboarding.err.notFound'),
      wrong_purpose:            t('onboarding.err.wrongPurpose'),
      no_user_bound:            t('onboarding.err.noUserBound'),
      network_error:            t('onboarding.err.networkError'),
      auth_api_missing:         t('onboarding.err.authApiMissing'),
    };
    const entry = map[key];
    if (typeof entry === 'function') return entry();
    if (typeof entry === 'string') return entry;
    return t('onboarding.err.requestFail', { key });
  }

  function startResendCooldown(sec = 60) {
    state.cooldownEnds = Date.now() + sec * 1000;
    if (state.cooldownTimer) { clearInterval(state.cooldownTimer); state.cooldownTimer = null; }
    tickCooldown();
  }

  function tickCooldown() {
    if (state.cooldownTimer) return;
    state.cooldownTimer = setInterval(() => {
      if (state.step !== '3c' || !mask) {
        clearInterval(state.cooldownTimer); state.cooldownTimer = null; return;
      }
      const left = Math.max(0, Math.ceil((state.cooldownEnds - Date.now()) / 1000));
      const btn = mask.querySelector('#obResend');
      if (!btn) return;
      if (left > 0) {
        btn.disabled = true;
        btn.textContent = t('onboarding.step3c.resendCooldown', { n: left });
      } else {
        btn.disabled = state.busy;
        btn.textContent = t('onboarding.step3c.resend');
        clearInterval(state.cooldownTimer); state.cooldownTimer = null;
      }
    }, 1000);
  }

  // ── 真平台登录检测(轮询 background DQ_GET_PLATFORM_STATUS,
  //    background/messages.js 用 chrome.cookies.getAll 在 boss/linkedin/indeed
  //    各自 domain 上 probe 已知 session cookie)──────────────────
  async function probeOnce() {
    return new Promise(res => {
      chrome.runtime.sendMessage(
        { type: 'DQ_GET_PLATFORM_STATUS', platform: state.platform },
        (resp) => { void chrome.runtime.lastError; res(resp || null); }
      );
    });
  }

  function startPlatformDetect() {
    if (typeof chrome === 'undefined' || !chrome.runtime?.sendMessage) return;
    if (state.detectTimer) return;
    // 立即跑一次(进入 step 4 用户可能早就登好了,不该等 2s)
    probeOnce().then(applyProbeResult);
    state.detectTimer = setInterval(async () => {
      const r = await probeOnce();
      applyProbeResult(r);
    }, 2000);
  }

  function applyProbeResult(resp) {
    const ok = resp?.ok && resp.platforms?.[state.platform]?.online;
    if (ok && !state.platOk) {
      state.platOk = true;
      if (state.detectTimer) { clearInterval(state.detectTimer); state.detectTimer = null; }
      if (state.step === 4) completeStep4();
    }
  }

  async function manualCheck() {
    const btn = mask.querySelector('#obManualCheck');
    if (btn) { btn.disabled = true; btn.textContent = t('onboarding.step4.manualChecking'); }
    const resp = await probeOnce();
    const platformInfo = resp?.platforms?.[state.platform] || {};
    if (platformInfo.online) {
      state.platOk = true;
      completeStep4();
      return;
    }
    if (btn) { btn.disabled = false; btn.textContent = t('onboarding.step4.manualCheck'); }
    const guide = getPlatformGuides()[state.platform];
    const detect = ({
      boss:     { domain: '.zhipin.com',  cookies: 'wt2 / bst / zp_token' },
      linkedin: { domain: '.linkedin.com', cookies: 'li_at' },
      indeed:   { domain: '.indeed.com',  cookies: 'CTK / SOCK / PPID' },
    })[state.platform] || { domain: state.platform, cookies: '?' };
    alert(
      t('onboarding.step4.alertNotDetected', { platform: guide?.label || state.platform }) + '\n\n' +
      t('onboarding.step4.alertBody', { domain: detect.domain, cookies: detect.cookies })
    );
  }

  async function finish() {
    stopPoll();
    const prefs = state.confirmedPrefs || null;
    try {
      const r = await chrome.storage.local.get(['user', 'jobseeker']);
      const u = (r && r.user) || {};
      u.role = state.role;
      u.platform = state.platform;
      u.account_method = state.accountMethod;
      u.email = state.accountEmail;
      u.onboarded = true;
      const patch = { user: u };
      // 缓存已确认偏好(chat 开场白 + 「我的」tab 复用)
      if (prefs) {
        patch.jobseeker = { ...((r && r.jobseeker) || {}), preferences: prefs };
      }
      await chrome.storage.local.set(patch);
    } catch (_) {}
    // 应用到 sidepanel state + 派事件
    window.DQ.state.role = state.role;
    window.DQ.state.platform = state.platform;
    window.DQ.config.role = state.role;
    window.DQ.config.platform = state.platform;
    document.body.dataset.role = state.role;
    close();
    emit(NAMES.ROLE_CHANGED, { role: state.role });
    emit(NAMES.PLATFORM_CHANGED, { platform: state.platform });
    // 引导结束:直接落到 AI 对话 tab,求职者再展示一条开场白
    try { window.DQ.app?.switchTab?.('chat'); } catch (_) {}
    if (state.role === 'jobseeker') {
      try { window.DQ.chat?.greet?.(prefs); } catch (_) {}
    }
  }

  function open() {
    if (mask) { mask.classList.add('open'); render(); return; }
    mask = document.createElement('div');
    mask.id = 'onboardingMask';
    mask.className = 'ob-mask open';
    document.body.appendChild(mask);
    refreshDetected().then(render);
  }

  function close() {
    if (state.detectTimer) { clearInterval(state.detectTimer); state.detectTimer = null; }
    stopPoll();
    state.modeSwitchFlow = false;
    if (!mask) return;
    mask.classList.remove('open');
    setTimeout(() => { try { mask?.remove(); } catch {} mask = null; }, 0);
  }

  // ── 启动时机 ───────────────────────────────────────────────────
  // 续跑引导:从 Step 1 角色选择起。由 app.js 启动路由调用 ——
  //   - 前置登录成功后的新用户(经 afterAccountReady → Step 1);
  //   - 「已登录但未走完引导」的用户(冷启动直接进引导)。
  // 启动首屏由 app.js 统一路由(未登录 → openLogin;已登录未引导 → 本函数)。
  async function startOnboarding() {
    let u = {};
    try {
      const r = await chrome.storage.local.get(['user']);
      u = (r && r.user) || {};
    } catch (_) {}
    state.step = 1;
    state.role = u.role || '';
    state.platform = u.platform || '';
    state.accountMethod = u.account_method || '';
    state.accountEmail = u.email || '';
    state.accountOk = false;
    state.alreadyOnboarded = !!u.onboarded;
    state.preAuthed = await isLoggedIn();
    if (state.preAuthed) state.accountOk = true;
    state.loginFirst = false;
    state.modeSwitchFlow = false;
    state.platOk = false;
    state.error = '';
    state.info = '';
    resetStep5();
    open();
  }

  async function openPlatformCheck({ role, platform } = {}) {
    state.step = 4;
    state.role = role || window.DQ?.state?.role || 'jobseeker';
    state.platform = platform || window.DQ?.state?.platform || 'boss';
    state.accountOk = true;
    state.loginFirst = false;
    state.modeSwitchFlow = true;
    state.platOk = false;
    state.error = '';
    state.info = '';
    resetStep5();
    open();
    let u = {};
    try {
      const r = await chrome.storage.local.get(['user']);
      u = (r && r.user) || {};
    } catch (_) {}
    state.role = role || window.DQ?.state?.role || u.role || 'jobseeker';
    state.platform = platform || window.DQ?.state?.platform || u.platform || 'boss';
    state.accountMethod = u.account_method || state.accountMethod || '';
    state.accountEmail = u.email || state.accountEmail || '';
    state.alreadyOnboarded = !!u.onboarded;
    state.preAuthed = await isLoggedIn();
    render();
    startPlatformDetect();
  }

  // 已登录判定:本地有 access / refresh token 即视为登录态
  async function isLoggedIn() {
    try {
      const a = await window.DQ?.authApi?.loadAuth?.();
      return !!(a && (a.accessToken || a.refreshToken));
    } catch (_) { return false; }
  }

  // 我的 tab > 切换角色 → 复用 wizard 从 step 1;已登录则不强迫重登
  window.addEventListener('dq:open-role-switcher', async () => {
    state.step = 1;
    let u = {};
    try {
      const r = await chrome.storage.local.get(['user']);
      u = (r && r.user) || {};
    } catch (_) {}
    state.role = window.DQ?.state?.role || u.role || '';
    state.platform = window.DQ?.state?.platform || u.platform || '';
    state.alreadyOnboarded = !!u.onboarded;
    state.preAuthed = await isLoggedIn();
    state.loginFirst = false;
    state.modeSwitchFlow = true;
    state.accountOk = true;
    state.platOk = false;
    // 已登录:带上账号信息,免得 finish() 用空 email 覆盖 storage
    if (state.preAuthed) {
      state.accountOk = true;
      state.accountEmail = u.email || state.accountEmail || '';
      state.accountMethod = u.account_method || state.accountMethod || 'email';
    }
    state.error = '';
    state.info = '';
    resetStep5();
    open();
  });

  window.addEventListener('dq:open-platform-check', (e) => {
    openPlatformCheck(e.detail || {});
  });

  // 资料初始化相关 state 复位(重跑 wizard 时清掉上一轮残留)
  function resetStep5() {
    stopPoll();
    state.bgMethod = '';
    state.resumeFile = null;
    state.resumeParseStatus = '';
    state.suggestion = null;
    state.prefsEditing = false;
    state.confirmedPrefs = null;
  }

  // 扩展首屏「登录 / 注册」:前置登录页作为第一屏直接呈现
  // (loginFirst → 无返回键 / 无进度条)。预填已存的 role/platform 供登录成功后续跑引导。
  async function openLogin() {
    try {
      const r = await chrome.storage.local.get(['user']);
      const u = (r && r.user) || {};
      state.role = u.role || '';
      state.platform = u.platform || '';
      state.alreadyOnboarded = !!u.onboarded;
    } catch (_) { state.role = ''; state.platform = ''; state.alreadyOnboarded = false; }
    state.accountMethod = '';
    state.accountEmail = '';
    state.accountOk = false;
    state.preAuthed = false;
    state.platOk = false;
    state.mode = 'signin';      // 默认登录;表单内 [登录|注册] 可切
    state.loginFirst = true;    // 前置登录页作为首屏
    state.modeSwitchFlow = false;
    state.error = '';
    state.info = '';
    resetStep5();
    state.step = 3;             // 直接进登录 / 注册表单,不再先走角色 / 平台
    open();
  }

  window.DQ = window.DQ || {};
  window.DQ.onboarding = { open, close, openLogin, startOnboarding, openPlatformCheck };
})();
