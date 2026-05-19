/*
 * modules/task-detail.js — 任务详情全屏覆盖层(v3 端口,源自 v2 task-detail.js).
 *
 * sidepanel 很窄,任务详情走「全屏覆盖层」(.js-container 同款 fixed inset:0),
 * 「启动新任务」走 modal(见 tasks.js)。覆盖层结构:
 *   ← 返回 │ 标题 │ 状态 chip
 *   平台 · 模板 · 时间 · 用时 · #id
 *   [结果] [输入] [日志] [跳过(N)]
 *   ┌─ tab body ───────────────┐
 *   │  按 template_id 分支渲染   │
 *   └──────────────────────────┘
 *   ┌─ 底部操作条(取消 / 继续)─┐
 *
 * 暴露:window.DQ.taskDetail = { open(taskId), close(), refresh() }
 *
 * 数据源:api.agentGw.taskDetail(id) → 完整 task row
 *        (inputs/progress/artifacts/skipped/item_states/timestamps/result_summary)。
 *
 * 「结果」tab 按 template_id 分支:
 *   - jobseeker:find_best_jobs  → top_jobs 职位卡(.dq-card 复用)
 *   - recruiter:hiring_pipeline → contacted 候选人卡
 *   - recruiter:inbox_triage    → 按 job 分组的 candidates
 *   - 其它 / 未知                → result_summary 纯文本兜底
 *
 * v2 → v3 降级(详见文件末注释):
 *   - 去掉「AI 评估」按钮(v3 无 window.DQ.evaluation 模块)。
 *   - 去掉 viewed_at PATCH(v3 api.js 无 taskItemPatch);点链接仅打开页面。
 *
 * v3 类:.js-container(全屏覆盖)+ .chip / .task-* / .dq-card* 复用 + td-* 新类。
 */
(function () {
  'use strict';

  const api = window.DQ && window.DQ.api;
  const POLL_MS = 5000;

  // ── 转义(对齐 tasks.js 的 escapeText / escapeAttr)─────────────────
  function escapeText(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function escapeAttr(s) { return escapeText(s).replace(/"/g, '&quot;'); }

  // ── 时间工具 ────────────────────────────────────────────────────────
  function fmtTime(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const pad = (n) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
        + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    } catch (_) { return iso; }
  }
  function fmtDuration(startIso, endIso) {
    if (!startIso) return '';
    const a = new Date(startIso).getTime();
    const b = endIso ? new Date(endIso).getTime() : Date.now();
    if (isNaN(a)) return '';
    const sec = Math.max(0, Math.round((b - a) / 1000));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60), s = sec % 60;
    return s ? `${m}m${s}s` : `${m}m`;
  }
  function fmtRelative(iso) {
    if (!iso) return '';
    const tm = new Date(iso).getTime();
    if (isNaN(tm)) return '';
    const sec = Math.max(0, Math.round((Date.now() - tm) / 1000));
    if (sec < 60) return t('taskDetail.relTime.justNow');
    if (sec < 3600) return t('taskDetail.relTime.minutesAgo', { n: Math.floor(sec / 60) });
    if (sec < 86400) return t('taskDetail.relTime.hoursAgo', { n: Math.floor(sec / 3600) });
    return t('taskDetail.relTime.daysAgo', { n: Math.floor(sec / 86400) });
  }

  // ── 状态 chip(复用 .chip 变体,对齐 tasks.js statusChip)──────────────
  function statusChip(status) {
    const map = {
      pending:            { cls: 'chip',            label: t('taskDetail.status.pending'),   icon: '⌛' },
      running:            { cls: 'chip chip-green', label: t('taskDetail.status.running'),   icon: 'RN' },
      paused_user_action: { cls: 'chip chip-amber', label: t('taskDetail.status.paused'),    icon: '⏸' },
      completed:          { cls: 'chip chip-blue',  label: t('taskDetail.status.completed'), icon: '✓' },
      failed:             { cls: 'chip chip-red',   label: t('taskDetail.status.failed'),    icon: '✕' },
      cancelled:          { cls: 'chip',            label: t('taskDetail.status.cancelled'), icon: '⊘' },
    };
    const x = map[status] || { cls: 'chip', label: status || '?', icon: '' };
    return `<span class="${x.cls}"><span aria-hidden="true">${x.icon}</span><span>${escapeText(x.label)}</span></span>`;
  }
  function platLabel(p) {
    return ({ boss: 'Boss', linkedin: 'LinkedIn', indeed: 'Indeed' })[p] || (p || '?');
  }

  // ── 状态 ────────────────────────────────────────────────────────────
  const state = {
    taskId: null,
    task: null,            // 完整 task row(taskDetail 返回)
    activeTab: 'result',   // result | inputs | log | skipped
    timer: null,
  };

  let container = null;    // 全屏覆盖层根节点(动态创建,append 到 body)
  let els = {};            // 覆盖层内的常用节点引用

  // ── 构建覆盖层骨架(只建一次)───────────────────────────────────────
  function ensureContainer() {
    if (container) return;
    container = document.createElement('div');
    container.id = 'taskDetailContainer';
    container.className = 'js-container td-container';
    container.innerHTML = `
      <div class="td-topbar">
        <button class="td-back" id="tdBackBtn" type="button" title="${t('taskDetail.backTitle')}">${t('taskDetail.back')}</button>
        <span class="td-topbar-title" id="tdTitle">${t('taskDetail.title')}</span>
        <span id="tdStatusChip"></span>
      </div>
      <div class="td-meta" id="tdMeta"></div>
      <nav class="td-tabs" id="tdTabs">
        <button class="td-tab active" data-tab="result" type="button">${t('taskDetail.tab.result')}</button>
        <button class="td-tab" data-tab="inputs" type="button">${t('taskDetail.tab.inputs')}</button>
        <button class="td-tab" data-tab="log" type="button">${t('taskDetail.tab.log')}</button>
        <button class="td-tab" data-tab="skipped" id="tdTabSkipped" type="button" style="display:none">${t('taskDetail.tab.skipped')}</button>
      </nav>
      <div class="td-body" id="tdBody"></div>
      <div class="td-actions" id="tdActions" hidden></div>`;
    document.body.appendChild(container);

    els = {
      back:    container.querySelector('#tdBackBtn'),
      title:   container.querySelector('#tdTitle'),
      status:  container.querySelector('#tdStatusChip'),
      meta:    container.querySelector('#tdMeta'),
      tabs:    container.querySelector('#tdTabs'),
      tabSkippedBtn: container.querySelector('#tdTabSkipped'),
      body:    container.querySelector('#tdBody'),
      actions: container.querySelector('#tdActions'),
    };
    bindEvents();
  }

  // ── 拉数据 ──────────────────────────────────────────────────────────
  async function fetchDetail(taskId) {
    if (!api || !api.agentGw) return null;
    try {
      const data = await api.agentGw.taskDetail(taskId);
      return (data && data.task) || null;
    } catch (e) {
      console.warn('[task-detail] fetch failed', e);
      return null;
    }
  }

  // ── 渲染:头部 ──────────────────────────────────────────────────────
  function renderHeader(task) {
    if (els.title) els.title.textContent = task.title || task.template_id || t('taskDetail.title');
    if (els.status) els.status.innerHTML = statusChip(task.status);

    const startedAt = task.started_at || task.created_at;
    const isTerminal = ['completed', 'failed', 'cancelled'].includes(task.status);
    const rawDur = isTerminal
      ? fmtDuration(startedAt, task.completed_at || task.cancelled_at)
      : (task.status === 'running' ? `${fmtDuration(startedAt, null)}${t('taskDetail.duration.running')}` : '');

    if (els.meta) {
      els.meta.innerHTML = `
        <div class="td-meta-row">
          <span><span class="td-pf-dot td-pf-${escapeAttr(task.platform || '')}"></span>${escapeText(platLabel(task.platform))}</span>
          <span>${escapeText(task.template_id || '')}</span>
          <span>${escapeText(fmtTime(startedAt))}</span>
          ${rawDur ? `<span>${t('taskDetail.meta.duration', { dur: escapeText(rawDur) })}</span>` : ''}
          <span class="td-meta-id">#${escapeText(String(task.id))}</span>
        </div>
        ${task.error ? `<div class="td-error"> ${escapeText(task.error)}</div>` : ''}`;
    }

    // 跳过 tab:仅 skipped > 0 显示
    const skippedN = (task.skipped || []).length;
    if (els.tabSkippedBtn) {
      els.tabSkippedBtn.style.display = skippedN > 0 ? '' : 'none';
      els.tabSkippedBtn.innerHTML = `${t('taskDetail.tab.skipped')} <span class="td-tab-count">${skippedN}</span>`;
    }
  }

  // ── 渲染:底部操作条(取消 / 继续 —— 从 v2 卡片 confirm 迁来这里)───────
  function renderActions(task) {
    if (!els.actions) return;
    const isPaused = task.status === 'paused_user_action';
    const canCancel = task.status === 'running' || task.status === 'pending' || isPaused;
    if (!isPaused && !canCancel) {
      els.actions.hidden = true;
      els.actions.innerHTML = '';
      return;
    }
    let html = '';
    if (isPaused) {
      html += `<button class="btn primary td-act-resume" data-act="resume" type="button">${t('taskDetail.act.resume')}</button>`;
    }
    if (canCancel) {
      html += `<button class="btn danger td-act-cancel" data-act="cancel" type="button">${t('taskDetail.act.cancel')}</button>`;
    }
    els.actions.innerHTML = html;
    els.actions.hidden = false;
  }

  // ── 渲染:tab body ──────────────────────────────────────────────────
  function renderActiveTab() {
    if (!els.body || !state.task) return;
    let html = '';
    switch (state.activeTab) {
      case 'result':  html = renderResultTab(state.task);  break;
      case 'inputs':  html = renderInputsTab(state.task);   break;
      case 'log':     html = renderLogTab(state.task);      break;
      case 'skipped': html = renderSkippedTab(state.task);  break;
      default:        html = renderResultTab(state.task);   break;
    }
    els.body.innerHTML = html;
    if (els.tabs) {
      els.tabs.querySelectorAll('.td-tab').forEach((b) => {
        b.classList.toggle('active', b.dataset.tab === state.activeTab);
      });
    }
  }

  // 「结果」tab — 按 template_id 分支
  function renderResultTab(task) {
    const tpl = task.template_id || '';
    const artifacts = task.artifacts || {};
    const itemStates = task.item_states || {};
    const summary = (task.result_summary || '').trim();

    if (tpl === 'jobseeker:find_best_jobs') {
      return renderTopJobsResult(task, artifacts, itemStates, summary);
    }
    if (tpl === 'recruiter:hiring_pipeline') {
      return renderHiringPipelineResult(task, artifacts, itemStates, summary);
    }
    if (tpl === 'recruiter:inbox_triage') {
      return renderInboxTriageResult(task, artifacts, itemStates, summary);
    }
    // 兜底:纯文本 summary
    return `
      <div class="td-summary">
        ${summary ? escapeText(summary).replace(/\n/g, '<br>') : `<div class="td-empty">${t('taskDetail.result.noResult')}</div>`}
      </div>`;
  }

  // jobseeker:find_best_jobs → top_jobs 职位卡
  function renderTopJobsResult(task, artifacts, itemStates, summary) {
    const jobs = artifacts.top_jobs || [];
    const fetched = artifacts.fetched_count;
    const compared = Math.max(fetched || 0, jobs.length);
    const skipped = (task.skipped || []).length;
    const platformText = platLabel(task.platform || 'boss');
    const inputs = task.inputs || {};

    const headParts = [
      inputs.keyword || inputs.job_keyword || t('taskDetail.result.noKeyword'),
      inputs.city || '', inputs.country || '', inputs.location || '',
    ].filter(Boolean).map(escapeText).join(' · ');

    const headerHtml = `
      <div class="td-result-head">
        <div class="td-result-q">${headParts}</div>
        <div class="td-result-stats">
          ${compared ? `<span>${t('taskDetail.result.compared')}<b>${compared}</b></span>` : ''}
          ${jobs.length ? `<span>${t('taskDetail.result.topN')}<b>${jobs.length}</b></span>` : ''}
          ${skipped > 0 ? `<span class="td-stat-skip">${t('taskDetail.result.skipped', { n: skipped })}</span>` : ''}
        </div>
      </div>`;

    if (!jobs.length) {
      return headerHtml + emptyResultHtml(t('taskDetail.result.noJobs.title'), summary || t('taskDetail.result.noJobs.hint'));
    }

    const cards = jobs.map((j, idx) => {
      const itemId = String(j.job_id || `job-${idx}`);
      const st = itemStates[itemId] || {};
      const viewedTag = st.viewed_at
        ? `<span class="td-viewed">${t('taskDetail.result.viewedAt', { time: escapeText(fmtRelative(st.viewed_at)) })}</span>` : '';
      const detailRow = [j.city, j.experience, j.degree].filter(Boolean).map(escapeText).join(' · ');
      const jdRow = j.jd_excerpt ? `<div class="td-card-jd">${escapeText(j.jd_excerpt)}…</div>` : '';
      const failedTag = j.fetch_failed
        ? `<span class="td-card-warn" title="${t('taskDetail.result.jobMissingDetailTitle')}">${t('taskDetail.result.jobMissingDetail')}</span>` : '';
      const linkBtn = j.platform_url
        ? `<button class="btn ghost td-link-btn" type="button" data-url="${escapeAttr(j.platform_url)}">${t('taskDetail.result.viewOn', { platform: escapeText(platformText) })}</button>`
        : '';
      const detailBtn = `<button class="btn ghost td-detail-btn" type="button" data-job-title="${escapeAttr(j.title || '')}">${t('taskDetail.result.getDetail')}</button>`;
      return `
        <div class="dq-card td-card" data-item-id="${escapeAttr(itemId)}">
          <div class="td-card-row">
            <span class="dq-card-idx">${idx + 1}</span>
            <div class="dq-card-main">
              <div class="dq-card-title">
                <span class="dq-card-name">${escapeText(j.title || t('taskDetail.result.noTitle'))}</span>${failedTag}
              </div>
              <div class="dq-card-sub">${escapeText(j.company || '')}${j.salary ? ' · ' : ''}${j.salary ? `<b>${escapeText(j.salary)}</b>` : ''}</div>
              ${detailRow ? `<div class="td-card-meta">${detailRow}</div>` : ''}
              ${jdRow}
              <div class="td-card-actions">${detailBtn}${linkBtn}${viewedTag}</div>
            </div>
          </div>
        </div>`;
    }).join('');

    return headerHtml + `<div class="td-card-list">${cards}</div>`;
  }

  // recruiter:hiring_pipeline → contacted 候选人卡
  function renderHiringPipelineResult(task, artifacts, itemStates, summary) {
    const contacted = artifacts.contacted || [];
    const skipped = (task.skipped || []).length;
    const platformText = platLabel(task.platform || 'boss');
    const inputs = task.inputs || {};

    const headerHtml = `
      <div class="td-result-head">
        <div class="td-result-q">${escapeText(inputs.job_keyword || '')}</div>
        <div class="td-result-stats">
          <span>${t('taskDetail.result.contacted')}<b>${contacted.length}</b></span>
          ${skipped > 0 ? `<span class="td-stat-skip">${t('taskDetail.result.skipped', { n: skipped })}</span>` : ''}
        </div>
      </div>`;

    if (!contacted.length) {
      return headerHtml + emptyResultHtml(t('taskDetail.result.noCands.title'), summary || t('taskDetail.result.noCands.hint'));
    }

    const cards = contacted.map((c, idx) => {
      const itemId = String(c.geek_id || `c-${idx}`);
      const st = itemStates[itemId] || {};
      const viewedTag = st.viewed_at
        ? `<span class="td-viewed">${t('taskDetail.result.viewedAt', { time: escapeText(fmtRelative(st.viewed_at)) })}</span>` : '';
      const linkBtn = c.platform_url
        ? `<button class="btn ghost td-link-btn" type="button" data-url="${escapeAttr(c.platform_url)}">${t('taskDetail.result.openOn', { platform: escapeText(platformText) })}</button>`
        : '';
      return `
        <div class="dq-card td-card" data-item-id="${escapeAttr(itemId)}">
          <div class="td-card-row">
            <span class="dq-card-idx">${idx + 1}</span>
            <div class="dq-card-main">
              <div class="dq-card-title"><span class="dq-card-name">${escapeText(c.name || t('taskDetail.result.noName'))}</span></div>
              <div class="dq-card-sub">${escapeText(c.result || t('taskDetail.result.sent'))}</div>
              ${(linkBtn || viewedTag) ? `<div class="td-card-actions">${linkBtn}${viewedTag}</div>` : ''}
            </div>
          </div>
        </div>`;
    }).join('');

    return headerHtml + `<div class="td-card-list">${cards}</div>`;
  }

  // recruiter:inbox_triage → 按 job 分组的 candidates
  function renderInboxTriageResult(task, artifacts, itemStates, summary) {
    const cands = artifacts.candidates || [];
    const jobsScanned = artifacts.jobs_scanned;
    const jobsWithPending = artifacts.jobs_with_pending;
    const platformText = platLabel(task.platform || 'indeed');

    const headerHtml = `
      <div class="td-result-head">
        <div class="td-result-q">${escapeText(platformText)} inbox</div>
        <div class="td-result-stats">
          ${typeof jobsScanned === 'number' ? `<span>${t('taskDetail.result.jobsScanned')}<b>${jobsScanned}</b>${t('taskDetail.result.jobsScannedSuffix')}</span>` : ''}
          ${typeof jobsWithPending === 'number' ? `<span>${t('taskDetail.result.pending')}<b>${jobsWithPending}</b></span>` : ''}
          <span>${t('taskDetail.result.total')}<b>${cands.length}</b>${t('taskDetail.result.totalSuffix')}</span>
        </div>
      </div>`;

    if (!cands.length) {
      return headerHtml + emptyResultHtml(t('taskDetail.result.inboxClear.title'), summary || t('taskDetail.result.inboxClear.hint'));
    }

    // 按 job_title 分组
    const byJob = {};
    cands.forEach((c) => {
      const k = c.job_title || '(unknown job)';
      (byJob[k] = byJob[k] || []).push(c);
    });

    const groups = Object.keys(byJob).map((job) => {
      const jobList = byJob[job];
      const cards = jobList.map((c) => {
        const itemId = String(c.submission_uuid || c.legacy_id || c.name || '');
        const st = itemStates[itemId] || {};
        const viewedTag = st.viewed_at
          ? `<span class="td-viewed">${t('taskDetail.result.viewedAt', { time: escapeText(fmtRelative(st.viewed_at)) })}</span>` : '';
        const yrsText = c.years ? t('taskDetail.result.yearsExp', { n: c.years }) : '';
        const meta = [c.current_role, yrsText]
          .filter(Boolean).map(escapeText).join(' · ');
        const linkBtn = c.platform_url
          ? `<button class="btn ghost td-link-btn" type="button" data-url="${escapeAttr(c.platform_url)}">${t('taskDetail.result.viewOn', { platform: escapeText(platformText) })}</button>`
          : '';
        return `
          <div class="dq-card td-card" data-item-id="${escapeAttr(itemId)}">
            <div class="td-card-row">
              <div class="dq-card-main">
                <div class="dq-card-title"><span class="dq-card-name">${escapeText(c.name || '(unknown)')}</span></div>
                ${meta ? `<div class="dq-card-sub">${meta}</div>` : ''}
                ${(linkBtn || viewedTag) ? `<div class="td-card-actions">${linkBtn}${viewedTag}</div>` : ''}
              </div>
            </div>
          </div>`;
      }).join('');
      return `
        <div class="td-group">
          <div class="td-group-head">▸ ${escapeText(job)} <span class="td-group-count">(${jobList.length})</span></div>
          <div class="td-card-list">${cards}</div>
        </div>`;
    }).join('');

    return headerHtml + groups;
  }

  function emptyResultHtml(title, hint) {
    return `
      <div class="td-empty-result">
        <div class="td-empty-title">${escapeText(title)}</div>
        <div class="td-empty-hint">${escapeText(hint || '')}</div>
      </div>`;
  }

  // 「输入」tab
  function renderInputsTab(task) {
    const inputs = task.inputs || {};
    const keys = Object.keys(inputs);
    const rows = keys.length
      ? keys.map((k) => `<tr><td>${escapeText(k)}</td><td>${escapeText(String(inputs[k] == null ? '' : inputs[k]))}</td></tr>`).join('')
      : `<tr><td colspan="2" class="td-empty">${t('taskDetail.inputs.noParams')}</td></tr>`;
    const debugRows = `
      <tr><td>task_id</td><td>${escapeText(String(task.id))}</td></tr>
      <tr><td>template_id</td><td>${escapeText(task.template_id || '')}</td></tr>
      <tr><td>role</td><td>${escapeText(task.role || '')}</td></tr>
      <tr><td>platform</td><td>${escapeText(task.platform || '')}</td></tr>
      <tr><td>created_at</td><td>${escapeText(fmtTime(task.created_at))}</td></tr>
      <tr><td>started_at</td><td>${escapeText(fmtTime(task.started_at) || '—')}</td></tr>
      <tr><td>completed_at</td><td>${escapeText(fmtTime(task.completed_at) || '—')}</td></tr>`;
    return `
      <div class="td-section-title">${t('taskDetail.inputs.title')}</div>
      <table class="td-kv">${rows}</table>
      <div class="td-section-title">${t('taskDetail.inputs.debug')}</div>
      <table class="td-kv td-kv-debug">${debugRows}</table>`;
  }

  // 「日志」tab — timestamps 时间线 + 当前 progress 快照
  function renderLogTab(task) {
    const p = task.progress || {};
    const items = [
      { label: t('taskDetail.log.created'),   iso: task.created_at },
      { label: t('taskDetail.log.started'),   iso: task.started_at },
      { label: t('taskDetail.log.paused'),    iso: task.paused_at },
      { label: t('taskDetail.log.resumed'),   iso: task.resumed_at },
      { label: t('taskDetail.log.completed'), iso: task.completed_at },
      { label: t('taskDetail.log.cancelled'), iso: task.cancelled_at },
    ].filter((x) => x.iso);
    const timeline = items.length
      ? items.map((x) => `
          <div class="td-log-row">
            <span class="td-log-time">${escapeText(fmtTime(x.iso))}</span>
            <span class="td-log-label">${escapeText(x.label)}</span>
          </div>`).join('')
      : `<div class="td-empty">${t('taskDetail.log.noTimeline')}</div>`;

    const progressRow = (p.current != null && p.total != null)
      ? `<div class="td-section-title">${t('taskDetail.log.progress')}</div>
         <div class="td-log-progress">
           ${escapeText(String(p.phase || ''))} ${escapeText(String(p.step_id || ''))} · ${p.current}/${p.total}
         </div>`
      : '';

    return `
      <div class="td-section-title">${t('taskDetail.log.timeline')}</div>
      <div class="td-log-timeline">${timeline}</div>
      ${progressRow}`;
  }

  // 「跳过」tab
  function renderSkippedTab(task) {
    const skipped = task.skipped || [];
    if (!skipped.length) return `<div class="td-empty">${t('taskDetail.skipped.noItems')}</div>`;
    const rows = skipped.map((s) => `
      <div class="td-skip-row">
        <span class="td-skip-idx">#${escapeText(String(s.idx == null ? '' : s.idx))}</span>
        <span class="td-skip-item">${escapeText(s.item_summary || t('taskDetail.skipped.noSummary'))}</span>
        <span class="td-skip-reason">${escapeText(s.reason || '')}</span>
        ${s.signal ? `<span class="td-skip-signal">${escapeText(s.signal)}</span>` : ''}
      </div>`).join('');
    return `<div class="td-skip-list">${rows}</div>`;
  }

  // ── 事件 ────────────────────────────────────────────────────────────
  function bindEvents() {
    // 返回
    els.back && els.back.addEventListener('click', close);

    // tab 切换
    els.tabs && els.tabs.addEventListener('click', (e) => {
      const tab = e.target.closest('.td-tab[data-tab]');
      if (!tab) return;
      state.activeTab = tab.dataset.tab;
      renderActiveTab();
    });

    // body 内:获取详情 / 平台跳转链接
    els.body && els.body.addEventListener('click', (e) => {
      // 「获取详情」→ 关闭详情覆盖层，切到 AI 对话并预填拉取 prompt
      const detailBtn = e.target.closest('.td-detail-btn');
      if (detailBtn) {
        const title = detailBtn.dataset.jobTitle || '';
        const prompt = title
          ? t('taskDetail.detailPromptTitle', { title })
          : t('taskDetail.detailPromptGeneric');
        close();
        try { window.DQ.app?.switchTab?.('chat'); } catch (_) {}
        try { window.DQ.chat?.prefill?.(prompt); } catch (_) {}
        return;
      }
      const linkBtn = e.target.closest('.td-link-btn[data-url]');
      if (!linkBtn) return;
      const url = linkBtn.dataset.url;
      if (!url) return;
      if (chrome && chrome.tabs && chrome.tabs.create) {
        chrome.tabs.create({ url, active: true });
      } else {
        window.open(url, '_blank', 'noopener');
      }
    });

    // 底部操作:取消 / 继续
    els.actions && els.actions.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-act]');
      if (!btn || !state.task) return;
      const act = btn.dataset.act;
      const id = state.task.id;
      btn.disabled = true;
      try {
        if (act === 'cancel') {
          if (!confirm(t('taskDetail.act.cancelConfirm'))) {
            btn.disabled = false;
            return;
          }
          await api.agentGw.taskCancel(id);
        } else if (act === 'resume') {
          await api.agentGw.taskResume(id);
        }
        await refresh();                          // 刷新覆盖层
        try { window.DQ.tasks && window.DQ.tasks.refresh && window.DQ.tasks.refresh(); }
        catch (_) {}                              // 同步刷新底层列表
      } catch (err) {
        btn.disabled = false;
        const prefix = act === 'cancel' ? t('taskDetail.act.cancelErr') : t('taskDetail.act.resumeErr');
        alert(prefix + ((err && err.message) || err));
      }
    });

    // Esc 关闭
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && container && container.classList.contains('open')) {
        close();
      }
    });
  }

  // ── 轮询 ────────────────────────────────────────────────────────────
  function startPolling() {
    stopPolling();
    state.timer = setInterval(refresh, POLL_MS);
  }
  function stopPolling() {
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  // ── 公开 API ────────────────────────────────────────────────────────
  async function open(taskId) {
    ensureContainer();
    state.taskId = taskId;
    state.activeTab = 'result';
    state.task = null;

    if (els.title) els.title.textContent = t('taskDetail.loading');
    if (els.status) els.status.innerHTML = '';
    if (els.meta) els.meta.innerHTML = '';
    if (els.tabSkippedBtn) els.tabSkippedBtn.style.display = 'none';
    if (els.actions) { els.actions.hidden = true; els.actions.innerHTML = ''; }
    if (els.body) els.body.innerHTML = `<div class="td-loading"><span class="spinner"></span> ${t('taskDetail.loading')}</div>`;
    container.classList.add('open');

    const task = await fetchDetail(taskId);
    if (!container.classList.contains('open')) return;   // 加载期间已被关闭
    if (!task) {
      if (els.body) els.body.innerHTML = `<div class="td-empty">${t('taskDetail.notFound')}</div>`;
      return;
    }
    state.task = task;
    renderHeader(task);
    renderActions(task);
    renderActiveTab();

    // 运行中 / 等待中 → 5s 轮询
    if (['running', 'paused_user_action', 'pending'].includes(task.status)) {
      startPolling();
    }
  }

  function close() {
    if (container) container.classList.remove('open');
    state.taskId = null;
    state.task = null;
    stopPolling();
  }

  async function refresh() {
    if (!state.taskId) return;
    const task = await fetchDetail(state.taskId);
    if (!task || task.id !== state.taskId) return;
    state.task = task;
    renderHeader(task);
    renderActions(task);
    renderActiveTab();
    // 终态 → 停止轮询
    if (['completed', 'failed', 'cancelled'].includes(task.status)) {
      stopPolling();
    }
  }

  window.DQ = window.DQ || {};
  window.DQ.taskDetail = { open, close, refresh };

  console.log('[task-detail] ready');

  /*
   * ── v2 → v3 降级说明 ──────────────────────────────────────────────
   * 1. AI 评估按钮:v2 每张职位卡有「🤖 AI 评估」→ window.DQ.evaluation.open。
   *    v3 sidepanel 无 evaluation 模块,故移除该按钮。job 评估在 v3 走「AI 对话」
   *    tab 的 job_list_card 动作,不在任务详情内。
   * 2. viewed_at 标记:v2 点平台链接会 PATCH /tasks/{id}/items/{itemId} 写
   *    viewed_at,卡片显示「✓ 已查看」。v3 shared/api.js 未暴露该 PATCH 方法
   *    (且改后端属本次范围外),故点链接仅打开页面、不写状态。若后端 row 里
   *    已带 item_states.viewed_at(历史数据),这里仍会渲染「✓ 查看」标签。
   * 3. risk_signals 引导文案:暂停态的处理指引在 tasks.js 卡片 / 这里的状态
   *    chip 用「暂停 · 等你」表达;底部「我已处理完 · 继续」按钮即 resume。
   */
})();
