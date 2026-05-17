/**
 * interceptor_linkedin_main.js — world: MAIN, run_at: document_start
 * 拦截 LinkedIn Voyager API 和 GraphQL 响应，提取人才搜索结果的 memberUrn + trackingId，
 * 通过 postMessage 发给 ISOLATED world 的 interceptor.js，再转发给 background.js。
 */
(function () {
  'use strict';

  const _origFetch = window.fetch.bind(window);

  window.fetch = async function (...args) {
    const req = args[0];
    const url = typeof req === 'string' ? req : (req instanceof Request ? req.url : String(req));

    // 审核补丁 #F2：pre-fetch 钩子放 try/catch，失败不影响页面 fetch。
    try {
      // Capture queryId from OUTGOING GraphQL requests (before calling original fetch)
      if (url.includes('/graphql?') || url.includes('/graphql&') || url.includes('graphql?variables')) {
        const m = url.match(/[?&]queryId=([\w.]+)/);
        if (m && m[1].includes('.')) {
          window.postMessage({
            __dinqExt: true, site: 'linkedin', type: 'QUERY_ID',
            queryId: m[1], capturedAt: Date.now(),
          }, 'https://www.linkedin.com');
        }
      }
    } catch (e) {}

    const resp = await _origFetch(...args);

    // post-fetch 处理全部放 try/catch。resp.clone() 可能抛（body 已被消费），
    // 放外面会把异常传回页面 fetch 调用方。
    try {
      if (url.includes('/voyager/api/') || url.includes('/graphql')) {
        resp.clone().json().then(json => {
          _handleLinkedinResponse(url, json);
        }).catch(() => {});
      }
    } catch (e) {}

    return resp;
  };

  function _handleLinkedinResponse(url, json) {
    // ── 人才搜索结果（GraphQL）────────────────────────────────────────────
    const clusters = json?.data?.searchDashByAll?.elements
      || json?.data?.searchDashClusters?.elements
      || [];

    const people = [];
    for (const cluster of clusters) {
      const items = cluster?.items || cluster?.elements || [];
      for (const item of items) {
        const r = item?.item?.entityResult || item?.entityResult;
        if (!r) continue;
        const memberUrn = r.targetUrn || r.entityUrn || '';
        if (!memberUrn) continue;
        // publicId：从 profileUrl 或 trackingUrn 中提取
        let publicId = '';
        if (r.navigationUrl) {
          const m = r.navigationUrl.match(/\/in\/([^/?]+)/);
          if (m) publicId = m[1];
        }
        people.push({
          memberUrn,
          trackingId: r.trackingId || '',
          publicId,
          name: r.title?.text || '',
        });
      }
    }

    if (people.length > 0) {
      window.postMessage({
        __dinqExt: true,
        site: 'linkedin',
        type: 'PEOPLE_SEARCH_TOKENS',
        people,
      }, 'https://www.linkedin.com');
    }

    // ── 职位搜索结果 ──────────────────────────────────────────────────────
    const jobElements = json?.data?.jobsDashJobCardsByJobCollectionSlug?.elements
      || json?.included?.filter(e => e.$type === 'com.linkedin.voyager.jobs.JobPosting') || [];

    const jobs = [];
    for (const j of jobElements) {
      const jobUrn = j.entityUrn || j['*jobPosting'] || '';
      if (!jobUrn) continue;
      jobs.push({
        jobUrn,
        trackingId: j.trackingUrn || j.trackingId || '',
        title: j.title || '',
        companyName: j.primaryDescription?.text || '',
      });
    }

    if (jobs.length > 0) {
      window.postMessage({
        __dinqExt: true,
        site: 'linkedin',
        type: 'JOB_SEARCH_TOKENS',
        jobs,
      }, 'https://www.linkedin.com');
    }
  }
})();
