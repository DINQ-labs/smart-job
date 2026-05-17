/**
 * content/dinq_probe.js — world: ISOLATED
 *
 * 探测握手通道：让 DINQ 前端（*.dinq.me / 本地开发）能知道扩展装没装、版本几何。
 *
 * 协议（page ↔ content script 走 window.postMessage，都带 __dinqProbe: true 防混淆）：
 *   1. 挂载时主动宣告：
 *        { __dinqProbe: true, type: 'EXT_VERSION', version, manifestVersion, ts }
 *   2. 前端可随时重探：
 *        { __dinqProbe: true, type: 'PROBE_VERSION' }     (page → content)
 *        → 我方立即回一次上述 EXT_VERSION 消息
 *   3. 扩展升级后,**旧** content script 的 chrome.runtime 进入 orphan 状态
 *      (chrome.runtime.id === undefined)。此时我们已经无法拿到最新 manifest,
 *      改发 EXT_ORPHAN 让页面提示用户刷新本页激活新版 content script:
 *        { __dinqProbe: true, type: 'EXT_ORPHAN', staleVersion, ts }
 *
 * 仅 postMessage，不读 DOM / 不读 cookies；权限最小。
 */
(function () {
  'use strict';

  // 启动时缓存一次,作为 fallback
  const initialManifest = chrome.runtime.getManifest();
  const initialName = initialManifest.name || 'boss-api-ext';
  const initialVersion = initialManifest.version || 'unknown';
  const initialManifestVersion = initialManifest.manifest_version || 3;

  function isOrphan() {
    // chrome.runtime 在扩展升级后会被 invalidate;chrome.runtime.id 变成 undefined。
    // 我们用这个判断当前 content script 是否是"旧的孤儿"。
    try {
      return !chrome.runtime || !chrome.runtime.id;
    } catch (_) {
      return true;
    }
  }

  function readFreshManifest() {
    // 即使在 orphan 状态,getManifest 在某些 Chrome 版本仍可调,只是返回旧值。
    // 调用方负责在 orphan 时跳过此值。
    try {
      return chrome.runtime.getManifest();
    } catch (_) {
      return null;
    }
  }

  function announce() {
    try {
      if (isOrphan()) {
        // 旧 content script 处于孤儿状态 —— 告诉页面"我读不到新版,请用户刷新本页"
        window.postMessage({
          __dinqProbe: true,
          type: 'EXT_ORPHAN',
          staleVersion: initialVersion,
          ts: Date.now(),
        }, location.origin);
        return;
      }
      // 每次 announce 重读 manifest,确保升级后(无 orphan 时)能拿到最新版本
      const fresh = readFreshManifest();
      const pkg = {
        __dinqProbe: true,
        type: 'EXT_VERSION',
        version: (fresh && fresh.version) || initialVersion,
        manifestVersion: (fresh && fresh.manifest_version) || initialManifestVersion,
        name: (fresh && fresh.name) || initialName,
        ts: Date.now(),
      };
      window.postMessage(pkg, location.origin);
    } catch (e) {
      // postMessage 在极罕见环境可能抛（iframe sandbox 等），忽略
    }
  }

  // 1. 主动宣告一次（content script 已经是 document_idle，前端监听器很可能已就绪）
  announce();

  // 2. 监听前端的重探请求
  window.addEventListener('message', (event) => {
    // 同 window 自发自收的 announce 也会进来，过滤掉
    if (event.source !== window) return;
    const d = event.data;
    if (!d || typeof d !== 'object') return;
    if (d.__dinqProbe !== true) return;
    if (d.type !== 'PROBE_VERSION') return;
    announce();
  });
})();
