/**
 * chains/indeed.js — Indeed 令牌链定义
 *
 * indeed_jobs: 职位搜索 → 查看详情
 *
 * 依赖（全局）：defineChain（core/token-chain.js）
 */

defineChain({
  namespace: 'indeed_jobs',
  entityKey: 'jobKey',              // e.g. '3f4a1b2c3d4e5f6a'
  defaultTtl: 30 * 60 * 1000,       // 30 分钟（Indeed 链接有效期较长）
  stages: [
    { name: 'search', field: 'searchToken' },
    { name: 'detail', field: 'viewToken',   requires: 'search' },
  ],
});
