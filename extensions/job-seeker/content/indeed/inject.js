/**
 * ext_shared/content/indeed_inject.js — Indeed 注入 (D4 / Sprint 3)
 *
 * 覆盖：
 *   /jobs (q=...)             职位搜索：★ + 批量条
 *   /viewjob?jk=<id>          职位详情：浮动 AI 按钮
 *   /smartapply 路径          申请表单：顶部"AI 已填写"提示条
 *
 * Indeed 招聘端目前没接（indeed_employer_* 工具是另一套 worker tab 模式，
 * 不在本次 sprint 范围）。
 */
(function () {
  'use strict';
  if (!window.DQ_Widgets || !window.DQ_PageContext) return;
  const W = window.DQ_Widgets;
  const PC = window.DQ_PageContext;
  W.injectCommonStyles();

  const INJECT_FLAG = 'data-dq-inject';

  function dispatch() {
    const path = location.pathname;
    if (/^\/jobs/.test(path) || /^\/q-.*-jobs/.test(path)) {
      runJobsList();
    } else if (/^\/viewjob/.test(path)) {
      runJobDetail();
    } else if (/\/smartapply/.test(path)) {
      runApplyForm();
    }
    reportContext();
  }

  // ── Jobs 列表 ────────────────────────────────────────────────────────────
  function runJobsList() {
    injectBatchBar();
    scanJobsList();
    if (window._dqIndeedJobMO) return;
    window._dqIndeedJobMO = new MutationObserver((muts) => {
      muts.forEach(m => m.addedNodes.forEach(n => n.nodeType === 1 && scanJobsList(n)));
    });
    window._dqIndeedJobMO.observe(document.body, { childList: true, subtree: true });
  }

  function scanJobsList(root) {
    // Indeed 列表 selector：mosaic-provider-jobcards / jobsearch-SerpJobCard
    const cards = (root || document).querySelectorAll(
      'div.job_seen_beacon, td.resultContent, div.jobsearch-SerpJobCard, li.cardOutline'
    );
    cards.forEach(injectStarOnJobCard);
  }

  function injectStarOnJobCard(card) {
    if (card.getAttribute(INJECT_FLAG)) return;
    // jk 从内部 link 拿
    const a = card.querySelector('a[data-jk], a[href*="jk="], a[href*="/viewjob"]');
    if (!a) return;
    let jk = a.getAttribute('data-jk');
    if (!jk) {
      const m = (a.getAttribute('href') || '').match(/[?&]jk=([^&]+)/);
      if (m) jk = m[1];
    }
    if (!jk) return;
    const pick = (sel) => {
      const el = card.querySelector(sel);
      return el ? el.textContent.trim() : '';
    };
    const job_name = pick('h2.jobTitle, .jobTitle, h2 a span');
    const company_name = pick('[data-testid="company-name"], .companyName, span.companyName');
    const city = pick('[data-testid="text-location"], .companyLocation');
    const salary_desc = pick('.salary-snippet-container, .estimated-salary, [data-testid="attribute_snippet_compensation"]');

    card.setAttribute(INJECT_FLAG, '1');
    const star = W.createStarButton({
      role: 'jobseeker', platform: 'indeed',
      external_id: jk,
      fields: { job_name, company_name, salary_desc, city },
    });
    const anchor = card.querySelector('h2.jobTitle, .jobTitle, h2') || a;
    if (anchor) anchor.insertAdjacentElement('afterend', star);
    else card.appendChild(star);
  }

  function injectBatchBar() {
    if (document.querySelector('.dq-batch-bar')) return;
    const host = document.querySelector('#mosaic-provider-jobcards, .jobsearch-ResultsList, main')
              || document.body;
    const bar = W.createBatchBar({
      text: (typeof t === 'function' ? t('content.batchAiAnalysisIndeed') : '<b>批量 AI 分析</b> 当前搜索结果，命中高匹配自动收藏'),
      primary: {
        label: (typeof t === 'function' ? t('content.startAnalysis') : '开始分析'),
        onClick: () => {
          chrome.runtime.sendMessage({
            type: 'DQ_TRIGGER_TASK',
            template_id: 'jobseeker:find_best_jobs',
            platform: 'indeed',
            inputs: { keyword: '', desired_count: 25 },
          }, (resp) => {
            W.showToast(resp?.ok
              ? (typeof t === 'function' ? t('content.taskCreated', {taskId: resp.task_id || ''}) : `任务已创建 #${resp.task_id || ''}`)
              : (resp?.error || (typeof t === 'function' ? t('content.taskFailed') : '失败')),
                        resp?.ok ? 'success' : 'error');
          });
        },
      },
    });
    host.parentNode?.insertBefore(bar, host);
  }

  // ── 职位详情 ────────────────────────────────────────────────────────────
  function runJobDetail() {
    const m = (location.search || '').match(/[?&]jk=([^&]+)/);
    if (!m) return;
    const jk = m[1];
    const title = (document.querySelector('h1, .jobsearch-JobInfoHeader-title')?.textContent || '').trim();
    const company = (document.querySelector('[data-testid="inlineHeader-companyName"], .icl-u-lg-mr--sm')?.textContent || '').trim();
    W.createFloatingAiButton({
      emoji: '🎯',
      label: (typeof t === 'function' ? t('content.aiAnalyzeJob') : 'AI 分析此职位'),
      prompt: (typeof t === 'function' ? t('content.aiAnalyzeIndeedJobPrompt', {title}) : `分析这个 Indeed 职位「${title}」和我的简历匹配度。`),
      context: { platform: 'indeed', kind: 'job_detail',
                 external_id: jk, fields: { job_name: title, company_name: company } },
    });
  }

  // ── 申请表单 ────────────────────────────────────────────────────────────
  function runApplyForm() {
    if (document.querySelector('.dq-apply-banner')) return;
    const banner = document.createElement('div');
    banner.className = 'dq-widget dq-apply-banner';
    banner.style.cssText = `
      position: sticky; top: 0; z-index: 1000;
      padding: 12px 16px; margin-bottom: 12px;
      background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 12px;
      font-size: 13px; font-weight: 600; color: #2563eb;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      display: flex; align-items: center; gap: 8px;
    `;
    banner.innerHTML = `<span>✨</span><span>${typeof t === 'function' ? t('content.applyBannerText') : 'AI 已填写大部分字段，请检查并补全剩余必填项'}</span>`;
    const host = document.querySelector('main, form, .ia-IndeedApply-form')
              || document.body;
    host.insertBefore(banner, host.firstChild);
  }

  // ── PageContext 上报 ─────────────────────────────────────────────────────
  function reportContext() {
    const meta = PC.detectPageKind(location.href);
    if (meta.kind === 'unknown') { PC.reportEmpty('indeed:not_a_target_page'); return; }
    const fields = {};
    if (meta.kind === 'job_detail') {
      const m = (location.search || '').match(/[?&]jk=([^&]+)/);
      fields.external_id = m ? m[1] : '';
      fields.job_name = (document.querySelector('h1')?.textContent || '').trim();
    }
    PC.report(PC.compose({ platform: 'indeed', kind: meta.kind, role: meta.role, fields }));
  }

  function start() { dispatch(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
  PC.watchUrl(() => {
    document.querySelectorAll('.dq-float-ai').forEach(el => el.remove());
    document.querySelectorAll('.dq-batch-bar').forEach(el => el.remove());
    document.querySelectorAll('.dq-apply-banner').forEach(el => el.remove());
    dispatch();
  });
})();
