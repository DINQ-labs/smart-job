/**
 * Indeed 雇主端 Token Chain — 职位→候选人搜索依赖链。
 *
 * - jobs / candidates:老 REST API(employer_list_jobs / employer_search_candidates)产生
 *   的 employer_job_id 链路 token。
 * - sourcing_search / sourcing_session:Smart Sourcing GraphQL
 *   (SmartSourcingResultsBFF)的会话级 token,贯穿一次简历库搜索 + 后续
 *   LogRcpCandidateSeen 反作弊埋点 + TalentEngagementSummary 候选人状态查询。
 *   两段不依赖 employerJobId(简历库 sourcing 不绑定具体岗位),独立 stage 管理。
 */
defineChain({
  namespace: 'indeed_employer',
  entityKey: 'employerJobId',
  defaultTtl: 60 * 60 * 1000, // 1 hour
  stages: [
    { name: 'jobs',       field: 'jobListToken' },
    { name: 'candidates', field: 'candidateListToken', requires: 'jobs' },
    // Smart Sourcing 简历库搜索独立链(不绑定 employerJobId,以最近一次搜索为准)
    { name: 'sourcing_search',  field: 'rcpRequestId',    ttl: 30 * 60 * 1000 },   // 30 min:单次搜索的 RCP request id
    { name: 'sourcing_session', field: 'searchSessionId', ttl: 60 * 60 * 1000 },   // 1 hour:简历库浏览会话 id
  ],
});
