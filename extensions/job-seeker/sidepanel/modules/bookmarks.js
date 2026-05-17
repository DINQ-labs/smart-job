/*
 * modules/bookmarks.js — 收藏 tab.列 + 删除 + 重新评估.
 *
 * v3 类:.bm-item / .bm-item-head / .bm-item-ico / .bm-item-title / .bm-item-meta /
 *        .bm-item-actions / .bm-act / .bm-filter-bar / .bm-filter-btn
 *
 * 不做(留 Phase 6):add(由 content script 注入按钮触发,直接调 background)、
 *                  按 score 过滤(MVP 用 all/high/mid/low 4 按钮)
 */
(function () {
  'use strict';
  const { NAMES, on } = window.DQ.events;
  const api = window.DQ.api;

  function $(id) { return document.getElementById(id); }
  const list = $('bookmarksList');
  const filterBar = $('bmFilterBar');

  const state = { items: [], filter: 'all' };

  function renderFilters() {
    if (!filterBar) return;
    const opts = [
      { v: 'all',  l: t('bookmarks.filter.all') },
      { v: 'high', l: t('bookmarks.filter.high') },
      { v: 'mid',  l: t('bookmarks.filter.mid') },
      { v: 'low',  l: t('bookmarks.filter.low') },
    ];
    filterBar.innerHTML = opts.map(o =>
      `<button class="bm-filter-btn ${state.filter === o.v ? 'active' : ''}" data-filter="${o.v}">${o.l}</button>`
    ).join('');
  }

  function applyFilter(items) {
    if (state.filter === 'all') return items;
    return items.filter(it => {
      const s = Number(it.match_score || 0);
      if (state.filter === 'high') return s >= 70;
      if (state.filter === 'mid')  return s >= 40 && s < 70;
      if (state.filter === 'low')  return s < 40;
      return true;
    });
  }

  function scoreChip(s) {
    if (s == null) return '';
    const n = Number(s);
    if (n >= 70) return `<span class="score score-high">${n}</span>`;
    if (n >= 40) return `<span class="score score-mid">${n}</span>`;
    return `<span class="score score-low">${n}</span>`;
  }

  function escapeText(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function renderItem(it) {
    const platIco = ({ boss: '🟧', linkedin: '🟦', indeed: '🟨' })[it.platform] || '🟪';
    return `
      <article class="bm-item" data-id="${it.id}">
        <div class="bm-item-head">
          <span class="bm-item-ico" aria-hidden="true">${platIco}</span>
          <span class="bm-item-title" title="${escapeText(it.title)}">${escapeText(it.title || t('bookmarks.item.noName'))}</span>
          ${scoreChip(it.match_score)}
        </div>
        <div class="bm-item-meta">${escapeText(it.company || '')} · ${escapeText(it.location || '')}</div>
        <div class="bm-item-actions">
          <button class="bm-act" data-action="reanalyze">${t('bookmarks.act.reanalyze')}</button>
          <button class="bm-act" data-action="open">${t('bookmarks.act.open')}</button>
          <button class="bm-act" data-action="delete">${t('bookmarks.act.delete')}</button>
        </div>
      </article>`;
  }

  function render() {
    if (!list) return;
    const shown = applyFilter(state.items);
    if (!shown.length) {
      list.innerHTML = `
        <div class="empty-state">
          <div class="empty-ico" aria-hidden="true">⭐</div>
          <div class="empty-title">${state.filter === 'all' ? t('bookmarks.empty.title') : t('bookmarks.empty.titleFiltered')}</div>
          <div class="empty-sub">${t('bookmarks.empty.sub')}</div>
        </div>`;
      return;
    }
    list.innerHTML = shown.map(renderItem).join('');
  }

  async function refresh() {
    const role     = window.DQ?.state?.role || 'jobseeker';
    const platform = window.DQ?.state?.platform || 'boss';
    if (!window.DQ?.state?.user?.loggedIn) { state.items = []; render(); return; }
    try {
      const data = await api.apiGw.bookmarksList({ role, platform });
      state.items = data?.bookmarks || [];
      render();
    } catch (e) {
      console.warn('[bookmarks] fetch failed', e);
    }
  }

  filterBar?.addEventListener('click', (e) => {
    const btn = e.target.closest('.bm-filter-btn[data-filter]');
    if (!btn) return;
    state.filter = btn.dataset.filter;
    renderFilters();
    render();
  });

  list?.addEventListener('click', async (e) => {
    const card = e.target.closest('.bm-item[data-id]');
    if (!card) return;
    const id = card.dataset.id;
    const action = e.target.closest('[data-action]')?.dataset.action;
    if (!action) return;
    const it = state.items.find(x => String(x.id) === String(id));
    if (action === 'open' && it?.url) {
      chrome.tabs?.create?.({ url: it.url });
    } else if (action === 'delete') {
      if (!confirm(t('bookmarks.confirm.delete'))) return;
      try { await api.apiGw.bookmarkDelete(id); refresh(); }
      catch (err) { alert(t('bookmarks.err.delete') + (err.message || err)); }
    } else if (action === 'reanalyze') {
      try {
        const r = await api.apiGw.reanalyzeBookmark(id);
        if (r?.match_score != null) {
          it.match_score = r.match_score;
          render();
        }
      } catch (err) { alert(t('bookmarks.err.reanalyze') + (err.message || err)); }
    }
  });

  on(NAMES.TAB_CHANGED, (e) => { if (e.detail?.tab === 'bookmarks') refresh(); });
  on(NAMES.AUTH_CHANGED, refresh);
  on(NAMES.PLATFORM_CHANGED, refresh);
  on(NAMES.ROLE_CHANGED, refresh);

  renderFilters();

  window.DQ = window.DQ || {};
  window.DQ.bookmarks = { refresh };
})();
