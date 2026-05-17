/**
 * ext_shared/content/injection-widgets.js — 三平台共用注入控件库 (Sprint 3 / D2-D4)
 *
 * 提供：
 *   - injectCommonStyles()        一次性注入 DESIGN.md token CSS 变量 + 组件样式
 *   - createStarButton(opts)      ★ 收藏按钮（toggle）
 *   - createMatchBadge(score)     匹配分角标（自动按 80/60 切绿/黄/红）
 *   - createBatchBar(opts)        列表顶部"批量分析 X 个"操作条
 *   - createFloatingAiButton(opts) 详情页右下角浮动 AI 分析按钮
 *
 * 配色全部使用 DESIGN.md 的 Slate + 语义色 token。
 * 调用 background:
 *   chrome.runtime.sendMessage({ type: 'DQ_BOOKMARK_ADD', ... })
 *   chrome.runtime.sendMessage({ type: 'DQ_TRIGGER_TASK', ... })
 *   chrome.runtime.sendMessage({ type: 'DQ_OPEN_CHAT_WITH_CONTEXT', ... })
 */
(function () {
  'use strict';

  // ── DESIGN.md token 注入（CSS 变量 + 组件类） ──────────────────────────
  function injectCommonStyles() {
    if (document.getElementById('dq-inject-style')) return;
    const s = document.createElement('style');
    s.id = 'dq-inject-style';
    s.textContent = `
      /* DESIGN.md tokens（仅本扩展注入元素用，不污染宿主页面变量）*/
      .dq-widget {
        --dq-slate-900: #0f172a; --dq-slate-700: #334155; --dq-slate-500: #64748b;
        --dq-slate-200: #e2e8f0; --dq-slate-100: #f1f5f9; --dq-white: #ffffff;
        --dq-blue-600: #2563eb;  --dq-blue-200: #bfdbfe;  --dq-blue-50:  #eff6ff;
        --dq-green-600:#16a34a;  --dq-green-200:#bbf7d0;  --dq-green-50: #f0fdf4;
        --dq-amber-600:#d97706;  --dq-amber-200:#fde68a;  --dq-amber-50: #fffbeb;
        --dq-red-600:  #dc2626;  --dq-red-200:  #fecaca;  --dq-red-50:   #fef2f2;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-weight: 600;
      }

      /* ── ★ 按钮 ─────────────────────────────────────── */
      .dq-star-btn {
        display:inline-flex; align-items:center; justify-content:center;
        width:24px; height:24px; margin-left:6px; padding:0;
        background: var(--dq-white); border: 1px solid var(--dq-slate-200);
        border-radius: 6px; cursor:pointer; color: var(--dq-slate-500);
        font-size: 14px; line-height: 1; transition: all 0.15s ease;
      }
      .dq-star-btn:hover { background: var(--dq-amber-50); color: var(--dq-amber-600); border-color: var(--dq-amber-200); }
      .dq-star-btn.dq-starred { background: var(--dq-amber-600); color: var(--dq-white); border-color: var(--dq-amber-600); }
      .dq-star-btn[disabled] { opacity: 0.5; cursor: wait; }

      /* ── 匹配分角标 ──────────────────────────────────── */
      .dq-match-badge {
        display:inline-flex; align-items:center; gap:2px;
        margin-left:6px; padding: 1px 7px;
        border-radius: 3px; font-size: 11px; font-weight: 700;
        background: var(--dq-slate-100); color: var(--dq-slate-700);
      }
      .dq-match-badge.dq-high  { background: var(--dq-green-50);  color: var(--dq-green-600); }
      .dq-match-badge.dq-mid   { background: var(--dq-amber-50);  color: var(--dq-amber-600); }
      .dq-match-badge.dq-low   { background: var(--dq-red-50);    color: var(--dq-red-600); }

      /* ── 批量操作条（列表顶部固定） ───────────────────── */
      .dq-batch-bar {
        position: sticky; top: 0; z-index: 100;
        display: flex; align-items: center; gap: 12px;
        padding: 10px 14px; margin: 0 0 8px 0;
        background: var(--dq-slate-900); color: var(--dq-white);
        border-radius: 12px; box-shadow: 0 8px 32px rgba(15,23,42,0.12);
        font-size: 13px;
      }
      .dq-batch-bar .dq-bar-text { flex: 1; }
      .dq-batch-bar .dq-bar-text b { color: var(--dq-white); font-weight: 800; }
      .dq-batch-bar .dq-bar-btn {
        padding: 6px 14px; border-radius: 6px; border: none;
        background: var(--dq-blue-600); color: var(--dq-white);
        font-size: 13px; font-weight: 700; cursor: pointer;
        transition: background 0.15s ease;
      }
      .dq-batch-bar .dq-bar-btn:hover { background: #1d4ed8; }
      .dq-batch-bar .dq-bar-btn.dq-ghost {
        background: transparent; color: var(--dq-white);
        border: 1px solid rgba(255,255,255,0.15);
      }
      .dq-batch-bar .dq-bar-btn.dq-ghost:hover { background: rgba(255,255,255,0.06); }

      /* ── 详情页浮动 AI 按钮 ─────────────────────────── */
      .dq-float-ai {
        position: fixed; right: 24px; bottom: 88px; z-index: 9999;
        display: flex; align-items: center; gap: 8px;
        padding: 10px 18px; border-radius: 50%;
        background: var(--dq-slate-900); color: var(--dq-white);
        font-size: 13px; font-weight: 700; cursor: pointer;
        box-shadow: 0 4px 16px rgba(15,23,42,0.2);
        border: none; transition: all 0.2s ease;
      }
      .dq-float-ai:hover { transform: translateY(-2px); box-shadow: 0 8px 32px rgba(15,23,42,0.12); }
      .dq-float-ai.dq-expanded { border-radius: 20px; padding: 12px 20px; }
      .dq-float-ai .dq-ai-emoji { font-size: 16px; }

      /* ── 提示 toast（顶部居中临时提示） ─────────────── */
      .dq-toast {
        position: fixed; top: 24px; left: 50%; transform: translateX(-50%);
        z-index: 10000;
        padding: 10px 18px; border-radius: 12px;
        background: var(--dq-slate-900); color: var(--dq-white);
        font-size: 13px; font-weight: 600;
        box-shadow: 0 8px 32px rgba(15,23,42,0.2);
        animation: dq-toast-in 0.2s ease;
      }
      .dq-toast.dq-success { background: var(--dq-green-600); }
      .dq-toast.dq-warn    { background: var(--dq-amber-600); }
      .dq-toast.dq-error   { background: var(--dq-red-600); }
      @keyframes dq-toast-in {
        from { opacity: 0; transform: translateX(-50%) translateY(-8px); }
        to   { opacity: 1; transform: translateX(-50%) translateY(0); }
      }
    `;
    document.head.appendChild(s);
  }

  // ── 临时提示 toast ────────────────────────────────────────────────────
  function showToast(msg, kind) {
    const t = document.createElement('div');
    t.className = `dq-widget dq-toast${kind ? ' dq-' + kind : ''}`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity 0.2s'; }, 2500);
    setTimeout(() => { t.remove(); }, 2800);
  }

  // ── ★ 收藏按钮 ────────────────────────────────────────────────────────
  /**
   * opts: {
   *   role: 'jobseeker'|'recruiter',
   *   platform: 'boss'|'linkedin'|'indeed',
   *   external_id: string,
   *   fields: {...},   // 透传给 DQ_BOOKMARK_ADD
   *   isStarred?: boolean,  // 初始态
   * }
   */
  function createStarButton(opts) {
    const btn = document.createElement('button');
    btn.className = 'dq-widget dq-star-btn';
    btn.type = 'button';
    btn.title = (typeof t === 'function' ? t('content.bookmark') : '收藏');
    btn.textContent = opts.isStarred ? '★' : '☆';
    if (opts.isStarred) btn.classList.add('dq-starred');

    let starred = !!opts.isStarred;
    let currentBmId = opts.bm_id || null;

    btn.addEventListener('click', (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      if (btn.hasAttribute('disabled')) return;
      btn.setAttribute('disabled', '1');

      if (starred && currentBmId) {
        // 已收藏 → 取消
        chrome.runtime.sendMessage({
          type: 'DQ_BOOKMARK_REMOVE', bm_id: currentBmId, role: opts.role,
        }, (resp) => {
          btn.removeAttribute('disabled');
          if (resp && resp.ok) {
            starred = false;
            btn.textContent = '☆';
            btn.classList.remove('dq-starred');
            showToast((typeof t === 'function' ? t('content.bookmarkRemoved') : '已取消收藏'), 'success');
          } else {
            showToast(resp?.error || (typeof t === 'function' ? t('content.bookmarkRemoveFail') : '取消收藏失败'), 'error');
          }
        });
      } else {
        chrome.runtime.sendMessage({
          type: 'DQ_BOOKMARK_ADD',
          role: opts.role, platform: opts.platform,
          source: 'manual', external_id: opts.external_id,
          fields: opts.fields || {},
        }, (resp) => {
          btn.removeAttribute('disabled');
          if (resp && resp.quota_exceeded) {
            showToast((typeof t === 'function' ? t('content.bookmarkQuotaFull', {used: resp.used, quota: resp.quota}) : `收藏已满 (${resp.used}/${resp.quota})，升级 Pro 解锁无限`), 'warn');
            return;
          }
          if (resp && resp.ok && resp.item) {
            starred = true;
            currentBmId = resp.item.id;
            btn.textContent = '★';
            btn.classList.add('dq-starred');
            showToast((typeof t === 'function' ? t('content.bookmarkAdded') : '已收藏'), 'success');
          } else {
            showToast(resp?.error || (typeof t === 'function' ? t('content.bookmarkFail') : '收藏失败'), 'error');
          }
        });
      }
    });
    return btn;
  }

  // ── 匹配分角标 ────────────────────────────────────────────────────────
  function createMatchBadge(score) {
    const b = document.createElement('span');
    b.className = 'dq-widget dq-match-badge';
    if (typeof score !== 'number') {
      b.textContent = '—';
      return b;
    }
    const s = Math.max(0, Math.min(100, Math.round(score)));
    b.textContent = (typeof t === 'function' ? t('content.matchScore', {score: s}) : s + '分');
    if (s >= 80) b.classList.add('dq-high');
    else if (s >= 60) b.classList.add('dq-mid');
    else b.classList.add('dq-low');
    return b;
  }

  // ── 列表顶部批量条 ────────────────────────────────────────────────────
  /**
   * opts: {
   *   text: '已选 0 项 / 共 25 项',
   *   primary: { label, onClick },
   *   secondary?: { label, onClick },
   * }
   */
  function createBatchBar(opts) {
    const bar = document.createElement('div');
    bar.className = 'dq-widget dq-batch-bar';
    const txt = document.createElement('div');
    txt.className = 'dq-bar-text';
    txt.innerHTML = opts.text || '';
    bar.appendChild(txt);

    if (opts.secondary) {
      const b2 = document.createElement('button');
      b2.className = 'dq-bar-btn dq-ghost';
      b2.textContent = opts.secondary.label;
      b2.addEventListener('click', opts.secondary.onClick);
      bar.appendChild(b2);
    }
    if (opts.primary) {
      const b1 = document.createElement('button');
      b1.className = 'dq-bar-btn';
      b1.textContent = opts.primary.label;
      b1.addEventListener('click', opts.primary.onClick);
      bar.appendChild(b1);
    }
    return bar;
  }

  // ── 详情页浮动 AI 按钮 ────────────────────────────────────────────────
  /**
   * opts: { label, prompt, context, emoji?='💬' }
   * 点击 → DQ_OPEN_CHAT_WITH_CONTEXT 打开 sidepanel 并预填 prompt
   */
  function createFloatingAiButton(opts) {
    if (document.querySelector('.dq-float-ai')) return null;  // 单例
    const btn = document.createElement('button');
    btn.className = 'dq-widget dq-float-ai dq-expanded';
    btn.type = 'button';
    btn.innerHTML = `<span class="dq-ai-emoji">${opts.emoji || '💬'}</span>${opts.label || (typeof t === 'function' ? t('content.aiAnalyzeJob') : 'AI 分析')}`;
    btn.title = opts.label || (typeof t === 'function' ? t('content.aiAnalyzeJobTitle') : 'AI 分析此页内容');
    btn.addEventListener('click', (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      chrome.runtime.sendMessage({
        type: 'DQ_OPEN_CHAT_WITH_CONTEXT',
        prompt: opts.prompt || '',
        context: opts.context || null,
      });
    });
    document.body.appendChild(btn);
    return btn;
  }

  // ── 暴露到 window（ISOLATED world） ────────────────────────────────────
  window.DQ_Widgets = {
    injectCommonStyles,
    createStarButton,
    createMatchBadge,
    createBatchBar,
    createFloatingAiButton,
    showToast,
  };
})();
