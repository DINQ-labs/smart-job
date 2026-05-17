/**
 * 动态命令请求构建器
 *
 * 将 JSON 命令定义中的 requestBuilder 模板转换为实际 API 请求，
 * 通过 executeBossApi 在 Worker Tab 中执行。
 *
 * 模板变量（支持 {{...}} 语法）：
 *   {{tokens.jobs.detail}}        jobs 链 detail 阶段 token 值
 *   {{tokens.geeks.search}}       geeks 链 search 阶段 token 值
 *   {{meta.jobs.encryptBossId}}   token 附属 meta 字段
 *   {{body.encryptJobId}}         命令参数
 *   {{timestamp}}                 Date.now()
 *   {{session.userId}}            当前用户 ID
 *
 * 依赖（全局）：tokenStore, executeBossApi（executor.js）
 */

/**
 * 为动态命令定义（JSON）生成可执行的 handler 函数。
 * Phase 5 实现：完整的 requestBuilder + extractors 支持。
 *
 * @param {object} cmdDef - 动态命令定义
 * @param {string} cmdDef.path - 命令路径
 * @param {object} [cmdDef.requestBuilder] - 请求模板
 * @param {object} [cmdDef.requires] - token 依赖声明（Phase 3 后完整支持）
 * @param {object} [cmdDef.produces] - token 产出声明（Phase 3 后完整支持）
 * @returns {Function|null} async handler(body) 或 null（定义不合法）
 */
function buildDynamicHandler(cmdDef) {
  // 若无 requestBuilder，暂时无法自动生成 handler（复杂命令需要代码）
  if (!cmdDef.requestBuilder) {
    console.warn(`[request-builder] "${cmdDef.path}" 无 requestBuilder，无法生成动态 handler`);
    return null;
  }

  return async function dynamicHandler(body = {}) {
    const rb = cmdDef.requestBuilder;

    // 1. 验证必填参数
    for (const param of (cmdDef.params || [])) {
      if (param.required && (body[param.name] == null || body[param.name] === '')) {
        throw new Error(`参数 ${param.name} 必填`);
      }
    }

    // 2. 构建模板上下文（token 解析）
    const ctx = _buildContext(body, cmdDef.requires);

    // 3. 渲染请求 URL
    const rawUrl = _resolveTemplate(rb.url || '', ctx);
    if (!rawUrl) throw new Error(`[dynamic:${cmdDef.path}] requestBuilder.url 不能为空`);

    // 4. 构建请求 body
    let requestBody = null;
    const method = (rb.method || 'GET').toUpperCase();

    if (rb.body && method !== 'GET') {
      const resolvedBody = _resolveTemplateObj(rb.body, ctx);
      if (rb.contentType === 'form' || rb.contentType === 'application/x-www-form-urlencoded') {
        requestBody = new URLSearchParams(resolvedBody).toString();
      } else {
        requestBody = JSON.stringify(resolvedBody);
      }
    }

    // 5. 执行请求（Worker Tab MAIN world）
    const headers = {};
    if (rb.contentType === 'form') {
      headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8';
    } else if (rb.body && method !== 'GET') {
      headers['Content-Type'] = 'application/json; charset=UTF-8';
    }

    const raw = await executeBossApi(rawUrl, {
      method,
      headers: Object.keys(headers).length ? headers : undefined,
      body: requestBody,
    });

    // 6. 提取并存储产出 token（uses extractors.js）
    if (cmdDef.produces) {
      await _storeExtractedTokens(cmdDef.produces, raw, body);
    }

    return extOk({ raw });
  };
}

// ── 模板引擎 ──────────────────────────────────────────────────────────────

/**
 * 构建模板上下文：{ body, tokens, meta, session, timestamp }
 */
function _buildContext(body, requires) {
  const tokens = {};
  const meta = {};

  if (requires) {
    const requiresList = Array.isArray(requires) ? requires : [requires];
    for (const req of requiresList) {
      const { chain: ns, stage, keyParam } = req;
      const entityKey = body[keyParam];
      if (!entityKey) continue;

      const chain = getChain(ns);
      if (!chain) continue;
      const stageConfig = chain._stageMap[stage];
      if (!stageConfig) continue;

      const entry = tokenStore.getChainToken(ns, entityKey, stageConfig.field);
      if (entry) {
        tokens[`${ns}.${stage}`] = entry.value;
        if (entry.meta) {
          for (const [k, v] of Object.entries(entry.meta)) {
            meta[`${ns}.${k}`] = v;
          }
        }
      }
    }
  }

  return {
    body,
    tokens,
    meta,
    session: tokenStore.session,
    timestamp: String(Date.now()),
  };
}

/**
 * 替换模板字符串中的 {{变量路径}} 表达式。
 * 支持嵌套路径：{{tokens.jobs.detail}} → ctx.tokens['jobs.detail']
 */
function _resolveTemplate(tpl, ctx) {
  if (typeof tpl !== 'string') return tpl;
  return tpl.replace(/\{\{([^}]+)\}\}/g, (_, expr) => {
    const parts = expr.trim().split('.');
    const root = parts[0];
    const rest = parts.slice(1).join('.');

    // 特殊路径处理
    if (root === 'tokens' && rest) return ctx.tokens[rest] || '';
    if (root === 'meta' && rest) return ctx.meta[rest] || '';
    if (root === 'session' && rest) return ctx.session[parts[1]] || '';
    if (root === 'body' && rest) return body_val(ctx.body, parts.slice(1)) || '';
    if (root === 'timestamp') return ctx.timestamp;

    // 通用深度路径
    let val = ctx;
    for (const p of parts) val = val?.[p];
    return val != null ? String(val) : '';
  });
}

function body_val(body, parts) {
  let v = body;
  for (const p of parts) v = v?.[p];
  return v != null ? String(v) : '';
}

/**
 * 递归替换对象中所有字符串值的模板变量
 */
function _resolveTemplateObj(obj, ctx) {
  if (typeof obj === 'string') return _resolveTemplate(obj, ctx);
  if (Array.isArray(obj)) return obj.map(v => _resolveTemplateObj(v, ctx));
  if (obj && typeof obj === 'object') {
    const result = {};
    for (const [k, v] of Object.entries(obj)) {
      result[k] = _resolveTemplateObj(v, ctx);
    }
    return result;
  }
  return obj;
}

// ── Token 存储（动态命令产出） ────────────────────────────────────────────

/**
 * 从 API 响应中提取 token 并存入 TokenStore
 * @param {object} produces - { chain, stage, extractor }
 * @param {object} result - API 响应
 * @param {object} body - 命令参数
 */
async function _storeExtractedTokens(produces, result, body) {
  const { chain: ns, stage, extractor } = produces;
  if (!ns || !stage || !extractor) return;

  const chain = getChain(ns);
  if (!chain) return;
  const stageConfig = chain._stageMap[stage];
  if (!stageConfig) return;

  const extracted = runExtractor(extractor, result, body);
  let changed = false;

  for (const { entityKey, value, meta } of extracted) {
    if (entityKey && value) {
      tokenStore.setChainToken(ns, entityKey, stageConfig.field, value, meta || {});
      changed = true;
    }
  }

  if (changed) await tokenStore._save();
}
