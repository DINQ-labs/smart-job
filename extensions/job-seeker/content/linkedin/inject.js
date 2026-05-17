/**
 * ext_shared/content/linkedin_inject.js — LinkedIn 注入 (D3 / Sprint 3)
 *
 * 双身份覆盖（求职 + 招聘）：
 *   /jobs/search /jobs/collections      Jobs 搜索：★ 收藏 + 顶部批量条
 *   /jobs/view/<id>                     Jobs 详情：浮动"AI 分析此职位"
 *   /talent/search /talent/hire         Talent 搜索（招聘端）：★ + 批量条
 *   /in/<publicId>                      Profile：浮动"AI 分析此候选人"
 *   /messaging                          消息：浮动"AI 起草"按钮（TODO，依赖 InMail compose 入口）
 *
 * LinkedIn job-id 在 URL 是数字，也可能在 jobPostingUrn entity card 上。
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
    if (/^\/jobs\/search/.test(path) || /^\/jobs\/collections/.test(path)) {
      runJobsList();
    } else if (/^\/jobs\/view\//.test(path)) {
      runJobDetail();
    } else if (/^\/talent\/(search|hire)/.test(path)) {
      runTalentList();
    } else if (/^\/in\//.test(path)) {
      runProfile();
    }
    reportContext();
  }

  // ── Jobs 搜索列表（求职端） ──────────────────────────────────────────────
  function runJobsList() {
    injectBatchBar('jobseeker', 'jobseeker:find_best_jobs', '职位');
    scanJobsList();
    if (window._dqLiJobMO) return;
    window._dqLiJobMO = new MutationObserver((muts) => {
      muts.forEach(m => m.addedNodes.forEach(n => n.nodeType === 1 && scanJobsList(n)));
    });
    window._dqLiJobMO.observe(document.body, { childList: true, subtree: true });
  }

  function scanJobsList(root) {
    // LinkedIn Jobs UI: 当前版本用 div.job-card-container 或 li.scaffold-layout__list-item
    const cards = (root || document).querySelectorAll(
      'div.job-card-container, li.scaffold-layout__list-item, li.jobs-search-results__list-item'
    );
    cards.forEach(injectStarOnJobCard);
  }

  function injectStarOnJobCard(card) {
    if (card.getAttribute(INJECT_FLAG)) return;
    // 提取 jobId from URL on anchor
    const a = card.querySelector('a[href*="/jobs/view/"]');
    if (!a) return;
    const m = a.getAttribute('href').match(/\/jobs\/view\/(\d+)/);
    if (!m) return;
    const jobId = m[1];
    const pick = (sel) => {
      const el = card.querySelector(sel);
      return el ? el.textContent.trim() : '';
    };
    const job_name = pick('.job-card-list__title, .job-card-container__link, h3, h4');
    const company_name = pick('.job-card-container__company-name, [data-test-job-card-company-name]');
    const city = pick('.job-card-container__metadata-item, .artdeco-entity-lockup__caption');

    card.setAttribute(INJECT_FLAG, '1');
    const star = W.createStarButton({
      role: 'jobseeker', platform: 'linkedin',
      external_id: jobId,
      fields: { job_name, company_name, city },
    });
    const anchor = card.querySelector('h3, h4, .job-card-list__title') || a;
    if (anchor) anchor.insertAdjacentElement('afterend', star);
    else card.appendChild(star);
  }

  // ── Jobs 详情：浮动按钮 ──────────────────────────────────────────────────
  function runJobDetail() {
    const m = location.pathname.match(/\/jobs\/view\/(\d+)/);
    if (!m) return;
    const jobId = m[1];
    const title = (document.querySelector('h1, .top-card-layout__title, .jobs-unified-top-card__job-title')?.textContent || '').trim();
    const company = (document.querySelector('.top-card-layout__entity-info, .jobs-unified-top-card__company-name')?.textContent || '').trim();
    W.createFloatingAiButton({
      emoji: '🎯',
      label: (typeof t === 'function' ? t('content.aiAnalyzeJob') : 'AI 分析此职位'),
      prompt: (typeof t === 'function' ? t('content.aiAnalyzeLinkedinJobPrompt', {title}) : `分析这个 LinkedIn 职位「${title}」和我的简历匹配度，给出评分和建议。`),
      context: { platform: 'linkedin', kind: 'job_detail',
                 external_id: jobId, fields: { job_name: title, company_name: company } },
    });
  }

  // ── Talent 列表（招聘端） ────────────────────────────────────────────────
  function runTalentList() {
    injectBatchBar('recruiter', 'recruiter:bulk_analyze_candidates', '候选人');
    scanTalentList();
    if (window._dqLiTalentMO) return;
    window._dqLiTalentMO = new MutationObserver((muts) => {
      muts.forEach(m => m.addedNodes.forEach(n => n.nodeType === 1 && scanTalentList(n)));
    });
    window._dqLiTalentMO.observe(document.body, { childList: true, subtree: true });
  }

  function scanTalentList(root) {
    const cards = (root || document).querySelectorAll(
      'div.talent-search-result, li.search-result, li[class*="result-card"]'
    );
    cards.forEach(injectStarOnTalentCard);
  }

  function injectStarOnTalentCard(card) {
    if (card.getAttribute(INJECT_FLAG)) return;
    const a = card.querySelector('a[href*="/in/"]');
    if (!a) return;
    const m = a.getAttribute('href').match(/\/in\/([^/?#]+)/);
    if (!m) return;
    const publicId = m[1];
    const pick = (sel) => {
      const el = card.querySelector(sel);
      return el ? el.textContent.trim() : '';
    };
    const geek_name = pick('h3, .entity-result__title-text, .talent-search-result__name');
    const position = pick('.entity-result__primary-subtitle, [class*="headline"]');
    const city = pick('.entity-result__secondary-subtitle, [class*="location"]');

    card.setAttribute(INJECT_FLAG, '1');
    const star = W.createStarButton({
      role: 'recruiter', platform: 'linkedin',
      external_id: publicId,
      fields: { geek_name, salary: '', city,
                experience_excerpt: position },
    });
    const anchor = card.querySelector('h3, .entity-result__title-text') || a;
    if (anchor) anchor.insertAdjacentElement('afterend', star);
    else card.appendChild(star);
  }

  // ── Profile 页：浮动按钮 ─────────────────────────────────────────────────
  function runProfile() {
    const m = location.pathname.match(/^\/in\/([^/?#]+)/);
    if (!m) return;
    const publicId = m[1];
    const name = (document.querySelector('h1, .text-heading-xlarge')?.textContent || '').trim();
    W.createFloatingAiButton({
      emoji: '🔍',
      label: (typeof t === 'function' ? t('content.aiAnalyzeCandidate') : 'AI 分析此候选人'),
      prompt: (typeof t === 'function' ? t('content.aiAnalyzeCandidatePrompt', {name}) : `分析这位 LinkedIn 候选人「${name}」是否匹配我的招聘需求。`),
      context: { platform: 'linkedin', kind: 'candidate_detail',
                 external_id: publicId, fields: { geek_name: name } },
    });
  }

  // ── 共用：列表批量条 ─────────────────────────────────────────────────────
  function injectBatchBar(role, template_id, label) {
    if (document.querySelector('.dq-batch-bar')) return;
    const host = document.querySelector('.jobs-search-results-list, .scaffold-layout__list, .search-results__list')
              || document.querySelector('main');
    if (!host) return;
    const i18nLabel = role === 'recruiter'
      ? (typeof t === 'function' ? t('content.batchBarCandidateLabel') : label)
      : (typeof t === 'function' ? t('content.batchBarJobLabel') : label);
    const bar = W.createBatchBar({
      text: (typeof t === 'function' ? t('content.batchAiAnalysisLinkedin', {label: i18nLabel}) : `<b>批量 AI 分析</b> ${label}，命中高匹配自动收藏`),
      primary: {
        label: (typeof t === 'function' ? t('content.startAnalysis') : '开始分析'),
        onClick: () => {
          chrome.runtime.sendMessage({
            type: 'DQ_TRIGGER_TASK', template_id, platform: 'linkedin',
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

  // ── PageContext 上报 ─────────────────────────────────────────────────────
  function reportContext() {
    const meta = PC.detectPageKind(location.href);
    if (meta.kind === 'unknown') { PC.reportEmpty('linkedin:not_a_target_page'); return; }
    const fields = {};
    if (meta.kind === 'job_detail') {
      const m = location.pathname.match(/\/jobs\/view\/(\d+)/);
      fields.external_id = m ? m[1] : '';
      fields.job_name = (document.querySelector('h1, .top-card-layout__title')?.textContent || '').trim();
    } else if (meta.kind === 'candidate_detail') {
      const m = location.pathname.match(/^\/in\/([^/?#]+)/);
      fields.external_id = m ? m[1] : '';
      fields.geek_name = (document.querySelector('h1')?.textContent || '').trim();
    }
    PC.report(PC.compose({ platform: 'linkedin', kind: meta.kind, role: meta.role, fields }));
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
    dispatch();
  });
})();
