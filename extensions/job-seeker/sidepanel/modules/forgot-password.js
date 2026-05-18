/*
 * modules/forgot-password.js — 忘记密码 / 重置密码 流程(全屏 overlay 弹窗).
 *
 * 入口:登录 Gate 上的「忘记密码?」链接(app.js 绑定 → window.DQ.forgotPassword.open()).
 *
 * 后端契约(job-portal-api,见 auth-api.js):
 *   POST /auth/password-reset          { email }                          → { sent, verification_id?, dev_hint? }
 *   POST /auth/password-reset/confirm  { verification_id, code, new_password } → { ok }
 *
 * 三步状态机:
 *   email  · 输入邮箱 → requestPasswordReset → 拿 verification_id
 *   reset  · 输入 6 位验证码 + 新密码(8-72 位)+ 确认密码 → confirmPasswordReset
 *   done   · 重置成功(服务端已撤销该用户全部 token)→ 引导去登录页重新登录
 *
 * 防枚举:后端对未注册/无密码邮箱返回 sent=false 且无 verification_id;
 *         此处如实提示用户检查邮箱,不强行进入下一步.
 */
(function () {
  'use strict';

  const authApi = () => window.DQ && window.DQ.authApi;

  // ── 状态 ────────────────────────────────────────────────
  let mask = null;
  let step = 'email';            // 'email' | 'reset' | 'done'
  let busy = false;
  const data = { email: '', verificationId: '', devHint: '' };

  function escapeText(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // 把后端错误 key 翻成用户提示
  function errMessage(err) {
    const d = (err && err.data) || {};
    if (err && err.status === 0) return t('forgot.errNetwork');
    if (err && err.status === 429) {
      const s = Number(d.retry_after_seconds || 0);
      return s > 0 ? t('forgot.errTooFrequent', { n: s }) : t('forgot.errTooFrequentDefault');
    }
    if (typeof d.attempts_left === 'number') {
      return t('forgot.errAttemptsLeft', { n: d.attempts_left });
    }
    const map = {
      not_found:          t('forgot.errCodeNotFound'),
      expired:            t('forgot.errExpired'),
      invalid_code:       t('forgot.errInvalidCodeKey'),
      mismatch:           t('forgot.errMismatch'),
      too_many_attempts:  t('forgot.errTooMany'),
      already_used:       t('forgot.errAlreadyUsed'),
      no_user_bound:      t('forgot.errNoUserBound'),
      email_send_failed:  t('forgot.errSendFailed'),
    };
    return map[d.error] || map[err && err.message] || t('forgot.errDefault');
  }

  function setMsg(text, kind) {
    const el = mask && mask.querySelector('#fpMsg');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'fp-msg' + (text ? ' ' + (kind || 'error') : '');
  }

  function setBusy(on) {
    busy = on;
    if (!mask) return;
    mask.querySelectorAll('button, input').forEach((el) => { el.disabled = on; });
    const cls = mask.querySelector('#fpClose');
    if (cls) cls.disabled = false;     // 关闭键始终可用
  }

  // ── 渲染各步骤 ──────────────────────────────────────────
  function bodyEmail() {
    return `
      <p class="fp-lead">${t('forgot.emailStep.lead')}</p>
      <label class="fp-label" for="fpEmail">${t('forgot.emailStep.label')}</label>
      <input class="fp-input" id="fpEmail" type="email" autocomplete="email"
             placeholder="you@example.com" value="${escapeText(data.email)}">
      <div class="fp-msg" id="fpMsg"></div>
      <button class="btn primary fp-submit" id="fpSendBtn" type="button">${t('forgot.emailStep.sendBtn')}</button>
      <button class="gate-forgot" id="fpCancel" type="button">${t('forgot.emailStep.cancelBtn')}</button>`;
  }

  function bodyReset() {
    let hint = '';
    if (/^\d{6}$/.test(data.devHint || '')) {
      hint = `<div class="fp-dev">${t('forgot.resetStep.devHintCode', { code: escapeText(data.devHint) })}</div>`;
    } else if (data.devHint === 'code_in_server_console') {
      hint = `<div class="fp-dev">${t('forgot.resetStep.devHint')}</div>`;
    }
    return `
      <p class="fp-lead">${t('forgot.resetStep.lead', { email: `<b>${escapeText(data.email)}</b>` })}</p>
      ${hint}
      <label class="fp-label" for="fpCode">${t('forgot.resetStep.codeLabel')}</label>
      <input class="fp-input" id="fpCode" type="text" inputmode="numeric"
             maxlength="6" autocomplete="one-time-code" placeholder="${t('forgot.resetStep.codePlaceholder')}">
      <button class="gate-forgot fp-resend" id="fpResend" type="button">${t('forgot.resetStep.resendBtn')}</button>
      <label class="fp-label" for="fpPwd">${t('forgot.resetStep.pwdLabel')}</label>
      <input class="fp-input" id="fpPwd" type="password" autocomplete="new-password"
             placeholder="${t('forgot.resetStep.placeholderPwd')}">
      <label class="fp-label" for="fpPwd2">${t('forgot.resetStep.pwd2Label')}</label>
      <input class="fp-input" id="fpPwd2" type="password" autocomplete="new-password"
             placeholder="${t('forgot.resetStep.placeholderPwd2')}">
      <div class="fp-msg" id="fpMsg"></div>
      <button class="btn primary fp-submit" id="fpConfirmBtn" type="button">${t('forgot.resetStep.confirmBtn')}</button>
      <button class="gate-forgot" id="fpBack" type="button">${t('forgot.resetStep.backBtn')}</button>`;
  }

  function bodyDone() {
    return `
      <div class="fp-done-ico" aria-hidden="true">✅</div>
      <p class="fp-lead">${t('forgot.doneStep.lead')}</p>
      <div class="fp-msg" id="fpMsg"></div>
      <button class="btn primary fp-submit" id="fpGoLogin" type="button">${t('forgot.doneStep.loginBtn')}</button>
      <button class="gate-forgot" id="fpDoneClose" type="button">${t('forgot.doneStep.closeBtn')}</button>`;
  }

  function render() {
    const titles = { email: t('forgot.titleEmail'), reset: t('forgot.titleReset'), done: t('forgot.titleDone') };
    let body = '';
    if (step === 'email') body = bodyEmail();
    else if (step === 'reset') body = bodyReset();
    else body = bodyDone();

    mask.innerHTML = `
      <article class="fp-card" role="dialog" aria-modal="true" aria-label="${t('forgot.ariaLabel')}">
        <header class="fp-head">
          <span class="fp-head-ico" aria-hidden="true">🔑</span>
          <span class="fp-head-title">${escapeText(titles[step])}</span>
          <button class="fp-close" id="fpClose" type="button" aria-label="${t('forgot.closeAriaLabel')}">✕</button>
        </header>
        <div class="fp-body">${body}</div>
      </article>`;

    mask.querySelector('#fpClose')?.addEventListener('click', close);

    if (step === 'email') {
      const input = mask.querySelector('#fpEmail');
      input?.focus();
      mask.querySelector('#fpSendBtn')?.addEventListener('click', submitEmail);
      mask.querySelector('#fpCancel')?.addEventListener('click', close);
      input?.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitEmail(); });
    } else if (step === 'reset') {
      mask.querySelector('#fpCode')?.focus();
      mask.querySelector('#fpConfirmBtn')?.addEventListener('click', submitReset);
      mask.querySelector('#fpResend')?.addEventListener('click', resend);
      mask.querySelector('#fpBack')?.addEventListener('click', () => {
        step = 'email'; render();
      });
      mask.querySelector('#fpPwd2')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') submitReset();
      });
    } else {
      mask.querySelector('#fpGoLogin')?.addEventListener('click', goLogin);
      mask.querySelector('#fpDoneClose')?.addEventListener('click', close);
    }
  }

  // ── 动作 ────────────────────────────────────────────────
  async function submitEmail() {
    if (busy) return;
    const email = (mask.querySelector('#fpEmail')?.value || '').trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setMsg(t('forgot.errInvalidEmail')); return;
    }
    setMsg(''); setBusy(true);
    try {
      const r = await authApi().requestPasswordReset({ email });
      if (!r || !r.sent || !r.verification_id) {
        // 防枚举:邮箱未注册 / 第三方登录无密码
        setMsg(t('forgot.errEmailNotFound'), 'error');
        return;
      }
      data.email = email;
      data.verificationId = r.verification_id;
      data.devHint = r.dev_hint || '';
      step = 'reset';
      render();
    } catch (err) {
      setMsg(errMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (busy) return;
    setMsg(''); setBusy(true);
    try {
      const r = await authApi().requestPasswordReset({ email: data.email });
      if (r && r.verification_id) data.verificationId = r.verification_id;
      data.devHint = (r && r.dev_hint) || data.devHint;
      setMsg(t('forgot.resendOk'), 'ok');
    } catch (err) {
      setMsg(errMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitReset() {
    if (busy) return;
    const code = (mask.querySelector('#fpCode')?.value || '').trim();
    const pwd  = mask.querySelector('#fpPwd')?.value || '';
    const pwd2 = mask.querySelector('#fpPwd2')?.value || '';
    if (!/^\d{6}$/.test(code)) { setMsg(t('forgot.errInvalidCode')); return; }
    if (pwd.length < 8 || pwd.length > 72) { setMsg(t('forgot.errPwdLength')); return; }
    if (pwd !== pwd2) { setMsg(t('forgot.errPwdMismatch')); return; }

    setMsg(''); setBusy(true);
    try {
      await authApi().confirmPasswordReset({
        verification_id: data.verificationId,
        code,
        new_password: pwd,
      });
      step = 'done';
      render();
    } catch (err) {
      setMsg(errMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function goLogin() {
    const domain = document.getElementById('gateDomain')?.textContent || 'dinq.me';
    try { chrome.tabs?.create?.({ url: `https://${domain}/` }); } catch (_) {}
    close();
  }

  // ── overlay 生命周期 ────────────────────────────────────
  function open() {
    step = 'email';
    busy = false;
    data.verificationId = '';
    data.devHint = '';
    if (!mask) {
      mask = document.createElement('div');
      mask.className = 'fp-mask';
      mask.id = 'forgotPwdMask';
      document.body.appendChild(mask);
      document.addEventListener('keydown', onKey);
    }
    mask.classList.add('open');
    render();
  }

  function close() {
    if (!mask) return;
    mask.classList.remove('open');
    document.removeEventListener('keydown', onKey);
    const m = mask;
    mask = null;
    setTimeout(() => { try { m.remove(); } catch (_) {} }, 0);
  }

  function onKey(e) {
    if (e.key === 'Escape' && mask && !busy) { e.preventDefault(); close(); }
  }

  window.DQ = window.DQ || {};
  window.DQ.forgotPassword = { open, close };
})();
