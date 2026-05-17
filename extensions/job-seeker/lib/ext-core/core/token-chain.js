/**
 * Token 链 DAG 引擎
 *
 * 将 token 链从硬编码的三级线性结构（list→detail→chat）抽象为
 * 可声明的有向无环图（DAG）。支持分支阶段和多实体类型。
 *
 * Phase 1：仅包含 schema 定义（defineChain/getChain）。
 * Phase 3：补充运行时 resolveRequires / storeProduces（需 TokenStore 泛化支持）。
 *
 * 使用方式（在 chains/builtin.js 中）：
 *   defineChain({
 *     namespace: 'jobs',
 *     entityKey: 'encryptJobId',
 *     defaultTtl: 300000,
 *     stages: [
 *       { name: 'list',   field: 'listSecurityId' },
 *       { name: 'detail', field: 'detailSecurityId', requires: 'list' },
 *       { name: 'chat',   field: 'chatSecurityId',   requires: 'detail', ttl: 1800000 },
 *     ],
 *   });
 */

const _chainRegistry = new Map();

/**
 * 定义 Token 链 schema
 *
 * @param {{
 *   namespace: string,          // TokenStore 命名空间（如 'jobs', 'geeks'）
 *   entityKey: string,          // 标识实体的参数名（如 'encryptJobId'）
 *   defaultTtl?: number,        // 默认过期时间 ms，默认 300000（5 分钟）
 *   stages: Array<{
 *     name: string,             // 阶段名（如 'list', 'detail', 'chat'）
 *     field: string,            // TokenStore 里的存储字段名
 *     requires?: string,        // 依赖的上游阶段名（DAG 有向边）
 *     ttl?: number,             // 该阶段专属 TTL，覆盖 defaultTtl
 *   }>
 * }} def
 */
function defineChain(def) {
  if (!def || !def.namespace) throw new Error('[token-chain] defineChain: namespace 必填');
  if (!Array.isArray(def.stages) || def.stages.length === 0) {
    throw new Error(`[token-chain] defineChain "${def.namespace}": stages 不能为空`);
  }

  // 构建 stageMap（name → stageConfig）用于 O(1) 查找
  const stageMap = {};
  for (const stage of def.stages) {
    stageMap[stage.name] = stage;
  }

  // _producerMap: stage → 产生该 stage token 的命令 path（由 defineCommand 在 commands/*.js 中填入）
  // Phase 2 填充，用于在 TokenMissingError 中给出 run_first 提示
  const producerMap = {};

  _chainRegistry.set(def.namespace, {
    ...def,
    _stageMap: stageMap,
    _producerMap: producerMap,
  });
}

/**
 * 获取链定义
 * @param {string} namespace
 * @returns {object|null}
 */
function getChain(namespace) {
  return _chainRegistry.get(namespace) || null;
}

/**
 * 注册命令与链阶段的对应关系（Phase 2 调用，用于错误提示）
 * @param {string} namespace
 * @param {string} stageName
 * @param {string} commandPath - 产生该阶段 token 的命令 path
 */
function registerChainProducer(namespace, stageName, commandPath) {
  const chain = _chainRegistry.get(namespace);
  if (chain) chain._producerMap[stageName] = commandPath;
}

/**
 * 获取某链某阶段的 TTL（优先取阶段专属 TTL，其次取链默认 TTL）
 * @param {string} namespace
 * @param {string} stageName
 * @returns {number} ms
 */
function getChainStageTtl(namespace, stageName) {
  const chain = _chainRegistry.get(namespace);
  if (!chain) return 300000;
  const stage = chain._stageMap[stageName];
  return (stage && stage.ttl) || chain.defaultTtl || 300000;
}

/**
 * 列出所有已定义链的摘要（供调试/admin 使用）
 */
function listChains() {
  return [..._chainRegistry.values()].map(c => ({
    namespace: c.namespace,
    entityKey: c.entityKey,
    defaultTtl: c.defaultTtl || 300000,
    stages: c.stages.map(s => ({
      name: s.name,
      field: s.field,
      requires: s.requires || null,
      ttl: s.ttl || null,
    })),
  }));
}

// ── Phase 3：运行时 requires 解析 ──────────────────────────────────────────

/**
 * 解析命令的 requires 声明，从 tokenStore 取出所需 token 并报告缺失。
 *
 * 用于 dispatch 前置校验：若任何 required stage 的 entityKey 或 token 缺失，
 * 返回结构化错误对象供上游统一处理（而非各 handler 自定义 throw 字符串）。
 *
 * @param {object|Array<object>} requires
 *   单条或多条 `{ chain, stage, keyParam }` 声明
 * @param {object} body
 *   命令参数，从中按 `keyParam` 取 entityKey
 * @param {object} tokenStoreRef
 *   TokenStore 实例（供测试注入；运行时默认使用全局 tokenStore）
 * @returns {{
 *   ok: boolean,
 *   tokens?: object,   // `${ns}.${stage}` → 已解析 token value
 *   meta?: object,
 *   missing?: { chain, stage, keyParam, entityKey, run_first, reason },
 * }}
 */
function resolveRequires(requires, body = {}, tokenStoreRef = null) {
  if (!requires) return { ok: true, tokens: {}, meta: {} };
  const list = Array.isArray(requires) ? requires : [requires];
  const store = tokenStoreRef || (typeof tokenStore !== 'undefined' ? tokenStore : null);
  const tokens = {};
  const meta = {};

  for (const req of list) {
    if (!req || !req.chain || !req.stage) continue;
    const { chain: ns, stage, keyParam } = req;

    const chainDef = _chainRegistry.get(ns);
    if (!chainDef) {
      return {
        ok: false,
        missing: {
          chain: ns, stage, keyParam, entityKey: '',
          run_first: null,
          reason: `chain "${ns}" 未定义`,
        },
      };
    }
    const stageConfig = chainDef._stageMap[stage];
    if (!stageConfig) {
      return {
        ok: false,
        missing: {
          chain: ns, stage, keyParam, entityKey: '',
          run_first: null,
          reason: `stage "${ns}.${stage}" 未定义`,
        },
      };
    }

    const entityKey = keyParam ? body[keyParam] : '';
    if (keyParam && !entityKey) {
      return {
        ok: false,
        missing: {
          chain: ns, stage, keyParam, entityKey: '',
          run_first: chainDef._producerMap[stage] || null,
          reason: `缺少参数 "${keyParam}"`,
        },
      };
    }

    if (!store || typeof store.getChainToken !== 'function') {
      return {
        ok: false,
        missing: {
          chain: ns, stage, keyParam, entityKey,
          run_first: null,
          reason: 'tokenStore 不可用',
        },
      };
    }

    const entry = store.getChainToken(ns, entityKey, stageConfig.field);
    if (!entry || entry.value == null) {
      return {
        ok: false,
        missing: {
          chain: ns, stage, keyParam, entityKey,
          run_first: chainDef._producerMap[stage] || null,
          reason: `${ns}.${stage} token 缺失或已过期`,
        },
      };
    }

    tokens[`${ns}.${stage}`] = entry.value;
    if (entry.meta) {
      for (const [k, v] of Object.entries(entry.meta)) {
        meta[`${ns}.${k}`] = v;
      }
    }
  }

  return { ok: true, tokens, meta };
}

/**
 * 从 resolveRequires 返回的 missing 生成标准响应体，供 dispatch 在 extOk 的
 * `data` 字段直接返回（保持 WS 协议层的 `ok: true` 语义，内层 `error` 字段
 * 向 Python 网关表达"handler 未执行因前置 token 缺失"）。
 */
function formatTokenMissing(missing) {
  const { chain, stage, keyParam, entityKey, run_first, reason } = missing;
  const hint = run_first
    ? `请先调用 ${run_first}`
    : `请先触发产出 ${chain}.${stage} 的前置命令`;
  return {
    error: 'TOKEN_MISSING',
    code: 'TOKEN_MISSING',
    chain,
    stage,
    keyParam: keyParam || null,
    entityKey: entityKey || '',
    run_first: run_first || null,
    message: `${chain}.${stage} 未就绪（${reason}）。${hint}`,
  };
}

/**
 * 将命令产出的 token 按 produces 声明存入 tokenStore。
 * Phase 3 占位 —— 静态命令自行在 handler 里 storeChainToken。
 * 动态命令（云端 config）由 request-builder._storeExtractedTokens 处理。
 * @param {object} produces
 * @param {object} result
 * @param {object} body
 */
async function storeProduces(produces, result, body) {
  // 静态/动态两条路径都已自管存储，此函数保留为未来统一接入点。
}
