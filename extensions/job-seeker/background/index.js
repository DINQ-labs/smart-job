try { importScripts('/lib/i18n/i18n.js', '/lib/i18n/dict/common.js', '/lib/i18n/dict/background.js'); } catch (e) {}

/**
 * Boss API Extension - background Service Worker（多账号/多会话版）
 *
 * 职责：
 * 1. 通过 WebSocket 连接 boss-api-gateway（可配置地址）
 * 2. 接收网关命令，分发到各 boss/* 处理器
 * 3. 管理 TokenStore（令牌链状态）
 * 4. 接收内容脚本上报的 securityId 并更新 TokenStore
 * 5. 支持 logout 命令（清除 cookies + 重置 token）
 * 6. 指数退避重连
 *
 * 依赖（importScripts 顺序很重要）：
 *   tokens.js → executor.js → api.js （executor 和 api 依赖 tokens）
 *   core/registry.js → core/token-chain.js（基础设施层，不依赖业务）
 *   chains/builtin.js（链定义，依赖 token-chain.js）
 *   commands/*.js（命令注册，依赖 registry.js + api.js）
 */

importScripts(
  // ① 通用多站点执行器（最先加载，被后续所有模块依赖）
  '../lib/ext-core/core/site-executor.js',
  // ①-b 表单操作原语库（依赖 site-executor.js 的 executeSiteFormAction）
  '../lib/ext-core/core/form-filler.js',
  // ② zhipin 业务层（executor 是薄包装，依赖 site-executor）
  '../lib/ext-core/bosszp/tokens.js', '../lib/ext-core/bosszp/executor.js', '../lib/ext-core/bosszp/api.js',
  // ③ 新站点 API 层
  '../lib/ext-core/linkedin/api.js',
  '../lib/ext-core/linkedin/form-handler.js',
  '../lib/ext-core/linkedin/recruiter-api.js',
  '../lib/ext-core/linkedin/search-scraper.js',
  '../lib/ext-core/indeed/api.js',
  '../lib/ext-core/indeed/form-handler.js',
  '../lib/ext-core/indeed/employer-api.js',
  // ④ 核心基础设施层（顺序：registry → token-chain → extractors → request-builder → config-sync）
  '../lib/ext-core/core/registry.js', '../lib/ext-core/core/token-chain.js',
  '../lib/ext-core/core/extractors.js',       // 提取器引擎（runExtractor）
  '../lib/ext-core/core/request-builder.js',  // 动态命令 handler 生成（buildDynamicHandler）
  '../lib/ext-core/core/config-sync.js',      // 云端配置同步（handleConfigUpdate, restoreDynamicConfig）
  // ⑤ 链定义（依赖 token-chain.js）
  '../lib/ext-core/chains/builtin.js',
  '../lib/ext-core/chains/linkedin.js',
  '../lib/ext-core/chains/indeed.js',
  '../lib/ext-core/chains/indeed_employer.js',
  // ⑥ 命令注册（依赖 registry.js + api.js；handler 内引用的全局变量在 SW 启动后均可用）
  '../lib/ext-core/commands/session.js',
  '../lib/ext-core/commands/jobs.js',
  '../lib/ext-core/commands/candidates.js',
  '../lib/ext-core/commands/misc.js',
  '../lib/ext-core/commands/linkedin.js',
  '../lib/ext-core/commands/linkedin_recruiter.js',
  '../lib/ext-core/commands/indeed.js',
  '../lib/ext-core/commands/indeed_employer.js',
  '../lib/ext-core/commands/dom.js',
  // ⑦ 长任务后台监听(Phase E)— chrome.alarms + notifications
  '../lib/ext-core/core/task-monitor.js',
  // ⑧ 语音输入桥(MVP 5)— sidepanel ↔ offscreen 协调
  '../lib/ext-core/core/voice-bridge.js',
  // ⑨ v3 标准化模块(同进程共享 globalThis)
  'page-context.js',
  'messages.js',
  // ⑩ 合并子系统(各自 IIFE 隔离;recorder 用 {action}、autofill 用 {type:'AF_*'} 区分消息)
  '../lib/recorder/recorder-bg.js',   // API 录制(CDP 隐身抓包,合并自 api-recorder-v2)
  '../lib/autofill/autofill-bg.js'    // 表单自动填写(合并自 autofill-ext)
);

const HEARTBEAT_INTERVAL_MS = 15000;
const RECONNECT_BASE = 1000;
const RECONNECT_MAX = 60000;
const EXT_NAME = 'bosszp';  // 扩展标识名(WS 上报),网关用于区分多种扩展来源
// EXT_KIND 现在从 chrome.storage.local.user.role 读(单 ext + onboarding 选 role)。
// 默认 'jobseeker' 给 onboarding 还没完成的初次启动场景;sidepanel 用户选完角色 +
// 此 SW 重启后会读到正确值。chrome.action 点击 / 重启浏览器都会触发 SW 拉起,
// 那次 init 时调 _loadExtKind 拿到最新值。
let EXT_KIND = 'jobseeker';
async function _loadExtKind() {
  try {
    const stored = await chrome.storage.local.get(['user']);
    const role = stored?.user?.role;
    if (role === 'jobseeker' || role === 'recruiter') {
      EXT_KIND = role;
    }
  } catch (_) {}
}
// onboarding 完成后会写 chrome.storage.local — SW 没法实时拿到,但有两条路径会重读:
// 1. 用户切角色 → onboarding.js 调 location.reload(),sidepanel 重启,SW 也会被叫醒
// 2. chrome.storage.onChanged 事件(下面注册)在 user 变化时同步刷 EXT_KIND
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.user) {
    const newRole = changes.user.newValue?.role;
    if (newRole === 'jobseeker' || newRole === 'recruiter') {
      EXT_KIND = newRole;
    }
  }
});
// 本扩展同时为三个站点提供 API 代理：通过 capabilities 消息告知网关，
// 网关的 find_by_site(site) 路由据此挑选会话。
// Service worker 不能访问 window — 这里 hardcode 的列表必须跟
// ext_shared/sidepanel-shared/platforms-config.js 的 manifest 同步。
// 加新平台时记得两处一起改。
const SUPPORTED_SITES = ['boss', 'linkedin', 'indeed'];

// Sprint 3 (D5): per-tab PageContext 缓存
// content script 上报后落这里，sidepanel 用 DQ_GET_PAGE_CONTEXT 主动拉，
// 或直接监听 DQ_PAGE_CONTEXT_UPDATE 广播。Tab 关闭时自动 GC。
const _pageContextCache = new Map();
chrome.tabs?.onRemoved?.addListener?.((tabId) => _pageContextCache.delete(tabId));

/**
 * 上报某站点的登录用户 ID（由各站点的 check_login handler 在成功时调用）。
 * 允许网关在未显式传 session_id 的情况下按 app_user_id 定位会话。
 */
function reportSiteStatus(site, appUserId) {
  if (!site) return;
  try {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'site_status',
        site,
        app_user_id: appUserId || '',
      }));
    }
  } catch (_) {}
}

let ws = null;
let heartbeatTimer = null;
let reconnectTimer = null;
let autoReconnect = true;
let reconnectAttempt = 0;
let currentSessionId = '';
let currentProxyUrl = '';

/** 供 commands/misc.js 等模块调用，避免直接跨文件赋值全局变量 */
function _clearCurrentSessionId() { currentSessionId = ''; }
let GATEWAY_WS_URL = 'wss://control.job.joyhouse.chat/ext/ws';
let STABLE_BROWSER_ID = '';
let PERSONAL_USER_ID = '';    // 当前登录用户 id —— 来自扩展登录 storage.local.auth
let PORTAL_ACCESS_TOKEN = ''; // 扩展登录(portal)的 access token,WS 握手鉴权用
let EXT_TOKEN = '';           // 静态连接 token(dev 覆盖,优先于 PORTAL_ACCESS_TOKEN)

// ── 初始化守卫 ────────────────────────────────────────────────────────────
// Service Worker 被唤醒时全局状态重置，需等待 tokenStore.load() 完成再处理消息。
let initializationPromise = null;

// ── 消息串行队列 ──────────────────────────────────────────────────────────
// 防止多条 WebSocket 消息并发执行 async 命令时竞争写 tokenStore。
const _msgQueue = [];
let _msgProcessing = false;

async function _drainMessageQueue() {
  if (_msgProcessing) return;
  _msgProcessing = true;
  while (_msgQueue.length > 0) {
    const rawData = _msgQueue.shift();
    try {
      await handleGatewayMessage(rawData);
    } catch (e) {
      console.error('[boss-api-ext] 消息队列处理异常:', e);
    }
  }
  _msgProcessing = false;
}

// ── 标准响应封装 ──────────────────────────────────────────────────────────
/**
 * 所有命令处理器的成功返回值统一用此包装。
 * 格式: { ok: true, code: 200, message: string, data: any }
 * 失败时直接 throw new Error(...)，由 handleGatewayMessage 捕获转为 { ok: false, error }
 */
function extOk(data, message = '') {
  return { ok: true, code: 200, message, data };
}

// ── 代理管理 ──────────────────────────────────────────────────────────────

/**
 * 设置或清除 Chrome 代理。
 * proxyUrl 格式: http://ip:port | https://ip:port | socks5://ip:port | socks4://ip:port
 * proxyUrl 为空字符串时清除代理（直连）。
 */
async function applyProxy(proxyUrl) {
  currentProxyUrl = proxyUrl || '';
  if (!proxyUrl) {
    await chrome.proxy.settings.clear({ scope: 'regular' });
    console.log('[boss-api-ext] 代理已清除（直连）');
    return;
  }
  try {
    const url = new URL(proxyUrl);
    const scheme = url.protocol.replace(':', ''); // http | https | socks5 | socks4
    const port = parseInt(url.port, 10);
    if (!url.hostname || isNaN(port)) throw new Error('hostname 或 port 无效');
    const config = {
      mode: 'fixed_servers',
      rules: {
        singleProxy: { scheme, host: url.hostname, port },
      },
    };
    await chrome.proxy.settings.set({ value: config, scope: 'regular' });
    console.log(`[boss-api-ext] 代理已设置: ${scheme}://${url.hostname}:${port}`);
  } catch (e) {
    console.error('[boss-api-ext] 代理设置失败:', e.message);
    throw new Error(`代理地址无效: ${proxyUrl} (${e.message})`);
  }
}

// ── 初始化：从 storage 读取网关配置 ──────────────────────────────────────

async function initGatewayUrl() {
  // gatewayHost / gatewayPort 由 options 设置页写入 chrome.storage.local
  // (sidepanel/shared/api.js 也读 local)—— 后台必须读同一个区,否则 WS 连接
  // 和 sidepanel 用的网关不一致,dev/自定义环境下整条命令链路连错服务器。
  const local = await chrome.storage.local.get(['gatewayHost', 'gatewayPort', 'apiGwUrl']);
  const sync = await chrome.storage.sync.get(['extToken']);
  const host = local.gatewayHost || '127.0.0.1';
  EXT_TOKEN = sync.extToken || '';

  // options 设置页显式指定的 api-gw URL 优先：把 http(s):// 换成 ws(s):// 即得 WS 端点。
  const apiGwUrl = (local.apiGwUrl || '').trim();
  if (apiGwUrl) {
    GATEWAY_WS_URL = apiGwUrl.replace(/^http/, 'ws').replace(/\/+$/, '') + '/ext/ws';
    return GATEWAY_WS_URL;
  }

  // 协议按 host 是否 localhost 推导,与 getAgentGwBaseUrl / api.js 保持一致。
  // WS 端点:新网关在根路径 /ext/ws。
  if (/^(127\.0\.0\.1|localhost)/.test(host)) {
    // 本地 dev:ws:// + 端口(默认 8767)
    const port = local.gatewayPort || '8767';
    const hostPort = host.includes(':') ? host : `${host}:${port}`;
    GATEWAY_WS_URL = `ws://${hostPort}/ext/ws`;
  } else {
    // 生产:固定控制域,wss:// 走 nginx 标准 443
    GATEWAY_WS_URL = 'wss://control.job.joyhouse.chat/ext/ws';
  }
  return GATEWAY_WS_URL;
}

/** 返回 agent-gateway (8769) 的 HTTP base URL —— 跟 chat-core.getAgentGwBaseUrl 同步。 */
async function getAgentGwBaseUrl() {
  try {
    // gatewayHost 与 options / sidepanel 一致,读 storage.local。
    const data = await chrome.storage.local.get(['agentGwUrl', 'gatewayHost']);
    // options 设置页显式指定的 agent-gw URL 优先；为空才回退到 gatewayHost 推导。
    const ov = (data.agentGwUrl || '').trim();
    if (ov) return ov.replace(/\/+$/, '');
    const host = data.gatewayHost || '127.0.0.1';
    if (/^(127\.0\.0\.1|localhost)/.test(host)) {
      return `http://${host.includes(':') ? host : host + ':8769'}`;
    }
    return `https://agent.job.joyhouse.chat`;
  } catch (_) {
    return `https://agent.job.joyhouse.chat`;
  }
}

/** 返回 gateway 的 HTTP base URL（供升级、版本检查等 HTTP 接口使用） */
function getGatewayHttpUrl() {
  // ws://host:port/api/v1/job-api/ext/ws → http://host:port
  // wss://host/api/v1/job-api/ext/ws    → https://host
  const wsUrl = GATEWAY_WS_URL;
  if (wsUrl.startsWith('wss://')) {
    const host = wsUrl.replace('wss://', '').split('/')[0];
    return `https://${host}`;
  }
  const host = wsUrl.replace('ws://', '').split('/')[0];
  return `http://${host}`;
}

/**
 * 返回 job-api-gateway 的 HTTP base URL。
 * 生产: https://control.job.joyhouse.chat   本地: http://127.0.0.1:8767
 * 新网关 HTTP 路由在根路径(/billing /bookmarks /jobs ...),无 /api/v1/job-api
 * 前缀 —— 与 sidepanel/shared/api.js apiGwBase() 一致。
 * 用于：bookmarks / billing / jobs 等 api-gw 端点。
 */
function getApiGwBaseUrl() {
  return getGatewayHttpUrl();
}

async function initStableBrowserId() {
  const d = await chrome.storage.local.get('stableBrowserId');
  if (d.stableBrowserId) {
    STABLE_BROWSER_ID = d.stableBrowserId;
    return;
  }
  STABLE_BROWSER_ID = crypto.randomUUID();
  await chrome.storage.local.set({ stableBrowserId: STABLE_BROWSER_ID });
}

// ── 登录身份 ──────────────────────────────────────────────────────────────
// 用户在扩展侧栏(onboarding / 设置页)用邮箱登录,auth-api.js 成功后写
// chrome.storage.local.auth = { accessToken, refreshToken, user:{id,email,...} }。
// 后台 WS 握手就用这里的 user_id + access token,与 sidepanel 同一套身份。
// 旧的 dinq.me cookie 身份体系(域名已下线)已整体移除。

async function loadAuthIdentity() {
  try {
    const r = await chrome.storage.local.get(['auth', 'userId']);
    const auth = r.auth || {};
    PERSONAL_USER_ID    = (auth.user && auth.user.id) || r.userId || '';
    PORTAL_ACCESS_TOKEN = auth.accessToken || '';
  } catch (_) {
    PERSONAL_USER_ID = '';
    PORTAL_ACCESS_TOKEN = '';
  }
  return PERSONAL_USER_ID;
}

// ── 指数退避重连 ──────────────────────────────────────────────────────────

function getReconnectDelay() {
  const base = Math.min(RECONNECT_BASE * Math.pow(2, reconnectAttempt++), RECONNECT_MAX);
  return base * (0.8 + Math.random() * 0.4); // ±20% 抖动
}

// ── WebSocket 连接管理 ────────────────────────────────────────────────────

async function connect() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  try {
    // 拉一次最新 EXT_KIND(SW cold start 后第一次连接前必走,确保 onboarding 写
    // 进的 user.role 能反映到 WS 握手参数;后续 onChanged 会同步增量)
    await _loadExtKind();
    await loadAuthIdentity();   // 每次(重)连读最新登录身份 + token
    const uidParam = PERSONAL_USER_ID ? `&user_id=${encodeURIComponent(PERSONAL_USER_ID)}` : '';
    const connToken = EXT_TOKEN || PORTAL_ACCESS_TOKEN;
    const tokenParam = connToken ? `&token=${encodeURIComponent(connToken)}` : '';
    // Phase 2: 加 kind=jobseeker/recruiter 让后端 kick 逻辑只踢同 (user_id, ext_kind) 的旧连接
    ws = new WebSocket(`${GATEWAY_WS_URL}?name=${EXT_NAME}&kind=${EXT_KIND}&bid=${STABLE_BROWSER_ID}${uidParam}${tokenParam}`);

    ws.onopen = () => {
      console.log('[boss-api-ext] 已连接网关:', GATEWAY_WS_URL);
      reconnectAttempt = 0; // 重置退避计数
      notifyPopup({ type: 'connectionState', connected: true });
      startHeartbeat();
    };

    ws.onclose = () => {
      console.log('[boss-api-ext] 网关连接断开');
      ws = null;
      stopHeartbeat();
      notifyPopup({ type: 'connectionState', connected: false, sessionId: currentSessionId });
      if (autoReconnect) {
        const delay = getReconnectDelay();
        console.log(`[boss-api-ext] ${delay.toFixed(0)}ms 后重连（第 ${reconnectAttempt} 次）`);
        reconnectTimer = setTimeout(connect, delay);
      }
    };

    ws.onerror = (e) => {
      console.warn('[boss-api-ext] WS 错误:', e);
    };

    ws.onmessage = (event) => {
      _msgQueue.push(event.data);
      _drainMessageQueue();
    };
  } catch (e) {
    console.error('[boss-api-ext] 连接失败:', e);
    if (autoReconnect) {
      const delay = getReconnectDelay();
      reconnectTimer = setTimeout(connect, delay);
    }
  }
}

function disconnect() {
  autoReconnect = false;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  stopHeartbeat();
  if (ws) { try { ws.close(); } catch (_) {} ws = null; }
  notifyPopup({ type: 'connectionState', connected: false });
}

function startHeartbeat() {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
    }
  }, HEARTBEAT_INTERVAL_MS);
}

function stopHeartbeat() {
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
}

function sendResponse(id, ok, result) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const payload = ok
    ? { id, ok: true, result }
    : { id, ok: false, error: String(result) };
  ws.send(JSON.stringify(payload));
}

function notifyPopup(msg) {
  try {
    chrome.runtime.sendMessage(msg, () => { void chrome.runtime.lastError; });
  } catch (_) {}
}

// ── 网关消息处理 ──────────────────────────────────────────────────────────

async function handleGatewayMessage(rawData) {
  // 确保 tokenStore 已从 storage 加载完成（Service Worker 被唤醒恢复时的守卫）
  if (initializationPromise) await initializationPromise;

  let msg;
  try {
    msg = JSON.parse(rawData);
  } catch (e) {
    return;
  }

  if (!msg || typeof msg !== 'object') return;

  if (msg.type === 'pong') return;

  // 云端配置推送（动态命令 + 动态链定义）
  if (msg.type === 'config_update') {
    const ack = await handleConfigUpdate(msg);
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'config_ack', ...ack }));
    }
    return;
  }

  if (msg.type === 'kicked') {
    console.warn('[boss-api-ext] 被服务端踢出:', msg.reason || '另一台设备已接入');
    // 必须先停止自动重连，否则 ws.onclose 触发后会立即重连，产生踢人风暴
    autoReconnect = false;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    notifyPopup({ type: 'kicked', reason: msg.reason || '另一台设备已接入，当前连接已断开' });
    return;
  }

  if (msg.type === 'registered') {
    currentSessionId = msg.sessionId || '';
    console.log('[boss-api-ext] 注册成功, browserId:', msg.browserId, 'sessionId:', currentSessionId.slice(0, 16));
    // 审核补丁 #R3：await applyProxy，确保队列里紧跟着的命令用新代理发请求。
    // 之前用 .catch() 放行，若网关在 registered 之后紧接着派发命令（自动化场景
    // 常见），前几个请求会走旧/无代理，触发 Boss 侧地域风控。
    if (msg.proxyUrl !== undefined) {
      try {
        await applyProxy(msg.proxyUrl);
      } catch (e) {
        console.warn('[boss-api-ext] 初始代理设置失败:', e.message);
      }
    }
    notifyPopup({ type: 'registered', browserId: msg.browserId, sessionId: currentSessionId, proxyUrl: msg.proxyUrl || '', appUserId: msg.appUserId || '' });
    // 持久化 sessionId
    if (currentSessionId) {
      chrome.storage.local.set({ sessionId: currentSessionId, browserId: msg.browserId })
        .catch(err => console.warn('[boss-api-ext] sessionId 持久化失败:', err));
    }
    // 上报扩展能力(本扩展一个连接同时承载 boss/linkedin/indeed 三站点)
    // manifest_version + ext_kind 让网关按 ext_kind (jobseeker/recruiter) 取
    // 对应的 min_compatible (job-api-gateway/static/versions.json) 校验。
    try {
      if (ws && ws.readyState === WebSocket.OPEN) {
        let extVersion = '';
        try { extVersion = chrome.runtime.getManifest().version || ''; } catch (_) {}
        ws.send(JSON.stringify({
          type: 'capabilities',
          sites: SUPPORTED_SITES,
          manifest_version: extVersion,
          ext_kind: EXT_KIND,  // Phase 2: 双 ext 独立版本号
        }));
      }
    } catch (_) {}
    // 上报当前动态配置版本，gateway 据此决定是否推送 config_update
    getDynamicConfigVersion().then(version => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'config_version', version: version || null }));
      }
    });
    return;
  }

  if (msg.id != null) {
    const { id, method, path, body = {} } = msg;
    const pathNorm = (path || '').replace(/^\/+/, '');
    const shortId = String(id).slice(0, 8);
    console.log(`[boss-api-ext] ← [${shortId}] ${method} ${pathNorm}`, body);

    try {
      const result = await dispatchCommand(method, pathNorm, body);
      const summary = JSON.stringify(result).slice(0, 200);
      console.log(`[boss-api-ext] → [${shortId}] OK: ${summary}`);
      sendResponse(id, true, result);
    } catch (e) {
      console.error(`[boss-api-ext] → [${shortId}] ERROR: ${e.message || String(e)}`);
      sendResponse(id, false, e.message || String(e));
    }
  }
}

// ── 命令分发 ──────────────────────────────────────────────────────────────

/**
 * 主分发入口：优先查 commandRegistry（commands/*.js 中注册的声明式命令），
 * 未命中则 fallback 到 _legacyDispatch（原 switch 表，Phase 2 逐步清空）。
 *
 * 若命令声明了 `enforceRequires: true`，在调用 handler 之前用 resolveRequires
 * 校验前置 token 是否就绪。缺失时直接返回结构化 TOKEN_MISSING 响应（不调用
 * handler，避免各 handler 自行 throw 字符串造成错误信息不一致）。
 */
async function dispatchCommand(method, path, body) {
  const cmd = getCommand(path);
  if (cmd) {
    if (cmd.enforceRequires === true && cmd.requires) {
      const check = resolveRequires(cmd.requires, body || {}, tokenStore);
      if (!check.ok) {
        const payload = formatTokenMissing(check.missing);
        console.warn(`[boss-api-ext] ${path} 前置 token 缺失: ${payload.message}`);
        return extOk(payload, payload.message);
      }
    }
    return cmd.handler(body);
  }
  return _legacyDispatch(method, path, body);
}

/**
 * 遗留分发表 —— Phase 2 完成后已清空所有 case。
 * 所有命令均已迁移到 commands/*.js 并通过 registry 分发。
 * 此函数仅作为最终兜底，理论上不会再被触达。
 */
async function _legacyDispatch(method, path, body) {
  throw new Error(`未知命令: ${method} ${path}`);
}

// ── QR 码辅助：确保登录页当前显示扫码界面 ────────────────────────────────

/**
 * 检测登录页所处状态，必要时点击"APP扫码登录"切换到扫码 tab。
 * 返回 'qr_ready' | 'qr_expired' | 'switched' | 'unknown'
 */
async function _ensureQRTabVisible(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    func: () => {
      // ① 已经在扫码 tab：存在二维码图片且没有手机号输入框
      const phoneInput = document.querySelector('input[type="tel"], input[placeholder*="手机"]');
      const phoneVisible = phoneInput && phoneInput.offsetParent !== null;

      if (!phoneVisible) {
        // 检查二维码是否过期
        const bodyText = document.body.innerText;
        if (bodyText.includes('请重新刷新二维码') || bodyText.includes('点击刷新')) {
          // 尝试点刷新按钮
          const allEls = document.querySelectorAll('*');
          for (const el of allEls) {
            const t = el.textContent.trim();
            if ((t === '刷新' || t === '点击刷新') && el.children.length === 0) {
              el.click();
              return 'qr_refreshed';
            }
          }
          return 'qr_expired';
        }
        return 'qr_ready';
      }

      // ② 在短信 tab，尝试点击"APP扫码登录"切换
      // 遍历 DOM 找到文本完全匹配的最小叶节点
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
      let node;
      while ((node = walker.nextNode())) {
        const text = node.textContent.trim();
        if (text === 'APP扫码登录') {
          node.click();
          return 'switched';
        }
      }
      // 兜底：找包含该文字且无子元素的元素
      for (const el of document.querySelectorAll('span, div, a, button, li')) {
        if (el.textContent.trim() === 'APP扫码登录') {
          el.click();
          return 'switched';
        }
      }
      return 'unknown';
    },
  });
  return results?.[0]?.result || 'unknown';
}

/**
 * 截取 tab 内的二维码图片。优先 captureVisibleTab，降级 DOM 提取。
 */
async function _captureQRFromTab(tab) {
  try {
    await chrome.tabs.update(tab.id, { active: true });
    await new Promise(r => setTimeout(r, 400));
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' });

    // 尝试定位页面中的二维码元素，裁剪出仅含二维码的区域
    let cropRect = null;
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: 'MAIN',
        func: () => {
          const selectors = [
            'img[class*="qrcode"]',
            'img[class*="qr-code"]',
            'canvas[class*="qr"]',
            '.qr-code img', '.qrcode img',
            '.dialog-qrcode img', '.scan-qrcode img',
            'img[src*="qrcode"]', 'img[src*="getqrcode"]',
          ];
          for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) {
              const r = el.getBoundingClientRect();
              if (r.width > 50 && r.height > 50) {
                const dpr = window.devicePixelRatio || 1;
                return { x: r.left * dpr, y: r.top * dpr, w: r.width * dpr, h: r.height * dpr, dpr };
              }
            }
          }
          return null;
        },
      });
      cropRect = results?.[0]?.result;
    } catch (e) {
      console.warn('[boss-api-ext] QR元素定位失败:', e.message);
    }

    if (cropRect && cropRect.w > 50) {
      try {
        const padding = Math.round(16 * (cropRect.dpr || 1));
        const blob = await fetch(dataUrl).then(r => r.blob());
        const bitmap = await createImageBitmap(blob);
        const sx = Math.max(0, cropRect.x - padding);
        const sy = Math.max(0, cropRect.y - padding);
        const sw = Math.min(bitmap.width - sx, cropRect.w + padding * 2);
        const sh = Math.min(bitmap.height - sy, cropRect.h + padding * 2);
        const canvas = new OffscreenCanvas(sw, sh);
        canvas.getContext('2d').drawImage(bitmap, sx, sy, sw, sh, 0, 0, sw, sh);
        bitmap.close();
        const outBlob = await canvas.convertToBlob({ type: 'image/png' });
        const reader = new FileReader();
        const croppedUrl = await new Promise(resolve => {
          reader.onload = () => resolve(reader.result);
          reader.readAsDataURL(outBlob);
        });
        return { qrcode_dataurl: croppedUrl, source: 'captureVisibleTab+crop' };
      } catch (cropErr) {
        console.warn('[boss-api-ext] 裁剪失败，返回全图:', cropErr.message);
      }
    }

    return { qrcode_dataurl: dataUrl, source: 'captureVisibleTab' };
  } catch (e) {
    console.warn('[boss-api-ext] captureVisibleTab 失败，DOM 提取:', e.message);
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN',
      func: () => {
        for (const c of document.querySelectorAll('canvas')) {
          if (c.width >= 80 && c.height >= 80) {
            try { return c.toDataURL('image/png'); } catch (_) {}
          }
        }
        for (const img of document.querySelectorAll('img')) {
          const w = img.naturalWidth || img.width;
          if (w >= 80) {
            try {
              const cv = document.createElement('canvas');
              cv.width = w; cv.height = img.naturalHeight || img.height;
              cv.getContext('2d').drawImage(img, 0, 0);
              return cv.toDataURL('image/png');
            } catch (_) {}
          }
        }
        return null;
      },
    });
    const dataUrl = results?.[0]?.result;
    if (!dataUrl) throw new Error('QR码截图失败，请在浏览器中直接扫码');
    return { qrcode_dataurl: dataUrl, source: 'dom' };
  }
}

// ── 内容脚本消息处理 ──────────────────────────────────────────────────────

// 审核补丁 #F3：包裹 async handler，确保 sync 抛错（如 getCommand 返回 null
// 后 `.handler()`）也能走 sendResponse 返回给前端，而不是让 popup 的
// chrome.runtime.sendMessage Promise 永久挂起。
// 待发 chat prompt 缓存:DQ_OPEN_CHAT_WITH_CONTEXT 触发时 sidepanel 可能还没加载,
// runtime.sendMessage 会丢;改为存这里,sidepanel 启动时用 DQ_GET_PENDING_PREFILL 拉。
let _pendingChatPrefill = null;

// 用法：_asyncReply(sendResponse, () => someAsyncFn()) 后 return true。
function _asyncReply(sendResponse, asyncFn) {
  // Promise.resolve().then 会把 sync 抛错转成 rejection，统一落到 catch。
  Promise.resolve().then(asyncFn).then(
    (res) => { try { sendResponse(res); } catch (_) {} },
    (err) => {
      try {
        sendResponse({ ok: false, error: (err && err.message) || String(err) });
      } catch (_) {}
    },
  );
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return;

  switch (msg.type) {
    case 'JOB_LIST_TOKENS':
      if (Array.isArray(msg.jobs)) {
        tokenStore.storeJobListTokens(msg.jobs);
        console.log('[boss-api-ext] 捕获 listSecurityId，职位数:', msg.jobs.length);
      }
      sendResponse({ ok: true });
      return false;

    case 'TOKEN_UPDATE':
      if (msg.data && msg.data.encryptJobId && msg.data.securityId) {
        const { encryptJobId, securityId, encryptBossId, apiUrl } = msg.data;
        if (apiUrl && apiUrl.includes('friend/add')) {
          // 2026-04-28:把 encryptBossId 一并落库,后端 lookupChatTokenByBoss
          // 才能按 friendList 的 encryptFriendId 反查到 chatSecurityId
          tokenStore.storeChatToken(encryptJobId, securityId, encryptBossId).catch(() => {});
        } else {
          tokenStore.storeDetailToken(encryptJobId, securityId, encryptBossId).catch(() => {});
        }
      }
      sendResponse({ ok: true });
      return false;

    case 'FINGERPRINT':
      if (msg.fp) {
        const fpUpdate = {
          deviceId: msg.fp.deviceId || tokenStore.session.deviceId,
          guid: msg.fp.guid || tokenStore.session.guid,
        };
        if (msg.fp.zpStoken && msg.fp.zpStoken !== tokenStore.session.zpStoken) {
          fpUpdate.zpStoken = msg.fp.zpStoken;
          fpUpdate.zpStokenUpdatedAt = Date.now();
        }
        tokenStore.updateSession(fpUpdate);
      }
      sendResponse({ ok: true });
      return false;

    case 'REQUEST_TOKENS':
      // Boss 自己 JS 请求里的 token / zp_token，供 geekTag 等需要这两个 header 的接口复用
      if (msg.token && msg.token !== tokenStore.session.bossToken) {
        tokenStore.updateSession({ bossToken: msg.token });
      }
      if (msg.zp_token && msg.zp_token !== tokenStore.session.zpStoken) {
        tokenStore.updateSession({ zpStoken: msg.zp_token, zpStokenUpdatedAt: Date.now() });
      }
      return false;

    case 'RESET_REGION_BLOCK': {
      // popup / options 手动触发"重新尝试 LinkedIn"等 → 清掉 site-executor
      // 的 region-blocked 缓存,下一次工具调用即可重开 worker tab。
      const site = msg.site || '';
      try {
        if (typeof self.clearSiteRegionBlocked === 'function') {
          self.clearSiteRegionBlocked(site || undefined);
          console.log('[boss-api-ext] region-block 已清除:', site || '(全部)');
        }
      } catch (e) {
        console.warn('[boss-api-ext] clearSiteRegionBlocked 异常:', e?.message || e);
      }
      sendResponse({ ok: true });
      return false;
    }

    case 'GET_REGION_BLOCK_STATE': {
      // popup 进入时查询当前 region-blocked 状态,显示对应按钮
      const site = msg.site || '';
      try {
        const state = (typeof self.getSiteRegionBlockedState === 'function')
          ? self.getSiteRegionBlockedState(site)
          : null;
        sendResponse({ ok: true, state });
      } catch (e) {
        sendResponse({ ok: false, error: String(e?.message || e) });
      }
      return false;
    }

    case 'ZP_STOKEN_ROTATE':
      if (msg.zpStoken && msg.zpStoken !== tokenStore.session.zpStoken) {
        tokenStore.updateSession({ zpStoken: msg.zpStoken, zpStokenUpdatedAt: Date.now() });
        console.log('[boss-api-ext] zp_stoken 轮转:', msg.zpStoken.slice(0, 20) + '...');
        // 主动推送给 gateway，让 Python 侧持有最新令牌
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: 'token_rotate',
            tokenType: 'zp_stoken',
            value: msg.zpStoken,
            sessionId: currentSessionId,
            ts: Date.now(),
          }));
        }
      }
      sendResponse({ ok: true });
      return false;

    case 'SITE_TOKEN': {
      // 来自 content/interceptor.js 的通用站点 token 消息（linkedin, indeed 等）
      const { site, tokenType, payload } = msg;
      if (site === 'linkedin' && tokenType === 'QUERY_ID') {
        // 存储 LinkedIn queryId（由 interceptor_linkedin_main.js 从出向请求 URL 提取）
        const { queryId, capturedAt } = payload;
        if (queryId && queryId.includes('.')) {
          const QUERY_IDS_KEY = 'linkedin_queryIds';
          const QUERY_ID_TTL_MS = 7 * 24 * 60 * 60 * 1000;
          chrome.storage.local.get(QUERY_IDS_KEY).then(stored => {
            const ids = stored[QUERY_IDS_KEY] || {};
            // 清理超过 7 天的条目
            const now = Date.now();
            for (const key of Object.keys(ids)) {
              if (now - (ids[key].capturedAt || 0) > QUERY_ID_TTL_MS) delete ids[key];
            }
            ids[queryId] = { capturedAt: capturedAt || now };
            return chrome.storage.local.set({ [QUERY_IDS_KEY]: ids });
          }).then(() => {
            console.log('[boss-api-ext] LinkedIn queryId 已捕获:', queryId);
          }).catch(e => console.warn('[boss-api-ext] queryId 存储失败:', e));
        }
      } else if (site === 'linkedin' && tokenType === 'PEOPLE_SEARCH_TOKENS') {
        const peopleEntries = (payload.people || [])
          .filter(p => p.memberUrn)
          .map(p => ({
            entityKey: p.memberUrn,
            field: 'trackingId',
            value: p.trackingId || '',
            meta: { publicId: p.publicId || '', name: p.name || '' },
          }));
        if (peopleEntries.length > 0) {
          tokenStore._batchSetChainTokens('linkedin_people', peopleEntries).catch(() => {});
        }
        console.log('[boss-api-ext] LinkedIn 人才 token 已捕获:', peopleEntries.length, '条');
      } else if (site === 'linkedin' && tokenType === 'JOB_SEARCH_TOKENS') {
        const jobEntries = (payload.jobs || [])
          .filter(j => j.jobUrn)
          .map(j => ({
            entityKey: j.jobUrn,
            field: 'trackingId',
            value: j.trackingId || '',
            meta: { title: j.title || '', companyName: j.companyName || '' },
          }));
        if (jobEntries.length > 0) {
          tokenStore._batchSetChainTokens('linkedin_jobs', jobEntries).catch(() => {});
        }
      } else if (site === 'indeed' && tokenType === 'JOB_SEARCH_TOKENS') {
        const indeedEntries = (payload.jobs || [])
          .filter(j => j.jobKey)
          .map(j => ({
            entityKey: j.jobKey,
            field: 'searchToken',
            value: j.jobKey,
            meta: { title: j.title || '', company: j.company || '', location: j.location || '' },
          }));
        if (indeedEntries.length > 0) {
          tokenStore._batchSetChainTokens('indeed_jobs', indeedEntries).catch(() => {});
        }
        console.log('[boss-api-ext] Indeed 职位 token 已捕获:', indeedEntries.length, '条');
      }
      sendResponse({ ok: true });
      return false;
    }

    case 'connect':
      autoReconnect = true;
      reconnectAttempt = 0;
      connect();
      sendResponse({ ok: true });
      return false;

    case 'disconnect':
      disconnect();
      sendResponse({ ok: true });
      return false;

    case 'reconnectWithNewUrl':
      // 保存新网关地址后重连
      initGatewayUrl().then(url => {
        console.log('[boss-api-ext] 使用新地址重连:', url, 'uid:', PERSONAL_USER_ID || '(none)');
        disconnect();
        autoReconnect = true;
        reconnectAttempt = 0;
        setTimeout(connect, 500);
      });
      sendResponse({ ok: true });
      return false;

    case 'upgradeExtension':
      (async () => {
        const isLocal = GATEWAY_WS_URL.startsWith('ws://127.');
        if (!isLocal) {
          // 云端模式：跳转到固定的 latest zip 下载 URL
          // 注意不用 gateway URL 推导——gateway (testapi.dinq.me) 和静态文件分发
          // (control.dinq.me) 是两个不同的域名/服务, 这里只能硬编码
          sendResponse({
            ok: false,
            cloud_mode: true,
            install_url: 'https://control.dinq.me/boss-api-ext-latest.zip',
          });
          return;
        }
        // 本地模式：通过本地 gateway 执行 git pull + reload
        try {
          const base = getGatewayHttpUrl();
          const resp = await fetch(`${base}/admin/extension/upgrade`, { method: 'POST' });
          const data = await resp.json();
          if (data.ok) {
            sendResponse({ ok: true, version: data.version, commit: data.commit, output: data.output });
            setTimeout(() => chrome.runtime.reload(), 500);
          } else {
            sendResponse({ ok: false, error: data.output || data.error || '升级失败' });
          }
        } catch (e) {
          sendResponse({ ok: false, error: String(e) });
        }
      })();
      return true;

    case 'checkExtensionVersion':
      (async () => {
        try {
          const base = getGatewayHttpUrl();
          // Phase 2: 带 ext=jobseeker/recruiter,后端各 ext 独立版本号
          const resp = await fetch(`${base}/ext/version?ext=${encodeURIComponent(EXT_KIND)}`);
          const data = await resp.json();
          sendResponse({ ok: true, ...data });
        } catch (e) {
          sendResponse({ ok: false, error: String(e) });
        }
      })();
      return true;

    case 'refreshSession':
      _asyncReply(sendResponse, async () => {
        const cmd = getCommand('boss/check_login');
        if (!cmd) throw new Error('boss/check_login 未注册');
        await cmd.handler({});
        return { ok: true, session: tokenStore.session };
      });
      return true;

    case 'refreshDinqLogin':   // 旧消息名保留兼容;现在重载扩展登录身份
      _asyncReply(sendResponse, async () => {
        const prevUserId = PERSONAL_USER_ID;
        await loadAuthIdentity();
        // 身份变了 → 用新 user_id 重连网关,让服务端踢掉旧身份 session
        if (PERSONAL_USER_ID !== prevUserId) {
          console.log('[boss-api-ext] 登录身份已变,重连网关:',
            (prevUserId || '(空)').slice(0, 8), '→', (PERSONAL_USER_ID || '(空)').slice(0, 8));
          tokenStore.setActiveUser(PERSONAL_USER_ID);
          disconnect();
          autoReconnect = true;
          reconnectAttempt = 0;
          setTimeout(connect, 300);
        }
        return { logged_in: !!PERSONAL_USER_ID, userId: PERSONAL_USER_ID };
      });
      return true;

    case 'getStatus':
      chrome.storage.local.get(['browserId', 'sessionId'], (data) => {
        sendResponse({
          connected: !!(ws && ws.readyState === WebSocket.OPEN),
          browserId: data.browserId || '',
          stableBrowserId: STABLE_BROWSER_ID,
          sessionId: currentSessionId || data.sessionId || '',
          session: tokenStore.session,
          jobCount: Object.keys(tokenStore.jobs).length,
          gatewayUrl: GATEWAY_WS_URL,
        });
      });
      return true;

    case 'list_commands':
      sendResponse({ ok: true, commands: listCommands() });
      return false;

    case 'exec_command': {
      const cmd = getCommand(msg.path);
      if (!cmd) {
        sendResponse({ ok: false, error: `命令不存在: ${msg.path}` });
        return false;
      }
      _asyncReply(sendResponse, async () => {
        const result = await cmd.handler(msg.params || {});
        return { ok: true, result };
      });
      return true;
    }

    // ── DQ 内联评估(zhipin 列表注入按钮) ─────────────────────────────────
    case 'DQ_EVALUATE_JOB':
      _asyncReply(sendResponse, async () => {
        console.log('[DQ_EVALUATE_JOB] start job=', msg.encrypt_job_id, 'uid=', PERSONAL_USER_ID || '(empty)');
        if (!PERSONAL_USER_ID) return { ok: false, error: (typeof t === 'function' ? t('bg.notLoggedIn') : '未登录，请先在扩展侧栏完成登录') };
        const base = await getAgentGwBaseUrl();
        const url = `${base}/jobs/evaluate-by-id`;
        // 后端 LLM 调用一般 5-15s,给 30s 上限保证 SW 不被 chrome 因 5min idle 回收;
        // AbortSignal.timeout 在 fetch 抛 TimeoutError → _asyncReply catch 返 ok:false
        const resp = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          signal: AbortSignal.timeout(30000),
          body: JSON.stringify({
            user_id: PERSONAL_USER_ID,
            platform: msg.platform || 'boss',
            encrypt_job_id: msg.encrypt_job_id || '',
            job_snapshot: msg.job_snapshot || {},
          }),
        });
        console.log('[DQ_EVALUATE_JOB] http', resp.status, url);
        if (!resp.ok) {
          const txt = await resp.text().catch(() => '');
          return { ok: false, error: `HTTP ${resp.status}: ${txt.slice(0, 120)}` };
        }
        return await resp.json();
      });
      return true;

    // ── Sprint 3 (D5): 前端 ↔ 后端桥接 ────────────────────────────────────
    // PageContext 上报：content script → background → sidepanel 广播
    case 'DQ_PAGE_CONTEXT': {
      const tabId = sender?.tab?.id;
      if (tabId) {
        _pageContextCache.set(tabId, { ...msg, tabId, receivedAt: Date.now() });
      }
      // 广播给 sidepanel（runtime.sendMessage 会到达所有扩展页面）
      try {
        chrome.runtime.sendMessage({ type: 'DQ_PAGE_CONTEXT_UPDATE', context: msg, tabId },
                                     () => { void chrome.runtime.lastError; });
      } catch (_) {}
      sendResponse({ ok: true });
      return false;
    }

    case 'DQ_GET_PAGE_CONTEXT': {
      // sidepanel 主动拉当前 active tab 的最新 context（启动 / Tab 切换时用）
      _asyncReply(sendResponse, async () => {
        const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!activeTab?.id) return { ok: true, context: null };
        const ctx = _pageContextCache.get(activeTab.id) || null;
        return { ok: true, context: ctx };
      });
      return true;
    }

    case 'DQ_BOOKMARK_ADD':
      _asyncReply(sendResponse, async () => {
        if (!PERSONAL_USER_ID) return { ok: false, error: (typeof t === 'function' ? t('bg.notLoggedIn') : '未登录，请先在扩展侧栏完成登录') };
        const base = getApiGwBaseUrl();
        const resp = await fetch(`${base}/bookmarks`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          signal: AbortSignal.timeout(15000),
          body: JSON.stringify({
            app_user_id: PERSONAL_USER_ID,
            role: msg.role || 'jobseeker',
            platform: msg.platform || 'boss',
            source: msg.source || 'manual',
            external_id: msg.external_id || '',
            ...(msg.fields || {}),
          }),
        });
        if (resp.status === 402) {
          // 配额满 — 返给注入脚本展示升级 Banner
          const body = await resp.json().catch(() => ({}));
          return { ok: false, quota_exceeded: true, ...body };
        }
        if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
        return await resp.json();
      });
      return true;

    case 'DQ_BOOKMARK_REMOVE':
      _asyncReply(sendResponse, async () => {
        if (!PERSONAL_USER_ID) return { ok: false, error: (typeof t === 'function' ? t('bg.notLoggedIn') : '未登录，请先在扩展侧栏完成登录') };
        const base = getApiGwBaseUrl();
        const url = `${base}/bookmarks/${msg.bm_id}?app_user_id=${encodeURIComponent(PERSONAL_USER_ID)}&role=${msg.role || 'jobseeker'}`;
        const resp = await fetch(url, {
          method: 'DELETE',
          credentials: 'include',
          signal: AbortSignal.timeout(15000),
        });
        if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
        return await resp.json();
      });
      return true;

    case 'DQ_TRIGGER_TASK':
      _asyncReply(sendResponse, async () => {
        if (!PERSONAL_USER_ID) return { ok: false, error: (typeof t === 'function' ? t('bg.notLoggedIn') : '未登录，请先在扩展侧栏完成登录') };
        const base = await getAgentGwBaseUrl();
        const resp = await fetch(`${base}/tasks/run`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          signal: AbortSignal.timeout(15000),
          body: JSON.stringify({
            user_id: PERSONAL_USER_ID,
            template_id: msg.template_id,
            platform: msg.platform || 'boss',
            inputs: msg.inputs || {},
          }),
        });
        if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
        return await resp.json();
      });
      return true;

    case 'DQ_OPEN_CHECKOUT':
      // Sprint 6 / G5: 余额不足卡片 / 我的页升级入口 → 调 api-gw POST /billing/checkout
      _asyncReply(sendResponse, async () => {
        if (!PERSONAL_USER_ID) return { ok: false, error: (typeof t === 'function' ? t('bg.notLoggedIn') : '未登录，请先在扩展侧栏完成登录') };
        const base = getApiGwBaseUrl();
        const resp = await fetch(`${base}/billing/checkout`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          signal: AbortSignal.timeout(15000),
          body: JSON.stringify({
            app_user_id: PERSONAL_USER_ID,
            plan: msg.plan || null,
            topup: msg.topup || null,
          }),
        });
        if (!resp.ok) {
          const t = await resp.text().catch(() => '');
          return { ok: false, error: `HTTP ${resp.status}: ${t.slice(0, 120)}` };
        }
        const data = await resp.json();
        return { ok: !!data.ok, url: data.url, mode: data.mode };
      });
      return true;

    case 'DQ_OPEN_CHAT_WITH_CONTEXT': {
      // sidePanel.open() 必须在 onMessage 的同步执行栈里调用：一旦进入
      // microtask / await，user gesture 上下文就失效，open() 会静默抛错。
      // 所以这里不能走 _asyncReply —— 它用 Promise.resolve().then 异步派发回调。
      _pendingChatPrefill = { prompt: msg.prompt || '', context: msg.context || null };
      const winId = sender?.tab?.windowId;
      if (winId != null && chrome.sidePanel?.open) {
        chrome.sidePanel.open({ windowId: winId }).catch(() => {});
      }
      // sidepanel 若已开:直接收这条;若刚被 open() 还在加载:靠启动时
      // DQ_GET_PENDING_PREFILL 兜底拉取。
      chrome.runtime.sendMessage({
        type: 'DQ_PREFILL_CHAT',
        prompt: msg.prompt || '',
        context: msg.context || null,
      }, () => { void chrome.runtime.lastError; });
      sendResponse({ ok: true });
      return true;
    }

    case 'DQ_GET_PENDING_PREFILL': {
      // sidepanel 启动时拉一次待发 prompt(取走即清空)
      const pending = _pendingChatPrefill;
      _pendingChatPrefill = null;
      sendResponse({ ok: true, ...(pending || {}) });
      return true;
    }

    case 'DQ_OPEN_PROFILE': {
      // sidePanel.open() 必须同步调用以保住 user gesture（同 DQ_OPEN_CHAT_WITH_CONTEXT）。
      const winId = sender?.tab?.windowId;
      if (winId != null && chrome.sidePanel?.open) {
        chrome.sidePanel.open({ windowId: winId }).catch(() => {});
      }
      // 广播给 sidepanel,切到 profile tab(sidepanel/app.js 监听 DQ_NAV_PROFILE)
      try { chrome.runtime.sendMessage({ type: 'DQ_NAV_PROFILE' }); } catch (_) {}
      sendResponse({ ok: true });
      return true;
    }

    case 'DQ_SHOW_EVAL_DETAIL': {
      // sidePanel.open() 必须同步调用以保住 user gesture（同 DQ_OPEN_CHAT_WITH_CONTEXT）。
      const winId = sender?.tab?.windowId;
      if (winId != null && chrome.sidePanel?.open) {
        chrome.sidePanel.open({ windowId: winId }).catch(() => {});
      }
      try {
        chrome.runtime.sendMessage({
          type: 'DQ_EVAL_RESULT',
          evaluated: msg.evaluated || {},
          job_snapshot: msg.job_snapshot || {},
        });
      } catch (_) {}
      sendResponse({ ok: true });
      return true;
    }
  }
  return false;
});

// ── 启动 ──────────────────────────────────────────────────────────────────

initializationPromise = tokenStore.load().then(async () => {
  await initStableBrowserId();
  await initGatewayUrl();
  // Indeed 招聘端 webRequest 头收割器:监听用户 tab 发出的 apis.indeed.com/graphql
  // 请求,抓 indeed-api-key / ctk 等真实 headers,后续 GraphQL 调用复用,
  // 解决 cookie-only fetch 401 问题。
  try { _setupIndeedHeaderHarvest(); } catch (_) {}
  // 启动时同步一次扩展登录身份(storage.local.auth),回填 PERSONAL_USER_ID,
  // 否则 WS 握手 query 里没 user_id,网关侧 entry.user_id="" 匹配不上当前用户。
  try {
    await loadAuthIdentity();
    tokenStore.setActiveUser(PERSONAL_USER_ID);   // '' 时落 default 分区
    console.log('[boss-api-ext] 登录身份:',
      PERSONAL_USER_ID ? PERSONAL_USER_ID.slice(0, 8) : '(未登录)');
  } catch (_) {}
  // 恢复上次云端推送的动态命令配置（SW 重启时保持动态命令可用）
  await restoreDynamicConfig();
  console.log('[boss-api-ext] 已初始化，stableBrowserId:', STABLE_BROWSER_ID, '网关:', GATEWAY_WS_URL,
    '| 注册命令:', listCommands().length);
  connect();
}).catch(err => {
  console.error('[boss-api-ext] 初始化失败:', err);
});

// ── 登录身份变化自动重连 ──────────────────────────────────────────────────
// 用户在扩展侧栏登录 / 登出 / 换账号 → auth-api.js 改写 chrome.storage.local.auth。
// 监听变化 → 重载身份 → user_id 变了就重连网关,让 entry.user_id 立刻跟上,
// 不需要用户手动关扩展再开。token 续期(auth 被改写)也会触发,确保握手用新 token。
let _authChangeDebounceTimer = null;
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local' || !changes.auth) return;
  if (_authChangeDebounceTimer) clearTimeout(_authChangeDebounceTimer);
  _authChangeDebounceTimer = setTimeout(async () => {
    _authChangeDebounceTimer = null;
    try {
      const prevUserId = PERSONAL_USER_ID;
      await loadAuthIdentity();
      if (PERSONAL_USER_ID !== prevUserId) {
        console.log('[boss-api-ext] 登录身份变化,重连网关:',
          prevUserId ? prevUserId.slice(0, 8) : '(空)',
          '→',
          PERSONAL_USER_ID ? PERSONAL_USER_ID.slice(0, 8) : '(空)');
        tokenStore.setActiveUser(PERSONAL_USER_ID);
        disconnect();
        autoReconnect = true;
        reconnectAttempt = 0;
        setTimeout(connect, 500);
      }
    } catch (e) {
      console.warn('[boss-api-ext] auth 变化处理失败:', e?.message || e);
    }
  }, 800);
});

// Phase E:启动长任务后台监听(chrome.alarms + notifications)
try {
  if (typeof setupTaskMonitor === 'function') setupTaskMonitor();
} catch (e) {
  console.warn('[task-monitor] setup error:', e);
}

// 单 ext + sidepanel UX:点击工具栏图标直接开 sidepanel(manifest 移除了
// default_popup,所以 click action 不再弹 popup,转而落到下面这条)。
// 必须在 chrome.action.onClicked 监听里调 sidePanel.open(因为 user gesture
// 限制),不能仅依赖 setPanelBehavior。两种方式都注册,任意一条生效都能开:
try {
  chrome.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true })
    .catch((e) => console.warn('[sidepanel] setPanelBehavior failed:', e));
} catch (_) {}
chrome.action.onClicked.addListener((tab) => {
  // tab.windowId 必传,否则 Chrome 不知道开哪个窗口的 sidepanel
  chrome.sidePanel?.open?.({ windowId: tab.windowId })
    .catch((e) => console.warn('[sidepanel] open failed:', e));
});
