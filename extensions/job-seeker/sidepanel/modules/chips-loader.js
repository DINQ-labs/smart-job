/*
 * modules/chips-loader.js — 动态 chip 拉取 + 渲染到 .chat-hints.
 *
 * 后端 GET /agent/cell/chips(走 DQ.api.agentGw.chips).
 * 缓存键含 role/platform/lang + page_type/page_item_count(防上下文穿透).
 * 监听 role/platform/page-context 变化自动 refresh.
 *
 * v3 视觉:.chat-hints 容器 + .chip + 状态变体(.chip-blue/-green/-amber/-red).
 */
(function () {
  'use strict';
  const { NAMES, on, emit } = window.DQ.events;
  const api = window.DQ.api;

  const STORAGE = chrome?.storage?.local;
  const CACHE_PREFIX = 'chip:v3:';
  let inFlight = null;
  let lastVersion = 0;
  let menuOpen = false;

  function detectLang() {
    // 产品为中文优先:整个扩展 UI(welcome/onboarding/各 tab)文案均硬编码中文,
    // chip 语言必须跟随产品语言,而不是浏览器 UI 语言 —— 否则英文环境的浏览器
    // 会让 chip 单独显示英文(与设计稿不符)。
    // 待将来有显式的应用内语言切换设置时,在此读取该设置。
    return 'zh';
  }

  function readPageContext() {
    const ctx = window.DQ?.state?.pageContext || {};
    return {
      page_type:       ctx.page_type || '',
      page_item_count: Number(ctx.page_item_count || 0) || 0,
    };
  }

  function cacheKey(role, platform, lang, pc) {
    return `${CACHE_PREFIX}${role}:${platform}:${lang}:${pc.page_type || '_'}:${pc.page_item_count || 0}`;
  }

  async function _read(key) {
    if (!STORAGE) return null;
    try { const r = await STORAGE.get([key]); return r[key] || null; } catch { return null; }
  }
  async function _write(key, value) {
    if (!STORAGE) return;
    try { await STORAGE.set({ [key]: value }); } catch {}
  }

  // 去掉字符串开头的表情符号 + 其后空白。chip 数据里有两种约定：icon 单独字段、
  // 或表情直接写进 label。当 icon 已单独渲染时，剥掉 label 头部重复的表情，避免两个图标。
  function stripLeadingEmoji(s) {
    return String(s || '').replace(
      /^(?:[\p{Extended_Pictographic}\u{1F1E6}-\u{1F1FF}️‍⃣])+\s*/u,
      '',
    );
  }

  function render(chips) {
    const host = document.getElementById('chatHints');
    const trigger = document.getElementById('chatCommandBtn');
    if (!host) return;
    host.innerHTML = '';
    host.classList.toggle('is-empty', !chips || !chips.length);
    host.hidden = !menuOpen || !chips || !chips.length;
    trigger?.classList.toggle('has-chips', !!(chips && chips.length));
    trigger?.setAttribute('aria-expanded', host.hidden ? 'false' : 'true');
    if (!chips || !chips.length) return;
    chips.forEach((c) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chat-hint';
      btn.dataset.prompt = c.send_text || c.label || '';
      btn.dataset.chipId = c.id || '';
      const rawLabel = c.label || c.send_text || '';
      const icon = c.icon ? `<span aria-hidden="true">${c.icon}</span>` : '';
      // icon 单独渲染时，label 头部若也带表情就剥掉，防止图标重复
      const label = c.icon ? stripLeadingEmoji(rawLabel) : rawLabel;
      btn.innerHTML = `${icon}<span>${label}</span>`;
      host.appendChild(btn);
    });
    emit(NAMES.CHIPS_UPDATED, { chips });
  }

  function setMenuOpen(open) {
    const host = document.getElementById('chatHints');
    const trigger = document.getElementById('chatCommandBtn');
    menuOpen = !!open;
    const hasItems = !!host?.querySelector('.chat-hint');
    if (host) host.hidden = !menuOpen || !hasItems;
    trigger?.setAttribute('aria-expanded', menuOpen && hasItems ? 'true' : 'false');
    trigger?.classList.toggle('open', menuOpen && hasItems);
  }

  async function refresh() {
    const role     = window.DQ?.state?.role || 'jobseeker';
    const platform = window.DQ?.state?.platform || 'boss';
    const lang     = detectLang();
    const pc       = readPageContext();
    const key      = cacheKey(role, platform, lang, pc);

    const cached = await _read(key);
    if (cached?.chips?.length) render(cached.chips);

    if (inFlight?.key === key) return inFlight.promise;
    const p = (async () => {
      try {
        const data = await api.agentGw.chips({
          role, platform, lang,
          page_type: pc.page_type, page_item_count: pc.page_item_count,
        });
        const chips = Array.isArray(data?.chips) ? data.chips : [];
        const v = data?.version || 0;
        lastVersion = v;
        if (!cached || cached.version !== v ||
            JSON.stringify(cached.chips) !== JSON.stringify(chips)) {
          await _write(key, { chips, version: v, cached_at: Date.now() });
          render(chips);
        }
        return chips;
      } catch (e) {
        console.warn('[chips-loader] fetch failed', e);
        return null;
      } finally {
        if (inFlight?.key === key) inFlight = null;
      }
    })();
    inFlight = { key, promise: p };
    return p;
  }

  // Slash 菜单:点击 / 展开快捷指令,点击指令后自动发送对应 prompt。
  document.addEventListener('click', async (e) => {
    const trigger = e.target.closest && e.target.closest('#chatCommandBtn');
    if (trigger) {
      e.preventDefault();
      const host = document.getElementById('chatHints');
      if (!host?.querySelector('.chat-hint')) {
        await refresh();
        setMenuOpen(true);
        return;
      }
      setMenuOpen(host.hidden);
      return;
    }

    const btn = e.target.closest && e.target.closest('#chatHints .chat-hint[data-prompt]');
    if (!btn) {
      if (!e.target.closest?.('.chat-command-wrap')) setMenuOpen(false);
      return;
    }
    const prompt = btn.dataset.prompt || '';
    setMenuOpen(false);
    const sendBtn = document.getElementById('chatSendBtn');
    if (sendBtn?.disabled) return;
    const chipId = btn.dataset.chipId || '';
    if (chipId) {
      api.agentGw.reportChipClick({
        chip_key: chipId,
        chip_label: (btn.textContent || '').trim(),
        role: window.DQ?.state?.role,
        platform: window.DQ?.state?.platform,
      }).catch(() => {});
    }
    if (prompt) window.DQ?.chat?.send?.(prompt);
  }, true);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') setMenuOpen(false);
  });

  on(NAMES.ROLE_CHANGED, refresh);
  on(NAMES.PLATFORM_CHANGED, refresh);
  on(NAMES.PAGE_CONTEXT_CHANGED, refresh);
  on(NAMES.AUTH_CHANGED, refresh);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refresh);
  } else {
    setTimeout(refresh, 0);
  }

  window.DQ = window.DQ || {};
  window.DQ.chips = { refresh };
})();
