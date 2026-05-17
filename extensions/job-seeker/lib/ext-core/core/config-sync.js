/**
 * 云端配置同步
 *
 * 监听来自 boss-api-gateway 的 config_update WebSocket 消息，
 * 将动态命令定义和链定义持久化到 chrome.storage.local，
 * 并重新加载命令注册表。
 *
 * 推送协议（gateway → extension）：
 * {
 *   "type": "config_update",
 *   "version": "2024-03-26-v2",
 *   "chains": {           // 可选，动态链定义（不能覆盖 builtin.js 中的链）
 *     "companies": { namespace, entityKey, stages: [...] }
 *   },
 *   "dynamic_commands": [ // 可选，动态命令定义（不能覆盖 commands/*.js 中的静态命令）
 *     { path, description, requestBuilder, produces, requires, params }
 *   ]
 * }
 *
 * 安全约束（MV3 CSP）：
 *   - 动态命令不能覆盖静态命令（registry.js 安全保护）
 *   - 动态命令的 handler 通过 requestBuilder + extractors 生成，不执行任意 JS 字符串
 *   - 动态链定义不能覆盖内置链（防止修改 jobs/geeks 链语义）
 *
 * 依赖（全局）：
 *   clearDynamicCommands, registerDynamicCommand（core/registry.js）
 *   defineChain, getChain（core/token-chain.js）
 *   buildDynamicHandler（core/request-builder.js，Phase 5 激活）
 */

const CONFIG_STORAGE_KEY = 'boss_ext_dynamic_config';

// 内置链命名空间（不可被动态链覆盖）
const BUILTIN_CHAIN_NAMESPACES = new Set([
  'jobs', 'geeks',                     // zhipin
  'linkedin_people', 'linkedin_jobs',  // LinkedIn
  'indeed_jobs',                       // Indeed
]);

/**
 * 处理来自 gateway 的 config_update 消息。
 * 由 background.js 的 handleGatewayMessage 调用。
 *
 * @param {object} msg - { type:'config_update', version, chains?, dynamic_commands? }
 * @returns {{ accepted: boolean, version: string, registered: number, skipped: number }}
 */
async function handleConfigUpdate(msg) {
  const { version, chains = {}, dynamic_commands = [] } = msg;

  if (!version) {
    console.warn('[config-sync] config_update 缺少 version，忽略');
    return { accepted: false, reason: 'missing version' };
  }

  // 检查版本是否更新（避免重复加载）
  const stored = await chrome.storage.local.get(CONFIG_STORAGE_KEY);
  const current = stored[CONFIG_STORAGE_KEY] || {};
  if (current.version === version) {
    console.log('[config-sync] 版本未变化，跳过:', version);
    return { accepted: false, reason: 'same version', version };
  }

  console.log('[config-sync] 接收配置更新:', version,
    `| 链: ${Object.keys(chains).length}`,
    `| 命令: ${dynamic_commands.length}`);

  // ── 1. 更新动态链定义 ──────────────────────────────────────────────────

  let chainsRegistered = 0;
  for (const [ns, chainDef] of Object.entries(chains)) {
    if (BUILTIN_CHAIN_NAMESPACES.has(ns)) {
      console.warn(`[config-sync] 动态链 "${ns}" 被拒绝：内置链不可覆盖（安全保护）`);
      continue;
    }
    if (!chainDef.namespace) chainDef.namespace = ns;
    try {
      defineChain(chainDef);
      chainsRegistered++;
      console.log(`[config-sync] 动态链已注册: ${ns}（${chainDef.stages?.length || 0} 个阶段）`);
    } catch (e) {
      console.warn(`[config-sync] 动态链 "${ns}" 注册失败:`, e.message);
    }
  }

  // ── 2. 清除旧动态命令，注册新动态命令 ────────────────────────────────

  clearDynamicCommands();

  let registered = 0;
  let skipped = 0;

  for (const cmdDef of dynamic_commands) {
    if (!cmdDef.path) {
      skipped++;
      continue;
    }

    // 为动态命令生成 handler（Phase 5：requestBuilder + extractors 激活后真正生效）
    const handler = buildDynamicHandler(cmdDef);
    if (!handler) {
      console.warn(`[config-sync] 动态命令 "${cmdDef.path}" handler 生成失败，跳过`);
      skipped++;
      continue;
    }

    const ok = registerDynamicCommand({ ...cmdDef, handler });
    if (ok) {
      registered++;
    } else {
      skipped++; // 被静态命令保护拒绝
    }
  }

  // ── 3. 持久化到 chrome.storage ──────────────────────────────────────

  await chrome.storage.local.set({
    [CONFIG_STORAGE_KEY]: {
      version,
      chains,
      dynamic_commands,
      updatedAt: Date.now(),
    },
  });

  console.log(`[config-sync] 完成: ${registered} 个命令已注册，${skipped} 个跳过，${chainsRegistered} 个链已更新`);

  return { accepted: true, version, registered, skipped, chains_registered: chainsRegistered };
}

/**
 * 从 chrome.storage 恢复上次推送的动态配置（SW 重启时调用）。
 * 在 tokenStore.load() 之后、connect() 之前调用。
 */
async function restoreDynamicConfig() {
  try {
    const stored = await chrome.storage.local.get(CONFIG_STORAGE_KEY);
    const config = stored[CONFIG_STORAGE_KEY];
    if (!config || !config.version) return;

    console.log('[config-sync] 恢复动态配置:', config.version,
      `| 命令: ${config.dynamic_commands?.length || 0}`);

    // 恢复动态链
    for (const [ns, chainDef] of Object.entries(config.chains || {})) {
      if (BUILTIN_CHAIN_NAMESPACES.has(ns)) continue;
      if (!chainDef.namespace) chainDef.namespace = ns;
      try { defineChain(chainDef); } catch (_) {}
    }

    // 恢复动态命令
    for (const cmdDef of (config.dynamic_commands || [])) {
      if (!cmdDef.path) continue;
      const handler = buildDynamicHandler(cmdDef);
      if (handler) registerDynamicCommand({ ...cmdDef, handler });
    }
  } catch (e) {
    console.warn('[config-sync] 恢复动态配置失败:', e.message);
  }
}

/**
 * 获取当前动态配置版本（供 gateway 判断是否需要推送）
 */
async function getDynamicConfigVersion() {
  const stored = await chrome.storage.local.get(CONFIG_STORAGE_KEY);
  return stored[CONFIG_STORAGE_KEY]?.version || null;
}
