/*
 * modules/welcome.js — 首次安装 4 屏轮播(DESIGN.md §6.3 wc-*).
 *
 * 注意:首屏已改为「登录 / 注册」(login-first),本轮播不再启动时自动弹出。
 *      window.DQ.welcome API 保留,需要时可手动 open()(如帮助入口)。
 *
 * 视觉跟 DESIGN.md §6 暗顶卡片(slate-900 hood) + 大功能图标圆 + 4 dots +
 * < > arrow + 居中 1/4 counter + slate-900 大 CTA + 免费开始无需信用卡 footer.
 *
 * 4 张卡片内容来自用户提供的截图:
 *   1 · AI 智能对话助手   💬
 *   2 · 批量任务自动化   📋
 *   3 · 收藏与跟踪管理   ⭐
 *   4 · 支持三大招聘平台 🚀  (含 Boss/LinkedIn/Indeed 三色 chip)
 */
(function () {
  'use strict';
  const { NAMES, emit } = window.DQ.events;

  function getSlides() {
    return [
      {
        icon: '💬',
        title: t('welcome.slide1.title'),
        lines: [
          t('welcome.slide1.line1'),
          t('welcome.slide1.line2'),
          t('welcome.slide1.line3'),
        ],
      },
      {
        icon: '📋',
        title: t('welcome.slide2.title'),
        lines: [
          t('welcome.slide2.line1'),
          t('welcome.slide2.line2'),
          t('welcome.slide2.line3'),
        ],
      },
      {
        icon: '⭐',
        title: t('welcome.slide3.title'),
        lines: [
          t('welcome.slide3.line1'),
          t('welcome.slide3.line2'),
          t('welcome.slide3.line3'),
        ],
      },
      {
        icon: '🚀',
        title: t('welcome.slide4.title'),
        lines: [
          t('welcome.slide4.line1'),
          t('welcome.slide4.line2'),
        ],
        platforms: [
          { key: 'boss',     label: t('common.platformBoss') },
          { key: 'linkedin', label: 'LinkedIn' },
          { key: 'indeed',   label: 'Indeed' },
        ],
      },
    ];
  }

  let mask = null;
  let idx = 0;

  function escapeText(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function renderSlide() {
    const SLIDES = getSlides();
    const s = SLIDES[idx];
    const isLast = idx === SLIDES.length - 1;
    const dots = SLIDES.map((_, i) =>
      `<span class="wc-dot ${i === idx ? 'active' : ''}"></span>`
    ).join('');
    const platformsHtml = s.platforms ? `
      <div class="wc-platforms">
        ${s.platforms.map(p =>
          `<span class="chip"><span class="dot ${p.key}" aria-hidden="true"></span>${escapeText(p.label)}</span>`
        ).join('')}
      </div>` : '';
    const linesHtml = s.lines.map(l => `<span>${escapeText(l)}</span>`).join('');

    mask.innerHTML = `
      <article class="wc-card" role="dialog" aria-label="${t('welcome.ariaLabel')}">
        <header class="wc-hood">
          <div class="wc-bot" aria-hidden="true">🤖</div>
          <div class="wc-brand">${t('welcome.brand')}</div>
          <div class="wc-brand-en">AI-powered Recruitment Assistant</div>
        </header>
        <div class="wc-body">
          <div class="wc-ico" aria-hidden="true">${escapeText(s.icon)}</div>
          <h2 class="wc-title">${escapeText(s.title)}</h2>
          <div class="wc-lines">${linesHtml}</div>
          ${platformsHtml}
        </div>
        <div class="wc-nav">
          <div class="wc-nav-top">
            <button type="button" class="wc-arrow" id="wcPrev" ${idx === 0 ? 'disabled' : ''}
                    aria-label="${t('welcome.ariaLabelPrev')}">‹</button>
            <div class="wc-dots">${dots}</div>
            <button type="button" class="wc-arrow" id="wcNext" ${isLast ? 'disabled' : ''}
                    aria-label="${t('welcome.ariaLabelNext')}">›</button>
          </div>
          <span class="wc-counter">${idx + 1} / ${SLIDES.length}</span>
        </div>
        <div class="wc-cta-wrap">
          <button type="button" class="wc-cta" id="wcCta">
            ${isLast ? t('welcome.ctaLast') : t('welcome.cta')}
          </button>
          <div class="wc-footer">${t('welcome.footer')}</div>
        </div>
      </article>`;

    // 绑定事件(每次重渲染都需重绑,因 innerHTML 替换)
    mask.querySelector('#wcPrev')?.addEventListener('click', prev);
    mask.querySelector('#wcNext')?.addEventListener('click', next);
    mask.querySelector('#wcCta') ?.addEventListener('click', () => {
      if (isLast) finish();
      else next();
    });
  }

  function prev() { if (idx > 0) { idx -= 1; renderSlide(); } }
  function next() { if (idx < getSlides().length - 1) { idx += 1; renderSlide(); } }

  async function finish() {
    // 持久化 welcomed=true,关闭 overlay,然后让 onboarding 接力跑 role 选择
    try {
      const r = await chrome.storage.local.get(['user']);
      const u = (r && r.user) || {};
      u.welcomed = true;
      await chrome.storage.local.set({ user: u });
    } catch (_) {}
    close();
    window.dispatchEvent(new CustomEvent('dq:welcome-done'));
  }

  function open() {
    if (mask) { mask.classList.add('open'); renderSlide(); return; }
    mask = document.createElement('div');
    mask.className = 'wc-mask open';
    mask.id = 'welcomeMask';
    document.body.appendChild(mask);
    renderSlide();
    // 键盘左右
    document.addEventListener('keydown', onKey);
  }

  function close() {
    if (!mask) return;
    mask.classList.remove('open');
    document.removeEventListener('keydown', onKey);
    setTimeout(() => { try { mask?.remove(); } catch {} mask = null; }, 0);
  }

  function onKey(e) {
    if (!mask?.classList.contains('open')) return;
    if (e.key === 'ArrowLeft')  { e.preventDefault(); prev(); }
    if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
    if (e.key === 'Enter')      { e.preventDefault();
      const isLast = idx === getSlides().length - 1;
      if (isLast) finish(); else next();
    }
  }

  async function maybeShow() {
    try {
      const r = await chrome.storage.local.get(['user']);
      const u = (r && r.user) || {};
      if (u.welcomed) return false;
      open();
      return true;
    } catch (_) { return false; }
  }

  // 首屏改为「登录 / 注册」(login-first):欢迎轮播不再启动时自动弹出。
  // 仍保留 window.DQ.welcome API,需要时(如帮助入口)可手动 open()。

  window.DQ = window.DQ || {};
  window.DQ.welcome = { open, close, maybeShow };
})();
