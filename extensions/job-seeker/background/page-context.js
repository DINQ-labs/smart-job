/*
 * background/page-context.js — PageContext cache helper(thin wrapper).
 *
 * 当前 cache 在 index.js 的 _pageContextCache(Map)。这里给 sidepanel 调用方
 * 提供 stable 接口,不让外部知道具体实现。Phase 4 后续将把 cache 从 index.js
 * 完全迁过来,index.js 那段删除即可,API 保持不变。
 *
 * 暴露:globalThis.DQ_PageContext = { get, set, clear, broadcast }
 */
(function () {
  'use strict';
  // 引用 index.js 的 _pageContextCache(SW 全局共享同一 V8 isolate)。
  // 如果 index.js 还没加载就 undefined,fallback 用本地 Map。
  function _cache() {
    if (typeof _pageContextCache === 'undefined' || !_pageContextCache) {
      // eslint-disable-next-line no-global-assign
      globalThis._pageContextCache = globalThis._pageContextCache || new Map();
      return globalThis._pageContextCache;
    }
    return _pageContextCache;
  }

  globalThis.DQ_PageContext = {
    set(tabId, payload) {
      if (!tabId) return;
      _cache().set(tabId, { ...payload, tabId, receivedAt: Date.now() });
    },
    get(tabId) {
      return _cache().get(tabId) || null;
    },
    clear(tabId) {
      _cache().delete(tabId);
    },
    /** 主动推 sidepanel(广播给所有扩展页面).可在 v3 content script 上报后调一次. */
    broadcast(tabId, context) {
      try {
        chrome.runtime.sendMessage(
          { type: 'DQ_PAGE_CONTEXT_UPDATE', context, tabId },
          () => { void chrome.runtime.lastError; }
        );
      } catch (_) {}
    },
  };
})();
