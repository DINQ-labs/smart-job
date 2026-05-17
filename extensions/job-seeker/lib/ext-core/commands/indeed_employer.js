/**
 * Indeed 雇主端命令 — 简历搜索与下载。
 *
 * 依赖:
 *   indeed/employer-api.js  (apiIndeedEmployer*)
 *   core/registry.js        (defineCommand)
 *   core/token-chain.js     (tokenStore)
 */

// ── 登录检测 ────────────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/check_login',
  description: '检查 Indeed 雇主账号登录状态,提取并缓存认证 token(三层判定:DOM + GraphQL probe + tab URL 启发式)',
  handler: async () => {
    let tokens = { csrf: '', apiKey: '', ctk: '', employerKey: '', workerTabUrl: '' };
    let domError = '';
    try {
      tokens = await indeedEmployerExtractTokens();
    } catch (e) {
      domError = e.message;
    }
    let loggedIn = !!(tokens.csrf || tokens.apiKey);
    let detectionMethod = loggedIn ? 'dom' : '';

    // 第 2 层:GraphQL 探针。cookie 调通即证明登录态有效。
    // 这能解决"用户已稳定登录但 __NEXT_DATA__ 还没渲染好"的时序误判。
    let probe = null;
    if (!loggedIn) {
      try {
        probe = await apiIndeedEmployerProbeAuth();
        if (probe.logged_in) {
          loggedIn = true;
          detectionMethod = 'graphql_probe';
          // 探针返回 employer_key 时,补到 tokens 里供下游 chain 使用
          if (probe.employer_key && !tokens.employerKey) {
            tokens.employerKey = probe.employer_key;
          }
        }
      } catch (e) {
        // 探针失败不影响主流程,留空 detection_method='failed'
        probe = { logged_in: false, error: e.message };
      }
    }

    // 第 3 层:tab URL 启发式。Indeed server-side 会把未登录用户重定向到
    // secure.indeed.com/account/login —— 反之用户能稳定停在
    // employers.indeed.com/<authed-path> 就是登录态。
    // 这是最后的兜底:DOM 抓不到 + GraphQL 401 时,只要看到用户的 tab 在
    // employer 已登录页面,就认定 logged_in=true。
    let tabHeuristicUrl = '';
    if (!loggedIn) {
      try {
        const userTab = await _findIndeedEmployerTab();
        if (userTab && userTab.url && _isAuthenticatedEmployerUrl(userTab.url)) {
          loggedIn = true;
          detectionMethod = 'tab_url_heuristic';
          tabHeuristicUrl = userTab.url;
        }
      } catch (_) { /* swallow */ }
    }

    if (!loggedIn) detectionMethod = 'failed';

    return extOk({
      logged_in: loggedIn,
      has_csrf: !!tokens.csrf,
      has_api_key: !!tokens.apiKey,
      has_employer_key: !!tokens.employerKey,
      // 诊断字段:LLM 按此区分"真未登录"vs"DOM 抓取异常"vs"Worker Tab 创建失败"
      detection_method: detectionMethod,           // 'dom' | 'graphql_probe' | 'tab_url_heuristic' | 'failed'
      worker_tab_url: tokens.workerTabUrl || '',   // Worker Tab 实际落到哪个 URL
      // 探针用了哪个 tab(true=用户真实 tab / false=后台 Worker Tab)
      // 401 + using_user_tab=false 通常意味着用户根本没开 employers.indeed.com
      using_user_tab: probe?.using_user_tab ?? false,
      // 第 3 层 tab URL 启发式命中时填入,LLM 据此告诉用户"我从你打开的 X 推断你已登录"
      ...(tabHeuristicUrl ? { tab_heuristic_url: tabHeuristicUrl } : {}),
      ...(probe?.hint ? { hint: probe.hint } : {}),
      ...(domError ? { dom_error: domError } : {}),
      ...(probe?.error ? { probe_error: probe.error } : {}),
    });
  },
});


// ── 列出职位 ────────────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/list_jobs',
  description: '列出当前雇主账号下的职位（返回 employerJobId 供后续搜索候选人）',
  produces: { chain: 'indeed_employer', stage: 'jobs' },
  handler: async ({ limit = 20 } = {}) => {
    const result = await apiIndeedEmployerListJobs(limit);
    // 缓存 employerJobId 到 token chain
    for (const job of result.jobs) {
      if (job.employerJobId) {
        tokenStore.setChainToken(
          'indeed_employer', job.employerJobId, 'jobListToken',
          job.employerJobId, { title: job.title }
        );
      }
    }
    return extOk(result);
  },
});


// ── 搜索候选人 ──────────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/search_candidates',
  description: '搜索某职位的候选人列表',
  requires: { chain: 'indeed_employer', stage: 'jobs', keyParam: 'employer_job_id' },
  produces: { chain: 'indeed_employer', stage: 'candidates' },
  handler: async ({
    employer_job_id,
    dispositions = 'NEW,PENDING,REVIEWED,PHONE_SCREENED,INTERVIEWED,OFFER_MADE',
    sort_by = 'APPLY_DATE',
    limit = 20,
    cursor = null,
  } = {}) => {
    if (!employer_job_id) throw new Error('employer_job_id 必填');

    const dispList = typeof dispositions === 'string'
      ? dispositions.split(',').map(d => d.trim()).filter(Boolean)
      : dispositions;

    const result = await apiIndeedEmployerSearchCandidates(employer_job_id, {
      dispositions: dispList,
      sortBy: sort_by,
      limit: Number(limit),
      cursor,
    });

    return extOk(result);
  },
});


// ── 获取候选人详情 + 简历附件 ───────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/get_candidate',
  description: '获取候选人的申请详情和简历附件信息',
  handler: async ({ submission_uuid, legacy_id } = {}) => {
    if (!submission_uuid && !legacy_id) {
      throw new Error('submission_uuid 或 legacy_id 必填其一');
    }

    const uuid = submission_uuid || legacy_id;
    const appData = await apiIndeedEmployerGetApplicationData(uuid);

    return extOk({
      submission_uuid: uuid,
      application_html_length: appData.applicationHtml?.length || 0,
      attachments: appData.attachments,
      post_body: appData.postBody,
    });
  },
});


// ── 下载简历 PDF ────────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/download_resume',
  description: '下载候选人简历 PDF（返回 base64_pdf）',
  handler: async ({ legacy_id, candidate_name = '' } = {}) => {
    if (!legacy_id) throw new Error('legacy_id 必填');

    const result = await apiIndeedEmployerDownloadResume(legacy_id);

    return extOk({
      base64_pdf: result.base64_pdf,
      candidate_id: legacy_id,
      candidate_name,
      size: result.size,
      mimeType: result.mimeType,
    });
  },
});


// ── 更新候选人状态 ─────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/update_candidate_status',
  description: '移动候选人到不同招聘阶段（如 NEW→REVIEWED→PHONE_SCREENED→INTERVIEWED→OFFER_MADE→HIRED/REJECTED）',
  handler: async ({ legacy_id, job_id, milestone_id } = {}) => {
    if (!legacy_id) throw new Error('legacy_id 必填（候选人 ID）');
    if (!job_id) throw new Error('job_id 必填（职位的 jobDataId）');
    if (!milestone_id) throw new Error('milestone_id 必填（目标阶段，如 REVIEWED）');

    const result = await apiIndeedEmployerUpdateCandidateStatus(legacy_id, job_id, milestone_id);
    return extOk(result);
  },
});


// ── 获取候选人消息记录 ─────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/get_conversations',
  description: '获取与某候选人的消息记录列表',
  handler: async ({ candidate_key } = {}) => {
    if (!candidate_key) throw new Error('candidate_key 必填（即候选人 legacyId）');

    const result = await apiIndeedEmployerGetConversations(candidate_key);
    return extOk(result);
  },
});


// ── 获取 AI 筛选摘要 ──────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/get_screening_summary',
  description: '获取候选人的 AI 面试状态、身份验证结果和智能筛选问答',
  handler: async ({ submission_uuid } = {}) => {
    if (!submission_uuid) throw new Error('submission_uuid 必填');

    const result = await apiIndeedEmployerGetScreeningSummary(submission_uuid);
    return extOk(result);
  },
});


// ── 获取面试安排 ───────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/get_interviews',
  description: '查看候选人的面试安排',
  handler: async ({ submission_uuid } = {}) => {
    if (!submission_uuid) throw new Error('submission_uuid 必填');

    // 构造 base64 candidateSubmissionId
    const candidateSubmissionId = btoa(
      `iri://apis.indeed.com/CandidateSubmission/${submission_uuid}`
    );
    const result = await apiIndeedEmployerGetInterviews(candidateSubmissionId);
    return extOk(result);
  },
});


// ── 获取详细匹配原因 ──────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/get_match_details',
  description: '获取候选人与职位的详细匹配原因（比搜索结果更详细）',
  handler: async ({ legacy_id } = {}) => {
    if (!legacy_id) throw new Error('legacy_id 必填');

    const result = await apiIndeedEmployerGetMatchDetails(legacy_id);
    return extOk(result);
  },
});


// ── 候选人兴趣反馈 ─────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/set_candidate_feedback',
  description: '给候选人打兴趣标签（YES=感兴趣, NO=不感兴趣, MAYBE=待定）',
  handler: async ({ legacy_id, sentiment } = {}) => {
    if (!legacy_id) throw new Error('legacy_id 必填');
    if (!sentiment) throw new Error('sentiment 必填（YES / NO / MAYBE）');

    const result = await apiIndeedEmployerSetCandidateFeedback(legacy_id, sentiment.toUpperCase());
    return extOk(result);
  },
});


// ── 获取筛选问答 ───────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/get_screening_answers',
  description: '获取候选人的筛选问答（教育、语言、工作经验等雇主设定的筛选问题和候选人回答）',
  handler: async ({ candidate_id } = {}) => {
    if (!candidate_id) throw new Error('candidate_id 必填（即 legacyId）');

    const result = await apiIndeedEmployerGetScreeningAnswers(candidate_id);
    return extOk(result);
  },
});


// ── 标记候选人已查看 ──────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/mark_candidate_viewed',
  description: '标记候选人为已查看（已读回执）',
  handler: async ({ submission_uuid } = {}) => {
    if (!submission_uuid) throw new Error('submission_uuid 必填');

    const result = await apiIndeedEmployerMarkCandidateViewed(submission_uuid);
    return extOk(result);
  },
});


// ── 发送消息给候选人 ──────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/send_message',
  description: '发送消息给候选人（自动查找已有会话或创建新会话）',
  handler: async ({ candidate_key, message_body, conversation_id, agg_job_key } = {}) => {
    if (!candidate_key) throw new Error('candidate_key 必填（候选人 legacyId）');
    if (!message_body) throw new Error('message_body 必填');

    // 如果没有 conversation_id，先尝试查找已有会话
    let convId = conversation_id || null;
    if (!convId) {
      const convs = await apiIndeedEmployerGetConversations(candidate_key);
      if (convs.conversations.length > 0) {
        convId = convs.conversations[0].id;
      }
    }

    // 无已有会话时必须提供 agg_job_key 才能创建新会话
    if (!convId && !agg_job_key) {
      throw new Error('未找到已有会话，需要提供 agg_job_key 来创建新会话');
    }

    const result = await apiIndeedEmployerSendMessage(candidate_key, message_body, {
      conversationId: convId,
      aggJobKey: convId ? null : agg_job_key,
    });
    return extOk(result);
  },
});


// ── 获取会话消息历史 ──────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/get_conversation_messages',
  description: '获取与候选人的完整消息历史',
  handler: async ({ conversation_id, limit = 50 } = {}) => {
    if (!conversation_id) throw new Error('conversation_id 必填（从 get_conversations 获取）');

    const result = await apiIndeedEmployerGetConversationMessages(conversation_id, Number(limit));
    return extOk(result);
  },
});


// ── 获取消息模板 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed_employer/get_message_templates',
  description: '获取已保存的消息模板列表',
  handler: async ({ limit = 20 } = {}) => {
    const result = await apiIndeedEmployerGetMessageTemplates(Number(limit));
    return extOk(result);
  },
});


// ══════════════════════════════════════════════════════════════════════════════
// Indeed Resume Search (主动搜索简历库)
// ══════════════════════════════════════════════════════════════════════════════


defineCommand({
  path: 'indeed_employer/search_resumes',
  description: '在 Indeed 简历库中按关键词搜索候选人（主动寻源）',
  produces: { chain: 'indeed_employer', stage: 'sourcing_search', keyParam: 'rcpRequestId' },
  handler: async ({ query, location = '', employer_job_id = '', offset = 0, filters = [] } = {}) => {
    if (!query) throw new Error('query 必填（搜索关键词）');

    const result = await apiIndeedEmployerSearchResumes(query, location, {
      employerJobId: employer_job_id,
      offset: Number(offset),
      filters: typeof filters === 'string' ? JSON.parse(filters) : filters,
    });
    // 把会话级 token 落 store,后续 log_candidate_seen 会按需取
    if (result.rcpRequestId) {
      tokenStore.setChainToken('indeed_employer', result.rcpRequestId, 'rcpRequestId', result.rcpRequestId);
    }
    if (result.searchSessionId) {
      tokenStore.setChainToken('indeed_employer', result.searchSessionId, 'searchSessionId', result.searchSessionId);
    }
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/get_talent_engagement',
  description: '查看与某候选人的互动历史（是否已联系过）',
  handler: async ({ candidate_id } = {}) => {
    if (!candidate_id) throw new Error('candidate_id 必填（候选人 accountKey）');

    const result = await apiIndeedEmployerGetTalentEngagement(candidate_id);
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/log_candidate_seen',
  description: 'Smart Sourcing 反作弊曝光埋点 —— 给搜索结果中看到的候选人打"已浏览"标',
  requires: { chain: 'indeed_employer', stage: 'sourcing_search', keyParam: 'rcp_request_id' },
  handler: async ({ candidate_ids, rcp_request_id, surface = 'sourcing-search', grps = [] } = {}) => {
    if (!candidate_ids) throw new Error('candidate_ids 必填(数组或 JSON 字符串)');
    if (!rcp_request_id) throw new Error('rcp_request_id 必填(取自最近一次 search_resumes 返回)');
    const ids = typeof candidate_ids === 'string' ? JSON.parse(candidate_ids) : candidate_ids;
    const grpsArr = typeof grps === 'string' ? JSON.parse(grps) : grps;
    const result = await apiIndeedEmployerLogCandidateSeen(ids, rcp_request_id, surface, grpsArr);
    return extOk(result);
  },
});


// ══════════════════════════════════════════════════════════════════════════════
// Spec 5.3 / 5.4 / 5.5 候选人评审 + 申请人筛选 + 消息(7 命令)
// ══════════════════════════════════════════════════════════════════════════════


defineCommand({
  path: 'indeed_employer/get_match_profile',
  description: 'spec 5.3 「分析候选人」: 取候选人 vs 岗位的匹配可解释性',
  handler: async ({ job_id } = {}) => {
    if (!job_id) throw new Error('job_id 必填(EmployerJob iri 或 legacyId)');
    const result = await apiIndeedEmployerGetMatchProfile(job_id);
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/get_candidate_submission',
  description: 'spec 5.3「查看简历」+ 候选人 submission 完整详情(含 resume URL)',
  handler: async ({ submission_id, first = 1 } = {}) => {
    if (!submission_id) throw new Error('submission_id 必填');
    const result = await apiIndeedEmployerGetCandidateSubmission(submission_id, {
      first: Number(first) || 1,
    });
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/find_applicants',
  description: 'spec 5.4 申请人列表(per-job RCP 匹配,可按匹配度排序)',
  produces: { chain: 'indeed_employer', stage: 'sourcing_search', keyParam: 'rcpRequestId' },
  handler: async ({
    employer_job_id, dispositions, sort_by, sort_order,
    created_after_ms, limit,
  } = {}) => {
    if (!employer_job_id) throw new Error('employer_job_id 必填(IRI 形态)');
    const opts = {
      dispositions: typeof dispositions === 'string'
        ? JSON.parse(dispositions)
        : (dispositions || undefined),
      sortBy: sort_by || 'APPLY_DATE',
      sortOrder: sort_order || 'DESCENDING',
      createdAfterMs: Number(created_after_ms) || 0,
      limit: Number(limit) || 20,
    };
    const result = await apiIndeedEmployerFindApplicants(employer_job_id, opts);
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/get_applicant_filters',
  description: 'spec 5.4 申请人 facet — locations/sentiments/milestones/shortlist/undecided counts',
  handler: async ({ employer_job_id } = {}) => {
    if (!employer_job_id) throw new Error('employer_job_id 必填');
    const result = await apiIndeedEmployerGetApplicantFilters(employer_job_id);
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/get_risk_assessment',
  description: 'spec 5.4 「自动标记可疑申请」真信号(替代 LLM 启发式)',
  handler: async ({ contexts } = {}) => {
    if (!contexts) throw new Error('contexts 必填(数组或 JSON 字符串)');
    const ctx = typeof contexts === 'string' ? JSON.parse(contexts) : contexts;
    const result = await apiIndeedEmployerGetRiskAssessment(ctx);
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/list_conversations_v2',
  description: 'spec 5.5 招聘端会话列表(支持 24h/3d/7d 时间窗口过滤)',
  handler: async ({ since_ms = 0, until_ms = 0, employer_job_id = '', limit = 20 } = {}) => {
    const result = await apiIndeedEmployerListConversationsV2({
      sinceMs: Number(since_ms) || 0,
      untilMs: Number(until_ms) || 0,
      employerJobId: employer_job_id,
      limit: Number(limit) || 20,
    });
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/get_conversation_thread',
  description: 'spec 5.5 单 conversation 完整 thread(回复时取上下文)',
  handler: async ({ conversation_id } = {}) => {
    if (!conversation_id) throw new Error('conversation_id 必填');
    const result = await apiIndeedEmployerGetConversationThread(conversation_id);
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/search_autocomplete',
  description: '搜索关键词或地点自动补全建议',
  handler: async ({ query, type = 'keyword' } = {}) => {
    if (!query) throw new Error('query 必填');

    const result = await apiIndeedEmployerSearchAutocomplete(query, type);
    return extOk(result);
  },
});


// ══════════════════════════════════════════════════════════════════════════════
// Indeed 岗位发布
// ══════════════════════════════════════════════════════════════════════════════


defineCommand({
  path: 'indeed_employer/list_draft_jobs',
  description: '列出所有草稿岗位',
  handler: async () => {
    const result = await apiIndeedEmployerListDraftJobs();
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/get_job_form',
  description: '加载岗位发布表单当前状态（查看已填写的字段）',
  handler: async ({ draft_job_id } = {}) => {
    if (!draft_job_id) throw new Error('draft_job_id 必填');

    const result = await apiIndeedEmployerGetJobForm(draft_job_id);
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/update_job_form',
  description: '更新岗位发布表单字段（title, description, pay, location 等）',
  handler: async ({ form_id, patch } = {}) => {
    if (!form_id) throw new Error('form_id 必填（从 get_job_form 获取）');
    if (!patch) throw new Error('patch 必填（要更新的字段）');

    const patchObj = typeof patch === 'string' ? JSON.parse(patch) : patch;
    const result = await apiIndeedEmployerUpdateJobForm(form_id, patchObj);
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/publish_job',
  description: '发布岗位（将草稿变为正式发布状态）',
  handler: async ({ form_id } = {}) => {
    if (!form_id) throw new Error('form_id 必填（从 get_job_form 获取）');

    const result = await apiIndeedEmployerPublishJob(form_id);
    return extOk(result);
  },
});


defineCommand({
  path: 'indeed_employer/optimize_job_description',
  description: '获取 AI 优化的岗位描述建议',
  handler: async ({ draft_job_id, title, language = 'en', country = 'US' } = {}) => {
    if (!draft_job_id) throw new Error('draft_job_id 必填');
    if (!title) throw new Error('title 必填（岗位标题）');

    const result = await apiIndeedEmployerOptimizeJobDescription(draft_job_id, title, language, country);
    return extOk(result);
  },
});
