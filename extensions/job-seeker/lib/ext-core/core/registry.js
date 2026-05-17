/**
 * 命令注册表
 *
 * 所有命令通过 defineCommand() 注册到全局 Map。
 * 静态命令（扩展代码定义）优先级高，云端动态命令不能覆盖静态命令（安全保护）。
 *
 * 使用方式（在 commands/*.js 中）：
 *   defineCommand({
 *     path: 'boss/search_jobs',
 *     description: '搜索职位',
 *     handler: async (body) => { ... return extOk({...}); }
 *   });
 */

const _commandRegistry = new Map();
const _staticPaths = new Set();

/**
 * 注册静态命令（扩展代码内置，受安全保护）
 * @param {{
 *   path: string,
 *   handler: Function,
 *   description?: string,
 *   requires?: object|Array<object>,  // { chain, stage, keyParam }
 *   produces?: object,                // { chain, stage, extractor? }
 *   enforceRequires?: boolean,        // true → dispatch 前置校验 token 是否就绪
 * }} def
 */
function defineCommand(def) {
  if (!def || !def.path) throw new Error('[registry] defineCommand: path 必填');
  if (!def.handler || typeof def.handler !== 'function') {
    throw new Error(`[registry] defineCommand "${def.path}": handler 必须是函数`);
  }
  _commandRegistry.set(def.path, { ...def, _static: true });
  _staticPaths.add(def.path);

  // 自动登记 produces → producer 映射，供 resolveRequires 生成 run_first 提示
  if (def.produces && def.produces.chain && def.produces.stage
      && typeof registerChainProducer === 'function') {
    try { registerChainProducer(def.produces.chain, def.produces.stage, def.path); }
    catch (_) {}
  }
}

/**
 * 注册动态命令（来自云端 config_update，由 core/config-sync.js 调用）
 * 如果同路径的静态命令已存在，注册被拒绝并返回 false。
 * @param {object} def
 * @returns {boolean} 是否注册成功
 */
function registerDynamicCommand(def) {
  if (!def || !def.path) return false;
  if (_staticPaths.has(def.path)) {
    console.warn(`[registry] 动态命令 "${def.path}" 被拒绝：静态命令不可覆盖（安全保护）`);
    return false;
  }
  _commandRegistry.set(def.path, { ...def, _dynamic: true });

  if (def.produces && def.produces.chain && def.produces.stage
      && typeof registerChainProducer === 'function') {
    try { registerChainProducer(def.produces.chain, def.produces.stage, def.path); }
    catch (_) {}
  }
  return true;
}

/**
 * 获取命令定义
 * @param {string} path
 * @returns {object|null}
 */
function getCommand(path) {
  return _commandRegistry.get(path) || null;
}

/**
 * 清除所有动态命令（重新加载云端配置前调用）
 */
function clearDynamicCommands() {
  for (const [path, cmd] of _commandRegistry) {
    if (cmd._dynamic) _commandRegistry.delete(path);
  }
}

/**
 * 从 handler 函数签名中提取参数名及默认值，生成参数模板对象。
 * 支持: async ({ a, b = 1, c = '', d = {} } = {}) => {}
 */
function _extractParams(handler) {
  try {
    const src = handler.toString().slice(0, 600);
    const start = src.indexOf('({');
    if (start === -1) return {};
    // 找到 { 对应的 }（计深度，忽略嵌套）
    let depth = 0, paramEnd = -1;
    for (let i = start + 1; i < src.length; i++) {
      if (src[i] === '{' || src[i] === '[') depth++;
      else if (src[i] === '}' || src[i] === ']') { depth--; if (depth === 0) { paramEnd = i; break; } }
    }
    if (paramEnd === -1) return {};
    const inner = src.slice(start + 2, paramEnd);
    const result = {};
    // 按逗号分割（跳过嵌套括号内的逗号）
    let cur = '', d = 0;
    for (const ch of inner + ',') {
      if (ch === '{' || ch === '[') d++;
      else if (ch === '}' || ch === ']') d--;
      else if (ch === ',' && d === 0) {
        const part = cur.trim();
        cur = '';
        if (!part) continue;
        const eq = part.indexOf('=');
        if (eq === -1) {
          if (part) result[part] = '';
        } else {
          const key = part.slice(0, eq).trim();
          const val = part.slice(eq + 1).trim();
          if (!key) continue;
          if (val === '{}') result[key] = {};
          else if (val === '[]') result[key] = [];
          else if (val === "''" || val === '""') result[key] = '';
          else { try { result[key] = JSON.parse(val); } catch (_) { result[key] = val.replace(/^['"`]|['"`]$/g, ''); } }
        }
        continue;
      }
      cur += ch;
    }
    return result;
  } catch (_) { return {}; }
}

/**
 * 列出所有已注册命令摘要（含参数模板）
 * @returns {Array<{path, description, type, params}>}
 */
function listCommands() {
  return [..._commandRegistry.values()].map(c => ({
    path: c.path,
    description: c.description || '',
    type: c._dynamic ? 'dynamic' : 'static',
    params: _extractParams(c.handler),
    requires: c.requires || null,
    produces: c.produces || null,
  }));
}
