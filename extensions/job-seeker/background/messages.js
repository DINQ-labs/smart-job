/*
 * background/messages.js — runtime.onMessage 标准化辅助 + 真平台登录检测.
 *
 * v3 加补的 runtime 消息:
 *   DQ_GET_PLATFORM_STATUS  → cookie-based 检测各平台是否已登录,返 { ok, platforms }
 *   DQ_WS_RECONNECT         → 触发 background/index.js 重连(若 reconnectWs 函数存在)
 *
 * 设计:
 *   - 检测靠 chrome.cookies.getAll(domain) + 已知 session cookie 名匹配,完全 local,无网络请求
 *   - 任何 cookie 失败 / 不存在 → online=false(保守)
 *   - 真接入 deep check 留 Phase 6(让 site-executor 跑一次 /me API)
 */
(function () {
  'use strict';
  if (!chrome?.runtime?.onMessage) return;

  // ── 各平台 session cookie 列表(任一存在 + 非空 = 已登录)──────
  // 来源:观察 v2 site-executor / token-chain 真业务里依赖的 cookie.
  // 加新平台只改这张表.
  const PLATFORM_LOGIN_PROBES = {
    boss: {
      domain: '.zhipin.com',
      // wt2 = 主登录 token(必然有);bst = backup;zp_token 在新版会带
      cookies: ['wt2', 'bst', 'zp_token'],
    },
    linkedin: {
      domain: '.linkedin.com',
      // li_at = 主认证 cookie(标准 LinkedIn 公开识别)
      cookies: ['li_at'],
    },
    indeed: {
      domain: '.indeed.com',
      // CTK 是 client token(登录后才稳定有 value);SOCK 是 session
      // 加 PPID(personal id)兜底
      cookies: ['CTK', 'SOCK', 'PPID'],
    },
  };

  async function probePlatform(platform) {
    const cfg = PLATFORM_LOGIN_PROBES[platform];
    if (!cfg) return { online: false, why: 'unknown platform' };
    try {
      const all = await new Promise(res =>
        chrome.cookies.getAll({ domain: cfg.domain }, (cs) => {
          void chrome.runtime.lastError; res(cs || []);
        })
      );
      const byName = Object.create(null);
      for (const c of all) if (c.value) byName[c.name] = c;
      const hit = cfg.cookies.find(name => byName[name]);
      if (hit) {
        return { online: true, signal: hit };
      }
      return { online: false, why: 'no session cookie found' };
    } catch (e) {
      return { online: false, why: String(e) };
    }
  }

  async function probeAll() {
    const platforms = {};
    for (const p of Object.keys(PLATFORM_LOGIN_PROBES)) {
      platforms[p] = await probePlatform(p);
    }
    return platforms;
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || typeof msg !== 'object') return;

    if (msg.type === 'DQ_GET_PLATFORM_STATUS') {
      // 单平台 cheap 模式(传 platform 字段)/ 全平台 mode
      const single = msg.platform;
      if (single) {
        probePlatform(single).then(s =>
          sendResponse({ ok: true, platforms: { [single]: s }, single })
        );
      } else {
        probeAll().then(platforms =>
          sendResponse({ ok: true, platforms })
        );
      }
      return true;   // async response
    }

    if (msg.type === 'DQ_WS_RECONNECT') {
      try {
        if (typeof reconnectWs === 'function') reconnectWs();
        sendResponse({ ok: true });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
      return false;
    }

    return;  // 其它消息让 index.js 的 listener 处理
  });
})();
