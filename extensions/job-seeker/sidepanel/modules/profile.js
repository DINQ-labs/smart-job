/*
 * modules/profile.js — 我的 tab(my-*).
 *
 * 渲染:头像 + 名字 + 邮箱 + 退出按钮 + 列表项(求职偏好 / 设置)
 *      + 内嵌「表单资料」(autofill 子系统,渲染进 #view-profile)。
 *      「求职偏好」点开后弹出编辑弹窗(openPrefsModal,字段对齐后端 /user-preferences)。
 *
 * v3 类:.my-avatar-row / .my-avatar / .my-name / .my-email / .my-list / .my-list-item
 */
(function () {
  'use strict';
  const { NAMES, on } = window.DQ.events;
  const api = window.DQ && window.DQ.api;

  // 各平台薪资档位(与 onboarding / 后端 preferences_router.py 的校验集一致)
  const SALARY_OPTIONS = {
    boss:     ['3k以下', '3-5k', '5-10k', '10-20k', '20-30k', '30-50k', '50k+'],
    linkedin: ['<$60k/yr', '$60k-100k/yr', '$100k-150k/yr', '$150k-200k/yr', '$200k+/yr', 'Unspecified'],
    indeed:   ['<$60k/yr', '$60k-100k/yr', '$100k-150k/yr', '$150k-200k/yr', '$200k+/yr', 'Unspecified'],
  };

  function $(id) { return document.getElementById(id); }
  const host = $('profileContent');

  function initials(name, email) {
    const s = (name || email || '?').trim();
    return s.charAt(0).toUpperCase() || '?';
  }

  function render() {
    if (!host) return;
    const u = window.DQ?.state?.user || {};
    const role = window.DQ?.state?.role || 'jobseeker';
    // 邮箱注册的账号通常没有 name —— 用邮箱前缀兜底,别误显示「未登录」
    const loggedIn = !!u.loggedIn;
    const displayName = u.name
      || (u.email ? u.email.split('@')[0] : '')
      || (loggedIn ? t('profile.loggedIn') : t('profile.notLoggedIn'));
    const displayEmail = u.email || (loggedIn ? '' : t('profile.pleaseLogin'));
    host.innerHTML = `
      <div class="my-avatar-row">
        <div class="my-avatar" aria-hidden="true">${initials(u.name, u.email)}</div>
        <div>
          <div class="my-name">${escapeText(displayName)}</div>
          <div class="my-email">${escapeText(displayEmail)}</div>
        </div>
        <button class="my-edit" data-action="logout">${t('profile.logout')}</button>
      </div>

      <div class="my-list" style="margin-top:var(--sp-3)">
        <div class="my-list-item" data-action="prefs">
          <span class="ico" aria-hidden="true">🎯</span>
          <span>${role === 'recruiter' ? t('profile.recruiterPrefs') : t('profile.prefs')}</span>
          <span class="arrow">›</span>
        </div>
        <div class="my-list-item" data-action="settings">
          <span class="ico" aria-hidden="true">⚙️</span>
          <span>${t('profile.settings')}</span>
          <span class="arrow">›</span>
        </div>
      </div>`;
    // 渲染并入「我的」的「表单资料」(autofill 子系统 → #view-profile)
    try { window.AF && AF.profile && AF.profile.ensure && AF.profile.ensure(); } catch (_) {}
  }

  function escapeText(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  // 写进 value="..." 属性时还要转义引号
  function escAttr(s) {
    return escapeText(s).replace(/"/g, '&quot;');
  }

  // ── 求职偏好编辑弹窗 ─────────────────────────────────────────────
  // 字段对齐后端 /user-preferences(job_role / city / salary_range / notes),
  // 与 onboarding Step 5c 同一套表单。点「求职偏好」即弹出,无额外按钮、无简历上传。
  function salaryOptionsHtml(platform, current) {
    const opts = SALARY_OPTIONS[platform] || SALARY_OPTIONS.boss;
    const list = [`<option value="">${t('prefs.salaryNoLimit')}</option>`];
    let matched = false;
    opts.forEach((o) => {
      const sel = current === o ? ' selected' : '';
      if (sel) matched = true;
      list.push(`<option value="${escAttr(o)}"${sel}>${escapeText(o)}</option>`);
    });
    // 旧版工作台可能写过不在档位里的薪资串,保留为当前值以免丢失
    if (current && !matched) {
      list.splice(1, 0,
        `<option value="${escAttr(current)}" selected>${escapeText(current)}${t('prefs.currentSuffix')}</option>`);
    }
    return list.join('');
  }

  async function openPrefsModal() {
    document.getElementById('prefsModalMask')?.remove();

    const role = window.DQ?.state?.role || 'jobseeker';
    const platform = window.DQ?.state?.platform || 'boss';
    const title = role === 'recruiter' ? t('prefs.recruiterTitle') : t('prefs.title');

    const mask = document.createElement('div');
    mask.id = 'prefsModalMask';
    mask.className = 'modal-mask open';
    mask.innerHTML = `
      <div class="modal-card">
        <div class="modal-head">
          <span class="title">${escapeText(title)}</span>
          <span class="close" id="prefsModalClose" role="button" aria-label="${t('prefs.close')}">✕</span>
        </div>
        <div class="modal-body" id="prefsModalBody">
          <div style="padding:var(--sp-6) 0;text-align:center;color:var(--muted);font-size:var(--text-sm)">${t('common.loading')}</div>
        </div>
        <div class="modal-footer">
          <button class="btn ghost" id="prefsModalCancel" type="button">${t('common.cancel')}</button>
          <button class="btn primary" id="prefsModalSave" type="button" disabled>${t('common.save')}</button>
        </div>
      </div>
    `;
    document.body.appendChild(mask);

    const close = () => mask.remove();
    mask.querySelector('#prefsModalClose').addEventListener('click', close);
    mask.querySelector('#prefsModalCancel').addEventListener('click', close);
    mask.addEventListener('click', (e) => { if (e.target === mask) close(); });

    // 当前偏好:后端为准,storage 兜底(离线时)
    let prefs = {};
    let resp = null;
    try {
      if (api && api.agentGw) resp = await api.agentGw.userPreferences(platform);
    } catch (_) { /* 离线:走 storage 兜底 */ }
    if (resp && resp.ok && resp.data) prefs = resp.data;
    if (!prefs.job_role && !prefs.city && !prefs.salary_range && !prefs.notes) {
      try {
        const s = await chrome.storage.local.get(['jobseeker']);
        if (s && s.jobseeker && s.jobseeker.preferences) prefs = s.jobseeker.preferences;
      } catch (_) {}
    }
    if (!document.body.contains(mask)) return;  // 加载期间已被关闭

    mask.querySelector('#prefsModalBody').innerHTML = `
      <form id="prefsModalForm" novalidate>
        <div class="ob-field-label">${t('prefs.fieldRole')}</div>
        <input type="text" class="ob-input" id="pmRole" placeholder="${t('prefs.placeholderRole')}"
               value="${escAttr(prefs.job_role || '')}">
        <div class="ob-field-label" style="margin-top:var(--sp-2)">${t('prefs.fieldCity')}</div>
        <input type="text" class="ob-input" id="pmCity" placeholder="${t('prefs.placeholderCity')}"
               value="${escAttr(prefs.city || '')}">
        <div class="ob-field-label" style="margin-top:var(--sp-2)">${t('prefs.fieldSalary')}</div>
        <select class="ob-input" id="pmSalary">${salaryOptionsHtml(platform, prefs.salary_range || '')}</select>
        <div class="ob-field-label" style="margin-top:var(--sp-2)">${t('prefs.fieldNotes')}</div>
        <input type="text" class="ob-input" id="pmNotes" placeholder="${t('prefs.placeholderNotes')}"
               value="${escAttr(prefs.notes || '')}">
        <div class="ob-form-error hidden" id="pmError" style="margin-top:var(--sp-2)"></div>
      </form>
    `;

    const saveBtn = mask.querySelector('#prefsModalSave');
    saveBtn.disabled = false;
    saveBtn.addEventListener('click', () => savePrefs(mask, platform));
    mask.querySelector('#prefsModalForm').addEventListener('submit', (e) => {
      e.preventDefault();
      savePrefs(mask, platform);
    });
    mask.querySelector('#pmRole')?.focus();
  }

  async function savePrefs(mask, platform) {
    const errBox = mask.querySelector('#pmError');
    const showErr = (msg) => {
      if (errBox) { errBox.textContent = msg; errBox.classList.remove('hidden'); }
    };
    const jobRole = (mask.querySelector('#pmRole')?.value || '').trim();
    if (!jobRole) { showErr(t('prefs.errRequireRole')); return; }

    const body = {
      job_role: jobRole,
      city: (mask.querySelector('#pmCity')?.value || '').trim(),
      salary_range: mask.querySelector('#pmSalary')?.value || '',
      notes: (mask.querySelector('#pmNotes')?.value || '').trim(),
    };

    const saveBtn = mask.querySelector('#prefsModalSave');
    const cancelBtn = mask.querySelector('#prefsModalCancel');
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = t('prefs.saving'); }
    if (cancelBtn) cancelBtn.disabled = true;
    if (errBox) errBox.classList.add('hidden');

    try {
      if (!api || !api.agentGw) throw new Error(t('prefs.apiNotReady'));
      await api.agentGw.savePreferences(platform, body);
      // 本地缓存:chat 开场白 / ContextBar 与 onboarding 共用同一结构
      try {
        const r = await chrome.storage.local.get(['jobseeker']);
        const js = (r && r.jobseeker) || {};
        await chrome.storage.local.set({ jobseeker: { ...js, preferences: body } });
      } catch (_) {}
      mask.remove();
      if (window.AF && AF.toast) AF.toast(t('prefs.saved'), 'success');
    } catch (err) {
      if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = t('common.save'); }
      if (cancelBtn) cancelBtn.disabled = false;
      showErr(t('prefs.saveFailed') + ((err && err.message) || err || t('prefs.unknownErr')));
    }
  }

  host?.addEventListener('click', async (e) => {
    const item = e.target.closest('[data-action]');
    if (!item) return;
    const a = item.dataset.action;
    switch (a) {
      case 'prefs':
        // 「求职偏好」:只弹出编辑弹窗,不再跳求职者工作台
        openPrefsModal();
        break;
      case 'logout':
        // 退出登录:撤销 refresh token + 清本地 auth,再跳到登录页
        try { await window.DQ.authApi?.logout?.(); } catch (_) {}
        try { window.DQ.onboarding?.openLogin?.(); } catch (_) {}
        break;
      case 'settings':
        chrome.runtime?.openOptionsPage?.();
        break;
      default: break;
    }
  });

  on(NAMES.TAB_CHANGED, (e) => { if (e.detail?.tab === 'profile') render(); });
  on(NAMES.AUTH_CHANGED, render);
  on(NAMES.ROLE_CHANGED, render);

  render();

  window.DQ = window.DQ || {};
  window.DQ.profile = { render };
})();
