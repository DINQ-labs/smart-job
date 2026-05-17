/**
 * ext_shared/content/zhipin_inject.js — Boss 直聘注入扩展 (D2 / Sprint 3)
 *
 * 在 zhipin.com 注入：
 *   - 列表页每张卡片右侧 ★ 收藏按钮
 *   - 列表页顶部"批量分析"操作条
 *   - 详情页右下角浮动"AI 分析此职位"按钮
 *   - 各页面切换时上报 PageContext 给 sidepanel
 *
 * 依赖（同 ISOLATED world 加载顺序保证）：
 *   - ext_shared/core/page-context.js → window.DQ_PageContext
 *   - ext_shared/content/injection-widgets.js → window.DQ_Widgets
 *   - 旧 zhipin_eval_inject.js 继续保留"评估"按钮（互不干扰）
 *
 * 平台路由：
 *   /web/geek/jobs*           求职端职位列表 → ★ + 批量条
 *   /job_detail/<id>.html     职位详情      → 浮动 AI 按钮
 *   /web/boss/recommend       招聘端候选人列表 → ★ + 批量条
 *   /web/boss/chat            招聘端聊天     → 浮动 AI 起草按钮（暂留 TODO）
 */
(function () {
  'use strict';

  // 依赖未加载（加载顺序问题）→ 静默 noop
  if (!window.DQ_Widgets || !window.DQ_PageContext) return;
  const W = window.DQ_Widgets;
  const PC = window.DQ_PageContext;

  W.injectCommonStyles();

  const INJECT_FLAG = 'data-dq-inject';

  // ── 路由调度 ────────────────────────────────────────────────────────────
  function dispatch() {
    const path = location.pathname;
    if (/^\/web\/geek\/(jobs|job-recommend)/.test(path)) {
      runJobseekerList();
    } else if (/^\/job_detail\//.test(path)) {
      runJobDetail();
    } else if (/^\/web\/boss\/recommend/.test(path)) {
      runRecruiterList();
    }
    // 其它页面：仅上报 context，不注入 UI
    reportContext();
  }

  // ── 求职端：职位列表注入 ────────────────────────────────────────────────
  function runJobseekerList() {
    injectListBatchBar('jobseeker', 'job_list', 'jobseeker:find_best_jobs');
    scanAndInjectJobCards();
    // 列表是 SPA 动态加载，挂 MO 监听
    if (window._dqListMO) return;
    window._dqListMO = new MutationObserver((muts) => {
      for (const m of muts) {
        m.addedNodes.forEach((n) => {
          if (n.nodeType === 1) scanAndInjectJobCards(n);
        });
      }
    });
    window._dqListMO.observe(document.body, { childList: true, subtree: true });
  }

  function scanAndInjectJobCards(root) {
    const lis = (root || document).querySelectorAll(
      'li[ka^="job_card_"], li.job-card-wrapper, li.job-card-box, ul.job-list-box > li, .job-list-container li'
    );
    lis.forEach(injectStarOnJobCard);
  }

  function injectStarOnJobCard(li) {
    if (li.getAttribute(INJECT_FLAG)) return;
    const link = li.querySelector('a[href*="/job_detail/"]');
    if (!link) return;
    const href = link.getAttribute('href') || '';
    const m = href.match(/\/job_detail\/([^.?]+)\.html/);
    if (!m) return;
    const encryptJobId = m[1];

    const url = new URL(href, location.origin);
    const securityId = url.searchParams.get('securityId') || '';
    const lid = url.searchParams.get('lid') || '';
    const pick = (sel) => {
      const el = li.querySelector(sel);
      return el ? el.textContent.trim() : '';
    };
    const job_name = pick('.job-name, .job-title, [class*="job-name"]');
    const company_name = pick('.company-name, [class*="company-name"]');
    const salary_desc = pick('.salary, [class*="salary"]');

    li.setAttribute(INJECT_FLAG, '1');
    const star = W.createStarButton({
      role: 'jobseeker', platform: 'boss',
      external_id: encryptJobId,
      fields: { job_name, company_name, salary_desc,
                list_security_id: securityId, lid },
    });
    const anchor = li.querySelector('.job-name, .job-title, [class*="job-title-text"]')
                 || link;
    if (anchor) anchor.insertAdjacentElement('afterend', star);
    else li.appendChild(star);
  }

  // ── 招聘端：候选人列表 ─────────────────────────────────────────────────
  function runRecruiterList() {
    injectListBatchBar('recruiter', 'candidate_list', 'recruiter:bulk_analyze_candidates');
    scanAndInjectCandidateCards();
    if (window._dqRecListMO) return;
    window._dqRecListMO = new MutationObserver((muts) => {
      muts.forEach(m => m.addedNodes.forEach(n => n.nodeType === 1 && scanAndInjectCandidateCards(n)));
    });
    window._dqRecListMO.observe(document.body, { childList: true, subtree: true });
  }

  function scanAndInjectCandidateCards(root) {
    const cards = (root || document).querySelectorAll(
      'li[ka^="geek_card_"], li.geek-item, .recommend-list li, [class*="geek-card"]'
    );
    cards.forEach(injectStarOnCandidateCard);
  }

  function injectStarOnCandidateCard(card) {
    if (card.getAttribute(INJECT_FLAG)) return;
    // Boss 候选人卡片 encryptUid + securityId 通常在内部 data 属性或 link
    const link = card.querySelector('a[href*="encryptUid="], a[href*="/web/boss/geek-detail/"]');
    if (!link) return;
    const href = link.getAttribute('href') || '';
    const u = new URL(href, location.origin);
    const encryptUid = u.searchParams.get('encryptUid')
                    || (href.match(/geek-detail\/([^.?/]+)/) || [])[1] || '';
    const securityId = u.searchParams.get('securityId') || '';
    if (!encryptUid) return;

    const pick = (sel) => {
      const el = card.querySelector(sel);
      return el ? el.textContent.trim() : '';
    };
    const geek_name = pick('.name, [class*="geek-name"]');
    const work_year = pick('.work-year, [class*="exp"]');
    const degree = pick('.degree, [class*="degree"]');
    const salary = pick('.salary, [class*="salary"]');
    const city = pick('.city, [class*="city"]');

    card.setAttribute(INJECT_FLAG, '1');
    const star = W.createStarButton({
      role: 'recruiter', platform: 'boss',
      external_id: encryptUid,
      fields: { geek_name, work_year, degree, salary, city,
                search_security_id: securityId },
    });
    const anchor = card.querySelector('.name, [class*="geek-name"]') || link;
    if (anchor) anchor.insertAdjacentElement('afterend', star);
    else card.appendChild(star);
  }

  // ── 列表顶部批量条 ────────────────────────────────────────────────────
  function injectListBatchBar(role, kind, template_id) {
    if (document.querySelector('.dq-batch-bar')) return;
    // 找列表容器
    const host = document.querySelector('.job-list-container, ul.job-list-box, .recommend-list')
              || document.querySelector('[class*="list-container"]');
    if (!host) return;
    const isRecruiter = role === 'recruiter';
    const objLabel = isRecruiter
      ? (typeof t === 'function' ? t('content.batchBarCandidateLabel') : '候选人')
      : (typeof t === 'function' ? t('content.batchBarJobLabel') : '职位');
    const bar = W.createBatchBar({
      text: (typeof t === 'function' ? t('content.batchAiAnalysis', {label: objLabel}) : `<b>批量 AI 分析</b> 当前页 ${objLabel}，命中高匹配自动收藏`),
      secondary: {
        label: (typeof t === 'function' ? t('content.bookmarkList') : '收藏列表'),
        onClick: () => {
          chrome.runtime.sendMessage({ type: 'DQ_OPEN_CHAT_WITH_CONTEXT',
                                        prompt: isRecruiter
                                          ? (typeof t === 'function' ? t('content.viewBookmarkedCandidates') : '@查看我收藏的候选人')
                                          : (typeof t === 'function' ? t('content.viewBookmarkedJobs') : '@查看我收藏的职位') });
        },
      },
      primary: {
        label: (typeof t === 'function' ? t('content.startAnalysis') : '开始分析'),
        onClick: () => {
          chrome.runtime.sendMessage({
            type: 'DQ_TRIGGER_TASK',
            template_id,
            platform: 'boss',
            inputs: { keyword: '', desired_count: 30 },
          }, (resp) => {
            if (resp && resp.ok) {
              W.showToast((typeof t === 'function' ? t('content.taskCreated', {taskId: resp.task_id || ''}) : `任务已创建 #${resp.task_id || ''}`), 'success');
            } else {
              W.showToast(resp?.error || (typeof t === 'function' ? t('content.taskFail') : '任务创建失败'), 'error');
            }
          });
        },
      },
    });
    host.parentNode?.insertBefore(bar, host);
  }

  // ── 职位详情：浮动 AI 按钮 ────────────────────────────────────────────
  function runJobDetail() {
    const m = location.pathname.match(/\/job_detail\/([^.?]+)\.html/);
    if (!m) return;
    const encryptJobId = m[1];
    const title = (document.querySelector('h1, .job-name, [class*="job-title"]')?.textContent || '').trim();
    const company = (document.querySelector('[class*="company-name"]')?.textContent || '').trim();

    W.createFloatingAiButton({
      emoji: '🎯',
      label: (typeof t === 'function' ? t('content.aiAnalyzeJob') : 'AI 分析此职位'),
      prompt: (typeof t === 'function' ? t('content.aiAnalyzeBossJobPrompt', {title}) : `分析这个职位「${title}」和我的简历匹配度，给出评分和建议。`),
      context: {
        platform: 'boss', kind: 'job_detail',
        external_id: encryptJobId,
        fields: { job_name: title, company_name: company },
      },
    });
  }

  // ── PageContext 上报 ─────────────────────────────────────────────────
  function reportContext() {
    const meta = PC.detectPageKind(location.href);
    if (meta.kind === 'unknown') {
      PC.reportEmpty('boss:not_a_target_page');
      return;
    }
    const fields = {};
    if (meta.kind === 'job_detail') {
      const m = location.pathname.match(/\/job_detail\/([^.?]+)\.html/);
      fields.external_id = m ? m[1] : '';
      fields.job_name = (document.querySelector('h1, .job-name')?.textContent || '').trim();
      fields.company_name = (document.querySelector('[class*="company-name"]')?.textContent || '').trim();
    } else if (meta.kind === 'job_list' || meta.kind === 'candidate_list') {
      fields.count = document.querySelectorAll('li.job-card-wrapper, li.job-card-box, li[ka^="geek_card_"]').length;
    }
    PC.report(PC.compose({ platform: 'boss', kind: meta.kind, role: meta.role, fields }));
  }

  // ── 启动 ───────────────────────────────────────────────────────────────
  function start() { dispatch(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
  // SPA 路由变化 → 重新 dispatch
  PC.watchUrl(() => {
    // 清旧浮按钮（详情页 → 列表页时移除浮标）
    document.querySelectorAll('.dq-float-ai').forEach(el => el.remove());
    document.querySelectorAll('.dq-batch-bar').forEach(el => el.remove());
    dispatch();
  });
})();
