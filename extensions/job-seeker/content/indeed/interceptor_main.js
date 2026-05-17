/**
 * interceptor_indeed_main.js — world: MAIN, run_at: document_start
 * 拦截 Indeed 搜索 API 响应，提取 jobKey 列表，
 * 通过 postMessage 发给 ISOLATED world 的 interceptor.js，再转发给 background.js。
 */
(function () {
  'use strict';

  const _origFetch = window.fetch.bind(window);

  window.fetch = async function (...args) {
    const req = args[0];
    const url = typeof req === 'string' ? req : (req instanceof Request ? req.url : String(req));
    const resp = await _origFetch(...args);

    // 审核补丁 #F2：post-fetch 处理放 try/catch。resp.clone() 可能抛（body 已被
    // 消费），放外面会把异常传回页面 fetch 调用方。
    try {
      const isJobSearch = url.includes('indeed.com') && (
        url.includes('/rpc/jobsearch') ||
        url.includes('/jobs') ||
        url.includes('/m/basecamp/viewjob') ||
        url.includes('/viewjob')
      );

      if (isJobSearch) {
        resp.clone().json().then(json => {
          _handleIndeedResponse(url, json);
        }).catch(() => {});
      }
    } catch (e) {}

    return resp;
  };

  function _handleIndeedResponse(url, json) {
    // Indeed 搜索结果格式（多种端点兼容）
    const rawJobs = json?.results
      || json?.jobCards
      || json?.data?.jobcards
      || [];

    const jobs = rawJobs.map(j => ({
      jobKey: j.jobkey || j.jobKey || j.jk || '',
      title: j.jobtitle || j.title || '',
      company: j.company || j.companyName || '',
      location: j.formattedLocation || j.location || '',
    })).filter(j => j.jobKey);

    if (jobs.length > 0) {
      window.postMessage({
        __dinqExt: true,
        site: 'indeed',
        type: 'JOB_SEARCH_TOKENS',
        jobs,
      }, 'https://www.indeed.com');
    }
  }
})();
