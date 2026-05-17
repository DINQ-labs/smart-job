/**
 * 内置 Token 链定义
 *
 * 将 boss-api-ext 硬编码的 jobs / geeks token 链用声明式 DAG 描述。
 * 依赖：core/token-chain.js（defineChain）
 *
 * 链结构：
 *
 *   jobs 链（求职者视角）：
 *     list → detail → chat
 *                  ↘ (未来可扩展其他分支，如 match, verify)
 *
 *   geeks 链（招聘方视角）：
 *     search → detail → chat
 */

// ── 求职者端：job token 链 ────────────────────────────────────────────────

defineChain({
  namespace: 'jobs',
  entityKey: 'encryptJobId',          // 所有 job 相关命令以此为实体 key
  defaultTtl: 5 * 60 * 1000,          // 5 分钟（与现有 TOKEN_TTL_MS 一致）

  stages: [
    {
      name: 'list',
      field: 'listSecurityId',         // 来源：search_jobs → zpData.jobList[].securityId
      // 无 requires：链的起点
    },
    {
      name: 'detail',
      field: 'detailSecurityId',       // 来源：get_job_detail → zpData.securityId
      requires: 'list',               // 必须先有 list 阶段 token
    },
    {
      name: 'chat',
      field: 'chatSecurityId',         // 来源：start_chat(friend/add) → zpData.securityId
      requires: 'detail',             // 必须先有 detail 阶段 token
      ttl: 30 * 60 * 1000,            // 聊天 session 更持久，30 分钟
    },
  ],
});

// ── 招聘方端：geek token 链 ───────────────────────────────────────────────

defineChain({
  namespace: 'geeks',
  entityKey: 'encryptGeekId',         // 所有 geek 相关命令以此为实体 key
  defaultTtl: 5 * 60 * 1000,

  stages: [
    {
      name: 'search',
      field: 'securityId',            // 来源：search_candidates → geekCard.securityId
    },
    {
      name: 'detail',
      field: 'detailSecurityId',      // 来源：get_candidate_detail → zpData.securityId
      requires: 'search',
    },
    {
      name: 'contact',
      field: 'chatSecurityId',        // 来源：contact_candidate(bossEnter) 后续
      requires: 'detail',
      ttl: 30 * 60 * 1000,
    },
  ],
});
