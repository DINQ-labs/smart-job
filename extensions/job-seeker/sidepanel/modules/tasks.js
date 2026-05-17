/*
 * modules/tasks.js — 任务 tab.列任务 + 状态徽 + 5s 轮询 + tab 角标
 *                     + 启动新任务(模板选择器 modal)+ 任务详情(全屏覆盖层).
 *
 * 任务详情走独立全屏覆盖层(modules/task-detail.js,window.DQ.taskDetail),
 * sidepanel 太窄;取消 / 继续 已从「点卡片 confirm」迁到覆盖层底部的按钮。
 * 「启动新任务」走 .modal-mask modal:列模板 → 选模板 → 填 inputs → taskRun。
 *
 * v3 类:.task-card / .task-card.status-* / .task-progress-bar / .chip-amber/-green/-red/-blue
 *        / .modal-mask · .modal-card(启动新任务 modal)。
 */
(function () {
  'use strict';
  const { NAMES, on, emit } = window.DQ.events;
  const api = window.DQ.api;
  const POLL_MS = 5000;

  function $(id) { return document.getElementById(id); }
  const list = $('tasksList');
  const tabsBadge = $('tabTasksBadge');

  const state = {
    tasks: [],
    timer: null,
    scope: 'current',
    templates: [],          // 已拉取的模板列表
  };

  // ── 转义 ────────────────────────────────────────────────────────────
  function escapeText(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function escapeAttr(s) { return escapeText(s).replace(/"/g, '&quot;'); }
  function relTime(iso) {
    if (!iso) return '';
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 60000) return t('tasks.relTime.justNow');
    if (ms < 3600000) return t('tasks.relTime.minutesAgo', { n: Math.floor(ms / 60000) });
    if (ms < 86400000) return t('tasks.relTime.hoursAgo', { n: Math.floor(ms / 3600000) });
    return new Date(iso).toLocaleDateString('zh-CN');
  }

  function statusChip(s) {
    const map = {
      pending:    { cls: 'chip',           label: t('tasks.status.pending'),   icon: '⌛' },
      running:    { cls: 'chip chip-green', label: t('tasks.status.running'),   icon: '⚡' },
      paused_user_action: { cls: 'chip chip-amber', label: t('tasks.status.paused'), icon: '⏸' },
      completed:  { cls: 'chip chip-blue',  label: t('tasks.status.completed'), icon: '✓' },
      failed:     { cls: 'chip chip-red',   label: t('tasks.status.failed'),    icon: '✕' },
      cancelled:  { cls: 'chip',            label: t('tasks.status.cancelled'), icon: '⊘' },
    };
    const x = map[s] || map.pending;
    return `<span class="${x.cls}"><span aria-hidden="true">${x.icon}</span><span>${x.label}</span></span>`;
  }

  function platLabel(p) {
    return ({ boss: 'Boss', linkedin: 'LinkedIn', indeed: 'Indeed' })[p] || (p || '').slice(0, 4);
  }

  function pauseRemaining(p) {
    if (!p || !p.pause_started_at || !p.pause_sec) return null;
    const start = new Date(p.pause_started_at).getTime();
    if (isNaN(start)) return null;
    return Math.max(0, Math.round((start + p.pause_sec * 1000 - Date.now()) / 1000));
  }

  function phaseLabel(p) {
    if (!p || !p.phase) return '';
    if (p.phase === 'batch_pausing') {
      const r = pauseRemaining(p);
      return r != null && r > 0 ? t('tasks.phase.batchResting', { sec: r }) : t('tasks.phase.batchRestingShort');
    }
    if (p.phase === 'retry_waiting') return t('tasks.phase.retryWaiting');
    if (p.phase === 'step_started')  return t('tasks.phase.stepStarted');
    if (p.phase === 'started')       return t('tasks.phase.started');
    return '';
  }

  function progressText(tk) {
    const p = tk.progress || {};
    const lab = phaseLabel(p);
    if (p.total) {
      const head = `${p.current || 0}/${p.total}`;
      return lab ? `${head} · ${lab}` : head;
    }
    return lab || (p.phase || '');
  }

  function progressPct(tk) {
    const p = tk.progress || {};
    if (p.total) return Math.round(((p.current || 0) / p.total) * 100);
    if (tk.status === 'completed') return 100;
    return 0;
  }

  function statusModifier(tk) {
    if (tk.status === 'paused_user_action') return 'status-paused';
    if (tk.status === 'failed') return 'status-failed';
    if (tk.status === 'completed') return 'status-completed';
    if (tk.status === 'cancelled') return 'status-cancelled';
    if (tk.status === 'running' && (tk.progress || {}).phase === 'batch_pausing') return 'status-batch-resting';
    if (tk.status === 'running') return 'status-running';
    return '';
  }

  function renderCard(tk) {
    const pct = progressPct(tk);
    const mod = statusModifier(tk);
    const skip = (tk.skipped || []).length;
    const hint = tk.status === 'paused_user_action'
      ? `<div class="task-alert"><div class="task-alert-head">${t('tasks.card.pauseHint')}</div><div>${t('tasks.card.pauseDesc')}</div></div>`
      : (tk.status === 'running' && (tk.progress || {}).phase === 'batch_pausing'
        ? `<div class="task-rest-hint">${t('tasks.card.restHint')}</div>`
        : '');
    return `
      <article class="task-card ${mod}" data-task-id="${tk.id}" tabindex="0" role="button">
        <div class="task-card-head">
          <span class="chip chip-dark">${platLabel(tk.platform)}</span>
          <span class="task-card-title" title="${escapeAttr(tk.title || tk.template_id)}">${escapeText(tk.title || tk.template_id)}</span>
          ${statusChip(tk.status)}
        </div>
        <div class="task-progress-bar"><div class="task-progress-fill" style="width:${pct}%"></div></div>
        <div class="task-meta">
          <span>${escapeText(progressText(tk))}${skip ? ` ${t('tasks.card.skipped', { n: skip })}` : ''}</span>
          <span>${relTime(tk.created_at)}</span>
        </div>
        ${hint}
      </article>`;
  }

  function render() {
    if (!list) return;
    if (!state.tasks.length) {
      list.innerHTML = `
        <div class="empty-state">
          <div class="empty-ico" aria-hidden="true">📋</div>
          <div class="empty-title">${t('tasks.empty.title')}</div>
          <div class="empty-sub">${t('tasks.empty.sub')}</div>
          <button class="btn primary" id="taskStartNewEmpty" type="button" style="margin-top:var(--sp-2)">${t('tasks.btn.startNew')}</button>
        </div>`;
      tabsBadge && tabsBadge.classList.add('hidden');
      return;
    }
    const active = state.tasks.filter(tk => ['running','paused_user_action','pending'].includes(tk.status));
    const done   = state.tasks.filter(tk => !['running','paused_user_action','pending'].includes(tk.status));
    let html = `
      <div class="task-toolbar">
        <button class="btn primary task-new-btn" id="taskStartNew" type="button">${t('tasks.btn.startNew')}</button>
      </div>`;
    if (active.length) {
      html += `<div class="task-group-label">${t('tasks.group.active', { n: active.length })}</div>`;
      html += active.map(renderCard).join('');
    }
    if (done.length) {
      html += `<div class="task-group-label task-group-label-2">${t('tasks.group.done', { n: done.length })}</div>`;
      html += done.map(renderCard).join('');
    }
    list.innerHTML = html;

    const running = active.length;
    if (running > 0 && tabsBadge) {
      tabsBadge.textContent = String(running);
      tabsBadge.classList.remove('hidden');
    } else if (tabsBadge) {
      tabsBadge.classList.add('hidden');
    }
    emit(NAMES.TASKS_REFRESHED, {
      running,
      paused: state.tasks.filter(tk => tk.status === 'paused_user_action').length,
    });
  }

  async function refresh() {
    const role     = window.DQ?.state?.role || 'jobseeker';
    const platform = window.DQ?.state?.platform || 'boss';
    if (!window.DQ?.state?.user?.loggedIn) { state.tasks = []; render(); return; }
    try {
      const data = await api.agentGw.tasks({ role, platform, scope: state.scope });
      state.tasks = data?.tasks || [];
      render();
    } catch (e) {
      console.warn('[tasks] fetch failed', e);
    }
  }

  function startPolling() {
    if (state.timer) return;
    state.timer = setInterval(refresh, POLL_MS);
    refresh();
  }
  function stopPolling() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  // ── 模板 inputs schema ──────────────────────────────────────────────
  // 后端 /tasks/templates 只回 id/title/description/emoji/step_count/estimated_min,
  // 不含字段 schema。schema 前端硬码(与 v2 tasks.js TEMPLATE_INPUT_SCHEMAS 一致)。
  // 若将来后端在 template 上带了 inputs_schema 数组,getInputSchema 会优先用它。
  // 格式:按 platform 分组;每条字段 { name, label, type, default, required, hint, options }。
  // type: 'text' | 'number' | 'textarea' | 'select'。
  const TEMPLATE_INPUT_SCHEMAS = {
    'jobseeker:find_best_jobs': {
      boss: [
        { name: 'keyword', label: t('tasks.field.keyword.label'), type: 'text', required: true,
          hint: t('tasks.field.keyword.hint') },
        { name: 'city', label: t('tasks.field.city.label'), type: 'text', default: '北京',
          hint: t('tasks.field.city.hint') },
        { name: 'desired_count', label: t('tasks.field.desiredCount.label'), type: 'number', default: 30,
          hint: t('tasks.field.desiredCount.hint') },
        { name: 'top_n', label: t('tasks.field.topN.label'), type: 'number', default: 10,
          hint: t('tasks.field.topN.hint') },
      ],
      linkedin: [
        { name: 'keyword', label: t('tasks.field.keyword.label'), type: 'text', required: true,
          hint: t('tasks.field.keyword.hintRequired') },
        { name: 'country', label: t('tasks.field.country.label'), type: 'select', default: '',
          options: [
            { value: '',   label: t('tasks.field.country.opt.global') },
            { value: 'US', label: t('tasks.field.country.opt.US') },
            { value: 'CN', label: t('tasks.field.country.opt.CN') },
            { value: 'CA', label: t('tasks.field.country.opt.CA') },
            { value: 'GB', label: t('tasks.field.country.opt.GB') },
            { value: 'JP', label: t('tasks.field.country.opt.JP') },
            { value: 'AU', label: t('tasks.field.country.opt.AU') },
            { value: 'SG', label: t('tasks.field.country.opt.SG') },
            { value: 'DE', label: t('tasks.field.country.opt.DE') },
            { value: 'IN', label: t('tasks.field.country.opt.IN') },
          ],
          hint: t('tasks.field.country.hintLinkedin') },
        { name: 'desired_count', label: t('tasks.field.desiredCount.label'), type: 'number', default: 30,
          hint: t('tasks.field.desiredCount.hintSimple') },
        { name: 'top_n', label: t('tasks.field.topN.label'), type: 'number', default: 10, hint: t('tasks.field.topN.hintSimple') },
      ],
      indeed: [
        { name: 'keyword', label: t('tasks.field.keyword.label'), type: 'text', required: true, hint: t('tasks.field.keyword.hintSimple') },
        { name: 'location', label: t('tasks.field.location.label'), type: 'text', default: '',
          hint: t('tasks.field.location.hint') },
        { name: 'country', label: t('tasks.field.country.label'), type: 'select', default: 'US',
          options: [
            { value: 'US', label: t('tasks.field.country.opt.US_indeed') },
            { value: 'GB', label: t('tasks.field.country.opt.GB_indeed') },
            { value: 'CA', label: t('tasks.field.country.opt.CA') },
            { value: 'AU', label: t('tasks.field.country.opt.AU') },
            { value: 'IN', label: t('tasks.field.country.opt.IN') },
            { value: 'SG', label: t('tasks.field.country.opt.SG') },
            { value: 'DE', label: t('tasks.field.country.opt.DE') },
          ],
          hint: t('tasks.field.country.hintIndeed') },
        { name: 'desired_count', label: t('tasks.field.desiredCount.label'), type: 'number', default: 30,
          hint: t('tasks.field.desiredCount.hintSimple') },
        { name: 'top_n', label: t('tasks.field.topN.label'), type: 'number', default: 10, hint: t('tasks.field.topN.hintSimple') },
      ],
    },
    'recruiter:hiring_pipeline': {
      boss: [
        { name: 'job_keyword', label: t('tasks.field.jobKeyword.label'), type: 'text', required: true,
          hint: t('tasks.field.jobKeyword.hintBoss') },
        { name: 'contact_count', label: t('tasks.field.contactCount.label'), type: 'number', default: 10, hint: t('tasks.field.contactCount.hintBoss') },
        { name: 'greeting_template', label: t('tasks.field.greetingTemplate.label'), type: 'textarea',
          hint: t('tasks.field.greetingTemplate.hint') },
      ],
      linkedin: [
        { name: 'job_keyword', label: t('tasks.field.jobKeyword.label'), type: 'text', required: true,
          hint: t('tasks.field.jobKeyword.hintLinkedin') },
        { name: 'contact_count', label: t('tasks.field.contactCount.label'), type: 'number', default: 10,
          hint: t('tasks.field.contactCount.hintLinkedin') },
        { name: 'greeting_template', label: t('tasks.field.inviteNote.label'), type: 'textarea',
          hint: t('tasks.field.inviteNote.hint') },
      ],
    },
    'recruiter:inbox_triage': {
      indeed: [
        { name: 'max_per_job', label: t('tasks.field.maxPerJob.label'), type: 'number', default: 5,
          hint: t('tasks.field.maxPerJob.hint') },
        { name: 'max_total', label: t('tasks.field.maxTotal.label'), type: 'number', default: 30,
          hint: t('tasks.field.maxTotal.hint') },
        { name: 'dispositions', label: t('tasks.field.dispositions.label'), type: 'text', default: 'NEW,PENDING',
          hint: t('tasks.field.dispositions.hint') },
      ],
    },
  };

  function getInputSchema(templateId, platform, tpl) {
    // 后端若带了 inputs_schema(数组)优先用
    if (tpl && Array.isArray(tpl.inputs_schema)) return tpl.inputs_schema;
    const s = TEMPLATE_INPUT_SCHEMAS[templateId];
    if (!s) return [];
    if (s.boss || s.linkedin || s.indeed) return s[platform] || [];
    return Array.isArray(s) ? s : [];
  }

  // 用用户求职偏好预填 keyword / city / job_keyword(只覆盖空 default)
  function applyPrefsToSchema(schema, prefs) {
    if (!prefs) return schema;
    return schema.map((f) => {
      const merged = Object.assign({}, f);
      if (f.name === 'keyword' && prefs.job_role) merged.default = prefs.job_role;
      if (f.name === 'job_keyword' && prefs.job_role) merged.default = prefs.job_role;
      if (f.name === 'city' && prefs.city) merged.default = prefs.city;
      return merged;
    });
  }

  // ── 启动新任务 modal ────────────────────────────────────────────────
  function closePickerModal() {
    const m = document.getElementById('taskPickerMask');
    if (m) m.remove();
  }

  async function openTemplatePicker() {
    if (!window.DQ?.state?.user?.loggedIn) {
      alert(t('tasks.modal.needLogin'));
      return;
    }
    closePickerModal();

    const mask = document.createElement('div');
    mask.id = 'taskPickerMask';
    mask.className = 'modal-mask open';
    mask.innerHTML = `
      <div class="modal-card">
        <div class="modal-head">
          <span class="title" id="taskPickerTitle">${t('tasks.modal.title')}</span>
          <span class="close" id="taskPickerClose" role="button" aria-label="${t('tasks.modal.close')}">✕</span>
        </div>
        <div class="modal-body" id="taskPickerBody">
          <div class="td-loading"><span class="spinner"></span> ${t('tasks.modal.loadingTpl')}</div>
        </div>
        <div class="modal-footer" id="taskPickerFooter" hidden></div>
      </div>`;
    document.body.appendChild(mask);

    mask.querySelector('#taskPickerClose').addEventListener('click', closePickerModal);
    mask.addEventListener('click', (e) => { if (e.target === mask) closePickerModal(); });

    // 每次重拉 —— 平台可能切换了,可用模板会变
    const role     = window.DQ?.state?.role || 'jobseeker';
    const platform = window.DQ?.state?.platform || 'boss';
    try {
      const data = await api.agentGw.templates({ role, platform });
      state.templates = (data && data.templates) || [];
    } catch (e) {
      console.warn('[tasks] templates fetch failed', e);
      state.templates = [];
    }
    if (!document.body.contains(mask)) return;   // 加载期间已关

    renderTemplateList(mask);
  }

  function renderTemplateList(mask) {
    const body = mask.querySelector('#taskPickerBody');
    const footer = mask.querySelector('#taskPickerFooter');
    const titleEl = mask.querySelector('#taskPickerTitle');
    if (titleEl) titleEl.textContent = t('tasks.modal.title');
    if (footer) { footer.hidden = true; footer.innerHTML = ''; }
    if (!body) return;

    if (!state.templates.length) {
      body.innerHTML = `
        <div class="empty-state">
          <div class="empty-ico" aria-hidden="true">🛠️</div>
          <div class="empty-title">${t('tasks.modal.noTpl.title')}</div>
          <div class="empty-sub">${t('tasks.modal.noTpl.sub')}</div>
        </div>`;
      return;
    }

    body.innerHTML = `<div class="task-tpl-list">${
      state.templates.map((tpl) => `
        <button class="task-tpl-card" type="button" data-template-id="${escapeAttr(tpl.id)}">
          <span class="task-tpl-emoji" aria-hidden="true">${escapeText(tpl.emoji || '⚡')}</span>
          <span class="task-tpl-text">
            <span class="task-tpl-title">${escapeText(tpl.title || tpl.id)}</span>
            <span class="task-tpl-desc">${escapeText(tpl.description || '')}</span>
            <span class="task-tpl-meta">${t('tasks.modal.tplMeta', { steps: tpl.step_count || 0, min: escapeText(String(tpl.estimated_min || '?')) })}</span>
          </span>
        </button>`).join('')
    }</div>`;

    body.querySelectorAll('.task-tpl-card[data-template-id]').forEach((card) => {
      card.addEventListener('click', () => renderInputsForm(mask, card.dataset.templateId));
    });
  }

  async function renderInputsForm(mask, templateId) {
    const body = mask.querySelector('#taskPickerBody');
    const footer = mask.querySelector('#taskPickerFooter');
    const titleEl = mask.querySelector('#taskPickerTitle');
    if (!body || !footer) return;

    const tpl = state.templates.find((tk) => tk.id === templateId) || {};
    const platform = window.DQ?.state?.platform || 'boss';
    let schema = getInputSchema(templateId, platform, tpl);

    // 用偏好预填(jobseeker keyword/city,recruiter job_keyword)
    let prefs = null;
    try {
      if (api && api.agentGw) {
        const resp = await api.agentGw.userPreferences(platform);
        if (resp && resp.ok && resp.data) prefs = resp.data;
      }
    } catch (_) { /* 离线:不预填 */ }
    if (!document.body.contains(mask)) return;
    schema = applyPrefsToSchema(schema, prefs);

    if (titleEl) titleEl.textContent = `${tpl.emoji || '⚡'} ${tpl.title || templateId}`;

    let fieldsHtml;
    if (!schema.length) {
      fieldsHtml = `<div class="task-form-empty">${t('tasks.form.noParams')}</div>`;
    } else {
      fieldsHtml = schema.map((f) => {
        const def = f.default != null ? String(f.default) : '';
        const reqMark = f.required ? '<span class="task-form-req">*</span>' : '';
        let inputHtml;
        if (f.type === 'textarea') {
          inputHtml = `<textarea class="ob-input" name="${escapeAttr(f.name)}" rows="3" placeholder="${escapeAttr(f.hint || '')}">${escapeText(def)}</textarea>`;
        } else if (f.type === 'number') {
          inputHtml = `<input class="ob-input" type="number" name="${escapeAttr(f.name)}" value="${escapeAttr(def)}" placeholder="${escapeAttr(f.hint || '')}">`;
        } else if (f.type === 'select' && Array.isArray(f.options)) {
          const opts = f.options.map((o) => {
            const ov = String(o.value == null ? '' : o.value);
            const ol = String(o.label == null ? ov : o.label);
            return `<option value="${escapeAttr(ov)}"${ov === def ? ' selected' : ''}>${escapeText(ol)}</option>`;
          }).join('');
          inputHtml = `<select class="ob-input" name="${escapeAttr(f.name)}">${opts}</select>`;
        } else {
          inputHtml = `<input class="ob-input" type="text" name="${escapeAttr(f.name)}" value="${escapeAttr(def)}" placeholder="${escapeAttr(f.hint || '')}">`;
        }
        return `
          <div class="task-form-field">
            <label class="ob-field-label">${escapeText(f.label)}${reqMark}</label>
            ${inputHtml}
            ${f.hint ? `<div class="ob-hint">${escapeText(f.hint)}</div>` : ''}
          </div>`;
      }).join('');
    }

    body.innerHTML = `
      ${tpl.description ? `<div class="task-form-desc">${escapeText(tpl.description)}</div>` : ''}
      <form id="taskInputsForm" class="task-form" novalidate>${fieldsHtml}</form>
      <div class="ob-form-error hidden" id="taskFormError"></div>`;

    footer.hidden = false;
    footer.innerHTML = `
      <button class="btn ghost" id="taskFormBack" type="button">${t('tasks.form.back')}</button>
      <button class="btn primary" id="taskFormSubmit" type="button">${t('tasks.form.submit')}</button>`;

    footer.querySelector('#taskFormBack').addEventListener('click', () => renderTemplateList(mask));
    const submitBtn = footer.querySelector('#taskFormSubmit');
    submitBtn.addEventListener('click', () => submitTask(mask, templateId, tpl, schema));
    const form = body.querySelector('#taskInputsForm');
    if (form) {
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        submitTask(mask, templateId, tpl, schema);
      });
    }
    const firstInput = body.querySelector('input, textarea, select');
    if (firstInput) firstInput.focus();
  }

  async function submitTask(mask, templateId, tpl, schema) {
    const form = mask.querySelector('#taskInputsForm');
    const errBox = mask.querySelector('#taskFormError');
    const showErr = (msg) => {
      if (errBox) { errBox.textContent = msg; errBox.classList.remove('hidden'); }
    };
    if (errBox) errBox.classList.add('hidden');

    const inputs = {};
    for (const f of (schema || [])) {
      const el = form ? form.querySelector(`[name="${f.name}"]`) : null;
      if (!el) continue;
      let v = el.value;
      if (f.required && !String(v).trim()) {
        showErr(t('tasks.form.required', { label: f.label }));
        el.focus();
        return;
      }
      if (f.type === 'number' && v !== '') {
        v = parseInt(v, 10);
        if (isNaN(v)) { showErr(t('tasks.form.mustBeNumber', { label: f.label })); el.focus(); return; }
      }
      if (String(v).trim()) inputs[f.name] = v;
    }

    const role     = window.DQ?.state?.role || 'jobseeker';
    const platform = window.DQ?.state?.platform || 'boss';
    const userId   = window.DQ?.state?.user?.id || '';
    if (!userId) { showErr(t('tasks.form.notLoggedIn')); return; }

    const submitBtn = mask.querySelector('#taskFormSubmit');
    const backBtn = mask.querySelector('#taskFormBack');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = t('tasks.form.submitting'); }
    if (backBtn) backBtn.disabled = true;

    try {
      await api.agentGw.taskRun({
        user_id: userId, role, platform,
        template_id: templateId,
        inputs,
        title: tpl.title || templateId,
      });
      closePickerModal();
      await refresh();
    } catch (err) {
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = t('tasks.form.submit'); }
      if (backBtn) backBtn.disabled = false;
      showErr(t('tasks.form.startFailed') + ((err && err.message) || err || t('common.empty')));
    }
  }

  // ── 事件 ────────────────────────────────────────────────────────────
  // Tab 切换:进 tasks tab 开 polling,离开停
  on(NAMES.TAB_CHANGED, (e) => {
    if (e.detail?.tab === 'tasks') startPolling();
    else stopPolling();
  });
  // 平台/角色/登录变 → 立即重拉
  on(NAMES.ROLE_CHANGED, refresh);
  on(NAMES.PLATFORM_CHANGED, refresh);
  on(NAMES.AUTH_CHANGED, refresh);

  // 列表点击委托:启动新任务按钮 / 任务卡 → 详情覆盖层
  list?.addEventListener('click', (e) => {
    if (e.target.closest('#taskStartNew') || e.target.closest('#taskStartNewEmpty')) {
      openTemplatePicker();
      return;
    }
    const card = e.target.closest('.task-card[data-task-id]');
    if (!card) return;
    const id = parseInt(card.dataset.taskId, 10);
    if (isNaN(id)) return;
    if (window.DQ?.taskDetail?.open) {
      window.DQ.taskDetail.open(id);
    } else {
      console.warn('[tasks] task-detail.js not loaded');
    }
  });
  // 键盘可达:任务卡 Enter / Space 也打开详情
  list?.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const card = e.target.closest('.task-card[data-task-id]');
    if (!card) return;
    e.preventDefault();
    const id = parseInt(card.dataset.taskId, 10);
    if (!isNaN(id) && window.DQ?.taskDetail?.open) window.DQ.taskDetail.open(id);
  });

  window.DQ = window.DQ || {};
  window.DQ.tasks = { refresh, openTemplatePicker };
})();
