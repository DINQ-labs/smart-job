/**
 * ext_shared/core/page-context.js — 共享 PageContext 提取库 (D1)
 *
 * 三平台 content script 共用：
 *   - detectPageKind(url): 返回页面种类（'job_list' / 'job_detail' / ...）
 *   - reportPageContext(payload): 通过 chrome.runtime.sendMessage 上报到 background
 *   - watchUrl(callback): 监听 SPA 路由变化（pushState/replaceState/popstate）
 *
 * 各平台 inject 脚本只负责"从 DOM 抽业务字段"，url→kind 映射统一在这里。
 *
 * 调用方约定（D2/D3/D4 注入脚本）：
 *   const ctx = window.DQ_PageContext.compose({ platform, kind, fields });
 *   window.DQ_PageContext.report(ctx);
 *
 * world: ISOLATED（content script），不污染页面 window。
 */
(function () {
  'use strict';

  // ── 平台 URL → kind 映射 ────────────────────────────────────────────────
  // 每个平台维护一组正则 → kind 的映射。第一个匹配命中即返回。
  const URL_PATTERNS = {
    boss: [
      { re: /^\/web\/geek\/(jobs|job-recommend)/,         kind: 'job_list',         role: 'jobseeker' },
      { re: /^\/web\/geek\/chat/,                         kind: 'chat',             role: 'jobseeker' },
      { re: /^\/job_detail\//,                            kind: 'job_detail',       role: 'jobseeker' },
      { re: /^\/web\/boss\/recommend/,                    kind: 'candidate_list',   role: 'recruiter' },
      { re: /^\/web\/boss\/chat/,                         kind: 'chat',             role: 'recruiter' },
      { re: /^\/web\/boss\/jobs/,                         kind: 'job_management',   role: 'recruiter' },
    ],
    linkedin: [
      { re: /^\/jobs\/search/,                            kind: 'job_list',         role: 'jobseeker' },
      { re: /^\/jobs\/view\//,                            kind: 'job_detail',       role: 'jobseeker' },
      { re: /^\/jobs\/collections/,                       kind: 'job_list',         role: 'jobseeker' },
      { re: /^\/talent\/search/,                          kind: 'candidate_list',   role: 'recruiter' },
      { re: /^\/talent\/hire/,                            kind: 'candidate_list',   role: 'recruiter' },
      { re: /^\/in\//,                                    kind: 'candidate_detail', role: 'recruiter' },
      { re: /^\/messaging/,                               kind: 'chat',             role: 'recruiter' },
    ],
    indeed: [
      { re: /^\/jobs/,                                    kind: 'job_list',         role: 'jobseeker' },
      { re: /^\/q-.*-jobs/,                               kind: 'job_list',         role: 'jobseeker' },
      { re: /^\/viewjob/,                                 kind: 'job_detail',       role: 'jobseeker' },
      { re: /\/smartapply/,                               kind: 'apply_form',       role: 'jobseeker' },
      { re: /\/applied/,                                  kind: 'apply_history',    role: 'jobseeker' },
      { re: /^\/employer\/dashboard/,                     kind: 'candidate_list',   role: 'recruiter' },
    ],
  };

  function detectPlatform(url) {
    const host = (typeof url === 'string' ? new URL(url, location.origin) : url).host;
    if (/zhipin\.com$/.test(host) || host === 'zhipin.com') return 'boss';
    if (/linkedin\.com$/.test(host))                          return 'linkedin';
    if (/indeed\.com$/.test(host))                            return 'indeed';
    return '';
  }

  function detectPageKind(url) {
    const u = (typeof url === 'string' ? new URL(url, location.origin) : url);
    const platform = detectPlatform(u);
    const patterns = URL_PATTERNS[platform] || [];
    const path = u.pathname;
    for (const p of patterns) {
      if (p.re.test(path)) {
        return { platform, kind: p.kind, role: p.role };
      }
    }
    return { platform, kind: 'unknown', role: '' };
  }

  // ── 组装 + 上报 ────────────────────────────────────────────────────────
  function compose(opts) {
    const url = opts.url || location.href;
    const meta = detectPageKind(url);
    return {
      type: 'DQ_PAGE_CONTEXT',
      platform: opts.platform || meta.platform,
      kind:     opts.kind     || meta.kind,
      role:     opts.role     || meta.role,
      url,
      title:    opts.title    || document.title || '',
      fields:   opts.fields   || {},      // 业务字段：job_name/company/salary/jd_excerpt / geek_name/...
      ts:       Date.now(),
    };
  }

  let _lastReportedHash = '';

  function _hashOf(ctx) {
    return [ctx.platform, ctx.kind, ctx.url, ctx.title,
            JSON.stringify(ctx.fields || {})].join('|');
  }

  function report(ctx) {
    if (!ctx || typeof ctx !== 'object') return;
    // 同 context 不重复上报（去抖 SPA 路由抖动）
    const h = _hashOf(ctx);
    if (h === _lastReportedHash) return;
    _lastReportedHash = h;
    try {
      chrome.runtime.sendMessage(ctx, () => { void chrome.runtime.lastError; });
    } catch (_e) { /* SW 冷启动时 sendMessage 偶发失败，下次 url 变更会重试 */ }
  }

  function reportEmpty(reason) {
    // 非平台页 / 路由切换到不支持页 → 上报 kind='unknown' 让 sidepanel 隐藏 ContextBar
    report({
      type: 'DQ_PAGE_CONTEXT',
      platform: detectPlatform(location.href),
      kind: 'unknown',
      role: '',
      url: location.href,
      title: document.title || '',
      fields: {},
      ts: Date.now(),
      reason: reason || '',
    });
  }

  // ── SPA 路由变化监听 ────────────────────────────────────────────────────
  // Boss/LinkedIn/Indeed 都是 SPA，页面 url 切换不重载，需 hook history API
  function watchUrl(callback) {
    let lastUrl = location.href;
    const fire = () => {
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        try { callback(location.href); } catch (_) { /* swallow */ }
      }
    };
    const wrap = (fn) => function () {
      const r = fn.apply(this, arguments);
      // 微任务延迟 → 让浏览器渲染完新 url 再回调
      Promise.resolve().then(fire);
      return r;
    };
    history.pushState   = wrap(history.pushState);
    history.replaceState = wrap(history.replaceState);
    window.addEventListener('popstate', fire);
    // 兜底：路由变化但 hash 也变（zhipin 部分页面）
    window.addEventListener('hashchange', fire);
  }

  // ── 暴露到 window（ISOLATED world 不污染 MAIN world） ────────────────────
  window.DQ_PageContext = {
    detectPlatform,
    detectPageKind,
    compose,
    report,
    reportEmpty,
    watchUrl,
  };
})();
