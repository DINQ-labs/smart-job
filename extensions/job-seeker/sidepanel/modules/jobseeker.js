/*
 * modules/jobseeker.js — 求职者工作台.
 *
 * 登录后自动检查:
 *   1. 是否有简历 → 没有则引导上传
 *   2. 是否设置求职偏好 → 没有则引导设置
 *   3. 完成后进入求职主页
 *
 * 布局(参考截图):
 *   - 顶部:操作按钮区(浏览职位/AI分析/投递/收藏/批量)
 *   - 左右卡片:我的简历 + 求职偏好(可展开/收起)
 *   - 下方:推荐职位列表
 */

(function () {
  'use strict';

  const { NAMES, on, emit } = window.DQ.events;
  const api = window.DQ?.api;

  // ── 状态 ─────────────────────────────────────────────────────────
  const state = {
    // 展开状态: 'none' | 'resume' | 'preferences'
    expanded: 'none',
    // 简历状态
    hasResume: false,
    resumeFileName: null,
    resumeUploadedAt: null,
    // 偏好状态
    hasPreferences: false,
    preferences: null,
    // 编辑状态
    editing: false,
    // UI 状态
    busy: false,
    error: '',
    info: '',
  };

  let container = null;

  // ── 工具函数 ─────────────────────────────────────────────────────
  function escapeText(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function formatDate(isoString) {
    if (!isoString) return '';
    const d = new Date(isoString);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  // ── 渲染:顶部操作按钮 ────────────────────────────────────────────
  function renderActionBar() {
    return `
      <div class="js-action-bar">
        <button class="js-close-btn" id="jsCloseBtn" title="${t('jobseeker.actionBarClose')}">✕</button>
        <div style="flex:1"></div>
        <button class="js-action-btn" data-action="browse" title="${t('jobseeker.actionBrowseTitle')}">
          <span class="js-action-ico">🔍</span>
          <span class="js-action-label">${t('jobseeker.actionBrowse')}</span>
        </button>
        <button class="js-action-btn" data-action="analyze" title="${t('jobseeker.actionAnalyzeTitle')}">
          <span class="js-action-ico">🤖</span>
          <span class="js-action-label">${t('jobseeker.actionAnalyze')}</span>
        </button>
        <button class="js-action-btn" data-action="apply" title="${t('jobseeker.actionApplyTitle')}">
          <span class="js-action-ico">📤</span>
          <span class="js-action-label">${t('jobseeker.actionApply')}</span>
        </button>
        <button class="js-action-btn" data-action="bookmark" title="${t('jobseeker.actionBookmarkTitle')}">
          <span class="js-action-ico">⭐</span>
          <span class="js-action-label">${t('jobseeker.actionBookmark')}</span>
        </button>
        <button class="js-action-btn" data-action="batch" title="${t('jobseeker.actionBatchTitle')}">
          <span class="js-action-ico">📋</span>
          <span class="js-action-label">${t('jobseeker.actionBatchLabel')}</span>
        </button>
      </div>
    `;
  }

  // ── 渲染:简历卡片 ────────────────────────────────────────────────
  function renderResumeCard() {
    const isExpanded = state.expanded === 'resume';

    if (!state.hasResume) {
      // 未上传简历状态
      return `
        <div class="js-card" id="jsResumeCard">
          <div class="js-card-header">
            <div class="js-card-title-row">
              <span class="js-card-ico">📄</span>
              <span class="js-card-title">${t('jobseeker.resumeTitle')}</span>
              <span class="js-card-badge js-card-badge-empty">${t('jobseeker.resumeNotUploaded')}</span>
            </div>
          </div>
          <div class="js-card-body">
            <div class="js-upload-area" id="jsUploadArea">
              <input type="file" id="jsResumeInput" accept=".pdf,.doc,.docx" hidden>
              <div class="js-upload-placeholder">
                <span class="js-upload-ico">📎</span>
                <div class="js-upload-text">${t('jobseeker.uploadClick')}</div>
                <div class="js-upload-hint">${t('jobseeker.uploadHint')}</div>
              </div>
            </div>
            ${state.error ? `<div class="js-alert js-alert-error">${escapeText(state.error)}</div>` : ''}
            ${state.info ? `<div class="js-alert js-alert-info">${escapeText(state.info)}</div>` : ''}
          </div>
        </div>
      `;
    }

    // 已上传简历状态
    return `
      <div class="js-card" id="jsResumeCard">
        <div class="js-card-header" data-card="resume">
          <div class="js-card-title-row">
            <span class="js-card-ico">📄</span>
            <span class="js-card-title">${t('jobseeker.resumeTitle')}</span>
            <span class="js-card-badge js-card-badge-success">${t('jobseeker.resumeUploaded')}</span>
          </div>
          <span class="js-card-arrow ${isExpanded ? 'expanded' : ''}">▼</span>
        </div>
        ${isExpanded ? `
          <div class="js-card-body">
            <div class="js-resume-file">
              <span class="js-resume-ico">📄</span>
              <div class="js-resume-info">
                <div class="js-resume-name">${escapeText(state.resumeFileName)}</div>
                <div class="js-resume-date">${formatDate(state.resumeUploadedAt)}</div>
              </div>
            </div>
            <div class="js-card-actions">
              <button class="btn secondary" id="jsChangeResumeBtn" ${state.busy ? 'disabled' : ''}>
                ${state.busy ? t('jobseeker.uploading') : t('jobseeker.changeResume')}
              </button>
            </div>
            ${state.error ? `<div class="js-alert js-alert-error">${escapeText(state.error)}</div>` : ''}
          </div>
        ` : ''}
      </div>
    `;
  }

  // ── 渲染:求职偏好卡片 ────────────────────────────────────────────
  function renderPreferencesCard() {
    const isExpanded = state.expanded === 'preferences';
    const isEditing = isExpanded && state.editing;
    const prefs = state.preferences || {};

    if (!state.hasPreferences) {
      // 未设置偏好状态
      return `
        <div class="js-card" id="jsPrefsCard">
          <div class="js-card-header">
            <div class="js-card-title-row">
              <span class="js-card-ico">🎯</span>
              <span class="js-card-title">${t('jobseeker.prefsTitle')}</span>
              <span class="js-card-badge js-card-badge-empty">${t('jobseeker.prefsNotSet')}</span>
            </div>
          </div>
          <div class="js-card-body">
            <p class="js-card-desc">${t('jobseeker.prefsDesc')}</p>
            <button class="btn primary" id="jsSetPrefsBtn">${t('jobseeker.prefsSetBtn')}</button>
          </div>
        </div>
      `;
    }

    // 已设置偏好状态
    return `
      <div class="js-card" id="jsPrefsCard">
        <div class="js-card-header" data-card="preferences">
          <div class="js-card-title-row">
            <span class="js-card-ico">🎯</span>
            <span class="js-card-title">${t('jobseeker.prefsTitle')}</span>
            <span class="js-card-badge js-card-badge-success">${t('jobseeker.prefsSet')}</span>
          </div>
          <span class="js-card-arrow ${isExpanded ? 'expanded' : ''}">▼</span>
        </div>
        ${isExpanded ? `
          <div class="js-card-body">
            ${isEditing ? renderPreferencesForm() : renderPreferencesSummary()}
          </div>
        ` : ''}
      </div>
    `;
  }

  // 偏好摘要视图(收起状态)
  function renderPreferencesSummary() {
    const prefs = state.preferences || {};
    const jobTypeMap = {fulltime: t('jobseeker.prefLabelFulltime'), parttime: t('jobseeker.prefLabelParttime'), remote: t('jobseeker.prefLabelRemote'), contract: t('jobseeker.prefLabelContract')};
    return `
      <div class="js-prefs-summary">
        ${prefs.jobTitle ? `<div class="js-prefs-row"><span class="js-prefs-label">${t('jobseeker.fieldRole')}</span><span class="js-prefs-value">${escapeText(prefs.jobTitle)}</span></div>` : ''}
        ${prefs.location ? `<div class="js-prefs-row"><span class="js-prefs-label">${t('jobseeker.fieldCity')}</span><span class="js-prefs-value">${escapeText(prefs.location)}</span></div>` : ''}
        ${(prefs.salaryMin || prefs.salaryMax) ? `
          <div class="js-prefs-row">
            <span class="js-prefs-label">${t('jobseeker.fieldSalaryMin')} / ${t('jobseeker.fieldSalaryMax')}</span>
            <span class="js-prefs-value">${prefs.salaryMin || '?'}-${prefs.salaryMax || '?'}k</span>
          </div>
        ` : ''}
        ${prefs.jobType?.length ? `
          <div class="js-prefs-row">
            <span class="js-prefs-label">${t('jobseeker.fieldJobType')}</span>
            <span class="js-prefs-value">${prefs.jobType.map(tp => jobTypeMap[tp] || tp).join('、')}</span>
          </div>
        ` : ''}
        ${prefs.industry ? `<div class="js-prefs-row"><span class="js-prefs-label">${t('jobseeker.fieldIndustry')}</span><span class="js-prefs-value">${escapeText(prefs.industry)}</span></div>` : ''}
        ${prefs.companySize ? `
          <div class="js-prefs-row">
            <span class="js-prefs-label">${t('jobseeker.fieldCompanySize')}</span>
            <span class="js-prefs-value">${prefs.companySize}</span>
          </div>
        ` : ''}
      </div>
      <div class="js-card-actions">
        <button class="btn primary" id="jsEditPrefsBtn">${t('jobseeker.prefsEditBtn')}</button>
      </div>
      ${state.error ? `<div class="js-alert js-alert-error">${escapeText(state.error)}</div>` : ''}
      ${state.info ? `<div class="js-alert js-alert-info">${escapeText(state.info)}</div>` : ''}
    `;
  }

  // 偏好编辑表单
  function renderPreferencesForm() {
    const prefs = state.preferences || {};
    return `
      <form id="jsPrefsForm" class="js-prefs-form">
        <div class="js-form-row">
          <div class="js-form-field">
            <label class="js-label">${t('jobseeker.fieldRole')}</label>
            <input type="text" class="js-input" id="jsJobTitle" placeholder="${t('jobseeker.placeholderRole')}"
                   value="${escapeText(prefs.jobTitle || '')}">
          </div>
          <div class="js-form-field">
            <label class="js-label">${t('jobseeker.fieldCity')}</label>
            <input type="text" class="js-input" id="jsLocation" placeholder="${t('jobseeker.placeholderCity')}"
                   value="${escapeText(prefs.location || '')}">
          </div>
        </div>

        <div class="js-form-row">
          <div class="js-form-field">
            <label class="js-label">${t('jobseeker.fieldSalaryMin')}</label>
            <input type="number" class="js-input" id="jsSalaryMin" placeholder="${t('jobseeker.placeholderSalaryMin')}"
                   value="${prefs.salaryMin || ''}" min="0">
          </div>
          <div class="js-form-field">
            <label class="js-label">${t('jobseeker.fieldSalaryMax')}</label>
            <input type="number" class="js-input" id="jsSalaryMax" placeholder="${t('jobseeker.placeholderSalaryMax')}"
                   value="${prefs.salaryMax || ''}" min="0">
          </div>
        </div>

        <div class="js-form-field">
          <label class="js-label">${t('jobseeker.fieldJobType')}</label>
          <div class="js-checkbox-group">
            <label class="js-checkbox"><input type="checkbox" name="jobType" value="fulltime" ${prefs.jobType?.includes('fulltime') ? 'checked' : ''}><span>${t('jobseeker.jobTypeFulltime')}</span></label>
            <label class="js-checkbox"><input type="checkbox" name="jobType" value="parttime" ${prefs.jobType?.includes('parttime') ? 'checked' : ''}><span>${t('jobseeker.jobTypeParttime')}</span></label>
            <label class="js-checkbox"><input type="checkbox" name="jobType" value="remote" ${prefs.jobType?.includes('remote') ? 'checked' : ''}><span>${t('jobseeker.jobTypeRemote')}</span></label>
            <label class="js-checkbox"><input type="checkbox" name="jobType" value="contract" ${prefs.jobType?.includes('contract') ? 'checked' : ''}><span>${t('jobseeker.jobTypeContract')}</span></label>
          </div>
        </div>

        <div class="js-form-row">
          <div class="js-form-field">
            <label class="js-label">${t('jobseeker.fieldIndustry')}</label>
            <input type="text" class="js-input" id="jsIndustry" placeholder="${t('jobseeker.placeholderIndustry')}"
                   value="${escapeText(prefs.industry || '')}">
          </div>
          <div class="js-form-field">
            <label class="js-label">${t('jobseeker.fieldCompanySize')}</label>
            <select class="js-input" id="jsCompanySize">
              <option value="">${t('jobseeker.companySizeNoLimit')}</option>
              <option value="1-20" ${prefs.companySize === '1-20' ? 'selected' : ''}>${t('jobseeker.companySize1')}</option>
              <option value="21-100" ${prefs.companySize === '21-100' ? 'selected' : ''}>${t('jobseeker.companySize2')}</option>
              <option value="101-500" ${prefs.companySize === '101-500' ? 'selected' : ''}>${t('jobseeker.companySize3')}</option>
              <option value="501-1000" ${prefs.companySize === '501-1000' ? 'selected' : ''}>${t('jobseeker.companySize4')}</option>
              <option value="1000+" ${prefs.companySize === '1000+' ? 'selected' : ''}>${t('jobseeker.companySize5')}</option>
            </select>
          </div>
        </div>

        <div class="js-card-actions">
          <button class="btn ghost" id="jsCancelPrefsBtn">${t('common.cancel')}</button>
          <button class="btn primary" type="submit" ${state.busy ? 'disabled' : ''}>
            ${state.busy ? t('jobseeker.savingPrefs') : t('jobseeker.savePrefs')}
          </button>
        </div>
      </form>
    `;
  }

  // ── 渲染:主内容区(推荐职位)─────────────────────────────────────
  function renderMainContent() {
    const hasBoth = state.hasResume && state.hasPreferences;

    if (!hasBoth) {
      // 未完成设置的提示
      const missing = [];
      if (!state.hasResume) missing.push(t('jobseeker.uploadResumeAction'));
      if (!state.hasPreferences) missing.push(t('jobseeker.setPrefsAction'));

      return `
        <div class="js-main-content">
          <div class="js-empty-state">
            <span class="js-empty-ico">📋</span>
            <div class="js-empty-title">${t('jobseeker.emptyTitle')}</div>
            <div class="js-empty-desc">
              ${t('jobseeker.emptyDesc', { missing: missing.join('、') })}
            </div>
          </div>
        </div>
      `;
    }

    // 已完成设置，显示职位推荐
    return `
      <div class="js-main-content">
        <div class="js-section-header">
          <h3 class="js-section-title">${t('jobseeker.recommendTitle')}</h3>
          <button class="js-refresh-btn" id="jsRefreshBtn">${t('common.refresh')}</button>
        </div>
        <div class="js-job-list" id="jsJobList">
          <div class="js-empty-state">
            <span class="js-empty-ico">🔍</span>
            <div class="js-empty-title">${t('jobseeker.aiAnalyzing')}</div>
            <div class="js-empty-desc">
              ${t('jobseeker.aiAnalyzingDesc')}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  // ── 主渲染函数 ─────────────────────────────────────────────────────
  function render() {
    if (!container) return;

    container.innerHTML = `
      <div class="js-page">
        <!-- 顶部操作按钮 -->
        ${renderActionBar()}

        <!-- 左右卡片:简历 + 偏好 -->
        <div class="js-cards-row">
          ${renderResumeCard()}
          ${renderPreferencesCard()}
        </div>

        <!-- 主内容区 -->
        ${renderMainContent()}
      </div>
    `;

    bind();
  }

  // ── 事件绑定 ───────────────────────────────────────────────────────
  function bind() {
    // 关闭按钮
    container.querySelector('#jsCloseBtn')?.addEventListener('click', () => {
      window.DQ.app?.showJobSeekerWorkspace(false);
    });

    // 顶部操作按钮
    container.querySelectorAll('.js-action-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.dataset.action;
        handleAction(action);
      });
    });

    // 卡片头部展开/收起
    container.querySelectorAll('.js-card-header[data-card]').forEach(header => {
      header.addEventListener('click', () => {
        const card = header.dataset.card;
        if (state.expanded === card) {
          state.expanded = 'none';
          state.editing = false;
        } else {
          state.expanded = card;
          state.editing = false;
        }
        render();
      });
    });

    // 简历上传
    const uploadArea = container.querySelector('#jsUploadArea');
    const fileInput = container.querySelector('#jsResumeInput');
    const changeResumeBtn = container.querySelector('#jsChangeResumeBtn');

    uploadArea?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', handleFileSelect);
    changeResumeBtn?.addEventListener('click', () => fileInput?.click());

    // 拖拽上传
    if (uploadArea && !state.hasResume) {
      uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('dragover');
      });
      uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('dragover');
      });
      uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        const file = e.dataTransfer?.files?.[0];
        if (file) handleFileUpload(file);
      });
    }

    // 偏好设置
    const setPrefsBtn = container.querySelector('#jsSetPrefsBtn');
    const editPrefsBtn = container.querySelector('#jsEditPrefsBtn');
    const cancelPrefsBtn = container.querySelector('#jsCancelPrefsBtn');
    const prefsForm = container.querySelector('#jsPrefsForm');

    setPrefsBtn?.addEventListener('click', () => {
      state.expanded = 'preferences';
      state.editing = true;
      render();
    });

    editPrefsBtn?.addEventListener('click', () => {
      state.editing = true;
      render();
    });

    cancelPrefsBtn?.addEventListener('click', () => {
      state.editing = false;
      render();
    });

    prefsForm?.addEventListener('submit', (e) => {
      e.preventDefault();
      savePreferences();
    });

    // 刷新按钮
    container.querySelector('#jsRefreshBtn')?.addEventListener('click', () => {
      // TODO: 重新加载职位推荐
    });
  }

  // ── 处理函数 ───────────────────────────────────────────────────────
  async function handleFileSelect(e) {
    const file = e.target.files?.[0];
    if (file) await handleFileUpload(file);
  }

  async function handleFileUpload(file) {
    const maxSize = 10 * 1024 * 1024; // 10MB
    if (file.size > maxSize) {
      state.error = t('jobseeker.errFileSize');
      state.info = '';
      render();
      return;
    }

    const validTypes = ['application/pdf', 'application/msword',
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
    if (!validTypes.includes(file.type)) {
      state.error = t('jobseeker.errFileType');
      state.info = '';
      render();
      return;
    }

    state.busy = true;
    state.error = '';
    state.info = '';
    render();

    try {
      // 真实上传到 agent-gateway /resume/upload(后台异步解析)
      await api.agentGw.resumeUpload(file, file.name);

      state.hasResume = true;
      state.resumeFileName = file.name;
      state.resumeUploadedAt = new Date().toISOString();
      state.busy = false;

      // 保存到 storage
      await chrome.storage.local.set({
        jobseeker: {
          resume: { uploaded: true, fileName: file.name, uploadedAt: state.resumeUploadedAt },
          preferences: state.preferences,
        },
      });

      render();
    } catch (err) {
      state.busy = false;
      state.error = t('jobseeker.uploadFail') + (err.message || err);
      render();
    }
  }

  async function savePreferences() {
    const jobTitle = document.getElementById('jsJobTitle')?.value?.trim();
    if (!jobTitle) {
      state.error = t('jobseeker.errRequireRole');
      render();
      return;
    }

    state.preferences = {
      jobTitle,
      location: document.getElementById('jsLocation')?.value?.trim() || '',
      salaryMin: document.getElementById('jsSalaryMin')?.value || '',
      salaryMax: document.getElementById('jsSalaryMax')?.value || '',
      jobType: Array.from(document.querySelectorAll('input[name="jobType"]:checked'))
                  .map(el => el.value),
      industry: document.getElementById('jsIndustry')?.value?.trim() || '',
      companySize: document.getElementById('jsCompanySize')?.value || '',
    };

    state.busy = true;
    state.error = '';
    state.info = '';
    render();

    try {
      // 真实保存到 agent-gateway /user-preferences/{uid}
      // 工作台表单字段较丰富,映射成后端的 job_role/city/salary_range/notes
      const p = state.preferences;
      const platform = window.DQ?.state?.platform || 'boss';
      const salaryRange = (p.salaryMin || p.salaryMax)
        ? `${p.salaryMin || ''}-${p.salaryMax || ''}k`
        : '';
      const noteParts = [];
      if (p.jobType?.length) noteParts.push('工作类型:' + p.jobType.join('/'));
      if (p.industry) noteParts.push('行业:' + p.industry);
      if (p.companySize) noteParts.push('公司规模:' + p.companySize);
      await api.agentGw.savePreferences(platform, {
        job_role: p.jobTitle,
        city: p.location || '',
        salary_range: salaryRange,
        notes: noteParts.join(' / '),
      });

      state.hasPreferences = true;
      state.busy = false;
      state.editing = false;

      // 保存到 storage
      await chrome.storage.local.set({
        jobseeker: {
          resume: { uploaded: state.hasResume, fileName: state.resumeFileName },
          preferences: state.preferences,
        },
      });

      state.info = t('jobseeker.saveOk');
      render();
    } catch (err) {
      state.busy = false;
      state.error = t('jobseeker.saveFail') + (err.message || err);
      render();
    }
  }

  async function handleAction(action) {
    switch (action) {
      case 'browse':
        // 切换到任务 tab 并刷新职位列表
        emit(NAMES.TAB_CHANGED, { tab: 'tasks' });
        break;
      case 'analyze':
        // 切换到 AI tab 并预填充分析请求
        emit(NAMES.TAB_CHANGED, { tab: 'chat' });
        break;
      case 'apply':
        // 触发投递操作
        alert(t('jobseeker.actionApplyDev'));
        break;
      case 'bookmark':
        // 触发收藏操作
        emit(NAMES.TAB_CHANGED, { tab: 'bookmarks' });
        break;
      case 'batch':
        // 打开批量操作面板
        alert(t('jobseeker.actionBatch'));
        break;
    }
  }

  // ── 初始化 ───────────────────────────────────────────────────────
  async function init() {
    try {
      const r = await chrome.storage.local.get(['jobseeker', 'user']);
      const js = r?.jobseeker || {};
      const user = r?.user || {};

      // 只有求职者角色才加载此页面
      if (user.role !== 'jobseeker') return;

      // 加载状态
      state.hasResume = js?.resume?.uploaded || false;
      state.resumeFileName = js?.resume?.fileName || null;
      state.resumeUploadedAt = js?.resume?.uploadedAt || null;
      state.hasPreferences = !!js?.preferences;
      state.preferences = js?.preferences || null;
    } catch (err) {
      console.error('JobSeeker init error:', err);
    }
  }

  // ── 打开/关闭 ─────────────────────────────────────────────────────
  async function open() {
    if (container) {
      container.classList.add('open');
      await init();
      render();
      return;
    }
    await init();
    container = document.createElement('div');
    container.id = 'jobseekerContainer';
    container.className = 'js-container';
    document.body.appendChild(container);
    render();
  }

  function close() {
    if (container) {
      container.classList.remove('open');
    }
  }

  // 监听事件
  //
  // 注意:工作台不再随 ROLE_CHANGED 自动弹出。求职引导(onboarding Step 5a/5b/5c)
  // 已接管简历/偏好的采集,引导结束直接落到 AI 对话 tab。这里 jobseeker 角色只
  // 刷新内部状态(供 dq:jobseeker-open 手动打开时复用),不再 open()。
  on(NAMES.ROLE_CHANGED, async (e) => {
    if (e.detail?.role === 'jobseeker') {
      await init();
    } else {
      close();
      window.DQ.app?.showJobSeekerWorkspace(false);
    }
  });

  // 启动时检查角色:只刷新状态,不自动弹出工作台
  chrome.storage.local.get(['user'], (r) => {
    if (r?.user?.role === 'jobseeker') {
      init();
    }
  });

  // 监听求职者相关事件（重新打开工作台）
  window.addEventListener('dq:jobseeker-open', () => {
    init().then(() => {
      open();
      window.DQ.app?.showJobSeekerWorkspace(true);
    });
  });

  window.DQ = window.DQ || {};
  window.DQ.jobseeker = {
    open,
    close,
    init,
    // 重置并重新打开引导
    async resetWizard() {
      try {
        const r = await chrome.storage.local.get(['jobseeker']);
        const js = r?.jobseeker || {};
        // 保留偏好设置，只重置简历状态（如果想完全重置，可以清空整个 jobseeker 对象）
        await chrome.storage.local.set({
          jobseeker: {
            ...js,
            resume: { uploaded: false, fileName: null, uploadedAt: null },
          },
        });
        await init();
        open();
        window.DQ.app?.showJobSeekerWorkspace(true);
      } catch (err) {
        console.error('Reset wizard error:', err);
      }
    },
    // 完全重置（清除所有设置）
    async resetAll() {
      try {
        await chrome.storage.local.set({ jobseeker: {} });
        await init();
        open();
        window.DQ.app?.showJobSeekerWorkspace(true);
      } catch (err) {
        console.error('Reset all error:', err);
      }
    },
  };
})();
