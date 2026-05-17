// =============================================================================
// API 录制子系统 — background（合并自 api-recorder-v2）
// 用 chrome.debugger（CDP Network domain）做隐身式流量抓取：无内容脚本注入，
// 页面 JS 无法感知。整体包在 IIFE 内，避免与 v3 SW 其它全局符号冲突。
//
// 消息以 { action } 区分（v3/autofill 用 { type }），监听器只处理本子系统消息。
// 服务端地址按 gatewayHost 本地优先推导（不再硬编码线上域名）。
// =============================================================================
(function () {
  'use strict';

  // --------------- State ---------------
  const sessions = new Map(); // tabId -> session
  const globalSettings = {
    serverUrl: '',           // 显式覆盖；空 = 按 gatewayHost 自动推导
    domainFilter: [],        // 空 = 抓全部
    captureTypes: ['XHR', 'Fetch'],  // 抓取的资源类型；空 = 全部
    maxBodySize: 262144,     // body 最大存储 256 KB
  };

  // 启动时恢复设置
  chrome.storage.local.get(['recorderSettings'], (result) => {
    if (chrome.runtime.lastError) return;
    if (result.recorderSettings) Object.assign(globalSettings, result.recorderSettings);
  });

  // 服务端基址：显式覆盖优先；否则按 gatewayHost 推导 agent-gw（本地优先）
  async function recorderBase() {
    if (globalSettings.serverUrl) return globalSettings.serverUrl;
    try {
      const r = await chrome.storage.local.get(['agentGwUrl', 'gatewayHost']);
      // options 设置页显式指定的 agent-gw URL 优先；为空才回退到 gatewayHost 推导。
      const ov = (r.agentGwUrl || '').trim();
      if (ov) return ov.replace(/\/+$/, '');
      const host = r.gatewayHost || '127.0.0.1';
      if (/^(127\.0\.0\.1|localhost|0\.0\.0\.0|192\.168\.|10\.)/.test(host)) {
        return 'http://' + (host.includes(':') ? host : host + ':8769');
      }
      return 'https://agent.job.joyhouse.chat';
    } catch (_) {
      return 'http://127.0.0.1:8769';
    }
  }

  // --------------- 悬挂请求 TTL 清理 ---------------
  const PENDING_TTL_MS = 30000;
  setInterval(() => {
    const now = Date.now();
    for (const session of sessions.values()) {
      for (const [requestId, record] of session.pendingRequests) {
        // record.addedAt 是墙钟时间(Date.now())；不能用 CDP 的 timestamp
        // —— 后者是单调时钟(秒,自启动起算),与 Date.now() 不同基准。
        const ageMs = now - record.addedAt;
        if (ageMs > PENDING_TTL_MS) session.pendingRequests.delete(requestId);
      }
    }
  }, 10000);

  // --------------- CDP 事件处理 ---------------
  chrome.debugger.onEvent.addListener((source, method, params) => {
    const session = sessions.get(source.tabId);
    if (!session || !session.recording) return;
    switch (method) {
      case 'Network.requestWillBeSent': onRequestWillBeSent(source.tabId, session, params); break;
      case 'Network.responseReceived':  onResponseReceived(source.tabId, session, params); break;
      case 'Network.loadingFinished':   onLoadingFinished(source.tabId, session, params); break;
      case 'Network.loadingFailed':     onLoadingFailed(source.tabId, session, params); break;
    }
  });

  function matchesDomainFilter(url) {
    if (!globalSettings.domainFilter || globalSettings.domainFilter.length === 0) return true;
    try {
      const hostname = new URL(url).hostname;
      return globalSettings.domainFilter.some((pattern) => {
        if (pattern.startsWith('*.')) {
          const suffix = pattern.slice(1);
          return hostname.endsWith(suffix);
        }
        return hostname === pattern || hostname.endsWith('.' + pattern);
      });
    } catch (e) {
      return false;
    }
  }

  function onRequestWillBeSent(tabId, session, params) {
    const { requestId, request, type, timestamp, initiator } = params;
    // 注意：CDP 的 requestWillBeSent.type 是「可选」字段，Chrome 常常缺省不发。
    // 资源类型过滤不能在这里做，否则 type===undefined 会把所有请求误杀；
    // 改到 responseReceived/loadingFinished 用权威的 type 再过滤。
    if (!matchesDomainFilter(request.url)) return;
    if (session.pendingRequests.has(requestId)) session.pendingRequests.delete(requestId);
    session.pendingRequests.set(requestId, {
      requestId,
      resourceType: type || null,
      timestamp,
      addedAt: Date.now(),
      initiator: initiator ? (initiator.type || null) : null,
      request: {
        url: request.url,
        method: request.method,
        headers: request.headers || {},
        postData: request.postData ? request.postData.substring(0, globalSettings.maxBodySize) : null,
        hasPostData: request.hasPostData || false,
      },
      response: null,
    });
    session.totalCount++;
    persistSessionMeta(tabId, session);
  }

  function onResponseReceived(tabId, session, params) {
    const { requestId, response, type } = params;
    const record = session.pendingRequests.get(requestId);
    if (!record) return;
    // responseReceived.type 是必填字段、权威 —— 覆盖可能缺失的 requestWillBeSent.type
    if (type) record.resourceType = type;
    record.response = {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers || {},
      mimeType: response.mimeType || '',
      body: null,
    };
  }

  async function onLoadingFinished(tabId, session, params) {
    const { requestId } = params;
    const record = session.pendingRequests.get(requestId);
    if (!record) return;
    session.pendingRequests.delete(requestId);

    // 资源类型过滤：用权威 resourceType(来自 responseReceived)；类型仍未知则放行。
    const ct = globalSettings.captureTypes;
    if (ct.length > 0 && record.resourceType && !ct.includes(record.resourceType)) return;

    const NO_BODY_STATUSES = new Set([204, 304]);
    const shouldFetchBody = record.response &&
      !NO_BODY_STATUSES.has(record.response.status) &&
      record.response.status >= 200;

    if (!shouldFetchBody) {
      if (record.response) {
        record.response.body = record.response.status === 304
          ? '[not modified — body in cache]'
          : '[no body]';
      }
    } else {
      try {
        const result = await chrome.debugger.sendCommand(
          { tabId }, 'Network.getResponseBody', { requestId }
        );
        if (record.response) {
          if (result.base64Encoded) {
            const sizeBytes = result.body ? Math.floor(result.body.length * 3 / 4) : 0;
            record.response.body = `[binary data, ~${sizeBytes} bytes]`;
          } else {
            if (result.body && result.body.length > globalSettings.maxBodySize) {
              const raw = result.body.substring(0, globalSettings.maxBodySize);
              const lastChar = raw.charCodeAt(raw.length - 1);
              const safeTruncated = (lastChar >= 0xD800 && lastChar <= 0xDBFF)
                ? raw.substring(0, raw.length - 1)
                : raw;
              record.response.body = safeTruncated + '\n...[truncated]';
            } else {
              record.response.body = result.body || '';
            }
          }
        }
      } catch (e) {
        if (record.response) {
          record.response.body = `[unavailable: ${e.message || 'unknown error'}]`;
        }
      }
    }

    if (record.request.hasPostData && !record.request.postData) {
      try {
        const result = await chrome.debugger.sendCommand(
          { tabId }, 'Network.getRequestPostData', { requestId }
        );
        if (result.postData) {
          record.request.postData = result.postData.substring(0, globalSettings.maxBodySize);
        }
      } catch (e) { /* not critical */ }
    }

    const completed = {
      id: requestId,
      resourceType: record.resourceType,
      timestamp: record.timestamp,
      request: record.request,
      response: record.response,
    };
    session.completedRequests.push(completed);
    if (session.completedRequests.length > 1000) {
      session.completedRequests = session.completedRequests.slice(-800);
    }
    sendToServer(session.sessionId, session.tabUrl, completed);
    chrome.runtime.sendMessage({
      action: 'captureUpdate', tabId, completedCount: session.completedRequests.length,
    }).catch(() => {});
  }

  function onLoadingFailed(tabId, session, params) {
    const { requestId, errorText } = params;
    const record = session.pendingRequests.get(requestId);
    if (!record) return;
    session.pendingRequests.delete(requestId);
    record.response = {
      status: 0, statusText: errorText || 'Network Error',
      headers: {}, mimeType: '', body: null,
    };
    const completed = {
      id: requestId,
      resourceType: record.resourceType,
      timestamp: record.timestamp,
      request: record.request,
      response: record.response,
    };
    session.completedRequests.push(completed);
    sendToServer(session.sessionId, session.tabUrl, completed);
    chrome.runtime.sendMessage({
      action: 'captureUpdate', tabId, completedCount: session.completedRequests.length,
    }).catch(() => {});
  }

  // --------------- debugger detach ---------------
  chrome.debugger.onDetach.addListener((source, reason) => {
    const tabId = source.tabId;
    const session = sessions.get(tabId);
    if (session) {
      session.recording = false;
      session.detachReason = reason;
      persistSessionMeta(tabId, session);
    }
  });

  // --------------- tab 关闭 ---------------
  chrome.tabs.onRemoved.addListener((tabId) => {
    const session = sessions.get(tabId);
    if (session && session.recording) {
      try { chrome.debugger.detach({ tabId }); } catch (e) {}
    }
    sessions.delete(tabId);
  });

  // --------------- 录制控制 ---------------
  async function startRecording(tabId) {
    const existing = sessions.get(tabId);
    if (existing && existing.recording) return existing.sessionId;

    let tabUrl = '';
    try {
      const tab = await chrome.tabs.get(tabId);
      tabUrl = tab.url || '';
    } catch (e) {}

    // service worker 重启 / 扩展重载后，内存里的 sessions 会清空，但上一轮
    // attach 的 CDP debugger 可能仍残留在 tab 上 —— 此时直接 attach 会抛
    // "Another debugger is already attached"。先尽力解除残留 attach（无则忽略）。
    try { await chrome.debugger.detach({ tabId }); } catch (e) { /* 无残留 attach */ }

    await chrome.debugger.attach({ tabId }, '1.3');
    await chrome.debugger.sendCommand({ tabId }, 'Network.enable', {
      maxPostDataSize: Math.min(globalSettings.maxBodySize, 10 * 1024 * 1024),
    });

    const sessionId = `rec_${Date.now()}`;
    const session = {
      sessionId, sessionName: '', tabId, tabUrl,
      recording: true, startTime: Date.now(), totalCount: 0,
      pendingRequests: new Map(), completedRequests: [], detachReason: null,
    };
    sessions.set(tabId, session);
    persistSessionMeta(tabId, session);
    return sessionId;
  }

  async function stopRecording(tabId) {
    const session = sessions.get(tabId);
    if (!session) return;
    session.recording = false;
    try { await chrome.debugger.detach({ tabId }); } catch (e) {}
    persistSessionMeta(tabId, session);
  }

  // --------------- 服务端通信 ---------------
  const FETCH_TIMEOUT_MS = 30000;

  async function sendToServer(sessionId, tabUrl, capture) {
    try {
      const base = await recorderBase();
      const resp = await fetch(`${base}/api/capture`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, tab_url: tabUrl, capture }),
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
      if (!resp.ok) console.warn('[API Recorder] server responded', resp.status);
    } catch (e) {
      console.warn('[API Recorder] send failed:', e.message || e);
    }
  }

  async function notifyServerSessionStart(sessionId, tabUrl, sessionName) {
    try {
      const base = await recorderBase();
      const body = { session_id: sessionId, tab_url: tabUrl };
      if (sessionName) body.session_name = sessionName;
      await fetch(`${base}/api/session/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
    } catch (e) { /* server unavailable */ }
  }

  // --------------- cookie 提取 ---------------
  async function getCookiesForUrl(url) {
    try {
      const cookies = await chrome.cookies.getAll({ url });
      return cookies.map((c) => ({
        name: c.name, value: c.value, domain: c.domain, path: c.path,
        httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite,
      }));
    } catch (e) {
      return [];
    }
  }

  // --------------- 持久化 ---------------
  function persistSessionMeta(tabId, session) {
    chrome.storage.local.set({
      [`recorderSession_${tabId}`]: {
        sessionId: session.sessionId,
        sessionName: session.sessionName || '',
        tabId: session.tabId,
        tabUrl: session.tabUrl,
        recording: session.recording,
        startTime: session.startTime,
        totalCount: session.totalCount,
        completedCount: session.completedRequests.length,
      },
    });
  }

  // --------------- 消息处理（仅本子系统 { action } 消息）---------------
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || typeof message.action !== 'string') return; // 放行给其它监听器
    const handler = messageHandlers[message.action];
    if (!handler) return;
    const result = handler(message, sender);
    if (result instanceof Promise) {
      result.then((r) => sendResponse(r)).catch((e) => sendResponse({ error: e.message }));
      return true;
    }
    sendResponse(result);
    return false;
  });

  const messageHandlers = {
    startRecording: async (msg) => {
      try {
        const sessionId = await startRecording(msg.tabId);
        const session = sessions.get(msg.tabId);
        if (msg.sessionName && session) session.sessionName = msg.sessionName;
        if (session) notifyServerSessionStart(sessionId, session.tabUrl, session.sessionName || '');
        return { success: true, sessionId };
      } catch (e) {
        return { success: false, error: e.message };
      }
    },

    stopRecording: async (msg) => {
      try {
        await stopRecording(msg.tabId);
        return { success: true };
      } catch (e) {
        return { success: false, error: e.message };
      }
    },

    getStatus: async (msg) => {
      const session = sessions.get(msg.tabId);
      return {
        recording: session ? session.recording : false,
        sessionId: session ? session.sessionId : null,
        sessionName: session ? (session.sessionName || '') : '',
        totalCount: session ? session.totalCount : 0,
        completedCount: session ? session.completedRequests.length : 0,
        detachReason: session ? session.detachReason : null,
        serverUrl: await recorderBase(),
      };
    },

    getRequests: (msg) => {
      const session = sessions.get(msg.tabId);
      if (!session) return { requests: [] };
      const limit = msg.limit || 50;
      return { requests: session.completedRequests.slice(-limit).reverse() };
    },

    clearRequests: (msg) => {
      const session = sessions.get(msg.tabId);
      if (session) {
        session.completedRequests = [];
        session.totalCount = 0;
        persistSessionMeta(msg.tabId, session);
      }
      return { success: true };
    },

    getAllData: (msg) => {
      const session = sessions.get(msg.tabId);
      if (!session) return { data: null };
      return {
        data: {
          sessionId: session.sessionId,
          tabUrl: session.tabUrl,
          startTime: session.startTime,
          requests: session.completedRequests,
        },
      };
    },

    getCookies: async (msg) => {
      const cookies = await getCookiesForUrl(msg.url);
      const header = cookies.map((c) => `${c.name}=${c.value}`).join('; ');
      return { cookies, header };
    },

    getSettings: () => ({ settings: globalSettings }),

    updateSettings: (msg) => {
      if (msg.settings) {
        Object.assign(globalSettings, msg.settings);
        chrome.storage.local.set({ recorderSettings: globalSettings });
      }
      return { success: true };
    },

    checkServer: async () => {
      try {
        const base = await recorderBase();
        const resp = await fetch(`${base}/api/health`, {
          method: 'GET', signal: AbortSignal.timeout(3000),
        });
        return { online: resp.ok };
      } catch (e) {
        return { online: false };
      }
    },

    triggerAnalysis: async (msg) => {
      const session = sessions.get(msg.tabId);
      if (!session) return { error: 'no active session' };
      try {
        const base = await recorderBase();
        const resp = await fetch(
          `${base}/api/sessions/${session.sessionId}/analyze`,
          { method: 'POST', signal: AbortSignal.timeout(120000) }
        );
        return await resp.json();
      } catch (e) {
        return { error: e.message };
      }
    },

    flushToServer: async (msg) => {
      const session = sessions.get(msg.tabId);
      if (!session) return { error: 'no session' };
      const base = await recorderBase();
      let sent = 0;
      for (const capture of session.completedRequests) {
        try {
          await fetch(`${base}/api/capture`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              session_id: session.sessionId, tab_url: session.tabUrl, capture,
            }),
            signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
          });
          sent++;
        } catch (e) {
          break;
        }
      }
      return { success: true, sent };
    },
  };

  console.log('[recorder] API 录制子系统已加载');
})();
