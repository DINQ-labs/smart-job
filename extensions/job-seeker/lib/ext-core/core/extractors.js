/**
 * Token 提取器引擎
 *
 * 从 API 响应中按照 produces.extractor 声明提取 token 数据。
 * 所有提取逻辑均为纯 JS（无 eval），符合 Chrome MV3 CSP 约束。
 *
 * 提取器类型（extractor.type）：
 *   'jsonpath'       - 单值提取：从响应中提取一个实体的 token
 *   'jsonpath_list'  - 列表提取：从响应数组中批量提取多个实体的 token
 *   'named:<fn>'     - 具名函数：调用预注册的具名提取器（复杂逻辑逃生口）
 *
 * 路径语法（支持 $.a.b.c 或 a.b.c，不支持通配符）：
 *   $.zpData.securityId
 *   zpData.jobList
 *
 * 使用方式：
 *   const items = runExtractor(cmdDef.produces.extractor, apiResult, body);
 *   // items: Array<{ entityKey: string, value: string, meta?: object }>
 */

// ── 具名提取器注册表（扩展代码预注册，供 named:<fn> 类型调用）────────────

const _namedExtractors = new Map();

/**
 * 注册具名提取器（用于无法用 jsonpath 描述的复杂提取逻辑）
 * @param {string} name
 * @param {(result: object, body: object) => Array<{entityKey, value, meta?}>} fn
 */
function registerExtractor(name, fn) {
  if (typeof fn !== 'function') throw new Error(`[extractors] registerExtractor "${name}": fn 必须是函数`);
  _namedExtractors.set(name, fn);
}

// ── 主提取函数 ────────────────────────────────────────────────────────────

/**
 * 执行提取器，返回提取到的 token 条目列表。
 *
 * @param {object} extractor - 提取器定义
 * @param {string} extractor.type - 'jsonpath' | 'jsonpath_list' | 'named:<fn>'
 * @param {object} result - API 响应对象
 * @param {object} body - 命令参数（用于模板变量）
 * @returns {Array<{ entityKey: string, value: string, meta?: object }>}
 */
function runExtractor(extractor, result, body) {
  if (!extractor || !extractor.type) return [];
  const { type } = extractor;

  // ── jsonpath: 单值提取 ─────────────────────────────────────────────────
  if (type === 'jsonpath') {
    // items: 定位到目标对象（如 $.zpData）
    const target = extractor.items ? _getByPath(result, extractor.items) : result;
    // entityKey: 实体标识符（支持模板变量或路径）
    const entityKey = _resolveExtractorExpr(extractor.entityKey, { body, result, item: target });
    // value: token 值路径
    const value = extractor.value ? _getByPath(target, extractor.value) : null;
    // meta: 附属字段
    const meta = _extractMeta(extractor.metaFields, target, body);
    return (entityKey && value) ? [{ entityKey, value: String(value), meta }] : [];
  }

  // ── jsonpath_list: 列表批量提取 ───────────────────────────────────────
  if (type === 'jsonpath_list') {
    const list = extractor.items ? _getByPath(result, extractor.items) : result;
    if (!Array.isArray(list)) {
      console.warn('[extractors] jsonpath_list: items 路径返回非数组', extractor.items, typeof list);
      return [];
    }
    return list.map(item => {
      // entityKey 优先从 item 中取，其次模板变量
      const entityKey = (extractor.entityKey && _getByPath(item, extractor.entityKey))
                     || _resolveExtractorExpr(extractor.entityKey, { body, result, item });
      const value = extractor.value ? _getByPath(item, extractor.value) : null;
      const meta = _extractMeta(extractor.metaFields, item, body);
      return { entityKey, value: value != null ? String(value) : null, meta };
    }).filter(e => e.entityKey && e.value);
  }

  // ── named: 具名提取器 ─────────────────────────────────────────────────
  if (type.startsWith('named:')) {
    const fnName = type.slice(6).trim();
    const fn = _namedExtractors.get(fnName);
    if (!fn) {
      console.warn(`[extractors] 具名提取器 "${fnName}" 未注册`);
      return [];
    }
    try {
      return fn(result, body) || [];
    } catch (e) {
      console.error(`[extractors] 具名提取器 "${fnName}" 执行异常:`, e.message);
      return [];
    }
  }

  console.warn('[extractors] 未知提取器类型:', type);
  return [];
}

// ── 辅助函数 ──────────────────────────────────────────────────────────────

/**
 * 按点路径取对象深层值。支持 $.a.b.c 和 a.b.c 两种格式。
 * @param {*} obj
 * @param {string} path - 如 '$.zpData.securityId' 或 'zpData.securityId'
 * @returns {*}
 */
function _getByPath(obj, path) {
  if (!path || obj == null) return undefined;
  const parts = path.replace(/^\$\.?/, '').split('.');
  let cur = obj;
  for (const p of parts) {
    if (cur == null) return undefined;
    cur = cur[p];
  }
  return cur;
}

/**
 * 解析提取器中的表达式：优先 JSON 路径取值，其次模板变量替换。
 */
function _resolveExtractorExpr(expr, ctx) {
  if (!expr) return null;
  // 如果是模板变量（含 {{）
  if (typeof expr === 'string' && expr.includes('{{')) {
    return expr.replace(/\{\{([^}]+)\}\}/g, (_, e) => {
      const parts = e.trim().split('.');
      let val = ctx;
      for (const p of parts) val = val?.[p];
      return val != null ? String(val) : '';
    }) || null;
  }
  // 否则当路径处理（从 item 中取）
  const val = _getByPath(ctx.item, expr);
  return val != null ? String(val) : null;
}

/**
 * 批量提取 meta 字段
 * @param {Record<string, string>} metaFields - { fieldName: 'path.to.value' }
 * @param {object} source - 数据源对象
 * @param {object} body - 命令参数（备用）
 * @returns {object}
 */
function _extractMeta(metaFields, source, body) {
  if (!metaFields) return {};
  const meta = {};
  for (const [k, path] of Object.entries(metaFields)) {
    const val = _getByPath(source, path);
    if (val != null) meta[k] = String(val);
  }
  return meta;
}

// ── 预注册内置具名提取器 ──────────────────────────────────────────────────

/**
 * 'named:jobListTokens' —— 从搜索结果批量提取 job list token
 * 与 commands/jobs.js 中 search_jobs 的逻辑等价，供动态命令复用。
 */
registerExtractor('jobListTokens', (result, body) => {
  const jobList = result?.zpData?.jobList || [];
  return jobList.map(job => ({
    entityKey: job.encryptJobId,
    value:     job.securityId,
    meta: {
      encryptBossId: job.encryptBossId || job.bossId || '',
      lid:           job.lid || '',
    },
  })).filter(e => e.entityKey && e.value);
});

/**
 * 'named:geekListTokens' —— 从候选人列表批量提取 geek search token
 */
registerExtractor('geekListTokens', (result, body) => {
  const list = result?.zpData?.geekList || result?.zpData?.geeks || [];
  return list.map(g => {
    const gc = g.geekCard || g;
    return {
      entityKey: gc.encryptGeekId || gc.encGeekId || '',
      value:     gc.securityId || '',
      meta: {
        encryptExpectId: gc.encryptExpectId || '',
        encryptJobId:    gc.encryptJobId || body.encrypt_job_id || '',
        lid:             gc.lid || '',
      },
    };
  }).filter(e => e.entityKey && e.value);
});
