/**
 * site-executor.js — 通用多站点 Worker Tab 执行器
 *
 * 每个站点维护一个独立的后台 Worker Tab，继承真实浏览器 Cookie，
 * 通过 chrome.scripting.executeScript 在 MAIN world 中执行 fetch 调用。
 *
 * 站点配置：
 *   zhipin   — Boss直聘
 *   linkedin — LinkedIn（自动附加 Csrf-Token）
 *   indeed   — Indeed
 */

const SITE_CONFIGS = {
  zhipin: {
    origin: 'https://www.zhipin.com',
    workerTabUrl: 'https://www.zhipin.com/web/geek/job',
  },
  linkedin: {
    origin: 'https://www.linkedin.com',
    workerTabUrl: 'https://www.linkedin.com/feed/',
  },
  indeed: {
    origin: 'https://www.indeed.com',
    workerTabUrl: 'https://www.indeed.com/',
  },
  indeed_employer: {
    origin: 'https://employers.indeed.com',
    // /jobs 是已认证 employer 的稳定 dashboard 入口;
    // 根路径 / 在某些账号状态下会重定向到营销页 → DOM 缺 __NEXT_DATA__,
    // 导致 token 抽取失败 → check_login 误判 logged_in=false。
    workerTabUrl: 'https://employers.indeed.com/jobs',
  },
};

// 每个站点独立跟踪 Worker Tab 状态
const _workerTabs = {};  // { [siteName]: { tabId: number|null, initialized: boolean } }

// 速率限制配置（in-memory 队列，SW 重启后重置 — 设计如此，见 CEO plan）
// Sprint 4 / E1 / PRD §101: 由固定 intervalMs 升级为 [intervalMin, intervalMax] 随机区间，
// 每次请求实际间隔在区间内均匀随机抖动，避免"每 N 秒一次"的等距模式被风控识别。
// 区间下限保留原有 intervalMs 安全值，上限拉至 PRD §101 的 2.3s 上限附近。
const SITE_RATE_LIMITS = {
  zhipin:   { intervalMin: 0,    intervalMax: 200 },    // 原 0；加 0-200ms 抖动避免突发
  linkedin: { intervalMin: 800,  intervalMax: 2300 },   // 原 1000；PRD §101 范围
  // Indeed viewjob / search 对频次敏感: 500ms 在批量分析 3 个职位时会触发
  // Cloudflare 挑战页 (见 v1.3.13 调研)。下限拉到 1500ms ~= 真实用户阅读节奏
  // 的下限。autocomplete / checkLogin 等低频调用基本不受影响。
  indeed:   { intervalMin: 1500, intervalMax: 2800 },   // 原 1800
  indeed_employer: { intervalMin: 800, intervalMax: 1800 },
};

// 上次请求时间戳（ephemeral — SW 重启后第一次请求允许立即发出）
const _lastRequestAt = {};

// 持久化退避状态（chrome.storage.local — 跨 SW 重启）
const _BACKOFF_KEY = 'site_backoff_state';

async function _getBackoffState() {
  try {
    const s = await chrome.storage.local.get(_BACKOFF_KEY);
    return s[_BACKOFF_KEY] || {};
  } catch (_) { return {}; }
}

async function _setBackoffUntil(siteName, ts) {
  try {
    const s = await _getBackoffState();
    s[siteName] = ts;
    await chrome.storage.local.set({ [_BACKOFF_KEY]: s });
  } catch (_) {}
}

async function _clearBackoff(siteName) {
  try {
    const s = await _getBackoffState();
    delete s[siteName];
    await chrome.storage.local.set({ [_BACKOFF_KEY]: s });
  } catch (_) {}
}

async function _waitForRateLimit(siteName) {
  const limit = SITE_RATE_LIMITS[siteName];
  if (!limit || (limit.intervalMax || 0) <= 0) return;

  // 检查持久化退避（429 后写入，跨 SW 重启生效）
  const backoffState = await _getBackoffState();
  const backoffUntil = backoffState[siteName] || 0;
  const now = Date.now();
  if (backoffUntil > now) {
    console.log(`[site-executor] ${siteName} 退避中，等待 ${backoffUntil - now}ms`);
    await new Promise(r => setTimeout(r, backoffUntil - now));
  }

  // Sprint 4 / E1: 每次请求随机间隔 ∈ [intervalMin, intervalMax]
  const targetWait = limit.intervalMin
                     + Math.random() * (limit.intervalMax - limit.intervalMin);
  const last = _lastRequestAt[siteName] || 0;
  const elapsed = Date.now() - last;
  if (elapsed < targetWait) {
    await new Promise(r => setTimeout(r, targetWait - elapsed));
  }
  _lastRequestAt[siteName] = Date.now();
}

function _getSiteState(siteName) {
  if (!_workerTabs[siteName]) _workerTabs[siteName] = { tabId: null, initialized: false };
  return _workerTabs[siteName];
}

// ── Region-blocked 缓存(2026-04-28)──────────────────────────────────────
// LinkedIn 在大陆地区被重定向到 linkedin.cn/incareer/home(HTTP 451);如果
// 不缓存这个判定,每次 voyager API 调用都会创建一个被立即重定向的新 tab,
// 用户的浏览器会攒一堆 .cn 错误标签。检测到后**5 分钟内不再自动开 tab**,
// 但允许通过 popup "重新尝试"按钮(调 self.clearSiteRegionBlocked)立刻重试。
//
// 双层缓存:
//   - in-memory `_regionBlocked` —— 同一 SW 生命周期内 0 ms 短路
//   - chrome.storage.local —— SW 重启后仍生效(避免重启窗口又开错 tab)
const _regionBlocked = {};  // { siteName: { until: ts, reason: string } }
const REGION_BLOCK_TTL_MS = 5 * 60 * 1000;
const _REGION_BLOCK_KEY = 'site_region_blocked';

async function _loadRegionBlockFromStorage() {
  try {
    const s = await chrome.storage.local.get(_REGION_BLOCK_KEY);
    const stored = s[_REGION_BLOCK_KEY] || {};
    const now = Date.now();
    for (const [site, val] of Object.entries(stored)) {
      if (val && val.until && val.until > now) _regionBlocked[site] = val;
    }
  } catch (_) {}
}
async function _persistRegionBlock(siteName, val) {
  try {
    const s = await chrome.storage.local.get(_REGION_BLOCK_KEY);
    const stored = s[_REGION_BLOCK_KEY] || {};
    if (val) stored[siteName] = val;
    else delete stored[siteName];
    await chrome.storage.local.set({ [_REGION_BLOCK_KEY]: stored });
  } catch (_) {}
}

// SW 启动时立刻拉一次,避免重启窗口里第一次调用又开错 tab
_loadRegionBlockFromStorage();

// 并发 dedup:同一站点的 ensureSiteWorkerTab 只允许一个并发,
// 多个工具并行调用同一平台时不会同时各开一个 tab。
const _ensurePromises = {};

/** 清除某站点(或全部)的 region-blocked 缓存 —— 用户手动重试时调用。 */
function clearSiteRegionBlocked(siteName) {
  if (siteName) {
    delete _regionBlocked[siteName];
    _persistRegionBlock(siteName, null);
  } else {
    for (const k of Object.keys(_regionBlocked)) delete _regionBlocked[k];
    chrome.storage.local.remove(_REGION_BLOCK_KEY).catch(() => {});
  }
}

/** 查询某站点当前是否处于 region-blocked 状态(供 popup / 命令层显示用)。 */
function getSiteRegionBlockedState(siteName) {
  const b = _regionBlocked[siteName];
  if (!b) return null;
  if (Date.now() >= b.until) {
    delete _regionBlocked[siteName];
    _persistRegionBlock(siteName, null);
    return null;
  }
  return { until: b.until, reason: b.reason };
}

// 暴露给 background.js 其它模块 + popup chrome.runtime 消息处理
self.clearSiteRegionBlocked = clearSiteRegionBlocked;
self.getSiteRegionBlockedState = getSiteRegionBlockedState;

/** 等待标签页加载完成 */
function waitForSiteTabLoad(tabId, timeoutMs = 15000) {
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, timeoutMs);

    function listener(id, changeInfo) {
      if (id === tabId && changeInfo.status === 'complete') {
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

/**
 * 找用户已经开着的 site tab(read-only fetch 路径首选)。
 *
 * 用户既然装扩展去 Boss/LinkedIn/Indeed 找工作,自然会开对应站点的 tab —
 * 那个 tab 就是天然 hydrated 的活 tab,queryId 自动捕获 / cookie 全新 /
 * 0 额外内存。我们直接复用,而不是另开"专属 worker tab"。
 *
 * 过滤掉登录/错误/检验页(不是真正的可操作 session),也排除掉 _state.tabId
 * 避免循环引用我们自己创建的那个。
 */
async function findUserOwnedSiteTab(siteName) {
  const cfg = SITE_CONFIGS[siteName];
  if (!cfg) return null;
  let allTabs;
  try {
    allTabs = await chrome.tabs.query({ url: `${cfg.origin}/*` });
  } catch (_) {
    return null;
  }
  const ownState = _getSiteState(siteName);
  // 命名变量避免闭包捕获引起 stale 问题
  const blockedPath = ['/login', '/authwall', '/checkpoint', '/uas/login', '/error'];
  const usable = (allTabs || []).filter((t) => {
    if (!t || !t.id || !t.url) return false;
    if (t.status !== 'complete') return false;       // 还在加载,等下一次
    if (t.id === ownState.tabId) return false;        // 我们自己的 worker tab,跳过
    if (!t.url.startsWith(cfg.origin)) return false;  // 重定向走了
    return !blockedPath.some((p) => t.url.includes(p));
  });
  if (usable.length === 0) return null;
  // 优先取 active=true 的(用户正在看的页面更可能 hydrated 完整);
  // 没有则取第一个。简单稳定。
  return usable.find((t) => t.active) || usable[0];
}

/**
 * 获取或创建指定站点的 Worker Tab。
 *
 * @param {string} siteName
 * @param {object} opts
 * @param {boolean} [opts.preferUserTab=false] — true 时优先复用用户已开的同站点 tab
 *   (read-only fetch 路径用,如 executeSiteApi)。复用拿到的 tab 不属于扩展管理,
 *   不会在 _state.tabId 里登记。用户没开过 → 退回创建专属 worker tab 的老路径。
 *   写入/导航/表单动作绝不能传 true(会干扰用户当前操作)。
 */
async function ensureSiteWorkerTab(siteName, opts = {}) {
  const cfg = SITE_CONFIGS[siteName];
  if (!cfg) throw new Error(`[site-executor] 未知站点: ${siteName}`);

  // 1) 短路:近期检测到 region-blocked,5 分钟内不再自动开 tab
  const blocked = _regionBlocked[siteName];
  if (blocked && Date.now() < blocked.until) {
    throw new Error(`[REGION_BLOCKED] ${siteName}: ${blocked.reason}`);
  }
  if (blocked) delete _regionBlocked[siteName];  // 过期清掉

  // 2) Read-only 调用先尝试复用用户已开的 tab(零成本、queryId 永远新鲜)。
  //    用户没开过才落到下面的"专属 worker tab"路径。
  if (opts.preferUserTab) {
    const userTab = await findUserOwnedSiteTab(siteName);
    if (userTab) return userTab;
  }

  // 3) 并发 dedup:已有 ensure 在跑就复用同一 promise
  if (_ensurePromises[siteName]) return _ensurePromises[siteName];

  _ensurePromises[siteName] = (async () => {
    try {
      const state = _getSiteState(siteName);

      if (state.tabId !== null) {
        try {
          const tab = await chrome.tabs.get(state.tabId);
          if (tab && tab.url && tab.url.startsWith(cfg.origin)) return tab;
          // 漂走了(被重定向到非 origin 域)→ 关掉旧 tab,避免攒一堆错误页
          try { await chrome.tabs.remove(state.tabId); } catch (_) {}
        } catch (_) {
          // 标签已关闭 / 不存在
        }
        state.tabId = null;
        state.initialized = false;
      }

      console.log(`[site-executor] 创建 ${siteName} Worker Tab:`, cfg.workerTabUrl);
      const tab = await chrome.tabs.create({ url: cfg.workerTabUrl, active: false });
      state.tabId = tab.id;
      state.initialized = false;

      await waitForSiteTabLoad(state.tabId, 15000);

      // 3) 加载完成后再次检查 URL —— 重定向到非 origin 域时(如 linkedin.com
      // → linkedin.cn/incareer/home),标记 region-blocked 并关闭漂移 tab。
      try {
        const after = await chrome.tabs.get(state.tabId);
        if (!after?.url?.startsWith(cfg.origin)) {
          const reason = `${cfg.origin} 被重定向到 ${after?.url || '(未知)'};可能是地区限制 / 网络不通 / 公司内网拦截`;
          const entry = { until: Date.now() + REGION_BLOCK_TTL_MS, reason };
          _regionBlocked[siteName] = entry;
          _persistRegionBlock(siteName, entry);  // 跨 SW 重启
          try { await chrome.tabs.remove(state.tabId); } catch (_) {}
          state.tabId = null;
          state.initialized = false;
          console.warn(`[site-executor] ${siteName} REGION_BLOCKED:`, reason);
          throw new Error(`[REGION_BLOCKED] ${siteName}: ${reason}`);
        }
      } catch (e) {
        if (String(e?.message || '').includes('REGION_BLOCKED')) throw e;
        // tab.get 失败的其它情况不算 region-blocked
      }

      state.initialized = true;
      console.log(`[site-executor] ${siteName} Worker Tab 就绪:`, state.tabId);
      return tab;
    } finally {
      delete _ensurePromises[siteName];
    }
  })();

  return _ensurePromises[siteName];
}

/**
 * 在站点 Worker Tab 的 MAIN world 中执行 fetch 并返回解析后的 JSON。
 * 包含：速率限制、429 退避重试（持久化）、HTTP 错误检测、CAPTCHA 检测。
 * @param {string} siteName  - 站点名（zhipin / linkedin / indeed）
 * @param {string} fullUrl   - 完整 URL（含 origin + path + query）
 * @param {object} options   - fetch 选项：method, headers, body
 */
async function executeSiteApi(siteName, fullUrl, options = {}) {
  await _waitForRateLimit(siteName);

  const limit = SITE_RATE_LIMITS[siteName] || { intervalMin: 0, intervalMax: 0 };
  // 仅对有速率限制的站点（linkedin/indeed）启用 429 重试
  const MAX_RETRIES = (limit.intervalMax || 0) > 0 ? 2 : 0;
  let lastError;

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    if (attempt > 0) {
      // Multiplicative-5x 退避：第 1 次重试等 5×，第 2 次等 25×（以 intervalMax 为基准更保守）
      const waitMs = limit.intervalMax * (attempt === 1 ? 5 : 25);
      const backoffUntil = Date.now() + waitMs;
      await _setBackoffUntil(siteName, backoffUntil);
      console.warn(`[site-executor] ${siteName} 429 退避 ${waitMs}ms (attempt ${attempt + 1}/${MAX_RETRIES + 1})`);
      await new Promise(r => setTimeout(r, waitMs));
      _lastRequestAt[siteName] = Date.now();
    }

    const result = await _executeSiteApiOnce(siteName, fullUrl, options);

    if (result.status === 429) {
      lastError = new Error(`HTTP 429: ${siteName} 请求被限速`);
      continue;
    }

    // 成功（含重试后成功）时清除持久化退避
    if (attempt > 0) await _clearBackoff(siteName);

    // HTTP 错误抛出（agent 侧可感知，避免静默失败）
    if (!result.ok) {
      throw new Error(`HTTP ${result.status}: ${siteName} API 错误 — ${fullUrl.split('?')[0]}`);
    }

    // 非 JSON 响应（LinkedIn 在限流/CAPTCHA 时返回 HTML 200）
    if (result.data?._rawText) {
      // 显式允许时（如 Indeed 新版 SERP 走 HTML SSR）,把原始 HTML 文本还给调用方解析
      if (options.allowNonJson) {
        return { _html: result.data._rawText, _status: result.status };
      }
      // 把前 300 字节塞进错误消息,方便上游 debug(captcha? HTML? 空? JSON 解析错误?)
      const preview = String(result.data._rawText).slice(0, 300)
        .replace(/\s+/g, ' ').trim();
      throw new Error(
        `[site-executor] ${siteName} 返回非 JSON 响应（可能被限流或需要验证码）`
        + ` status=${result.status} preview=${preview}`
      );
    }

    return result.data;
  }

  throw lastError || new Error(`[site-executor] ${siteName} 请求失败（已超出最大重试次数）`);
}

/** 单次请求执行（不含速率限制和重试逻辑） */
async function _executeSiteApiOnce(siteName, fullUrl, options) {
  // preferUserTab:fetch 是只读注入,优先用用户已开的 tab(zero-cost、queryId 新鲜)。
  // 没有则退回专属 worker tab。
  const tab = await ensureSiteWorkerTab(siteName, { preferUserTab: true });
  if (!tab || !tab.id) throw new Error(`[site-executor] ${siteName} Worker Tab 不可用`);

  const fetchOpts = {
    method: options.method || 'GET',
    credentials: 'include',
    headers: {
      'Accept': 'application/json, text/plain, */*',
      'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8',
      'Content-Type': options.contentType || 'application/json',
      ...(options.headers || {}),
    },
  };
  if (options.body) {
    fetchOpts.body = typeof options.body === 'string' ? options.body : JSON.stringify(options.body);
  }

  // 注入到 Worker Tab MAIN world 的 fetch 函数（不允许使用闭包外变量）
  const fetchFunc = async (url, opts, site) => {
    // LinkedIn: 从 cookie 中提取 CSRF token
    if (site === 'linkedin') {
      const m = document.cookie.match(/JSESSIONID="?ajax:([^";]+)/);
      if (m) opts.headers['Csrf-Token'] = 'ajax:' + m[1];
      opts.headers['x-restli-protocol-version'] = '2.0.0';
      opts.headers['x-li-lang'] = 'en_US';
      opts.headers['x-li-track'] = JSON.stringify({ clientVersion: '1.13.9958', mpVersion: '1.13.9958' });
    }
    try {
      const resp = await fetch(url, opts);
      const text = await resp.text();
      let data;
      try { data = JSON.parse(text); } catch (_) { data = { _rawText: text }; }
      return { ok: resp.ok, status: resp.status, data };
    } catch (e) {
      return { ok: false, status: 0, error: String(e) };
    }
  };

  let results;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN',
      func: fetchFunc,
      args: [fullUrl, fetchOpts, siteName],
    });
  } catch (e) {
    // Worker Tab 可能已关闭，重建后重试
    console.warn(`[site-executor] ${siteName} executeScript 失败，重建:`, String(e));
    _getSiteState(siteName).tabId = null;
    const newTab = await ensureSiteWorkerTab(siteName);
    results = await chrome.scripting.executeScript({
      target: { tabId: newTab.id },
      world: 'MAIN',
      func: fetchFunc,
      args: [fullUrl, fetchOpts, siteName],
    });
  }

  const result = results && results[0] && results[0].result;
  if (!result) throw new Error(`[site-executor] ${siteName} executeScript 返回空结果`);
  if (result.error) throw new Error(result.error);
  return result;
}

/**
 * 在站点 Worker Tab 中执行二进制 fetch，返回 base64 Data URL。
 * 用于下载图片等非 JSON 内容。
 */
async function executeSiteApiBinary(siteName, fullUrl) {
  // 二进制 fetch 也是只读,跟 _executeSiteApiOnce 一样优先复用用户 tab
  const tab = await ensureSiteWorkerTab(siteName, { preferUserTab: true });
  if (!tab || !tab.id) throw new Error(`[site-executor] ${siteName} Worker Tab 不可用`);

  const cfg = SITE_CONFIGS[siteName];
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: async (url, origin) => {
      try {
        const resp = await fetch(url, {
          method: 'GET',
          credentials: 'include',
          headers: { 'Accept': 'image/*,*/*', 'Referer': origin + '/' },
        });
        if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
        const buf = await resp.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let bin = '';
        for (let i = 0; i < bytes.byteLength; i++) bin += String.fromCharCode(bytes[i]);
        return { ok: true, base64: btoa(bin), contentType: resp.headers.get('Content-Type') || 'image/png' };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },
    args: [fullUrl, cfg.origin],
  });

  const r = results?.[0]?.result;
  if (!r) throw new Error(`[site-executor] ${siteName} binary executeScript 返回空结果`);
  if (!r.ok) throw new Error(`executeSiteApiBinary 失败: ${r.error}`);
  const mimeType = r.contentType.split(';')[0].trim() || 'image/png';
  return `data:${mimeType};base64,${r.base64}`;
}

/** 导航 Worker Tab 到指定 URL（用于登录等需要用户看到页面的场景） */
async function navigateSiteWorkerTab(siteName, url) {
  const state = _getSiteState(siteName);
  let tabId = state.tabId;
  if (tabId !== null) {
    try { await chrome.tabs.get(tabId); } catch (_) { tabId = null; state.tabId = null; }
  }
  if (tabId === null) {
    const tab = await chrome.tabs.create({ url, active: true });
    state.tabId = tab.id;
    return tab;
  }
  await chrome.tabs.update(tabId, { url, active: true });
  return { id: tabId, url };
}

/** 重置站点 Worker Tab（如 logout 后清理） */
function resetSiteWorkerTab(siteName) {
  const state = _getSiteState(siteName);
  if (state.tabId) {
    try { chrome.tabs.update(state.tabId, { url: 'about:blank' }); } catch (_) {}
    state.tabId = null;
  }
  state.initialized = false;
}

/**
 * 在站点标签页的 MAIN world 中执行表单操作函数。
 * 与 executeSiteApi 类似，但用于表单填写而非 fetch 请求。
 * @param {string} siteName - 站点名（zhipin / linkedin / indeed）
 * @param {Function} action - form-filler.js 中的函数
 * @param {...any} args - 传递给 action 的参数
 * @returns {Promise<any>} action 的返回值
 */
async function executeSiteFormAction(siteName, action, ...args) {
  const tab = await ensureSiteWorkerTab(siteName);
  if (!tab || !tab.id) throw new Error(`[site-executor] ${siteName} Worker Tab 不可用`);

  // Sprint 4 / E3: 在 action 前先注入 human-simulator.js，让 form-filler 的 human 变体可调用 window.DQ_Human
  await _ensureHumanSimulator(tab.id);

  let results;
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN',
      func: action,
      args: args,
    });
  } catch (e) {
    // Worker Tab 可能已关闭，重建后重试
    console.warn(`[site-executor] ${siteName} form action 失败，重建:`, String(e));
    _getSiteState(siteName).tabId = null;
    const newTab = await ensureSiteWorkerTab(siteName);
    await _ensureHumanSimulator(newTab.id);
    results = await chrome.scripting.executeScript({
      target: { tabId: newTab.id },
      world: 'MAIN',
      func: action,
      args: args,
    });
  }

  const result = results && results[0] && results[0].result;
  if (!result) throw new Error(`[site-executor] ${siteName} form action 返回空结果`);
  return result;
}

/**
 * 注入 human-simulator.js 到指定 tab 的 MAIN world。
 * 幂等：重复注入只是覆盖 window.DQ_Human，无副作用。
 */
async function _ensureHumanSimulator(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      world: 'MAIN',
      files: ['lib/ext-core/core/human-simulator.js'],
    });
  } catch (e) {
    console.warn('[site-executor] human-simulator inject failed (action 仍会跑，但缺人类化):', String(e));
  }
}

/**
 * 在指定标签页（非 Worker Tab）的 MAIN world 中执行表单操作。
 * 用于需要在前台标签页执行的场景（如 LinkedIn Easy Apply 模态框）。
 * @param {number} tabId - 目标标签页 ID
 * @param {Function} action - form-filler.js 中的函数
 * @param {...any} args - 传递给 action 的参数
 * @returns {Promise<any>} action 的返回值
 */
async function executeFormActionOnTab(tabId, action, ...args) {
  await _ensureHumanSimulator(tabId);
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    func: action,
    args: args,
  });
  const result = results && results[0] && results[0].result;
  if (!result) throw new Error(`[site-executor] tab ${tabId} form action 返回空结果`);
  return result;
}

/** 标签关闭时清理所有站点状态 */
chrome.tabs.onRemoved.addListener((tabId) => {
  for (const state of Object.values(_workerTabs)) {
    if (state.tabId === tabId) {
      state.tabId = null;
      state.initialized = false;
    }
  }
});
