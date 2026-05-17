/**
 * interceptor_main.js — world: MAIN, run_at: document_start
 * 在页面主世界直接拦截 fetch，通过 postMessage 上报给 ISOLATED world。
 * 不注入任何 script 标签，不受 CSP 限制。
 */
(function () {
  if (window.__BOSS_API_INTERCEPTOR__) return;
  window.__BOSS_API_INTERCEPTOR__ = true;

  const origFetch = window.fetch.bind(window);
  window.fetch = async function (...args) {
    // 捕获 Boss 自己 JS 请求里的 token / zp_token（供扩展复用到需要这两个 header 的接口）
    try {
      const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
      if (url.includes('zhipin.com/wapi/')) {
        const init = args[1] || {};
        const h = init.headers || {};
        const token = h['token'] || h['Token'] || '';
        const zpToken = h['zp_token'] || h['zp-token'] || '';
        if (token || zpToken) {
          window.postMessage({
            __bossApiExt: true,
            type: 'REQUEST_TOKENS',
            token,
            zp_token: zpToken,
          }, 'https://www.zhipin.com');
        }
      }
    } catch (e) {}

    const resp = await origFetch(...args);

    // 审核补丁 #F2：post-fetch 处理全部放 try/catch。resp.clone() 可能抛
    // （body 已被消费等），移进 try 确保异常不污染到页面 fetch 调用方。
    try {
      const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
      if (url.includes('zhipin.com/wapi/')) {
        const cloned = resp.clone();
        cloned.json().then(json => {
          if (!json || !json.zpData) return;
          const zpData = json.zpData;

          if (Array.isArray(zpData.jobList) && zpData.jobList.length > 0) {
            window.postMessage({
              __bossApiExt: true,
              type: 'JOB_LIST_TOKENS',
              jobs: zpData.jobList.map(j => ({
                encryptJobId: j.encryptJobId,
                securityId: j.securityId,
                encryptBossId: j.encryptBossId,
                bossId: j.bossId,
                lid: j.lid || '',
              })),
            }, 'https://www.zhipin.com');
          }

          if (zpData.securityId && (zpData.encryptJobId || zpData.jobId)) {
            window.postMessage({
              __bossApiExt: true,
              type: 'TOKEN_UPDATE',
              data: {
                securityId: zpData.securityId,
                encryptJobId: zpData.encryptJobId || zpData.jobId,
                encryptBossId: zpData.encryptBossId || zpData.bossId,
                apiUrl: url,
              },
            }, 'https://www.zhipin.com');
          }
        }).catch(() => {});
      }
    } catch (e) {}

    return resp;
  };

  // 上报设备指纹（含 zp_stoken 初始值）
  try {
    const fp = {
      deviceId: localStorage.getItem('pc_device_id') || '',
      guid: localStorage.getItem('guid') || '',
      wt2: document.cookie.match(/wt2=([^;]+)/)?.[1] || '',
      zpStoken: document.cookie.match(/__zp_stoken__=([^;]+)/)?.[1] || '',
    };
    window.postMessage({ __bossApiExt: true, type: 'FINGERPRINT', fp }, 'https://www.zhipin.com');
  } catch (e) {}

  // 监听 zp_stoken 轮转：每 10 秒对比 cookie，发现变化则上报
  let _lastZpStoken = '';
  try { _lastZpStoken = document.cookie.match(/__zp_stoken__=([^;]+)/)?.[1] || ''; } catch (e) {}
  setInterval(() => {
    try {
      const current = document.cookie.match(/__zp_stoken__=([^;]+)/)?.[1] || '';
      if (current && current !== _lastZpStoken) {
        _lastZpStoken = current;
        window.postMessage({ __bossApiExt: true, type: 'ZP_STOKEN_ROTATE', zpStoken: current }, 'https://www.zhipin.com');
      }
    } catch (e) {}
  }, 10000);
})();
