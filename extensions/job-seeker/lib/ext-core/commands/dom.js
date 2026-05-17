/**
 * DOM 视觉 + 点击 + 导航命令（v1.6 新增）
 *
 * 三个站点（zhipin / linkedin / indeed）各 6 条同名命令：
 *   {site}/get_clickables       — 列页面所有可点击元素
 *   {site}/get_dom_snapshot     — 完整 DOM 树（截断）
 *   {site}/click_by_idx         — 用 snapshot_id + idx 点击（带 selector 漂移 fallback）
 *   {site}/click_by_text        — 按可见文本点击
 *   {site}/wait_for             — 等元素出现
 *   {site}/navigate_to          — 导航 Worker Tab 到指定 URL（host 白名单）
 *
 * 依赖（全局）：
 *   defineCommand, extOk（成功路径）；失败路径直接 throw new Error()
 *   executeSiteFormAction / ensureSiteWorkerTab (core/site-executor.js)
 *   formGetClickables / formGetDomSnapshot / formClickByText / formClickElement /
 *     formWaitForElement (core/form-filler.js)
 *
 * snapshot 缓存：内存 Map（不走 tokenStore / chrome.storage.local）
 *   - 5s TTL，过期即删
 *   - LRU 上限 100 条
 *   - 故意不持久化：5s 后必失效，SW 重启 restore 全是死数据，浪费 storage 配额
 */

// ── snapshot 缓存（in-memory Map，5s TTL + LRU 100）───────────────────────

const _DOM_SNAPSHOT_TTL_MS = 5000;
const _DOM_SNAPSHOT_MAX = 100;
const _domSnapshots = new Map();   // Map<sid, { ts, clickables }>

function _genSnapshotId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return 'snap_' + crypto.randomUUID().replace(/-/g, '').slice(0, 16);
  }
  return 'snap_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10);
}

function _putSnapshot(id, clickables) {
  // Map 保留插入序；满则弹最早的（LRU 简化版：access 不重排）
  if (_domSnapshots.size >= _DOM_SNAPSHOT_MAX) {
    const oldest = _domSnapshots.keys().next().value;
    _domSnapshots.delete(oldest);
  }
  _domSnapshots.set(id, { ts: Date.now(), clickables });
}

function _getSnapshot(id) {
  const entry = _domSnapshots.get(id);
  if (!entry) return { ok: false, error: 'snapshot 不存在或已过期' };
  if (Date.now() - entry.ts > _DOM_SNAPSHOT_TTL_MS) {
    _domSnapshots.delete(id);
    return { ok: false, error: 'snapshot expired (>' + _DOM_SNAPSHOT_TTL_MS + 'ms)' };
  }
  return { ok: true, clickables: entry.clickables };
}

// ── host 白名单（防 prompt injection 跨站导航）──────────────────────────────
// 用 URL parser 而非正则，避免 userinfo / 大小写 / IDN 边界 case。

const _SITE_HOST_SUFFIXES = {
  zhipin: 'zhipin.com',
  linkedin: 'linkedin.com',
  indeed: 'indeed.com',
};

function _isAllowedUrl(siteName, url) {
  const suffix = _SITE_HOST_SUFFIXES[siteName];
  if (!suffix) return false;
  let parsed;
  try {
    parsed = new URL(url);
  } catch (_) {
    return false;
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return false;
  const host = parsed.hostname.toLowerCase();
  // 完全相等 或 子域（前缀以 . 结尾）
  return host === suffix || host.endsWith('.' + suffix);
}


// ── 静默导航（不抢用户前台焦点）─────────────────────────────────────────────
// navigateSiteWorkerTab 默认 active:true 给登录场景用；agent 调用应静默。

async function _navigateQuietly(siteName, url, loadTimeoutMs = 15000) {
  const tab = await ensureSiteWorkerTab(siteName);   // 已存在则返回，不存在则创建（active:false）
  if (!tab || !tab.id) throw new Error(`无法获取 ${siteName} Worker Tab`);
  await chrome.tabs.update(tab.id, { url });          // 不带 active 参数 → 不抢焦点
  // 等导航 + load 完成；避免后续 wait_for_selector 撞到老 DOM
  if (typeof waitForSiteTabLoad === 'function') {
    await waitForSiteTabLoad(tab.id, loadTimeoutMs);
  }
  return tab;
}

// ── 6 条命令 × 3 站点的工厂函数 ─────────────────────────────────────────────

/**
 * @param {string} cmdSite  路径前缀，如 'boss' | 'linkedin' | 'indeed'
 * @param {string} siteName executeSiteFormAction 用的站名，如 'zhipin' | 'linkedin' | 'indeed'
 */
function _registerDomCommandsFor(cmdSite, siteName) {
  // 1. get_clickables ─────────────────────────────────────────
  defineCommand({
    path: `${cmdSite}/get_clickables`,
    description: `回传 ${cmdSite} Worker Tab 所有可点击元素列表（含 idx + selector + text + rect）。返回 snapshot_id 供 click_by_idx 使用，5s TTL。`,
    handler: async ({ rootSelector = 'body', includeHidden = false, maxItems = 200 } = {}) => {
      const result = await executeSiteFormAction(
        siteName, formGetClickables, rootSelector, { includeHidden, maxItems },
      );
      if (!result || !result.ok) {
        return extOk(result || { ok: false, error: 'no result' });
      }
      const sid = _genSnapshotId();
      _putSnapshot(sid, result.clickables);
      return extOk({ ...result, snapshot_id: sid });
    },
  });

  // 2. get_dom_snapshot ───────────────────────────────────────
  defineCommand({
    path: `${cmdSite}/get_dom_snapshot`,
    description: `回传 ${cmdSite} Worker Tab 完整 DOM 树（截断）。比 get_clickables 详细但 token 消耗大。`,
    handler: async ({ rootSelector = 'body', maxDepth = 6, maxNodes = 500, includeText = true } = {}) => {
      const result = await executeSiteFormAction(
        siteName, formGetDomSnapshot, rootSelector,
        { maxDepth, maxNodes, includeText },
      );
      return extOk(result || { ok: false, error: 'no result' });
    },
  });

  // 3. click_by_idx ───────────────────────────────────────────
  defineCommand({
    path: `${cmdSite}/click_by_idx`,
    description: `用 snapshot_id + idx 点击。selector 失效时自动用快照里的 text 走文本匹配 fallback。`,
    handler: async ({ snapshot_id, idx, timeout_ms = 5000, fallback_text = true } = {}) => {
      if (!snapshot_id) throw new Error('snapshot_id 必填');
      if (idx == null) throw new Error('idx 必填');
      const snap = _getSnapshot(snapshot_id);
      if (!snap.ok) throw new Error(snap.error);
      const item = snap.clickables[idx];
      if (!item) throw new Error(`idx=${idx} 越界，clickables 共 ${snap.clickables.length} 项`);

      // 先用 selector 试
      const r1 = await executeSiteFormAction(
        siteName, formClickElement, item.selector, timeout_ms,
      );
      if (r1 && r1.ok) {
        return extOk({ ok: true, clicked_via: 'selector',
                       selector: item.selector, text: item.text });
      }

      // selector 失效 → 退到 text fallback
      if (fallback_text && item.text) {
        const r2 = await executeSiteFormAction(
          siteName, formClickByText, item.text,
          { tag: item.tag, timeoutMs: timeout_ms, nth: 0 },
        );
        if (r2 && r2.ok) {
          return extOk({ ok: true, clicked_via: 'text_fallback',
                         selector: item.selector, text: item.text,
                         matched_count: r2.matched_count });
        }
        throw new Error(`selector 和 text fallback 都失败: ${r1?.error}; ${r2?.error}`);
      }
      throw new Error(r1?.error || 'selector 失败且未启用 text fallback');
    },
  });

  // 4. click_by_text ──────────────────────────────────────────
  defineCommand({
    path: `${cmdSite}/click_by_text`,
    description: `按页面可见文本点击；多匹配时用 nth 选第几个。selector 漂移场景的稳定备选。`,
    handler: async ({ text, tag = '', exact = false, root_selector = 'body', timeout_ms = 5000, nth = 0 } = {}) => {
      if (!text) throw new Error('text 必填');
      const result = await executeSiteFormAction(
        siteName, formClickByText, text,
        { tag, exact, rootSelector: root_selector, timeoutMs: timeout_ms, nth },
      );
      return extOk(result || { ok: false, error: 'no result' });
    },
  });

  // 5. wait_for ───────────────────────────────────────────────
  defineCommand({
    path: `${cmdSite}/wait_for`,
    description: `等元素出现；用于 click 后等新页面 / 新弹窗渲染。`,
    handler: async ({ selector, timeout_ms = 10000 } = {}) => {
      if (!selector) throw new Error('selector 必填');
      const result = await executeSiteFormAction(
        siteName, formWaitForElement, selector, timeout_ms,
      );
      return extOk(result || { ok: false, error: 'no result' });
    },
  });

  // 6. navigate_to ────────────────────────────────────────────
  defineCommand({
    path: `${cmdSite}/navigate_to`,
    description: `导航 ${cmdSite} Worker Tab 到指定 URL（host 必须在 ${cmdSite} 域内，跨站拒绝）。可选 wait_for_selector 等导航后元素。`,
    handler: async ({ url, wait_for_selector = '', timeout_ms = 15000 } = {}) => {
      if (!url) throw new Error('url 必填');
      if (!_isAllowedUrl(siteName, url)) {
        throw new Error(`URL 跨站不允许: ${url}（${siteName} 站点限定 host 白名单）`);
      }
      let tab;
      try {
        tab = await _navigateQuietly(siteName, url, timeout_ms);
      } catch (e) {
        throw new Error(`导航失败: ${e}`);
      }
      // 可选：导航 + load 后等具体元素出现
      let waited = false;
      if (wait_for_selector) {
        const w = await executeSiteFormAction(
          siteName, formWaitForElement, wait_for_selector, timeout_ms,
        );
        waited = !!(w && w.ok && w.found);
      }
      return extOk({
        ok: true,
        tab_id: tab && tab.id,
        url,
        waited,
        wait_for_selector: wait_for_selector || null,
      });
    },
  });
}

// 注册 3 个站点
_registerDomCommandsFor('boss', 'zhipin');
_registerDomCommandsFor('linkedin', 'linkedin');
_registerDomCommandsFor('indeed', 'indeed');
