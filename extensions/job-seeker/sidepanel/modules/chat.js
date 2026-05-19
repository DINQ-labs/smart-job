/*
 * modules/chat.js — AI 对话流 + 输入.
 *
 * MVP 实现:
 *   - 渲染 .chat-row + .chat-bubble(DESIGN.md §4 非对称圆角)
 *   - 发送 = POST /agent/sse 流式响应,把 text_delta 累加到当前 AI 气泡
 *   - 内嵌 insufficient_credit 卡片(amber)+ 任务进度通知(运行中切到 tasks tab 提示)
 *
 * 不做(留 Phase 6 +):语音输入(voice-bridge)、结构化卡片(analysis-card)、
 *                  历史会话回放(history)、模板触发的 task_confirm 卡(tc-* 类)
 *
 * v3 类:.chat / .chat-row / .chat-bubble / .chat-input-box
 */
(function () {
  'use strict';
  const { NAMES, on, emit } = window.DQ.events;
  const api = window.DQ.api;

  function $(id) { return document.getElementById(id); }
  const stream = $('chatStream');
  const input  = $('chatInput');
  const send   = $('chatSendBtn');

  let greeted = false;   // 防止开场白重复出现

  function appendUserBubble(text) {
    if (!stream) return;
    const row = document.createElement('div');
    row.className = 'chat-row from-user';
    row.innerHTML = `<div class="chat-bubble"></div>`;
    row.querySelector('.chat-bubble').textContent = text;
    stream.appendChild(row);
    stream.scrollTop = stream.scrollHeight;
  }

  function appendAiBubble(initial = '') {
    if (!stream) return null;
    const row = document.createElement('div');
    row.className = 'chat-row from-ai';
    row.innerHTML = `
      <div class="chat-bubble"></div>`;
    row.querySelector('.chat-bubble').textContent = initial;
    stream.appendChild(row);
    stream.scrollTop = stream.scrollHeight;
    return row.querySelector('.chat-bubble');
  }

  function appendAiBlock(title, bodyHtml, actions) {
    if (!stream) return;
    const row = document.createElement('div');
    row.className = 'chat-row from-ai chat-row-block';
    const actionHtml = Array.isArray(actions) && actions.length
      ? `<div class="ai-block-actions">${actions.map((a) =>
          `<button type="button" class="chip" data-prompt="${escapeHtml(a.prompt || a.label || '')}">${escapeHtml(a.label || '')}</button>`
        ).join('')}</div>`
      : '';
    row.innerHTML = `
      <section class="ai-block">
        <div class="ai-block-kicker">SmartJob AI</div>
        <h2 class="ai-block-title">${escapeHtml(title || '')}</h2>
        <div class="ai-block-body">${bodyHtml || ''}</div>
        ${actionHtml}
      </section>`;
    row.querySelectorAll('[data-prompt]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (input) {
          input.value = btn.dataset.prompt || '';
          input.focus();
          input.dispatchEvent(new Event('input', { bubbles: true }));
        }
      });
    });
    stream.appendChild(row);
    stream.scrollTop = stream.scrollHeight;
  }

  function appendInsufficientCreditCard(detail) {
    if (!stream) return;
    const row = document.createElement('div');
    row.className = 'chat-row from-ai';
    const required = detail?.required || '?';
    const available = detail?.available ?? 0;
    row.innerHTML = `
      <div class="task-alert chat-task-alert">
        <div class="task-alert-head">${t('chat.credit.title')}</div>
        <div>${t('chat.credit.body', { required, available })}</div>
        <div style="display:flex;gap:var(--sp-2);margin-top:var(--sp-2)">
          <button class="btn primary" data-action="topup">${t('chat.credit.topup')}</button>
          <button class="btn ghost" data-action="portal">${t('chat.credit.portal')}</button>
        </div>
      </div>`;
    row.querySelector('[data-action="topup"]').addEventListener('click', async () => {
      try {
        const r = await api.apiGw.checkout({ plan: 'topup' });
        if (r?.url) chrome.tabs?.create?.({ url: r.url });
      } catch (e) { alert(t('chat.credit.topupErr') + (e.message || e)); }
    });
    row.querySelector('[data-action="portal"]').addEventListener('click', async () => {
      try {
        const r = await api.apiGw.portal();
        if (r?.url) chrome.tabs?.create?.({ url: r.url });
      } catch (e) { alert(t('chat.credit.portalErr') + (e.message || e)); }
    });
    stream.appendChild(row);
    stream.scrollTop = stream.scrollHeight;
  }

  // ── 可点击 chip 行 ──────────────────────────────────────────────
  // chips: [{ label, sendText }] —— 点击即把 sendText 作为用户消息发送。
  function appendChipRow(chips) {
    if (!stream || !chips || !chips.length) return;
    const row = document.createElement('div');
    row.className = 'chat-chips';
    chips.forEach((c) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chip';
      btn.textContent = c.label;
      btn.addEventListener('click', () => {
        if (send && send.disabled) return;
        row.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        row.classList.add('used');
        if (input) input.value = c.sendText;
        onSend();
      });
      row.appendChild(btn);
    });
    stream.appendChild(row);
    stream.scrollTop = stream.scrollHeight;
  }

  // 解析 AI 气泡末行的 `[a, b, c]` chip 数组(modes/search.py 规约:登录成功 /
  // 分步推进的每步,最后一行必须是 chip 数组)→ 抽出来渲染成可点击 chip。
  function extractChips(aiBubble) {
    if (!aiBubble) return;
    const full = aiBubble.textContent || '';
    const m = full.match(/`\[([^\]`]+)\]`\s*$/);
    if (!m) return;
    const items = m[1].split(',').map((s) => s.trim()).filter(Boolean);
    if (!items.length) return;
    aiBubble.textContent = full.slice(0, m.index).replace(/\s+$/, '');
    appendChipRow(items.map((label) => ({ label, sendText: label })));
  }

  // ── 职位 / 候选人结果富卡片 ─────────────────────────────────────
  function matchClass(p) { return p >= 70 ? 'high' : p >= 50 ? 'mid' : 'low'; }

  // job_list_card 缺 actions 时的默认动作 —— 后端只给 Indeed/LinkedIn 下发
  // actions,Boss 不下发;与 v2 joblist.js defaultActions 对齐,保证 Boss
  // 职位卡也有「分析职位 / 打招呼」按钮。
  const DEFAULT_JOB_ACTIONS = [
    {
      id: 'analyze', label_zh: '分析职位', label_en: 'Analyze',
      send_template_zh: '分析编号 {ids} 的职位', send_template_en: 'Analyze jobs {ids}',
      variant: 'secondary', require_selection: true,
    },
    {
      id: 'greet', label_zh: '打招呼', label_en: 'Greet',
      send_template_zh: '给编号 {ids} 的职位打招呼',
      send_template_en: 'Greet recruiters for jobs {ids}',
      variant: 'primary', require_selection: true,
    },
  ];

  // 动作对象可能是 {x_zh,x_en} 双语形态(后端下发 / DEFAULT_JOB_ACTIONS)或单语
  // {x};按当前界面语言取,逐级回退 —— 保证英文界面下动作按钮也跟随语言。
  function pickLocalized(obj, base) {
    if (!obj) return '';
    var loc = (window.DQI18N && window.DQI18N.getLocale && window.DQI18N.getLocale()) || 'zh';
    var order = loc === 'en'
      ? [base + '_en', base, base + '_zh']
      : [base + '_zh', base, base + '_en'];
    for (var i = 0; i < order.length; i++) {
      if (obj[order[i]]) return obj[order[i]];
    }
    return '';
  }

  // 渲染 job_list_card / candidate_list_card 事件:表头 + 全选 + 条目列表
  // (可选 checkbox)+ 动作按钮。动作把 send_template 的 {ids} 替换为勾选
  // 的编号后作为用户消息发送。
  function appendListCard(card) {
    if (!stream || !card) return;
    const isJob = card.type === 'job_list_card';
    const items = (isJob ? card.jobs : card.candidates) || [];
    if (!items.length) return;
    // 后端对 Boss 不下发 actions —— 职位卡用默认「分析职位 / 打招呼」兜底
    let actions = Array.isArray(card.actions) ? card.actions : [];
    if (!actions.length && isJob) actions = DEFAULT_JOB_ACTIONS;
    const needSel = actions.some((a) => a.require_selection);

    const head = t('chat.listCard.head', { scanned: card.scanned || items.length, matched: card.matched || 0 });
    const entityLabel = isJob ? t('chat.listCard.entityJob') : t('chat.listCard.entityCandidate');
    const itemsHtml = items.map((it) => {
      const mp = it.match_percent;
      const badge = (typeof mp === 'number')
        ? `<span class="dq-match ${matchClass(mp)}">${mp}%</span>` : '';
      let title, sub;
      if (isJob) {
        title = it.title || t('chat.listCard.noTitle');
        sub = [it.company, it.city, it.mode, it.salary].filter(Boolean).join(' · ');
      } else {
        const yoe = (typeof it.years_experience === 'number') ? t('chat.yoe', { n: it.years_experience }) : '';
        title = it.name || t('chat.listCard.noName');
        sub = [it.current_role, it.current_company, it.city, yoe, it.education]
          .filter(Boolean).join(' · ');
      }
      return `
        <label class="dq-card-item">
          ${needSel ? `<input class="dq-card-check" type="checkbox" value="${escapeHtml(String(it.index))}">` : ''}
          <div class="dq-card-main">
            <div class="dq-card-title">
              <span class="dq-card-idx">${escapeHtml(String(it.index))}</span>
              <span class="dq-card-name">${escapeHtml(title)}</span>${badge}
            </div>
            ${sub ? `<div class="dq-card-sub">${escapeHtml(sub)}</div>` : ''}
          </div>
        </label>`;
    }).join('');

    const actionsHtml = actions.length ? `
      <div class="dq-card-actions">
        ${actions.map((a, i) =>
          `<button type="button" class="btn ${a.variant === 'primary' ? 'primary' : 'ghost'}" data-act="${i}">${escapeHtml(pickLocalized(a, 'label') || t('chat.listCard.action'))}</button>`
        ).join('')}
        <div class="dq-card-tip" hidden></div>
      </div>` : '';

    // 有勾选类动作时,表头下加一行「全选」
    const selAllHtml = needSel
      ? `<label class="dq-card-selall"><input type="checkbox"><span>${t('chat.listCard.selAll')}</span></label>`
      : '';

    const row = document.createElement('div');
    row.className = 'chat-row from-ai';
    row.innerHTML = `
      <div class="dq-card">
        <div class="dq-card-head">
          <span>${escapeHtml(entityLabel)}</span>
          <span>${escapeHtml(head)}</span>
        </div>
        ${selAllHtml}
        <div class="dq-card-list">${itemsHtml}</div>
        ${actionsHtml}
      </div>`;

    // 全选 ↔ 单条勾选 双向联动 + 动作按钮选中计数
    const selAll = row.querySelector('.dq-card-selall input');
    const itemBoxes = Array.from(row.querySelectorAll('.dq-card-item input[type="checkbox"]'));
    const actBtns = Array.from(row.querySelectorAll('[data-act]'));

    // 动作按钮:选中数 > 0 时标签后缀「(N)」;require_selection 动作未选时禁用
    function updateActionStates() {
      const count = itemBoxes.filter((c) => c.checked).length;
      actBtns.forEach((btn) => {
        const a = actions[Number(btn.dataset.act)];
        if (!a) return;
        const label = pickLocalized(a, 'label') || t('chat.listCard.action');
        btn.textContent = count > 0 ? `${label} (${count})` : label;
        btn.disabled = !!a.require_selection && count === 0;
      });
    }

    if (selAll) {
      const syncCheckedRows = () => {
        itemBoxes.forEach((c) => {
          c.closest('.dq-card-item')?.classList.toggle('is-selected', c.checked);
        });
      };
      const syncSelAll = () => {
        const n = itemBoxes.filter((c) => c.checked).length;
        selAll.checked = n > 0 && n === itemBoxes.length;
        selAll.indeterminate = n > 0 && n < itemBoxes.length;
      };
      selAll.addEventListener('change', () => {
        itemBoxes.forEach((c) => { c.checked = selAll.checked; });
        selAll.indeterminate = false;
        syncCheckedRows();
        updateActionStates();
      });
      itemBoxes.forEach((c) => c.addEventListener('change', () => {
        syncCheckedRows();
        syncSelAll();
        updateActionStates();
      }));
    }

    actBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        const a = actions[Number(btn.dataset.act)];
        if (!a) return;
        const ids = needSel
          ? Array.from(row.querySelectorAll('.dq-card-item input:checked')).map((c) => c.value)
          : [];
        const tip = row.querySelector('.dq-card-tip');
        if (a.require_selection && !ids.length) {
          if (tip) { tip.textContent = t('chat.listCard.tip'); tip.hidden = false; }
          return;
        }
        if (tip) tip.hidden = true;
        const tpl = pickLocalized(a, 'send_template');
        const text = tpl.replace(/\{ids\}/g, ids.join(','));
        if (!text || (send && send.disabled)) return;
        if (input) input.value = text;
        onSend();
      });
    });

    updateActionStates();   // 初始渲染:require_selection 动作显示为禁用

    stream.appendChild(row);
    stream.scrollTop = stream.scrollHeight;
  }

  // SSE 解析:返回一个 async 流式 handler.每次 text_delta 累加 + scroll
  // 后端路由:POST /agent/sse(见 job-agent-gateway/server.py)。
  // 契约:user_id / role / platform 走 query string;消息体字段名是 text。
  async function streamChat(text, ssePath = '/agent/sse') {
    const role     = window.DQ?.state?.role || 'jobseeker';
    const platform = window.DQ?.state?.platform || 'boss';
    const uid      = await api.getUserId();
    const baseUrl  = await api.baseUrls.agentGwBase();

    // user_id 走 query string,与 shared/api.js 各业务方法一致
    const qs = new URLSearchParams({ role, platform });
    if (uid) qs.set('user_id', uid);

    // 已登录时带上 Bearer(gateway JWT 中间件用)
    const headers = { 'Content-Type': 'application/json' };
    try {
      const auth = await window.DQ?.authApi?.loadAuth?.();
      if (auth && auth.accessToken) headers['Authorization'] = `Bearer ${auth.accessToken}`;
    } catch (_) {}

    const resp = await fetch(`${baseUrl}${ssePath}?${qs}`, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify({
        role, platform,
        text,                                          // 后端读 body.text
        page_context: window.DQ?.state?.pageContext || null,
      }),
    });
    if (!resp.ok || !resp.body) {
      const err = await resp.text().catch(() => '');
      appendAiBubble(t('chat.reqFailed', { status: resp.status, err: err.slice(0, 120) }));
      return;
    }

    const aiBubble = appendAiBubble('');

    // 思考过程:首条 thinking_delta 到达时懒创建可折叠块,插在 AI 气泡之前。
    let thinkEl = null, thinkBody = null, thinkSummary = null;
    let thinkText = '', thinkStart = 0, thinkDone = false;
    function ensureThinking() {
      if (thinkEl) return;
      thinkStart = Date.now();
      thinkEl = document.createElement('details');
      thinkEl.className = 'chat-thinking';
      thinkEl.open = true;
      thinkSummary = document.createElement('summary');
      thinkSummary.textContent = t('chat.thinking');
      thinkBody = document.createElement('div');
      thinkBody.className = 'thinking-body';
      thinkEl.appendChild(thinkSummary);
      thinkEl.appendChild(thinkBody);
      const aiRow = aiBubble.closest('.chat-row') || aiBubble.parentElement;
      stream.insertBefore(thinkEl, aiRow);
    }
    function finishThinking(finalContent) {
      if (!thinkEl || thinkDone) return;
      thinkDone = true;
      if (finalContent) { thinkText = String(finalContent); thinkBody.textContent = thinkText; }
      const sec = Math.max(1, Math.round((Date.now() - thinkStart) / 1000));
      thinkSummary.textContent = t('chat.thinkingDone', { sec });
      thinkEl.open = false;   // 完成后默认折叠
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        // 解析 SSE event/data
        let event = 'message', data = '';
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) data += line.slice(5).trim();
        }
        if (!data) continue;
        let payload; try { payload = JSON.parse(data); } catch { continue; }
        switch (event) {
          case 'text_delta':
            if (aiBubble) {
              aiBubble.textContent += String(payload.delta || '');
              stream.scrollTop = stream.scrollHeight;
            }
            break;
          case 'insufficient_credit':
            appendInsufficientCreditCard(payload);
            emit(NAMES.INSUFFICIENT_CREDIT, payload);
            break;
          case 'task_event':
            // 任务被 chat agent 启动了 → 提示用户去 tasks tab
            appendAiBubble(t('chat.taskStarted', { id: payload.task_id }));
            break;
          case 'action_buttons': {
            // 结构化按钮(登录重检 / 身份切换等)→ 渲染成可点击 chip
            const actions = Array.isArray(payload.actions) ? payload.actions : [];
            appendChipRow(actions.map((a) => ({
              label: pickLocalized(a, 'label'),
              sendText: pickLocalized(a, 'send_text') || pickLocalized(a, 'label'),
            })).filter((c) => c.label && c.sendText));
            break;
          }
          case 'job_list_card':
          case 'candidate_list_card':
            // 职位 / 候选人结果富卡片(列表 + 匹配度 + 可选 + 动作按钮)
            appendListCard(payload);
            break;
          case 'thinking_delta':
            // 模型思考过程:流式追加到可折叠块(AI 气泡之前)
            ensureThinking();
            thinkText += String(payload.delta || '');
            thinkBody.textContent = thinkText;
            stream.scrollTop = stream.scrollHeight;
            break;
          case 'thinking_done':
            finishThinking(payload.content);
            break;
          // 其它 event(tool_call / tool_result 等)暂不渲染
        }
      }
    }
    finishThinking();   // 兜底:thinking_done 没来也把「思考中…」收尾
    // 流结束:把 AI 气泡末行的 `[a,b,c]` chip 数组渲染成可点击 chip
    extractChips(aiBubble);
  }

  async function onSend() {
    const text = (input?.value || '').trim();
    if (!text) return;
    input.value = '';
    send.disabled = true;
    appendUserBubble(text);
    try { await streamChat(text); }
    catch (e) {
      console.error('[chat] send failed', e);
      appendAiBubble(t('chat.sendError') + (e.message || e) + ')');
    } finally {
      send.disabled = false;
      input?.focus();
    }
  }

  send?.addEventListener('click', onSend);
  input?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  });
  // textarea 自动高度
  input?.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(120, input.scrollHeight) + 'px';
  });

  // ── 开场白(onboarding finish() 走完后调一次)──────────────────
  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  const PLATFORM_LABEL = { linkedin: 'LinkedIn', indeed: 'Indeed' };

  /**
   * 引导结束后展示一条 AI 开场白气泡。
   * @param {{job_role?:string, city?:string, salary_range?:string}} prefs
   *        缺省(跳过路径)时走兜底文案,不做插值。
   */
  function greet(prefs) {
    if (!stream) return;
    const platform = window.DQ?.state?.platform || 'boss';
    const platLabel = PLATFORM_LABEL[platform] || t('common.platformBoss');

    const p = prefs || {};
    const parts = [p.job_role, p.city, p.salary_range].filter(Boolean).map(escapeHtml);
    const bgLine = parts.length
      ? `${t('chat.greet.bgKnown')}<b>${parts.join(' · ')}</b>`
      : t('chat.greet.bgUnknown');

    const body = `
      <p>${bgLine}</p>
      <p>${t('chat.greet.browse', { platform: escapeHtml(platLabel) })}</p>
      <div class="ai-block-section">${t('chat.greet.canHelp')}</div>
      <ul>
        <li>${t('chat.greet.analyze')}</li>
        <li>${t('chat.greet.optimize')}</li>
        <li>${t('chat.greet.batch')}</li>
        <li>${t('chat.greet.draft')}</li>
      </ul>`;
    appendAiBlock(t('chat.greet.hello'), body, [
      { label: '总结当前岗位', prompt: '总结当前岗位' },
      { label: '评估匹配度', prompt: '评估当前职位与我的匹配度' },
      { label: '生成招呼语', prompt: '帮我生成一段打招呼消息' },
    ]);
    greeted = true;
  }

  /**
   * 已 onboarded 的求职者重新打开扩展时:聊天流为空则自动补一条开场白。
   * onboarding 走完会主动调 greet();本函数覆盖"老用户重开扩展"场景。
   * 偏好取自 onboarding 落盘的 chrome.storage.local.jobseeker.preferences,
   * 不发网络请求;缺省时 greet() 自带兜底文案。
   */
  async function maybeGreet() {
    if (greeted || !stream || stream.children.length > 0) return;
    let onboarded = false, role = '', prefs = null;
    try {
      const r = await chrome.storage.local.get(['user', 'jobseeker']);
      onboarded = !!(r.user && r.user.onboarded);
      role      = (r.user && r.user.role) || '';
      prefs     = (r.jobseeker && r.jobseeker.preferences) || null;
    } catch (_) {}
    if (!onboarded || role !== 'jobseeker') return;   // 开场白仅对求职者
    greet(prefs);
  }

  // app.js 完成 loadUserPrefs() 后派 AUTH_CHANGED —— 那时 state.platform 已就绪。
  on(NAMES.AUTH_CHANGED, maybeGreet);
  // 兜底:万一已错过 AUTH_CHANGED,DOM ready 后再试一次。
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(maybeGreet, 60));
  } else {
    setTimeout(maybeGreet, 60);
  }

  // 把文本填进输入框(不自动发送)—— 平台页浮动「AI 分析」按钮用,
  // 让用户看清 prompt 再决定发送,避免在打开面板时直接消耗 Credit。
  function prefill(text) {
    if (!input) return;
    input.value = text || '';
    input.style.height = 'auto';
    input.style.height = Math.min(120, input.scrollHeight) + 'px';
    input.focus();
    input.scrollIntoView?.({ block: 'nearest' });
  }

  // 在对话流里呈现一条职位评估结果(平台页「查看详情」用)。
  // 结构化富卡片是后续 gap,这里先用一条可读的 AI 气泡承载。
  function showEvalResult(evaluated, jobSnapshot) {
    if (!stream) return;
    const ev = evaluated || {};
    const job = jobSnapshot || {};
    const score = Number(ev.score) || 0;
    const reasons = Array.isArray(ev.reasons) ? ev.reasons : [];
    const titlePart = job.title ? '：' + escapeHtml(job.title) : '';
    const lines = [
      `${t('chat.eval.title')}${titlePart}`,
      `${t('chat.eval.score')}<b>${score}</b>${ev.level ? ' · ' + escapeHtml(ev.level) : ''}`,
    ];
    if (reasons.length) {
      lines.push(t('chat.eval.keypoints'));
      reasons.slice(0, 6).forEach(r => lines.push('· ' + escapeHtml(r)));
    }
    const row = document.createElement('div');
    row.className = 'chat-row from-ai';
    row.innerHTML = `
      <div class="chat-bubble">${lines.join('<br>')}</div>`;
    stream.appendChild(row);
    stream.scrollTop = stream.scrollHeight;
  }

  window.DQ = window.DQ || {};
  window.DQ.chat = {
    send: (txt) => { input.value = txt; onSend(); },
    greet,
    prefill,
    showEvalResult,
  };
})();
