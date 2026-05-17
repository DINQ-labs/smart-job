/**
 * chains/linkedin.js — LinkedIn 令牌链定义
 *
 * linkedin_people: 人才搜索 → 查看资料 → 发送联系
 * linkedin_jobs:   职位搜索 → 查看详情
 *
 * 依赖（全局）：defineChain（core/token-chain.js）
 */

defineChain({
  namespace: 'linkedin_people',
  entityKey: 'memberUrn',           // e.g. 'urn:li:fsd_profile:ACoAAA...'
  defaultTtl: 10 * 60 * 1000,       // 10 分钟
  stages: [
    { name: 'search',  field: 'trackingId' },                                // 搜索结果中的 trackingId
    { name: 'profile', field: 'profileId',      requires: 'search' },        // 访问过的 profileUrn
    { name: 'contact', field: 'conversationUrn', requires: 'profile' },      // 发过消息后的会话 URN
  ],
});

defineChain({
  namespace: 'linkedin_jobs',
  entityKey: 'jobId',               // 纯数字 LinkedIn job id (如 '4322939058')
  defaultTtl: 10 * 60 * 1000,
  stages: [
    { name: 'search', field: 'searchToken' },
    { name: 'detail', field: 'detailToken', requires: 'search' },
  ],
});
