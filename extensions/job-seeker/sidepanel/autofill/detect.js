/*
 * modules/detect.js — 检测填写主流程（含 P5.2 多步编排）
 *
 * 检测(帧感知) → 匹配(模板优先) → 填写 → 记录 → 自动推进下一步 / 缺字段补充。
 * 多步：自动点 Next、逐步填写，到 Submit 前停下交用户确认。
 */
(function () {
  'use strict';
  window.AF = window.AF || {};
  const esc = (s) => AF.esc(s);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const CONF_THRESHOLD = 0.6;
  const MAX_STEPS = 12;

  let fields = [];
  let nav = {};
  let mappings = [];
  let unresolved = [];
  let captureActive = false;
  let captureOn = false;   // 抓包会话存在 → 显示「提交抓包」按钮
  let step = 1;
  // idle | matched | running | supplement | submit | done
  let mode = 'idle';

  function host() { return document.getElementById('view-detect'); }
  function fieldByFid(fid) { return fields.find((f) => f.fid === fid); }
  function frameOf(fid) { return (fieldByFid(fid) || {}).frameId || 0; }
  function fieldSignature() {
    return fields.map((f) => (f.label || '') + '|' + (f.type || '')).sort().join(';');
  }

  function init() { render(); }

  function render() {
    const el = host();
    if (!el) return;
    el.innerHTML = `
      <div class="card">
        <button class="btn btn-primary btn-block" id="af-detect-btn">${t('autofill.detectBtn')}</button>
        <div id="af-status" class="hint"></div>
      </div>
      <div id="af-result"></div>
    `;
    el.querySelector('#af-detect-btn').addEventListener('click', runDetect);
    if (fields.length) renderResult();
  }

  function setStatus(msg) {
    const s = host()?.querySelector('#af-status');
    if (s) s.textContent = msg || '';
  }

  function profileSnapshot() {
    const p = Object.assign({}, AF.state.profile);
    delete p.__resume__;
    return p;
  }

  // ── 检测入口（用户点「检测」）──────────────────────────────────────────
  async function runDetect() {
    step = 1;
    mode = 'idle';
    setStatus(t('autofill.detecting'));

    captureActive = false;
    captureOn = false;
    try {
      const s = await chrome.storage.local.get(['captureRequests']);
      if (s.captureRequests) {
        const cb = await AF.api.captureBegin();
        captureActive = !!(cb && cb.ok);
        captureOn = captureActive;
      }
    } catch (e) { /* ignore */ }

    const ok = await detectCore(false);
    if (!ok) return;
    mode = 'matched';
    await runMatch();
  }

  // 检测当前页面（帧感知）。skipOcr：多步轮询时不走截图兜底。
  async function detectCore(skipOcr) {
    const r = await AF.api.detect();
    if (!r || !r.ok) {
      setStatus(t('autofill.detectFailed', { err: (r && r.error) || '' }));
      AF.toast((r && r.error) || t('autofill.detectFailedToast'), 'error');
      return false;
    }
    fields = r.fields || [];
    nav = r.nav || {};
    AF.state.lastPage = { url: r.pageUrl, title: r.pageTitle };

    if (!fields.length) {
      if (skipOcr) return false;
      setStatus(t('autofill.ocrDetecting'));
      const ocr = await AF.api.ocrDetect({ pageUrl: r.pageUrl });
      if (ocr && ocr.ok && ocr.fields && ocr.fields.length) {
        fields = ocr.fields;
        return true;
      }
      mappings = []; unresolved = [];
      setStatus(ocr && ocr.error ? t('autofill.ocrFailed', { err: ocr.error }) : t('autofill.noFields'));
      renderResult();
      return false;
    }
    return true;
  }

  async function runMatch() {
    const r = await AF.api.match({
      fields,
      profileSnapshot: profileSnapshot(),
      pageUrl: AF.state.lastPage && AF.state.lastPage.url,
    });
    if (!r || !r.ok) { setStatus(t('autofill.matchFailed')); return; }
    mappings = r.mappings || [];
    unresolved = r.unresolved || [];
    const tpl = r.template_hits ? t('autofill.matchTplHits', { n: r.template_hits }) : '';
    setStatus(t('autofill.matchDone', {
      fallback: r.fallback ? t('autofill.matchFallback') : '',
      mapped: mappings.length,
      unresolved: unresolved.length,
      tpl,
    }));
    renderResult();
    pushHighlight();
  }

  function pushHighlight() {
    const items = [];
    mappings.forEach((m) => items.push({ fid: m.fid, status: 'filled', frameId: frameOf(m.fid) }));
    unresolved.forEach((u) => items.push({ fid: u.fid, status: 'pending', frameId: frameOf(u.fid) }));
    AF.api.highlight({ items });
  }

  // ── 渲染 ────────────────────────────────────────────────────────────────
  function footerHtml() {
    if (mode === 'matched') {
      return '<button class="btn btn-primary btn-block" id="af-fill-btn"'
        + (mappings.length ? '' : ' disabled') + '>' + t('autofill.fillBtn', { n: mappings.length }) + '</button>';
    }
    if (mode === 'running') return '<div class="hint">' + t('autofill.stepRunning', { step }) + '</div>';
    if (mode === 'submit') {
      return '<div class="card-amber" style="padding:8px;border-radius:6px;font-size:12px">'
        + t('autofill.submitHint') + '</div>';
    }
    if (mode === 'done') return '<div class="hint">' + t('autofill.done') + '</div>';
    if (mode === 'supplement') return '<div class="hint">' + t('autofill.supplementWait') + '</div>';
    return '';
  }

  function renderResult() {
    const box = host()?.querySelector('#af-result');
    if (!box) return;
    if (!fields.length) {
      box.innerHTML = '<div class="empty">' + t('autofill.noFormFields') + '</div>';
      return;
    }
    const rows = mappings.map((m) => {
      const f = fieldByFid(m.fid) || {};
      const conf = m.confidence == null ? 1 : m.confidence;
      const low = conf < CONF_THRESHOLD;
      const tplChip = m.source === 'template'
        ? '<span class="chip chip-tpl">' + t('autofill.chipTpl') + '</span>' : '';
      return `<div class="field-row ${low ? 'is-low' : ''}">
        <div class="field-main">
          <div class="field-label">${esc(f.label || f.name || m.fid)}</div>
          <div class="field-value">${esc(m.value)}</div>
        </div>
        ${tplChip}
        <span class="chip ${low ? 'chip-amber' : 'chip-green'}">
          ${low ? t('autofill.chipConfirm') : t('autofill.chipFillable')} ${Math.round(conf * 100)}%
        </span>
      </div>`;
    }).join('');

    const stepTag = step > 1 ? t('autofill.stepTag', { step }) : '';
    box.innerHTML = `
      <div class="card">
        <div class="card-title">${t('autofill.resultTitle', { stepTag, total: fields.length })}</div>
        ${rows || '<div class="empty">' + t('autofill.noAutoFields') + '</div>'}
        ${footerHtml()}
      </div>
      <div id="af-supplement"></div>
      ${captureOn ? `
      <div class="card">
        <button class="btn btn-block" id="af-capture-flush">${t('autofill.captureFlushBtn')}</button>
        <div class="hint">${t('autofill.captureFlushHint')}</div>
      </div>` : `
      <div class="hint" style="padding:6px 2px">${t('autofill.captureOffHint')}</div>`}
    `;
    const fb = box.querySelector('#af-fill-btn');
    if (fb) fb.addEventListener('click', runFill);
    const cfb = box.querySelector('#af-capture-flush');
    if (cfb) cfb.addEventListener('click', flushCapture);

    if (unresolved.length) {
      AF.supplement.render(
        box.querySelector('#af-supplement'),
        unresolved.map((u) => fieldByFid(u.fid)).filter(Boolean),
        onSupplemented
      );
    }
  }

  // ── 填写 ────────────────────────────────────────────────────────────────
  function actionFor(fid, value) {
    const f = fieldByFid(fid) || {};
    const m = mappings.find((x) => x.fid === fid);
    return {
      fid, value,
      selector: f.selector, shadowPath: f.shadowPath, relocate: f.relocate,
      type: f.type,
      // K6：优先用知识库学到的 widget（重访自愈：覆盖本次可能误判的启发式分类）
      widget: (m && m.widget) || f.widget,
      options: f.options, frameId: f.frameId,
    };
  }

  // 填写当前步：表单字段 + 简历上传 + 高亮 + 记录
  async function fillCurrent() {
    const actions = mappings
      .map((m) => actionFor(m.fid, m.value))
      .filter((a) => (a.selector || a.shadowPath) && a.widget !== 'file');

    setStatus(t('autofill.stepFilling', { step }));
    let res = { filled: [], failed: [] };
    if (actions.length) {
      const r = await AF.api.fill({ actions });
      res = (r && r.result) || res;
    }
    await uploadResumeIfAny();

    const filled = (res.filled || []).length;
    const failed = (res.failed || []).length;
    setStatus(failed
      ? t('autofill.stepFilled', { step, filled, failed })
      : t('autofill.stepFilledOk', { step, filled }));

    AF.api.highlight({ items: [].concat(
      (res.filled || []).map((fid) => ({ fid, status: 'filled', frameId: frameOf(fid) })),
      (res.failed || []).map((x) => ({ fid: x.fid, status: 'failed', frameId: frameOf(x.fid) }))
    ) });

    AF.log.add({
      url: AF.state.lastPage && AF.state.lastPage.url,
      title: AF.state.lastPage && AF.state.lastPage.title,
      filled, failed, unresolved: unresolved.length,
    });
    sendRecord(buildOutcome(res));
    return res;
  }

  // 简历上传：找 file 控件，优先 label 含 resume/cv/简历 的那个
  async function uploadResumeIfAny() {
    if (!AF.state.profile || !AF.state.profile.__resume__) return;
    const files = fields.filter((f) => f.widget === 'file');
    if (!files.length) return;
    const target = files.find((f) =>
      /resume|cv|简历|附件|attach/i.test((f.label || '') + (f.name || '') + (f.ariaLabel || '')))
      || (files.length === 1 ? files[0] : null);
    if (!target) return;
    const ur = await AF.api.uploadResume({
      locator: {
        selector: target.selector, shadowPath: target.shadowPath,
        frameId: target.frameId, relocate: target.relocate,
      },
    });
    if (ur && ur.ok && ur.result && ur.result.ok) AF.toast(t('autofill.resumeUploaded'), 'success');
  }

  // 用户点「自动填写」→ 填当前步 → 进入多步推进
  async function runFill() {
    await fillCurrent();
    await advance();
  }

  // ── P5.2 多步推进 ───────────────────────────────────────────────────────
  async function advance() {
    mode = 'running';
    renderResult();
    let guard = 0;
    while (guard++ < MAX_STEPS) {
      if (unresolved.filter((u) => u.required).length) {
        mode = 'supplement';
        setStatus(t('autofill.supplementPending', { step }));
        renderResult();
        return;   // 等用户补充，onSupplemented 会续跑
      }
      if (nav && nav.next) {
        const prevSig = fieldSignature();
        setStatus(t('autofill.nextStep'));
        const cn = await AF.api.clickNav({ locator: nav.next });
        if (!cn || !cn.ok) { finish('done', t('autofill.nextStepFailed')); return; }
        const advanced = await pollNextStep(prevSig);
        if (!advanced) { finish('done', t('autofill.nextStepEnd')); return; }
        step++;
        await fillCurrent();
        continue;
      }
      if (nav && nav.submit) { finish('submit', t('autofill.submitReady')); return; }
      finish('done', t('autofill.fillDone')); return;
    }
    finish('done', t('autofill.maxSteps'));
  }

  function finish(m, statusMsg) {
    mode = m;
    setStatus(statusMsg);
    renderResult();
    if (captureActive) {
      captureActive = false;
      // 不立即上传：抓包窗口交给 background 持有，延到用户真正手动提交
      // （导航 / beforeunload / 标签关闭）时再 flush，确保抓到提交请求。
      AF.api.captureFinalize();
    }
  }

  // 「提交抓包」按钮：立刻 flush 当前抓包会话并上传，不等自动触发。
  async function flushCapture() {
    const btn = host()?.querySelector('#af-capture-flush');
    if (btn) { btn.disabled = true; btn.textContent = t('autofill.captureSubmitting'); }
    const r = await AF.api.captureFlushNow();
    if (r && r.ok) {
      captureOn = false;
      AF.toast(r.count ? t('autofill.captureSubmitted', { count: r.count })
                       : t('autofill.captureEmpty'), 'success');
    } else if (r && r.busy) {
      AF.toast(t('autofill.captureBusy'), 'error');
    } else {
      AF.toast(t('autofill.captureError', { err: (r && r.error) || t('autofill.captureUnknownError') }), 'error');
    }
    renderResult();   // captureOn 仍 true 时按钮重新渲染，可重试
  }

  // 点 Next 后轮询，等下一步表单渲染出来（兼容 SPA 与整页跳转）
  async function pollNextStep(prevSig) {
    for (let i = 0; i < 12; i++) {
      await sleep(900);
      const ok = await detectCore(true);
      if (ok && fields.length && fieldSignature() !== prevSig) {
        await runMatch();
        return true;
      }
    }
    return false;
  }

  // ── 记录知识库 ──────────────────────────────────────────────────────────
  function buildOutcome(fillRes) {
    const filledSet = new Set((fillRes && fillRes.filled) || []);
    return mappings
      .filter((m) => m.profile_key)
      .map((m) => ({
        fid: m.fid,
        profile_key: m.profile_key,
        confidence: m.confidence,
        filled_ok: filledSet.has(m.fid),
        user_corrected: false,
        from: m.source === 'template' ? 'template' : 'match',
      }));
  }

  function sendRecord(outcome) {
    if (!outcome.length || !fields.length) return;
    AF.api.recordTemplate({
      page_url: AF.state.lastPage && AF.state.lastPage.url,
      page_title: AF.state.lastPage && AF.state.lastPage.title,
      fields,
      outcome,
    });
  }

  // ── 缺字段补充 ──────────────────────────────────────────────────────────
  async function onSupplemented(values) {
    const actions = [];
    const outcome = [];
    const suppFids = new Set();
    for (const v of values) {
      const f = fieldByFid(v.fid);
      if (!f || !v.value) continue;
      actions.push(Object.assign(actionFor(v.fid, v.value)));
      const key = 'custom:' + (f.label || f.name || v.fid);
      AF.state.profile[key] = v.value;
      outcome.push({
        fid: v.fid, profile_key: key,
        filled_ok: true, user_corrected: true, from: 'supplement',
      });
      suppFids.add(v.fid);
    }
    await AF.api.saveProfile(AF.state.profile);
    if (actions.length) {
      const r = await AF.api.fill({ actions });
      const filled = ((r && r.result && r.result.filled) || []).length;
      AF.toast(t('autofill.supplementFilled', { n: filled }), 'success');
      AF.api.highlight({
        items: actions.map((a) => ({ fid: a.fid, status: 'filled', frameId: a.frameId })),
      });
    }
    sendRecord(outcome);
    unresolved = unresolved.filter((u) => !suppFids.has(u.fid));

    // 多步流程中补充完 → 自动续跑
    if (mode === 'supplement') {
      if (unresolved.filter((u) => u.required).length) {
        setStatus(t('autofill.stillRequired'));
        renderResult();
      } else {
        await advance();
      }
    } else {
      renderResult();
    }
  }

  AF.detect = { init, render };
})();
