/*
 * sidepanel/autofill/profile.js — 表单资料(合并自 autofill-ext)
 *
 * 已并入 v3「我的」tab:渲染进 #view-profile(位于 #tab-profile 内)。
 * 账号信息由「我的」tab 顶部统一显示,本模块只保留 简历 + 我的信息 两个可折叠分区。
 *
 * profile 是扁平 KV 对象;简历另存于 __resume__ 键。
 * 简历上传时除存进本地 __resume__,还会上送 agent-gateway /resume/upload ——
 * 评估 / AI 匹配查的是 agent-gateway 简历库,不上送会被判为「未上传简历」。
 * 对外:AF.profile.ensure() — 懒加载后端 profile 再渲染;AF.profile.render() — 直接重渲染。
 */
(function () {
  'use strict';
  window.AF = window.AF || {};
  AF.state = AF.state || { profile: {}, lastPage: null, auth: null };
  const esc = (s) => AF.esc(s);

  const COMMON = [
    ['fullName', 'autofill.field.fullName'],
    ['firstName', 'autofill.field.firstName'],
    ['lastName', 'autofill.field.lastName'],
    ['email', 'autofill.field.email'],
    ['phone', 'autofill.field.phone'],
    ['city', 'autofill.field.city'],
    ['state', 'autofill.field.state'],
    ['country', 'autofill.field.country'],
    ['address', 'autofill.field.address'],
    ['zipCode', 'autofill.field.zipCode'],
    ['linkedin', 'autofill.field.linkedin'],
    ['github', 'autofill.field.github'],
    ['website', 'autofill.field.website'],
    ['currentCompany', 'autofill.field.currentCompany'],
    ['currentTitle', 'autofill.field.currentTitle'],
    ['experienceYears', 'autofill.field.experienceYears'],
    ['expectedSalary', 'autofill.field.expectedSalary'],
  ];

  const expanded = new Set();
  let _loaded = false;

  function host() { return document.getElementById('view-profile'); }

  // 懒加载:首次进「我的」tab 时从后端拉一次 profile,之后只重渲染
  async function ensure() {
    if (!_loaded) {
      _loaded = true;
      try {
        const r = await AF.api.getProfile();
        if (r && r.ok) AF.state.profile = r.profile || {};
      } catch (_) { /* 本地兜底 */ }
      // 历史简历(此前只存了本地)一次性补送到 agent-gateway,供评估 / AI 匹配用
      syncResumeToGateway();
      loadResumeParsed();
    }
    render();
  }

  function render() {
    const el = host();
    if (!el) return;
    const p = (AF.state && AF.state.profile) || {};
    const resume = p.__resume__;

    const commonRows = COMMON.map(([k, labelKey]) => `
      <div class="pf-row">
        <label class="pf-label">${esc(t(labelKey))}</label>
        <input class="input" data-pf="${esc(k)}" value="${esc(p[k] || '')}">
      </div>
    `).join('');

    const customKeys = Object.keys(p).filter((k) => k.indexOf('custom:') === 0);
    const customRows = customKeys.map((k) => `
      <div class="pf-row">
        <label class="pf-label">${esc(k.slice(7))}</label>
        <input class="input" data-pf="${esc(k)}" value="${esc(p[k] || '')}">
      </div>
    `).join('');

    const filledCount = Object.keys(p)
      .filter((k) => k.indexOf('__') !== 0 && String(p[k] || '').trim())
      .length;

    el.innerHTML = `
      <div class="pf-subhead">${t('autofill.profileSubhead')}</div>

      ${section('resume', t('autofill.sectionResume'),
        resume ? esc(resume.fileName) : t('autofill.resumeNoFile'), `
        <label class="btn btn-block">
          ${resume ? t('autofill.resumeReupload') : t('autofill.resumeUploadBtn')}
          <input type="file" id="af-resume" accept=".pdf,.doc,.docx" hidden>
        </label>
        ${renderResumeParsed()}
      `)}

      ${section('info', t('autofill.sectionInfo'),
        filledCount ? t('autofill.infoFilled', { n: filledCount }) : t('autofill.infoEmpty'), `
        ${commonRows}
        ${customRows
          ? '<div class="pf-subhead">' + t('autofill.customFieldsSubhead') + '</div>' + customRows
          : ''}
        <button class="btn btn-primary btn-block" id="af-pf-save">${t('autofill.saveProfileBtn')}</button>
      `)}
    `;

    el.querySelectorAll('[data-toggle]').forEach((h) => {
      h.addEventListener('click', () => toggleSection(h.dataset.toggle));
    });
    el.querySelector('#af-resume').addEventListener('change', onResume);
    el.querySelector('#af-pf-save').addEventListener('click', save);
  }

  function section(name, title, meta, bodyHtml) {
    const open = expanded.has(name);
    return `
      <div class="card pf-section">
        <div class="pf-sec-head" data-toggle="${name}">
          <span class="pf-sec-title">${esc(title)}</span>
          <span class="pf-sec-meta">${esc(meta)}</span>
          <span class="pf-sec-arrow">${open ? '▾' : '▸'}</span>
        </div>
        <div class="pf-sec-body${open ? '' : ' hidden'}" data-body="${name}">
          ${bodyHtml}
        </div>
      </div>
    `;
  }

  function toggleSection(name) {
    const el = host();
    const body = el && el.querySelector('[data-body="' + name + '"]');
    const head = el && el.querySelector('[data-toggle="' + name + '"]');
    if (!body) return;
    const nowHidden = body.classList.toggle('hidden');
    if (nowHidden) expanded.delete(name);
    else expanded.add(name);
    const arrow = head && head.querySelector('.pf-sec-arrow');
    if (arrow) arrow.textContent = nowHidden ? '▸' : '▾';
  }

  function onResume(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const base64 = String(reader.result).split(',')[1] || '';
      AF.state.profile.__resume__ = {
        fileName: file.name,
        mimeType: file.type || 'application/pdf',
        base64,
      };
      AF.toast(t('autofill.resumeRead'), 'success');
      expanded.add('resume');
      render();
    };
    reader.readAsDataURL(file);
    // 同步上送 agent-gateway 简历库 ——「评估 / AI 匹配」读的是这个库,
    // 只存本地 __resume__ 的话点「评估」会被判为「请先上传简历」。
    pushResumeToGateway(file).then((ok) => {
      if (!ok) { if (AF.toast) AF.toast(t('autofill.resumeSyncFailed'), 'warn'); return; }
      // 上送成功 → 后端异步解析,先标记「解析中」再轮询拉解析结果
      AF.state.resumeParsed = { parse_status: 'parsing', parsed: null };
      render();
      pollResumeParsed();
    });
  }

  // 把一份简历 File 上送 agent-gateway(/resume/upload);成功返回 true。
  // autofill 子系统原本只把简历存进本地 profileCache.__resume__(供表单填充),
  // 后端简历库收不到 —— 评估 / AI 匹配都查 agent-gateway 简历库,故补送一份。
  async function pushResumeToGateway(file) {
    const agentGw = window.DQ && window.DQ.api && window.DQ.api.agentGw;
    if (!agentGw || !agentGw.resumeUpload || !file) return false;
    try {
      await agentGw.resumeUpload(file, file.name);
      return true;
    } catch (err) {
      console.warn('[AF] 简历上送 agent-gateway 失败:', (err && err.message) || err);
      return false;
    }
  }

  // __resume__ 里的 base64 还原成 File(历史简历补送用)
  function resumeFileFromState() {
    const r = AF.state.profile && AF.state.profile.__resume__;
    if (!r || !r.base64) return null;
    try {
      const bin = atob(r.base64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new File([bytes], r.fileName || 'resume.pdf',
        { type: r.mimeType || 'application/pdf' });
    } catch (_) { return null; }
  }

  // 一次性:本地有简历但 agent-gateway 简历库还没有时,补送一份。
  // 修复「在表单资料里传过简历,但点评估仍提示请先上传简历」。
  async function syncResumeToGateway() {
    const agentGw = window.DQ && window.DQ.api && window.DQ.api.agentGw;
    if (!agentGw || !agentGw.resumeStatus) return;
    const file = resumeFileFromState();
    if (!file) return;
    try {
      const st = await agentGw.resumeStatus();
      if (st && st.has_resume) return;  // 网关已有,无需补送
    } catch (_) { return; }             // 状态查不到就不动作,下次再试
    pushResumeToGateway(file);
  }

  // ── 简历 AI 解析结果 ──────────────────────────────────────────────
  // 从 agent-gateway 拉简历解析结果(/resume/{uid}),存进 AF.state.resumeParsed。
  async function loadResumeParsed() {
    const agentGw = window.DQ && window.DQ.api && window.DQ.api.agentGw;
    if (!agentGw || !agentGw.resumeParsed) return;
    try {
      const r = await agentGw.resumeParsed();
      if (r && r.ok) {
        AF.state.resumeParsed = { parse_status: r.parse_status || '', parsed: r.parsed || null };
        if (r.parse_status === 'done') expanded.add('resume');  // 解析好了自动展开给用户看
        render();
      }
    } catch (_) { /* 拿不到就不显示解析块 */ }
  }

  // 上传后后端异步解析,轮询拉结果(~3s 一次,最多 ~12 次)
  function pollResumeParsed(attempts) {
    attempts = (attempts == null) ? 12 : attempts;
    const agentGw = window.DQ && window.DQ.api && window.DQ.api.agentGw;
    if (!agentGw || !agentGw.resumeParsed || attempts <= 0) return;
    setTimeout(async () => {
      let r = null;
      try { r = await agentGw.resumeParsed(); } catch (_) { r = null; }
      if (r && r.ok) {
        AF.state.resumeParsed = { parse_status: r.parse_status || '', parsed: r.parsed || null };
        render();
        if (r.parse_status === 'done' || r.parse_status === 'failed') return;  // 终态停轮询
      }
      pollResumeParsed(attempts - 1);
    }, 3000);
  }

  // 渲染简历 AI 解析结果块(嵌在「简历」分区里,上传按钮下方)
  function renderResumeParsed() {
    const rp = AF.state && AF.state.resumeParsed;
    if (!rp || !rp.parse_status) return '';
    if (rp.parse_status === 'pending' || rp.parse_status === 'parsing') {
      return '<div class="rz-status">' + t('autofill.resumeParsing') + '</div>';
    }
    if (rp.parse_status === 'failed') {
      return '<div class="rz-status rz-fail">' + t('autofill.resumeParseFailed') + '</div>';
    }
    if (rp.parse_status !== 'done' || !rp.parsed) return '';
    const p = rp.parsed;
    const joinArr = (a) => Array.isArray(a) ? a.filter(Boolean).join('、') : (a || '');
    const rows = [];
    const row = (k, v) => {
      if (v == null || v === '') return;
      rows.push(`<div class="rz-row"><span class="rz-k">${esc(k)}</span><span class="rz-v">${esc(v)}</span></div>`);
    };
    row(t('autofill.resumeFieldName'), p.name);
    row(t('autofill.resumeFieldDegree'), p.degree);
    row(t('autofill.resumeFieldWorkYears'), p.work_years);
    row(t('autofill.resumeFieldTargetPos'), joinArr(p.target_positions));
    row(t('autofill.resumeFieldTargetCity'), joinArr(p.target_cities));
    const salary = p.target_salary_raw
      || ((p.target_salary_min || p.target_salary_max)
          ? `${p.target_salary_min || '?'} - ${p.target_salary_max || '?'}` : '');
    row(t('autofill.resumeFieldSalary'), salary);

    let skillsHtml = '';
    if (Array.isArray(p.skills) && p.skills.length) {
      skillsHtml = '<div class="rz-row"><span class="rz-k">' + t('autofill.resumeFieldSkills') + '</span>'
        + '<span class="rz-v rz-chips">'
        + p.skills.slice(0, 30).map((s) => `<span class="chip chip-tpl">${esc(s)}</span>`).join('')
        + '</span></div>';
    }

    let expHtml = '';
    if (Array.isArray(p.work_experience) && p.work_experience.length) {
      const items = p.work_experience.slice(0, 5).map((w) => {
        w = w || {};
        const head = [w.company, w.title].filter(Boolean).map(esc).join(' · ') || t('autofill.resumeExpFallback');
        const dur = w.duration ? `<span class="rz-exp-dur">${esc(w.duration)}</span>` : '';
        const desc = w.desc ? `<div class="rz-exp-desc">${esc(w.desc)}</div>` : '';
        return `<div class="rz-exp-item"><div class="rz-exp-head">${head}${dur}</div>${desc}</div>`;
      }).join('');
      expHtml = `<div class="rz-sub">${t('autofill.resumeSubWorkExp')}</div><div>${items}</div>`;
    }

    let selfHtml = '';
    if (p.self_evaluation) {
      const sv = String(p.self_evaluation);
      const txt = sv.length > 240 ? sv.slice(0, 240) + '…' : sv;
      selfHtml = `<div class="rz-sub">${t('autofill.resumeSubSelf')}</div><div class="rz-self">${esc(txt)}</div>`;
    }

    return `
      <div class="pf-subhead">${t('autofill.resumeParseResult')}</div>
      <div class="rz-block">
        ${rows.join('')}
        ${skillsHtml}
        ${expHtml}
        ${selfHtml}
      </div>`;
  }

  async function save() {
    host().querySelectorAll('[data-pf]').forEach((inp) => {
      AF.state.profile[inp.dataset.pf] = inp.value.trim();
    });
    const r = await AF.api.saveProfile(AF.state.profile);
    AF.toast(r && r.fallback ? t('autofill.profileSavedLocal') : t('autofill.profileSaved'), 'success');
    expanded.add('info');
    render();
  }

  AF.profile = { render, ensure };
})();
