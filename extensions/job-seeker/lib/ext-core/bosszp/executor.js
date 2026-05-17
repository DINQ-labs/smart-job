/**
 * bosszp/executor.js — 薄包装器
 *
 * 将原有 zhipin 专属接口委托给 core/site-executor.js，
 * 接口 100% 向后兼容，call site 无需修改。
 *
 * 依赖（全局）：ensureSiteWorkerTab, executeSiteApi, executeSiteApiBinary,
 *              navigateSiteWorkerTab, resetSiteWorkerTab, waitForSiteTabLoad
 *              （均由 core/site-executor.js 提供）
 */

const ZHIPIN_ORIGIN = 'https://www.zhipin.com';

/** 获取或创建 zhipin Worker Tab */
async function ensureWorkerTab() {
  return ensureSiteWorkerTab('zhipin');
}

/** 等待 tab 加载完成（向后兼容） */
function waitForTabLoad(tabId, timeoutMs) {
  return waitForSiteTabLoad(tabId, timeoutMs);
}

/**
 * 在 zhipin Worker Tab 的 MAIN world 中执行 fetch 并返回解析后的 JSON。
 * @param {string} path    - API 路径（相对于 zhipin origin），如 /wapi/zpjob/list/geek/v2
 * @param {object} options - fetch 选项：method, headers, body
 */
async function executeBossApi(path, options = {}) {
  // 合并 zhipin 专属默认 headers
  const mergedOptions = {
    ...options,
    headers: {
      'Origin': ZHIPIN_ORIGIN,
      'Referer': ZHIPIN_ORIGIN + '/',
      'Accept-Language': 'zh-CN,zh;q=0.9',
      ...(options.headers || {}),
    },
  };
  return executeSiteApi('zhipin', ZHIPIN_ORIGIN + path, mergedOptions);
}

/**
 * 在 zhipin Worker Tab 的 MAIN world 中执行二进制 fetch，返回 base64 Data URL。
 * @param {string} path - API 路径
 */
async function executeBossApiBinary(path) {
  return executeSiteApiBinary('zhipin', ZHIPIN_ORIGIN + path);
}

/** 导航 zhipin Worker Tab 到指定 URL（用于登录） */
async function navigateWorkerTab(url) {
  return navigateSiteWorkerTab('zhipin', url);
}

/** 重置 zhipin Worker Tab 状态（供 logout 时调用） */
function resetWorkerTab() {
  return resetSiteWorkerTab('zhipin');
}
