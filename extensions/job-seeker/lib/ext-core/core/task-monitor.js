/**
 * ext_shared/core/task-monitor.js — 长任务后台监听 (Phase E)
 *
 * 在 background service worker 跑,即使 sidepanel 关掉也持续监控:
 *   1. chrome.alarms 每 60s tick → 拉 /tasks?status=running,paused_user_action
 *   2. diff 上次 snapshot,触发两类 chrome.notifications:
 *        - paused_user_action 状态首次出现 → "⚠ 任务暂停,需要协助"
 *        - completed / failed 状态首次出现(从 running/paused 转为 terminal) → "✓/✕ 任务完成"
 *   3. notification click → 打开 sidepanel(若 chrome.sidePanel.open 可用)
 *
 * 依赖:
 *   - chrome.alarms / chrome.notifications permissions(加在 manifest.json)
 *   - PERSONAL_USER_ID(background.js 全局)— 没登录就不 poll
 *   - EXT_KIND(background.js 全局)— 'jobseeker' / 'recruiter'
 *   - GATEWAY_HTTP(background.js 全局,可选)— agent gateway base URL
 *
 * 使用:在 background.js 末尾调 setupTaskMonitor()。
 */
(function (root) {
  'use strict';

  const ALARM_NAME = 'dq-task-poll';
  const POLL_INTERVAL_MIN = 1.0;        // 1 分钟(chrome.alarms 最小 1 min)
  const SNAPSHOT_KEY = 'dqTaskSnapshot';   // chrome.storage.local
  const RECENT_NOTIFY_KEY = 'dqTaskRecentNotify';  // 去重 30s 窗口
  const NOTIFY_DEDUP_WINDOW_MS = 30 * 1000;

  // ── 工具:拿 agent gateway base URL ─────────────────────
  async function getAgentGwBase() {
    try {
      const stored = await chrome.storage.local.get(['agentGwUrl', 'gatewayHost']);
      // options 设置页显式指定的 agent-gw URL 优先；为空才回退到 gatewayHost 推导。
      const ov = (stored.agentGwUrl || '').trim();
      if (ov) return ov.replace(/\/+$/, '');
      const host = stored.gatewayHost || '127.0.0.1';
      if (/^(127\.0\.0\.1|localhost)/.test(host)) {
        return `http://${host.includes(':') ? host : host + ':8769'}`;
      }
      return 'https://agent.job.joyhouse.chat';
    } catch (e) {
      return 'https://agent.job.joyhouse.chat';
    }
  }

  // ── 工具:拿当前用户 ID(PERSONAL_USER_ID 在 background.js 全局)──
  function getUserId() {
    try {
      return (typeof PERSONAL_USER_ID !== 'undefined' && PERSONAL_USER_ID) || '';
    } catch (_) { return ''; }
  }

  function getRole() {
    try {
      return (typeof EXT_KIND !== 'undefined' && EXT_KIND) || 'jobseeker';
    } catch (_) { return 'jobseeker'; }
  }

  // ── snapshot 持久化:chrome.storage.local 而非内存,SW 可能被 chrome 杀 ──
  async function loadSnapshot() {
    const r = await chrome.storage.local.get([SNAPSHOT_KEY, RECENT_NOTIFY_KEY]);
    return {
      tasks: r[SNAPSHOT_KEY] || {},               // { task_id: { status, paused_signal } }
      recent: r[RECENT_NOTIFY_KEY] || {},         // { dedup_key: timestamp }
    };
  }

  async function saveSnapshot(tasks, recent) {
    await chrome.storage.local.set({
      [SNAPSHOT_KEY]: tasks,
      [RECENT_NOTIFY_KEY]: recent,
    });
  }

  // ── 推通知(带去重) ──
  // iconUrl: chrome.notifications type=basic 必填。我们的 ext 没自带 icons/,
  // 用 manifest.icons 拿(Chrome 自动加),没有就从 chrome.runtime.getURL('') 取,
  // 都没有就 fallback 一个 1x1 透明 png data URL(MV3 不接受 data URL → 留空让 chrome 用默认)。
  function _resolveIconUrl() {
    try {
      const m = chrome.runtime.getManifest();
      const icons = m.icons || m.action?.default_icon;
      if (icons) {
        const pick = icons['128'] || icons['64'] || icons['48'] || icons['32'] || icons['16'];
        if (pick) return chrome.runtime.getURL(pick);
      }
    } catch (_) {}
    return ''; // 留空 chrome 用默认
  }
  const ICON_URL = _resolveIconUrl();

  async function notify(opts, dedupKey, recent) {
    const now = Date.now();
    if (dedupKey && recent[dedupKey] && (now - recent[dedupKey]) < NOTIFY_DEDUP_WINDOW_MS) {
      return; // 30s 内相同 dedupKey 只推一次
    }
    if (dedupKey) recent[dedupKey] = now;
    // 清掉过期 dedup 记录
    for (const k of Object.keys(recent)) {
      if (now - recent[k] > NOTIFY_DEDUP_WINDOW_MS * 4) delete recent[k];
    }
    try {
      // notificationId 用 task-{id} 形式让 click handler 拿到
      const id = opts._notificationId || ('dq-task-' + Math.random().toString(36).slice(2));
      delete opts._notificationId;
      const notifOpts = {
        type: 'basic',
        title: opts.title,
        message: opts.message,
        priority: opts.priority ?? 1,
        requireInteraction: !!opts.requireInteraction,
      };
      if (ICON_URL) notifOpts.iconUrl = ICON_URL;
      await new Promise((resolve) => {
        chrome.notifications.create(id, notifOpts, () => resolve());
      });
    } catch (e) {
      console.warn('[task-monitor] notify failed:', e);
    }
  }

  // ── 状态文案 ────────────────────────────────────
  const PAUSE_GUIDES = {
    'boss:captcha':                'Boss 检测到滑块验证。请到 zhipin.com 完成验证后回来点继续。',
    'boss:logged_out':             'Boss 登录失效。请重新登录后回来点继续。',
    'boss:phone_verify':           'Boss 要求短信验证。',
    'linkedin:region_block':       'LinkedIn 区域被封。请打开 VPN 切到境外网络。',
    'linkedin:trust_intervention': 'LinkedIn 要求验证身份。',
    'linkedin:logged_out':         'LinkedIn 登录失效,请重新登录。',
    'indeed:cloudflare_challenge': 'Indeed 要求 Cloudflare 验证。',
  };

  function pauseMessage(signalId) {
    return PAUSE_GUIDES[signalId] || `平台触发风控 (${signalId || '未知'})。请回到 sidepanel 查看详情。`;
  }

  // ── 主轮询 ─────────────────────────────────────
  async function pollTasks() {
    const userId = getUserId();
    if (!userId) return;
    const role = getRole();
    let tasks = [];
    try {
      const base = await getAgentGwBase();
      const qs = new URLSearchParams({
        user_id: userId, role,
        status: 'running,paused_user_action,completed,failed,cancelled',
        limit: '50',
      });
      const r = await fetch(`${base}/tasks?${qs}`);
      if (!r.ok) return;
      const data = await r.json();
      tasks = data.tasks || [];
    } catch (e) {
      // 网络错误时不动 snapshot,下次 tick 重试
      return;
    }

    const { tasks: prev, recent } = await loadSnapshot();
    const next = {};

    for (const t of tasks) {
      const id = String(t.id);
      next[id] = {
        status: t.status,
        paused_signal: t.paused_signal || '',
        title: t.title || t.template_id || '',
        platform: t.platform || '',
      };
      const before = prev[id];

      // 1. running → paused_user_action 转移 → 推 paused 通知
      if (t.status === 'paused_user_action' && (!before || before.status !== 'paused_user_action')) {
        await notify({
          title: '⚠ 任务暂停,需要你的协助',
          message: `[${platformLabel(t.platform)}] ${t.title || t.template_id} — ${pauseMessage(t.paused_signal)}`,
          priority: 2,
          requireInteraction: true,
          _notificationId: `dq-task-paused-${id}`,
        }, `paused-${id}-${t.paused_signal}`, recent);
      }

      // 2. running/paused → terminal 转移 → 推完成/失败通知
      const TERMINAL = ['completed', 'failed', 'cancelled'];
      const wasActive = before && (before.status === 'running' || before.status === 'paused_user_action');
      if (TERMINAL.includes(t.status) && wasActive) {
        const icon = t.status === 'completed' ? '✓' : (t.status === 'failed' ? '✕' : '⊘');
        const verb = t.status === 'completed' ? '完成' : (t.status === 'failed' ? '失败' : '已取消');
        await notify({
          title: `${icon} 任务${verb}`,
          message: `[${platformLabel(t.platform)}] ${t.title || t.template_id} ${t.status === 'completed' ? '— 详情看 sidepanel' : ''}`,
          priority: 1,
          _notificationId: `dq-task-${t.status}-${id}`,
        }, `${t.status}-${id}`, recent);
      }
    }

    await saveSnapshot(next, recent);
  }

  function platformLabel(p) {
    return ({ boss: 'Boss', linkedin: 'LinkedIn', indeed: 'Indeed' })[p] || (p || '');
  }

  // ── alarm 监听 ──────────────────────────────────
  function onAlarm(alarm) {
    if (alarm.name !== ALARM_NAME) return;
    pollTasks().catch((e) => console.warn('[task-monitor] poll error:', e));
  }

  // ── notification click → 打开 sidepanel(尽力而为) ──
  async function onNotificationClick(notificationId) {
    chrome.notifications.clear(notificationId);
    try {
      // chrome.sidePanel.open 需要 active window,SW 没有 windowId 时退化为开新 tab
      const win = await chrome.windows.getCurrent({ populate: false }).catch(() => null);
      if (win && chrome.sidePanel?.open) {
        await chrome.sidePanel.open({ windowId: win.id });
        return;
      }
    } catch (_) {}
    // 退化:打开扩展 options(里面有任务 tab 跳转)— 没有的话 noop
    try {
      await chrome.tabs.create({
        url: chrome.runtime.getURL('sidepanel.html'),
      });
    } catch (_) {}
  }

  // ── 入口 ────────────────────────────────────────
  async function setup() {
    if (!chrome.alarms || !chrome.notifications) {
      console.warn('[task-monitor] alarms/notifications API 不可用,跳过');
      return;
    }
    try {
      await chrome.alarms.create(ALARM_NAME, {
        periodInMinutes: POLL_INTERVAL_MIN,
        delayInMinutes: POLL_INTERVAL_MIN,
      });
      chrome.alarms.onAlarm.addListener(onAlarm);
      chrome.notifications.onClicked.addListener(onNotificationClick);
      console.log('[task-monitor] ready (poll every', POLL_INTERVAL_MIN, 'min)');
    } catch (e) {
      console.warn('[task-monitor] setup failed:', e);
    }
  }

  // 暴露给 background.js
  root.setupTaskMonitor = setup;
  root.taskMonitorPollNow = pollTasks;
})(self);
