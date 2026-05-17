/*
 * shared/events.js — 集中事件名常量.
 *
 * 防散落 magic string. 所有模块订阅/派发 DQ 事件都从这里拿名字.
 * 暴露到 window.DQ.events.
 *
 * 添加事件 = 在 NAMES 加一条 + 文档化 detail shape.
 */
(function () {
  'use strict';

  const NAMES = Object.freeze({
    // ── 身份 / 角色 / 平台 ──
    AUTH_CHANGED:        'dq:auth-changed',          // detail = { loggedIn: boolean }
    ROLE_CHANGED:        'dq:role-changed',          // detail = { role: 'jobseeker'|'recruiter' }
    PLATFORM_CHANGED:    'dq:platform-changed',      // detail = { platform: 'boss'|'linkedin'|'indeed' }

    // ── UI 状态 ──
    TAB_CHANGED:         'dq:tab-changed',           // detail = { tab: string }
    PAGE_CONTEXT_CHANGED:'dq:page-context-changed',  // detail = pageContext

    // ── 数据 ──
    CHIPS_UPDATED:       'dq:chips-updated',         // detail = { chips: [...] }
    BOOKMARKS_REFRESHED: 'dq:bookmarks-refreshed',   // detail = { count: number }
    TASKS_REFRESHED:     'dq:tasks-refreshed',       // detail = { running: number, paused: number }

    // ── Credit / Billing ──
    CREDIT_UPDATED:      'dq:credit-updated',        // detail = { balance: number }
    INSUFFICIENT_CREDIT: 'dq:insufficient-credit',   // detail = { sku, required, available }
  });

  // 简单 on/off/emit 帮助函数,不强求(模块也可直接 window.addEventListener,但统一更好)
  function on(name, handler)  { window.addEventListener(name, handler); }
  function off(name, handler) { window.removeEventListener(name, handler); }
  function emit(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }

  window.DQ = window.DQ || {};
  window.DQ.events = { NAMES, on, off, emit };
})();
