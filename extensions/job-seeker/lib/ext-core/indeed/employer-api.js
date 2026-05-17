/**
 * indeed/employer-api.js — Indeed 雇主端 API 层
 *
 * 通过 employers.indeed.com Worker Tab 发起请求，自动携带 employer cookie。
 * GraphQL API: https://apis.indeed.com/graphql?co=US&locale=en-US
 * 简历下载:   https://employers.indeed.com/api/catws/resume/v2/download
 *
 * 依赖: core/site-executor.js (executeSiteApi, executeSiteApiBinary, ensureSiteWorkerTab)
 */

// ── 认证 Token 缓存 ────────────────────────────────────────────────────────

let _indeedEmployerTokens = null; // { apiKey, ctk, csrf, employerKey, ts, workerTabUrl }


// ── webRequest 拦截:从用户真实 tab 发出的 GraphQL 请求里抓 indeed-api-key ──
//
// 背景:Indeed apolloClient 内部用私有 fetch instance(非 window.fetch),
// 我们调 window.fetch 即使在 user tab 也拿不到 patched 版本 → 401。
// 解法:监听用户 tab 发出的真实 graphql 请求,从 request headers 抠 indeed-api-key
// + indeed-ctk + indeed-tk 等,缓存到 _indeedHarvestedHeaders 供后续 GraphQL 调用复用。
// 这是最可靠的"借用" —— 直接拿 Indeed 自己加的头。
//
// 命中条件:URL 含 apis.indeed.com/graphql,initiator origin = employers.indeed.com。

let _indeedHarvestedHeaders = null;  // { 'indeed-api-key', 'indeed-ctk', ..., capturedAt }
const _HEADER_HARVEST_TTL_MS = 30 * 60 * 1000;  // 30 min

function _hasFreshHarvestedHeaders() {
  return _indeedHarvestedHeaders
    && Date.now() - _indeedHarvestedHeaders.capturedAt < _HEADER_HARVEST_TTL_MS;
}

function _getHarvestedHeaders() {
  return _hasFreshHarvestedHeaders() ? _indeedHarvestedHeaders : null;
}

// chrome.webRequest 在 SW 重启时 listener 会丢,需要重新注册。导出给 background.js
// 调一次完成初始化。
function _setupIndeedHeaderHarvest() {
  if (typeof chrome === 'undefined' || !chrome.webRequest) return false;
  try {
    if (chrome.webRequest.onBeforeSendHeaders.hasListener(_indeedHeaderListener)) {
      return true;  // 已注册
    }
    chrome.webRequest.onBeforeSendHeaders.addListener(
      _indeedHeaderListener,
      { urls: ['https://apis.indeed.com/graphql*'] },
      ['requestHeaders']  // MV3 不允许 'extraHeaders' / 'blocking',只读模式 OK
    );
    return true;
  } catch (e) {
    console.warn('[indeed-harvest] webRequest 注册失败:', e?.message || e);
    return false;
  }
}

function _indeedHeaderListener(details) {
  // 只信任来自 employers.indeed.com 的请求(忽略求职端 indeed.com)
  const init = details.initiator || details.documentUrl || '';
  if (!init.startsWith('https://employers.indeed.com')) return;

  const wanted = new Set([
    'indeed-api-key', 'indeed-ctk', 'indeed-tk',
    'x-csrf-token', 'x-indeed-csrf-token',
    // 部分链路用 'authorization': 'Bearer ...',也抓上
    'authorization',
  ]);
  const harvested = {};
  for (const h of details.requestHeaders || []) {
    const name = (h.name || '').toLowerCase();
    if (wanted.has(name) && h.value) harvested[name] = h.value;
  }
  if (Object.keys(harvested).length === 0) return;

  // merge 累加:不同请求可能带不同子集的头
  const prev = _indeedHarvestedHeaders || {};
  _indeedHarvestedHeaders = {
    ...prev,
    ...harvested,
    capturedAt: Date.now(),
  };
}

async function indeedEmployerExtractTokens() {
  // 缓存 10 分钟 —— 仅在缓存里至少有一个 token 时才复用,避免上一次时序问题
  // 抽空导致后续 10min 持续返回 logged_in=false。
  if (_indeedEmployerTokens
      && Date.now() - _indeedEmployerTokens.ts < 600_000
      && (_indeedEmployerTokens.csrf || _indeedEmployerTokens.apiKey || _indeedEmployerTokens.employerKey)) {
    return _indeedEmployerTokens;
  }

  const tab = await ensureSiteWorkerTab('indeed_employer');
  if (!tab || !tab.id) throw new Error('indeed_employer Worker Tab 不可用');

  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: () => {
      // 从 cookie 提取 csrf token
      const cookies = document.cookie.split(';').reduce((acc, c) => {
        const [k, ...v] = c.trim().split('=');
        acc[k] = v.join('=');
        return acc;
      }, {});
      const csrf = cookies['indeedcsrftoken'] || '';

      // 从页面 meta/script 提取 api key 和 ctk
      let apiKey = '';
      let ctk = '';
      let employerKey = '';

      // 尝试从 meta 标签提取
      const metaCtk = document.querySelector('meta[name="indeed-ctk"]');
      if (metaCtk) ctk = metaCtk.content;

      // 尝试从 window.__NEXT_DATA__ 或 window.__data__ 提取
      try {
        const nextData = window.__NEXT_DATA__;
        if (nextData?.props?.pageProps?.indeedApiKey) {
          apiKey = nextData.props.pageProps.indeedApiKey;
        }
        if (nextData?.props?.pageProps?.ctk) {
          ctk = ctk || nextData.props.pageProps.ctk;
        }
      } catch (_) {}

      // 尝试从 script 标签中提取 indeed-api-key
      if (!apiKey) {
        const scripts = document.querySelectorAll('script');
        for (const s of scripts) {
          const text = s.textContent || '';
          const m = text.match(/"indeed-api-key"\s*:\s*"([a-f0-9]+)"/);
          if (m) { apiKey = m[1]; break; }
          const m2 = text.match(/indeedApiKey\s*[=:]\s*"([a-f0-9]+)"/);
          if (m2) { apiKey = m2[1]; break; }
        }
      }

      // 从 cookie 提取 employer key
      employerKey = cookies['EK'] || '';

      // 把当前 URL 一并返回(诊断用 —— 招聘端 dashboard / 营销页 / 登录页区分)
      return { csrf, apiKey, ctk, employerKey, workerTabUrl: location.href };
    },
    args: [],
  });

  const r = results?.[0]?.result;
  if (!r) throw new Error('Failed to extract Indeed employer tokens');

  // 仅在抽到至少一个 token 时缓存。失败抽取直接回返,下次重试不读 stale 数据。
  if (r.csrf || r.apiKey || r.employerKey) {
    _indeedEmployerTokens = { ...r, ts: Date.now() };
  }
  return r;
}


// ── GraphQL 通用调用 ────────────────────────────────────────────────────────

const GRAPHQL_ENDPOINT = 'https://apis.indeed.com/graphql?co=US&locale=en-US';

async function indeedEmployerGraphQL(operationName, query, variables = {}) {
  const tokens = await indeedEmployerExtractTokens();
  // 优先用用户已打开的真实 tab(page bootstrap 完整),fallback 到 Worker Tab。
  // 同 apiIndeedEmployerProbeAuth 的策略 —— 真实 tab 是 Indeed apolloClient
  // 已 ready 的环境,凡 webRequest 抓到的 headers 都来自该 tab 的成功请求,
  // 在同一 tab 上 replay 这些 headers 命中率最高。
  let tab = await _findIndeedEmployerTab();
  if (!tab) tab = await ensureSiteWorkerTab('indeed_employer');
  if (!tab || !tab.id) throw new Error('indeed_employer Worker Tab 不可用');

  // 三方融合 headers:DOM 抓取 + webRequest 拦截 + 默认。webRequest 拦到的
  // 是 Indeed 自己加的真实头,优先级最高(覆盖 DOM 抓的旧值)。
  const harvested = _getHarvestedHeaders() || {};
  const mergedHeaders = {
    'Content-Type': 'application/json',
    'Accept': '*/*',
  };
  if (tokens.apiKey) mergedHeaders['indeed-api-key'] = tokens.apiKey;
  if (tokens.ctk)    mergedHeaders['indeed-ctk'] = tokens.ctk;
  // webRequest 抓到的覆盖 DOM 抓的(它更准 —— 来自实际成功请求)
  for (const [k, v] of Object.entries(harvested)) {
    if (k !== 'capturedAt') mergedHeaders[k] = v;
  }

  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: async (url, body, headers) => {
      try {
        const resp = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          headers,
          body: JSON.stringify(body),
        });
        if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
        const json = await resp.json();
        return { ok: true, data: json };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },
    args: [
      GRAPHQL_ENDPOINT,
      { operationName, query, variables },
      mergedHeaders,
    ],
  });

  const r = results?.[0]?.result;
  if (!r) throw new Error('GraphQL executeScript returned empty');
  if (!r.ok) throw new Error(`GraphQL failed: ${r.error}`);
  if (r.data?.errors?.length) {
    throw new Error(`GraphQL error: ${JSON.stringify(r.data.errors[0])}`);
  }
  return r.data;
}


// ── GraphQL Queries ─────────────────────────────────────────────────────────

const GQL_FIND_EMPLOYER_JOBS = `
query FindEmployerJobs($input: FindEmployerJobsInput!) {
  findEmployerJobs(input: $input) {
    results {
      employerJob {
        id
        jobData {
          id
          title
          company
          ... on HostedJobPost {
            legacyId
            status
            location { formatted { short __typename } __typename }
            advertisingLocations { location active __typename }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    estimatedTotalResultsCount
    __typename
  }
}`;

const GQL_FIND_RCP_MATCHES = `
query FindRCPMatches($input: OrchestrationMatchesInput!) {
  findRCPMatches(input: $input) {
    overallMatchCount
    matchConnection {
      pageInfo { hasNextPage hasPreviousPage __typename }
      matches {
        matchId { id type __typename }
        candidateSubmission {
          id
          data {
            submissionUuid
            created
            candidateIdentity {
              candidateId
              jobseekerAccountKey
              __typename
            }
            profile {
              name { displayName __typename }
              location { country location __typename }
              contact { phoneNumber __typename }
              __typename
            }
            metadata {
              data {
                ... on CandidateEmploymentHistory { jobTitle company __typename }
                __typename
              }
              __typename
            }
            milestone {
              milestone { category milestoneId __typename }
              __typename
            }
            sources { sourceType __typename }
            resume {
              ... on CandidatePdfResume { id downloadUrl __typename }
              ... on CandidateHtmlFile { id downloadUrl body __typename }
              __typename
            }
            ... on IndeedApplyCandidateSubmission { legacyID __typename }
            ... on LegacyCandidateSubmission { legacyID __typename }
            ... on EmployerGeneratedCandidateSubmission { legacyID __typename }
            job {
              matchExplanations {
                requirementMatchExplanations {
                  matchType jobRequirementType
                  formatted { snippet __typename }
                  __typename
                }
                __typename
              }
              __typename
            }
            __typename
          }
          talentRepresentation {
            experience {
              company title
              dateRange {
                ... on TalentRepresentationDateRangePast {
                  fromDate { year __typename } toDate { year __typename } __typename
                }
                ... on TalentRepresentationDateRangeCurrent {
                  fromDate { year __typename } __typename
                }
                __typename
              }
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}`;

const GQL_ORIGINAL_APPLICATION_DATA = `
query OriginalApplicationData($input: OriginalApplicationDataInput!) {
  originalApplicationData(input: $input) {
    applicationPreview {
      attachmentCookie
      html
      fileName
      __typename
    }
    attachments {
      downloadUrl
      fileId
      fileName
      mimeType
      __typename
    }
    postBody {
      downloadUrl
      fileId
      fileName
      mimeType
      __typename
    }
    __typename
  }
}`;


const GQL_UPDATE_CANDIDATE_STATUS = `
mutation UpdateCandidateStatus($statusInput: UpdateCandidateSubmissionMilestoneInput!) {
  updateCandidateSubmissionMilestone(input: $statusInput) {
    candidateSubmissionMilestone {
      category
      milestoneId
      __typename
    }
    __typename
  }
}`;


const GQL_FIND_CONVERSATIONS_BY_CANDIDATE = `
query FindConversationsByCandidateKey($advertiserKey: String!, $candidateKey: String!, $enableAdjacentContextSearch: Boolean = false) {
  findConversations(
    input: {filter: {contexts: [APPLICATION, HQM], scope: [{key: ADVERTISER_KEY, value: $advertiserKey}, {key: CANDIDATE_KEY, value: $candidateKey}]}, options: {adjacentContextSearch: $enableAdjacentContextSearch, allowUninitiatedConversationResults: true}}
  ) {
    conversations {
      id
      creationDateTime
      __typename
    }
    __typename
  }
}`;


const GQL_SMART_SCREENING_SUMMARY = `
query Nex_SmartScreeningSummary_Applicant($scoutApplicationId: ID!, $candidateSubmissionId: ID!) {
  aiInterviewNode: node(id: $candidateSubmissionId) {
    ... on CandidateSubmission {
      id
      aiInterviews {
        id
        status
        interviewType
        __typename
      }
      __typename
    }
    __typename
  }
  scoutApplicationNode: node(id: $scoutApplicationId) {
    ... on ScoutApplication {
      id
      nexusDetails {
        isRescoring
        atsSyncStatus
        identityVerificationReport {
          inferredStatus
          verificationResult
          __typename
        }
        smartScreening {
          qualificationSummary {
            qualifications {
              question
              answer
              dealbreaker
              qualified
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


const GQL_FIND_EMPLOYER_INTERVIEWS = `
query FindEmployerInterviews($input: FindEmployerInterviewsInput!) {
  findEmployerInterviews(input: $input) {
    employerInterviews {
      id
      status
      startTime
      endTime
      interviewType
      location
      notes
      __typename
    }
    pageInfo {
      hasNextPage
      __typename
    }
    __typename
  }
}`;


const GQL_GET_MATCH_EXPLANATIONS = `
query getMatchExplanations($legacyId: String!) {
  candidateSubmissions(input: {legacyIds: [$legacyId]}) {
    results {
      ... on CandidateSubmission {
        id
        data {
          job {
            matchExplanations {
              requirementMatchExplanations {
                matchType
                jobRequirementType
                formatted {
                  snippet
                  __typename
                }
                __typename
              }
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


const GQL_CREATE_CANDIDATE_FEEDBACK = `
mutation CreateEmployerCandidateSubmissionFeedback($createEmployerCandidateSubmissionFeedbackInput: CreateEmployerCandidateSubmissionFeedbackInput!) {
  createEmployerCandidateSubmissionFeedback(
    input: $createEmployerCandidateSubmissionFeedbackInput
  ) {
    feedback {
      id
      created
      ... on EmployerCandidateSentiment {
        interestLevel
        __typename
      }
      __typename
    }
    __typename
  }
}`;


const GQL_MARK_CANDIDATE_VIEWED = `
mutation MarkCandidateSubmissionViewed($markCandidateSubmissionViewedId: ID!) {
  markCandidateSubmissionViewed(id: $markCandidateSubmissionViewedId) {
    candidateSubmission {
      id
      __typename
    }
    __typename
  }
}`;


const GQL_SEND_CONVERSATION_EVENT = `
mutation SendConversationEvent($attachments: [ConversationAttachmentInput!], $clientName: String!, $conversationId: ID, $context: ConversationContextAndScopeInput, $eventId: String, $messageBody: String!, $payload: [ConversationEventMetadataInput!]) {
  sendConversationEvent(
    input: {attachments: $attachments, conversation: {id: $conversationId, contextAndScope: $context}, eventId: $eventId, messageBody: $messageBody, messageContentFormat: "PLAIN", source: $clientName, type: MESSAGE, payload: $payload}
  ) {
    conversationId
    event {
      id
      type
      messageBody
      messageContentFormat
      publicationDateTime
      author {
        accountKey
        role
        __typename
      }
      __typename
    }
    __typename
  }
}`;


const GQL_GET_CONVERSATION_AND_EVENTS = `
query GetConversationAndEvents($conversationId: ID!, $last: Int, $before: String) {
  conversation(input: {conversationId: $conversationId}) {
    id
    creationDateTime
    context
    scope {
      key
      value
      __typename
    }
    eventsConnection(last: $last, before: $before) {
      edges {
        node {
          id
          type
          messageBody
          messageContentFormat
          messagePreview
          publicationDateTime
          author {
            accountKey
            role
            __typename
          }
          subType
          __typename
        }
        __typename
      }
      pageInfo {
        hasNextPage
        hasPreviousPage
        startCursor
        endCursor
        __typename
      }
      __typename
    }
    __typename
  }
}`;


const GQL_FETCH_TEMPLATES = `
query FetchTemplates($first: Int, $after: String, $input: FetchMessageTemplatesInput) {
  fetchMessageTemplates(first: $first, after: $after, input: $input) {
    edges {
      node {
        id
        title
        body
        labels
        isShared
        __typename
      }
      __typename
    }
    pageInfo {
      hasNextPage
      endCursor
      __typename
    }
    __typename
  }
}`;


const GQL_SMART_SOURCING_SEARCH = `
query SmartSourcingSearch($input: FindSourcingMatchesInput!) {
  findSourcingMatches(input: $input) {
    originalRcpResult {
      rcpRequestId
      searchSessionId
      overallMatchCount
      matchConnection {
        pageInfo {
          hasNextPage
          hasPreviousPage
          __typename
        }
        matches {
          rcpRequestId
          matchId {
            id
            __typename
          }
          highlights {
            text
            __typename
          }
          sourcingProfile {
            accountKey
            permissionToken
            profileCard {
              accountKey
              firstName
              lastName
              location {
                localizedValue
                __typename
              }
              educations {
                school
                degree
                __typename
              }
              experiences {
                title
                company
                fromDate
                toDate
                __typename
              }
              credentials {
                title
                __typename
              }
              skills {
                text
                __typename
              }
              dateModified
              lastClickedDate
              isFreeToContact
              resumeType
              activityData {
                lastActivityTimestamp
                __typename
              }
              __typename
            }
            engagementSummary {
              permissions {
                action
                status
                __typename
              }
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      refinementBuckets {
        key
        label
        items {
          count
          label
          value
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


const GQL_TALENT_ENGAGEMENT_SUMMARY = `
query TalentEngagementSummary($input: TalentEngagementSummaryInput!) {
  talentEngagementSummary(input: $input) {
    engagements {
      type
      created
      senderEmail
      status
      statusHistory {
        status
        modifiedDate
        __typename
      }
      __typename
    }
    __typename
  }
}`;


// 反作弊曝光埋点。Smart Sourcing 搜索后,Indeed 真实客户端会对每个看到的候选人
// 发一次 LogRcpCandidateSeen,作为"真人浏览"信号。不调会被风控认定为爬虫,
// 后续搜索质量下降 / 被限流。
const GQL_LOG_RCP_CANDIDATE_SEEN = `
mutation LogRcpCandidateSeen($input: LogRcpCandidateSeenInput!) {
  logRcpCandidateSeen(input: $input) {
    candidateResults {
      encryptedJobseekerAccountId
      eventKey
      matchId
      __typename
    }
    __typename
  }
}`;


// ── 招聘端登录探针(REST,cookie-only,同源端点) ────────────────────────
//
// 用途:DOM 抓 token 不到时的 fallback。
//
// 设计变迁:
// - v1: GraphQL `currentEmployerEntity` —— Indeed apolloClient 私有 fetch 不
//   patch window.fetch,401(缺 indeed-api-key)
// - v2(1.6.10): 加 webRequest 抓 headers,401 → 400(op name 不被 persisted
//   query gateway 认识)
// - v3(当前): 改用 REST `/segment/enterpriseDomainService/v2/user/profile` ——
//   employer 自家域同源,只要 cookie OK 就返 200 + 用户 profile,无 GraphQL
//   协议负担 / 无 op name 校验。从抓包 17773435 提取。
//
// 备注:`AUTH_PROBE_*` 常量保留旧 GraphQL 字段名供 fallback 兼容,但首选 REST。
const AUTH_PROBE_REST_URL = 'https://employers.indeed.com/segment/enterpriseDomainService/v2/user/profile';

const GQL_AUTH_PROBE = `
query CurrentEmployerEntityProbe {
  currentEmployerEntity {
    id
    key
    __typename
  }
}`;


// 失败结果短暂缓存(5s),避免 LLM rapid retry 触发 Indeed 反作弊。成功结果不缓存
// (走 indeedEmployerExtractTokens 的 10min 缓存 + 上游 logged_in 决定是否复用)。
let _probeAuthFailCache = null;  // { ts, result }
const PROBE_FAIL_CACHE_TTL_MS = 5000;

/** 优先找用户已打开的 employers.indeed.com tab(active 状态)。这种 tab 的
 *  页面 JavaScript 已完整 bootstrap(apolloClient + auth interceptor 都装好了),
 *  Indeed 自己的 fetch wrapper 会自动注入 indeed-api-key / x-csrf-token 等
 *  全套 auth 头。直接在这个 tab 跑探针成功率最高。
 *
 *  fallback:找不到时,用 Worker Tab(/jobs)—— 但 Worker Tab 是 active:false
 *  模式打开,Indeed 某些 lazy-loaded 模块不会执行,fetch wrapper 可能没初始化,
 *  所以 fallback 路径成功率低于真实 tab。 */
async function _findIndeedEmployerTab() {
  try {
    const tabs = await chrome.tabs.query({
      url: ['https://employers.indeed.com/*'],
    });
    // 优先 active 状态的 tab(用户正在看的那个)
    const active = tabs.find(t => t.active && t.status === 'complete');
    if (active) return active;
    const ready = tabs.find(t => t.status === 'complete');
    if (ready) return ready;
    return tabs[0] || null;
  } catch (_) {
    return null;
  }
}


/** 第 3 层兜底:tab URL 启发式判定登录态。
 *
 *  原理:Indeed 的核心保护是 server-side 重定向 —— 未登录用户访问
 *  employers.indeed.com/jobs 会被 302 到 secure.indeed.com/account/login。
 *  反之:**如果用户的 tab 稳定停在 employers.indeed.com/<authed-path> 不是
 *  /account/login,那他就是登录态的**。这是最客观的信号,不需要 GraphQL。
 *
 *  典型 authed paths(non-login):
 *    /jobs, /candidates, /messages, /smart-sourcing, /interviews, /analytics,
 *    /tools, /billing, /settings, /home(空根路径)
 *  典型 login paths:
 *    /account/login, /account/signin, /home/?redirect=...(redirect 参数 = 未登录)
 */
function _isAuthenticatedEmployerUrl(url) {
  if (!url || !url.startsWith('https://employers.indeed.com/')) return false;
  const path = url.replace('https://employers.indeed.com', '').toLowerCase();
  // 明确未登录的 path
  if (path.includes('/account/login') || path.includes('/account/signin')) return false;
  if (path.includes('redirect=') || path.includes('redirect_to=')) return false;
  // 明确已登录的 path
  if (/^\/(jobs|candidates|messages|smart-sourcing|interviews|analytics|tools|billing|settings|reports|onboarding|home)\b/.test(path)) {
    return true;
  }
  // 根路径或不在已知列表 —— 保守假设:未明确未登录,即视为已登录(用户能停在
  // employers.indeed.com 不被重定向就说明 cookie 有效)
  return true;
}


async function apiIndeedEmployerProbeAuth() {
  // 失败缓存命中(5s 内同样的失败立刻返回,避免 rapid retry)
  if (_probeAuthFailCache && Date.now() - _probeAuthFailCache.ts < PROBE_FAIL_CACHE_TTL_MS) {
    return { ..._probeAuthFailCache.result, cached: true };
  }
  _probeAuthFailCache = null;

  // 优先找用户已打开的真实 tab(authenticated, page bootstrap 完整),
  // 其次 fallback 到 Worker Tab。
  let tab = await _findIndeedEmployerTab();
  let usingUserTab = !!tab;
  if (!tab) {
    tab = await ensureSiteWorkerTab('indeed_employer');
    usingUserTab = false;
  }
  if (!tab || !tab.id) {
    const r = { logged_in: false, error: 'Worker Tab 不可用' };
    _probeAuthFailCache = { ts: Date.now(), result: r };
    return r;
  }

  // 在 employers.indeed.com 的 MAIN world 跑探针。
  // **关键修复**:Indeed apolloClient 用私有 fetch instance,window.fetch
  // 不被 patch → cookie-only fetch 会 401(GraphQL apis.indeed.com)。
  // 解法:换 REST 端点 employers.indeed.com/segment/.../user/profile —— 同源,
  // cookie 自动带,无 GraphQL persisted query 校验,200 OK = 已登录。
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: async (restUrl) => {
      try {
        const resp = await fetch(restUrl, {
          method: 'GET',
          credentials: 'include',
          headers: { 'Accept': 'application/json' },
        });
        if (!resp.ok) return { ok: false, status: resp.status };
        const json = await resp.json();
        return { ok: true, data: json };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },
    args: [AUTH_PROBE_REST_URL],
  });

  const r = results?.[0]?.result;
  if (!r) {
    const out = {
      logged_in: false,
      error: 'probe executeScript empty',
      using_user_tab: usingUserTab,
    };
    _probeAuthFailCache = { ts: Date.now(), result: out };
    return out;
  }
  if (!r.ok) {
    const out = {
      logged_in: false,
      error: r.error || `HTTP ${r.status}`,
      using_user_tab: usingUserTab,
      hint: r.status === 401 && !usingUserTab
        ? '后台 Worker Tab 调 GraphQL 时 401 —— 用户没有打开 employers.indeed.com 真实 tab,'
          + 'Worker Tab 的 fetch wrapper 没完整 bootstrap。建议:让用户在 Chrome 中保持 '
          + 'employers.indeed.com 至少一个 tab 打开,然后再试。'
        : undefined,
    };
    _probeAuthFailCache = { ts: Date.now(), result: out };
    return out;
  }

  // REST `/segment/.../user/profile` 返回 employer profile JSON。
  // 200 OK + 含 employerKey / userId 任一字段 = 已登录。
  // 抓包 17773435 实测响应包含: { employerKey, userId, email, name, ... }
  const profile = r.data || {};
  const employerKey = profile.employerKey || profile.advertiserKey || profile.employer_key || '';
  const userId      = profile.userId      || profile.id              || '';
  const loggedIn    = !!(employerKey || userId);

  if (!loggedIn) {
    const out = {
      logged_in: false,
      using_user_tab: usingUserTab,
      error: 'profile 200 但缺 employerKey/userId(可能是 anonymous 端点)',
    };
    _probeAuthFailCache = { ts: Date.now(), result: out };
    return out;
  }

  return {
    logged_in: true,
    employer_key: employerKey,
    employer_id: userId,
    using_user_tab: usingUserTab,
  };
}


// ── Spec 5.3 / 5.4 / 5.5 GraphQL Queries(候选人评审 + 申请人筛选 + 消息) ──
//
// 来源:抓包 17773489(候选人评审)+ 17773492(消息)+ 17773435/17773433。
// 7 个查询全部从真实 wire 协议提取,简化为我们需要的字段子集。

// 候选人 vs 岗位的匹配可解释性(spec 5.3 "分析候选人")
const GQL_MATCH_PROFILE = `
query MatchProfile($jobId: ID!) {
  getMatchingProfile(jobId: $jobId) {
    id
    userFitQualities {
      id
      rawValue
      state
      __typename
    }
    __typename
  }
}`;


// 候选人 submission 完整详情(spec 5.3 "查看简历"取 resume URL + AI 分析数据源)
const GQL_LEGACY_FIND_CANDIDATE_SUBMISSION = `
query LegacyFindCandidateSubmission($input: FindCandidateSubmissionsInput!, $first: Int) {
  findCandidateSubmissions(input: $input, first: $first) {
    candidateSubmissions {
      id
      data {
        created
        legacyID
        submissionUuid
        profile {
          name { displayName __typename }
          location { __typename }
          contact { phoneNumber __typename }
          __typename
        }
        milestone {
          milestone { category id __typename }
          __typename
        }
        feedback {
          feedback { id interestLevel __typename }
          __typename
        }
        jobs {
          jobs {
            id
            jobData {
              id
              legacyId
              jobKey
              title
              location { formatted { short __typename } __typename }
              advertisingLocations { publicJobPageUrl location __typename }
              externalJobPageUrl
              __typename
            }
            __typename
          }
          __typename
        }
        resume {
          __typename
        }
        sources { __typename }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


// 申请人匹配列表(spec 5.4 筛选申请人核心)。per-job 候选人 RCP 列表。
const GQL_FIND_RCP_MATCHES_V2 = `
query FindRCPMatches($input: FindRCPMatchesInput!) {
  findRCPMatches(input: $input) {
    rcpRequestId
    searchSessionId
    overallMatchCount
    overallMatchCountWithoutLimit
    matchConnection {
      matches {
        matchId { id type __typename }
        candidateSubmission {
          id
          data {
            created
            legacyID
            submissionUuid
            profile {
              name { displayName __typename }
              location { __typename }
              __typename
            }
            milestone { milestone { category __typename } __typename }
            sentiments { __typename }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


// 申请人筛选 facet(spec 5.4 维度 + 自动标记可疑的辅助数据)
const GQL_FIND_GROUPED_FILTER_OPTIONS = `
query FindGroupedCandidateSubmissionFilterOptions(
  $filterDataInput: FindCandidateSubmissionsInput!,
  $statusDataInput: FindCandidateSubmissionsInput!,
  $shortlistCountInput: FindCandidateSubmissionsInput!,
  $undecidedCountInput: FindCandidateSubmissionsInput!
) {
  filterData: findCandidateSubmissions(input: $filterDataInput) {
    filterOptions {
      locations {
        locations { value count __typename }
        __typename
      }
      sentiments {
        sentiments { value count __typename }
        __typename
      }
      __typename
    }
    __typename
  }
  milestoneData: findCandidateSubmissions(input: $statusDataInput) {
    filterOptions {
      hiringMilestones {
        milestoneIds { value count __typename }
        __typename
      }
      __typename
    }
    __typename
  }
  shortlistCountData: findCandidateSubmissions(input: $shortlistCountInput) {
    filterOptions {
      sentiments {
        sentiments { value count __typename }
        __typename
      }
      __typename
    }
    __typename
  }
  undecidedCountData: findCandidateSubmissions(input: $undecidedCountInput) {
    filterOptions {
      sentiments {
        sentiments { value count __typename }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


// 风险评估(spec 5.4 "自动标记可疑申请"真信号源 — 替代 LLM 启发式)
const GQL_GET_RISK_ASSESSMENT = `
query getRiskAssessment($contexts: [RiskContext!]!) {
  assessRiskForContexts(input: {contexts: $contexts}) {
    action
    reason
    limitInfo {
      limit
      remaining
      __typename
    }
    __typename
  }
}`;


// 招聘端会话列表(spec 5.5 — 支持 since/limit 时间窗口过滤)
const GQL_FIND_CONVERSATIONS_V2 = `
query FindConversations($input: FindConversationsInput!) {
  findConversations(input: $input) {
    conversations {
      id
      creationDateTime
      context
      lastEvent {
        id
        type
        subType
        publicationDateTime
        messagePreview
        author { accountKey role __typename }
        __typename
      }
      participants {
        accountKey
        participantName
        role
        removed
        __typename
      }
      scope { key value __typename }
      __typename
    }
    __typename
  }
}`;


// 单 conversation 完整 thread(spec 5.5 回复时取上下文)
// 重命名避免与上面 GQL_GET_CONVERSATION_AND_EVENTS(老 spec, eventsConnection 形式)冲突。
// 本变量是从 17773489 抓包提取的 spec 5.5 简化版,以 conversation(id:...) 为入口。
const GQL_GET_CONVERSATION_AND_EVENTS_V2 = `
query GetConversationAndEvents($conversationId: ID!) {
  conversation(id: $conversationId) {
    id
    creationDateTime
    context
    scope { key value __typename }
    lastEvent {
      id
      author { accountKey role __typename }
      type
      subType
      messageContentFormat
      messagePreview
      publicationDateTime
      __typename
    }
    participants {
      accountKey
      participantName
      role
      removed
      accessLevel
      __typename
    }
    __typename
  }
}`;


// ── Job Posting GraphQL ────────────────────────────────────────────────────

const GQL_FIND_DRAFT_JOB_POSTS = `
query findDraftJobPosts($input: FindDraftJobPostsInput!) {
  findDraftJobPosts(input: $input) {
    results {
      draftJobPost {
        id
        dateCreated
        title
        description
        jobPostingForm {
          id
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


const GQL_FIND_JOB_POSTING_FORM = `
query FindJobPostingFormByDraftJobId(
  $draftJobId: ID!,
  $includeTitleField: Boolean! = true,
  $includeLocationField: Boolean! = true,
  $includeDescriptionField: Boolean! = true,
  $includePayField: Boolean! = true,
  $includeJobTypeField: Boolean! = true,
  $includeBenefitsField: Boolean! = true,
  $includeHiresNeededField: Boolean! = true,
  $includeApplicationSettingsField: Boolean! = true,
  $includeWorkingHoursField: Boolean! = false,
  $includeJobLocaleField: Boolean! = true,
  $includeJobCompanyNameField: Boolean! = false,
  $includeOccupationField: Boolean! = false,
  $includeAccommodationsContactInfoField: Boolean! = false,
  $includeExpectedHireDateField: Boolean! = false
) {
  findJobPostingFormByDraftJobId(draftJobId: $draftJobId) {
    id
    data {
      title @include(if: $includeTitleField) { data { title } __typename }
      jobLocation @include(if: $includeLocationField) { data { location workLocationType roleLocationType largerArea } __typename }
      description @include(if: $includeDescriptionField) { data { description } __typename }
      pay @include(if: $includePayField) { data { minimumMinor maximumMinor period type } __typename }
      jobType @include(if: $includeJobTypeField) { data { types } __typename }
      benefits @include(if: $includeBenefitsField) { data { benefits benefitsOptOut } __typename }
      hiresNeeded @include(if: $includeHiresNeededField) { data { hiresNeeded } __typename }
      applicationSettings @include(if: $includeApplicationSettingsField) { data { applicationCallToAction applyEmailAddresses requireResume } __typename }
      jobLocale @include(if: $includeJobLocaleField) { data { country language } __typename }
      __typename
    }
    __typename
  }
}`;


const GQL_PERSIST_JOB_POSTING_FORM = `
mutation PersistJobPostingForm($input: PersistJobPostingFormInput!) {
  JobPostingForm {
    persist(input: $input) {
      result {
        id
        targetEntities {
          id
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


const GQL_PUBLISH_JOB_POSTING_FORM = `
mutation PublishJobPostingForm($input: PublishJobPostingFormInput!) {
  JobPostingForm {
    publish(input: $input) {
      ... on PublishJobPostingFormSuccessPayload {
        employerJob {
          id
          jobData {
            id
            country
            language
            ... on HostedJobPost {
              legacyId
              title
              status
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      ... on PublishJobPostingFormErrorPayload {
        errors {
          message
          field
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


const GQL_DRAFT_JOB_DESCRIPTION_OPTIMIZATION = `
query DraftJobDescriptionOptimization($ids: [ID!]!, $jobInputs: JobOptimizationJobInputsInput) {
  draftJobPosts(ids: $ids) {
    results {
      draftJobPost {
        id
        jobOptimization {
          id
          autoJobDescription(input: {jobInputs: $jobInputs}) {
            jobDescriptionOptimization {
              recommendationId
              newParam {
                jobDescription
                __typename
              }
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}`;


// ── 业务 API 封装 ───────────────────────────────────────────────────────────

async function apiIndeedEmployerListJobs(limit = 20) {
  const resp = await indeedEmployerGraphQL('FindEmployerJobs', GQL_FIND_EMPLOYER_JOBS, {
    input: {
      limit,
      filter: {
        employerJobPermission: null,
        allOf: [{
          anyOf: [{ createdOnIndeed: true }, { claimed: true }],
          isEditable: true,
        }],
      },
    },
  });
  const results = resp?.data?.findEmployerJobs?.results || [];
  return {
    jobs: results.map(r => {
      const job = r.employerJob || {};
      const data = job.jobData || {};
      return {
        employerJobId: job.id,
        jobDataId: data.id,
        title: data.title,
        company: data.company,
        legacyId: data.legacyId,
        status: data.status,
      };
    }),
    total: resp?.data?.findEmployerJobs?.estimatedTotalResultsCount || 0,
  };
}


async function apiIndeedEmployerSearchCandidates(employerJobId, options = {}) {
  const {
    dispositions = ['NEW', 'PENDING', 'REVIEWED', 'PHONE_SCREENED', 'INTERVIEWED', 'OFFER_MADE'],
    sortBy = 'APPLY_DATE',
    sortOrder = 'DESCENDING',
    limit = 20,
    cursor = null,
  } = options;

  const surfaceContext = [
    ...dispositions.map(d => ({ contextKey: 'DISPOSITION', contextPayload: d })),
    { contextKey: 'SORT_BY', contextPayload: sortBy },
    { contextKey: 'SORT_ORDER', contextPayload: sortOrder },
  ];

  const input = {
    clientSurfaceName: 'candidate-list-page',
    defaultStrategyId: 'U20GF',
    limit,
    context: { surfaceContext },
    identifiers: {
      jobIdentifiers: { employerJobId },
    },
  };
  if (cursor) input.cursor = cursor;

  const resp = await indeedEmployerGraphQL('FindRCPMatches', GQL_FIND_RCP_MATCHES, { input });
  const matchData = resp?.data?.findRCPMatches || {};
  const matches = matchData?.matchConnection?.matches || [];

  return {
    totalCount: matchData.overallMatchCount || 0,
    hasNextPage: matchData?.matchConnection?.pageInfo?.hasNextPage || false,
    candidates: matches.map(m => {
      const sub = m.candidateSubmission || {};
      const data = sub.data || {};
      const profile = data.profile || {};
      const meta = (data.metadata || [])[0]?.data || {};
      const experience = (sub.talentRepresentation?.experience || []);
      const matchExps = data.job?.matchExplanations?.requirementMatchExplanations || [];

      return {
        submissionUuid: data.submissionUuid,
        legacyId: data.legacyID,
        candidateId: data.candidateIdentity?.candidateId,
        name: profile.name?.displayName,
        location: profile.location?.location,
        country: profile.location?.country,
        phone: profile.contact?.phoneNumber,
        currentTitle: meta.jobTitle,
        currentCompany: meta.company,
        appliedAt: data.created,
        milestone: data.milestone?.milestone?.milestoneId,
        sourceType: data.sources?.[0]?.sourceType,
        experience: experience.map(e => ({
          company: e.company,
          title: e.title,
          from: e.dateRange?.fromDate?.year,
          to: e.dateRange?.toDate?.year,
        })),
        matchHighlights: matchExps
          .filter(e => e.matchType === 'POSITIVE')
          .map(e => e.formatted?.snippet)
          .filter(Boolean),
        hasResume: !!data.resume,
      };
    }),
  };
}


async function apiIndeedEmployerGetApplicationData(submissionUuid) {
  const resp = await indeedEmployerGraphQL(
    'OriginalApplicationData',
    GQL_ORIGINAL_APPLICATION_DATA,
    { input: { submissionUuid } },
  );
  const appData = resp?.data?.originalApplicationData || {};
  return {
    applicationHtml: appData.applicationPreview?.html || '',
    attachments: (appData.attachments || []).map(a => ({
      fileName: a.fileName,
      mimeType: a.mimeType,
      downloadUrl: a.downloadUrl,
      fileId: a.fileId,
    })),
    postBody: appData.postBody ? {
      fileName: appData.postBody.fileName,
      mimeType: appData.postBody.mimeType,
      downloadUrl: appData.postBody.downloadUrl,
    } : null,
  };
}


async function apiIndeedEmployerDownloadResume(legacyId) {
  const tokens = await indeedEmployerExtractTokens();
  // CSRF 双源:DOM/cookie 抓取 + webRequest 拦截。任一存在即可。
  const harvested = _getHarvestedHeaders() || {};
  const csrf = tokens.csrf
    || harvested['x-csrf-token']
    || harvested['x-indeed-csrf-token']
    || '';
  if (!csrf) {
    throw new Error('CSRF token not available — 请在 employers.indeed.com 任意候选人页操作一次(如点击候选人姓名),让扩展捕获 CSRF。');
  }

  const url = `https://employers.indeed.com/api/catws/resume/v2/download?id=${encodeURIComponent(legacyId)}&indeedcsrftoken=${encodeURIComponent(csrf)}`;

  const tab = await ensureSiteWorkerTab('indeed_employer');
  if (!tab || !tab.id) throw new Error('indeed_employer Worker Tab 不可用');

  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: async (downloadUrl, csrfTok, employerKey) => {
      try {
        const headers = {
          'Accept': 'application/json',
          'x-indeed-api': '1',
          'x-indeed-rpc': '1',
        };
        if (csrfTok) headers['csrf'] = csrfTok;
        if (employerKey) headers['indeed-employer-key'] = employerKey;

        const resp = await fetch(downloadUrl, {
          method: 'GET',
          credentials: 'include',
          headers,
        });
        if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };

        const contentType = resp.headers.get('Content-Type') || 'application/pdf';
        const buf = await resp.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let bin = '';
        for (let i = 0; i < bytes.byteLength; i++) bin += String.fromCharCode(bytes[i]);
        return { ok: true, base64: btoa(bin), contentType, size: bytes.byteLength };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },
    args: [url, csrf, tokens.employerKey],
  });

  const r = results?.[0]?.result;
  if (!r) throw new Error('Resume download executeScript returned empty');
  if (!r.ok) throw new Error(`Resume download failed: ${r.error}`);

  const mimeType = (r.contentType || 'application/pdf').split(';')[0].trim();
  return {
    base64_pdf: `data:${mimeType};base64,${r.base64}`,
    size: r.size,
    mimeType,
  };
}


async function apiIndeedEmployerUpdateCandidateStatus(legacyId, jobId, milestoneId) {
  // 从 legacyId 构造 base64 编码的 candidateSubmissionId
  const candidateSubmissionId = btoa(
    `iri://apis.indeed.com/CandidateSubmission/V1__CALLY__${legacyId}`
  );

  const resp = await indeedEmployerGraphQL(
    'UpdateCandidateStatus',
    GQL_UPDATE_CANDIDATE_STATUS,
    {
      statusInput: {
        move: {
          milestoneId,
          candidateSubmissionEmployerJobIdPairs: [
            { candidateSubmissionId, jobId },
          ],
        },
      },
    },
  );

  const result = resp?.data?.updateCandidateSubmissionMilestone?.candidateSubmissionMilestone;
  return {
    milestoneId: result?.milestoneId || milestoneId,
    category: result?.category || '',
    candidateSubmissionId,
  };
}


async function apiIndeedEmployerGetConversations(candidateKey) {
  const tokens = await indeedEmployerExtractTokens();
  const advertiserKey = tokens.employerKey;
  if (!advertiserKey) throw new Error('employerKey 不可用 — 请确认已登录 employers.indeed.com');

  const resp = await indeedEmployerGraphQL(
    'FindConversationsByCandidateKey',
    GQL_FIND_CONVERSATIONS_BY_CANDIDATE,
    {
      enableAdjacentContextSearch: true,
      advertiserKey,
      candidateKey,
    },
  );

  const conversations = resp?.data?.findConversations?.conversations || [];
  return {
    conversations: conversations.map(c => ({
      id: c.id,
      createdAt: c.creationDateTime,
    })),
    count: conversations.length,
  };
}


async function apiIndeedEmployerGetScreeningSummary(submissionUuid) {
  // 构造两个 base64 ID
  const candidateSubmissionId = btoa(
    `iri://apis.indeed.com/CandidateSubmission/${submissionUuid}`
  );
  const scoutApplicationId = btoa(
    `iri://apis.indeed.com/ScoutApplication/${submissionUuid}`
  );

  const resp = await indeedEmployerGraphQL(
    'Nex_SmartScreeningSummary_Applicant',
    GQL_SMART_SCREENING_SUMMARY,
    { scoutApplicationId, candidateSubmissionId },
  );

  const aiNode = resp?.data?.aiInterviewNode || {};
  const scoutNode = resp?.data?.scoutApplicationNode || {};
  const nexus = scoutNode?.nexusDetails || {};
  const screening = nexus?.smartScreening?.qualificationSummary?.qualifications || [];
  const identity = nexus?.identityVerificationReport || {};

  return {
    aiInterviews: (aiNode.aiInterviews || []).map(i => ({
      id: i.id,
      status: i.status,
      type: i.interviewType,
    })),
    identityVerification: {
      status: identity.inferredStatus || null,
      result: identity.verificationResult || null,
    },
    screeningQuestions: screening.map(q => ({
      question: q.question,
      answer: q.answer,
      dealbreaker: q.dealbreaker,
      qualified: q.qualified,
    })),
    isRescoring: nexus.isRescoring || false,
    atsSyncStatus: nexus.atsSyncStatus || null,
  };
}


async function apiIndeedEmployerGetInterviews(candidateSubmissionId) {
  const resp = await indeedEmployerGraphQL(
    'FindEmployerInterviews',
    GQL_FIND_EMPLOYER_INTERVIEWS,
    {
      input: {
        byCandidateSubmission: { candidateSubmissionId },
        byCoordinationType: 'ALL',
      },
    },
  );

  const interviews = resp?.data?.findEmployerInterviews?.employerInterviews || [];
  return {
    interviews: interviews.map(i => ({
      id: i.id,
      status: i.status,
      startTime: i.startTime,
      endTime: i.endTime,
      type: i.interviewType,
      location: i.location,
      notes: i.notes,
    })),
    count: interviews.length,
  };
}


async function apiIndeedEmployerGetMatchDetails(legacyId) {
  const resp = await indeedEmployerGraphQL(
    'getMatchExplanations',
    GQL_GET_MATCH_EXPLANATIONS,
    { legacyId },
  );

  const results = resp?.data?.candidateSubmissions?.results || [];
  if (!results.length) return { explanations: [] };

  const matchExps = results[0]?.data?.job?.matchExplanations?.requirementMatchExplanations || [];
  return {
    explanations: matchExps.map(e => ({
      matchType: e.matchType,
      requirementType: e.jobRequirementType,
      snippet: e.formatted?.snippet || '',
    })),
  };
}


async function apiIndeedEmployerSetCandidateFeedback(legacyId, sentiment) {
  const candidateSubmissionIds = btoa(
    `iri://apis.indeed.com/CandidateSubmission/V1__CALLY__${legacyId}`
  );

  const resp = await indeedEmployerGraphQL(
    'CreateEmployerCandidateSubmissionFeedback',
    GQL_CREATE_CANDIDATE_FEEDBACK,
    {
      createEmployerCandidateSubmissionFeedbackInput: {
        candidateSubmissionIds,
        sentiment,
      },
    },
  );

  const feedbacks = resp?.data?.createEmployerCandidateSubmissionFeedback?.feedback || [];
  const fb = feedbacks[0] || {};
  return {
    interestLevel: fb.interestLevel || sentiment,
    feedbackId: fb.id || '',
  };
}


async function apiIndeedEmployerGetScreeningAnswers(candidateId) {
  const tokens = await indeedEmployerExtractTokens();
  const harvested = _getHarvestedHeaders() || {};
  const csrf = tokens.csrf
    || harvested['x-csrf-token']
    || harvested['x-indeed-csrf-token']
    || '';
  if (!csrf) {
    throw new Error('CSRF token not available — 请在 employers.indeed.com 任一候选人详情页操作一次(如点击候选人卡片),让扩展捕获 CSRF。');
  }

  const url = `https://employers.indeed.com/api/v2/iq/job/answers?candidateIds=${encodeURIComponent(candidateId)}&indeedcsrftoken=${encodeURIComponent(csrf)}&indeedClientApplication=candeval/candeval-modules`;

  const tab = await ensureSiteWorkerTab('indeed_employer');
  if (!tab || !tab.id) throw new Error('indeed_employer Worker Tab 不可用');

  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: async (fetchUrl) => {
      try {
        const resp = await fetch(fetchUrl, {
          method: 'GET',
          credentials: 'include',
          headers: { 'Accept': 'application/json' },
        });
        if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
        return { ok: true, data: await resp.json() };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },
    args: [url],
  });

  const r = results?.[0]?.result;
  if (!r) throw new Error('Screening answers executeScript returned empty');
  if (!r.ok) throw new Error(`Screening answers failed: ${r.error}`);

  const payload = r.data?.data || {};
  const questions = payload.questions || [];
  const candidates = payload.candidates || [];
  const candidate = candidates.find(c => c.candidateId === candidateId) || candidates[0] || {};
  const answers = candidate.answers || [];

  // 将问题和答案匹配起来
  const qMap = {};
  for (const q of questions) {
    qMap[q.questionId] = {
      name: q.name,
      label: q.localizedTemplateLabel,
      question: q.jobseekerText,
      required: q.required,
      requirement: q.requirement?.label || '',
    };
  }

  return {
    totalRequirements: candidate.totalRequirements || 0,
    requirementsMet: candidate.requirementsMet || 0,
    screeningAnswers: answers.map(a => {
      const q = qMap[a.questionId] || {};
      const val = a.values?.[0] || {};
      return {
        category: q.name || '',
        question: q.question || '',
        requirement: q.requirement || '',
        answer: val.label || val.answer || '',
        met: a.requirementMet,
      };
    }),
  };
}


async function apiIndeedEmployerMarkCandidateViewed(submissionUuid) {
  const resp = await indeedEmployerGraphQL(
    'MarkCandidateSubmissionViewed',
    GQL_MARK_CANDIDATE_VIEWED,
    { markCandidateSubmissionViewedId: submissionUuid },
  );

  const sub = resp?.data?.markCandidateSubmissionViewed?.candidateSubmission;
  return { marked: !!sub, candidateSubmissionId: sub?.id || '' };
}


async function apiIndeedEmployerSendMessage(candidateKey, messageBody, options = {}) {
  const { conversationId = null, aggJobKey = null } = options;
  const tokens = await indeedEmployerExtractTokens();
  const advertiserKey = tokens.employerKey;
  if (!advertiserKey) throw new Error('employerKey 不可用 — 请确认已登录 employers.indeed.com');

  // 生成唯一 eventId
  const eventId = crypto.randomUUID();

  const variables = {
    attachments: [],
    clientName: 'messaging-react',
    eventId,
    messageBody,
    payload: [],
  };

  if (conversationId) {
    // 回复已有会话
    variables.conversationId = conversationId;
  } else {
    // 创建新会话
    if (!aggJobKey) throw new Error('新会话需要 aggJobKey');
    variables.context = {
      context: 'HQM_DRADIS',
      scope: {
        preOrPostApply: {
          advertiserKey,
          aggJobKey,
          candidateKey,
        },
      },
    };
  }

  const resp = await indeedEmployerGraphQL(
    'SendConversationEvent',
    GQL_SEND_CONVERSATION_EVENT,
    variables,
  );

  const result = resp?.data?.sendConversationEvent || {};
  const event = result.event || {};
  return {
    conversationId: result.conversationId || '',
    eventId: event.id || eventId,
    messageBody: event.messageBody || messageBody,
    sentAt: event.publicationDateTime || '',
  };
}


async function apiIndeedEmployerGetConversationMessages(conversationId, limit = 50) {
  const resp = await indeedEmployerGraphQL(
    'GetConversationAndEvents',
    GQL_GET_CONVERSATION_AND_EVENTS,
    { conversationId, last: limit },
  );

  const conv = resp?.data?.conversation || {};
  const edges = conv.eventsConnection?.edges || [];
  const scope = conv.scope || [];

  // 从 scope 提取关键 ID
  const scopeMap = {};
  for (const s of scope) scopeMap[s.key] = s.value;

  return {
    conversationId: conv.id || conversationId,
    context: conv.context || '',
    createdAt: conv.creationDateTime || '',
    candidateKey: scopeMap.CANDIDATE_KEY || '',
    jobKey: scopeMap.JOB_KEY || '',
    messages: edges.map(e => {
      const n = e.node || {};
      return {
        id: n.id,
        type: n.type,
        body: n.messageBody || '',
        preview: n.messagePreview || '',
        sentAt: n.publicationDateTime || '',
        authorRole: n.author?.role || '',
        subType: n.subType || '',
      };
    }),
    hasMore: conv.eventsConnection?.pageInfo?.hasPreviousPage || false,
  };
}


async function apiIndeedEmployerGetMessageTemplates(limit = 20) {
  const resp = await indeedEmployerGraphQL(
    'FetchTemplates',
    GQL_FETCH_TEMPLATES,
    { first: limit, input: { includeShared: true } },
  );

  const edges = resp?.data?.fetchMessageTemplates?.edges || [];
  return {
    templates: edges.map(e => {
      const n = e.node || {};
      return {
        id: n.id,
        title: n.title || '',
        body: n.body || '',
        labels: n.labels || [],
        isShared: n.isShared || false,
      };
    }),
    hasMore: resp?.data?.fetchMessageTemplates?.pageInfo?.hasNextPage || false,
  };
}


// ── Indeed Resume Search (resumes.indeed.com) ──────────────────────────────

async function _getRecaptchaToken() {
  const tab = await ensureSiteWorkerTab('indeed_employer');
  if (!tab || !tab.id) throw new Error('indeed_employer Worker Tab 不可用');

  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: async (sitekey) => {
      try {
        // 如果 grecaptcha 未加载，动态加载
        if (typeof grecaptcha === 'undefined' || !grecaptcha?.enterprise?.execute) {
          await new Promise((resolve, reject) => {
            const s = document.createElement('script');
            s.src = `https://www.google.com/recaptcha/enterprise.js?render=${sitekey}`;
            s.onload = () => {
              // grecaptcha.enterprise.ready 可能需要等待
              const wait = () => {
                if (typeof grecaptcha !== 'undefined' && grecaptcha?.enterprise?.execute) resolve();
                else setTimeout(wait, 100);
              };
              wait();
            };
            s.onerror = () => reject(new Error('Failed to load reCAPTCHA'));
            document.head.appendChild(s);
          });
        }
        const token = await grecaptcha.enterprise.execute(sitekey, { action: 'smart-sourcing-search' });
        return { ok: true, token };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },
    args: ['6Lc9gCUsAAAAADS7USRntozv1GCc6cabzeKDsYjJ'],
  });

  const r = results?.[0]?.result;
  if (!r?.ok) return ''; // 返回空字符串，让搜索尝试无 captcha
  return r.token;
}


async function apiIndeedEmployerSearchResumes(query, location = '', options = {}) {
  const {
    employerJobId = '',
    filters = [],
    offset = 0,
  } = options;

  // 尝试获取 reCAPTCHA token（可选，搜索可能无需）
  let captchaToken = '';
  try {
    captchaToken = await _getRecaptchaToken();
  } catch (_) { /* ignore */ }

  const input = {
    searchCriteria: {
      queryString: query,
      isNaturalLanguageSearch: false,
    },
    surfaceName: 'SOURCING_OMNI',
    location: location || '',
    filters: {
      radius: { distance: '25' },
      refinements: filters,
    },
    pagination: [{ type: 'ORIGINAL', offset }],
  };

  if (employerJobId) {
    input.jobIdentifiers = { employerJobId };
  }
  if (captchaToken) {
    input.captcha = {
      from: 'smart-sourcing-search',
      sitekey: '6Lc9gCUsAAAAADS7USRntozv1GCc6cabzeKDsYjJ',
      token: captchaToken,
    };
  }

  let resp;
  try {
    resp = await indeedEmployerGraphQL(
      'SmartSourcingSearch',
      GQL_SMART_SOURCING_SEARCH,
      { input },
    );
  } catch (e) {
    if (String(e?.message || e).includes('HTTP 400')) {
      throw new Error(
        'Smart Sourcing 简历库搜索被 Indeed 拒绝(HTTP 400)。这通常是 query 体未在 Indeed persisted query 白名单中。' +
        '建议:① 在 employers.indeed.com 上手动搜一次简历库(任意关键词),让扩展抓到真实请求格式;' +
        '② 或改用 find_applicants 看已申请岗位的候选人(此路径已确认可用)。'
      );
    }
    throw e;
  }

  const result = resp?.data?.findSourcingMatches?.originalRcpResult || {};
  const matches = result?.matchConnection?.matches || [];

  return {
    // Smart Sourcing 会话级 token —— 后续 log_candidate_seen 必须带 rcpRequestId,
    // searchSessionId 跨多次搜索维持会话连续性(供 chains/indeed_employer.js 落 store)。
    rcpRequestId: result.rcpRequestId || '',
    searchSessionId: result.searchSessionId || '',
    totalCount: result.overallMatchCount || 0,
    hasNextPage: result?.matchConnection?.pageInfo?.hasNextPage || false,
    candidates: matches.map(m => {
      const sp = m.sourcingProfile || {};
      const pc = sp.profileCard || {};
      const engagement = sp.engagementSummary || {};

      return {
        accountKey: sp.accountKey || pc.accountKey,
        name: [pc.firstName, pc.lastName].filter(Boolean).join(' '),
        location: pc.location?.localizedValue || '',
        resumeType: pc.resumeType || '',
        isFreeToContact: pc.isFreeToContact || false,
        lastActivity: pc.activityData?.lastActivityTimestamp || null,
        dateModified: pc.dateModified || null,
        education: (pc.educations || []).map(e => ({
          school: e.school,
          degree: e.degree,
        })),
        experience: (pc.experiences || []).map(e => ({
          title: e.title,
          company: e.company,
          from: e.fromDate,
          to: e.toDate,
        })),
        skills: (pc.skills || []).map(s => s.text),
        credentials: (pc.credentials || []).map(c => c.title),
        highlights: (m.highlights || []).map(h => h.text),
        engagementPermissions: (engagement.permissions || []).map(p => ({
          action: p.action,
          status: p.status,
        })),
      };
    }),
    refinements: (result.refinementBuckets || []).map(b => ({
      key: b.key,
      label: b.label,
      items: (b.items || []).map(i => ({
        label: i.label,
        value: i.value,
        count: i.count,
      })),
    })),
  };
}


async function apiIndeedEmployerGetTalentEngagement(candidateId) {
  const resp = await indeedEmployerGraphQL(
    'TalentEngagementSummary',
    GQL_TALENT_ENGAGEMENT_SUMMARY,
    { input: { candidateId } },
  );

  const engagements = resp?.data?.talentEngagementSummary?.engagements || [];
  return {
    engagements: engagements.map(e => ({
      type: e.type,
      created: e.created,
      senderEmail: e.senderEmail || '',
      status: e.status,
      statusHistory: (e.statusHistory || []).map(h => ({
        status: h.status,
        date: h.modifiedDate,
      })),
    })),
    hasEngaged: engagements.length > 0,
  };
}


// 反作弊曝光埋点。建议由 agent_loop 在 search_resumes 返回 matches[] 后**自动 fire**,
// 不让 LLM 主动决策(每次搜索都该调,LLM 容易漏掉)。
//
// 入参:
//   candidate_ids: [encryptedJobseekerAccountId, ...] —— 取自 search_resumes
//                  返回的 candidates[].accountKey
//   rcp_request_id: 取自 search_resumes 返回的 rcpRequestId(同一搜索批次)
//   surface: 默认 "sourcing-search"(简历库主搜索),可选 "candidate-detail"
//   grps: 可选 A/B 实验分组数组(从 search 响应里抓的话更精确,这里默认空)
async function apiIndeedEmployerLogCandidateSeen(candidateIds, rcpRequestId, surface = 'sourcing-search', grps = []) {
  if (!Array.isArray(candidateIds) || candidateIds.length === 0) {
    throw new Error('candidate_ids 不能为空');
  }
  if (!rcpRequestId) {
    throw new Error('rcp_request_id 必填(取自最近一次 search_resumes 返回)');
  }

  const input = {
    feedbackContexts: candidateIds.map(id => ({
      encryptedJobseekerAccountId: id,
      rcpRequestId,
    })),
    outreachEligibility: { outreachEligible: false },
    surfaceContext: {
      timestampAtSurface: Date.now(),
      grps: Array.isArray(grps) ? grps : [],
      surface,
    },
  };

  const resp = await indeedEmployerGraphQL(
    'LogRcpCandidateSeen',
    GQL_LOG_RCP_CANDIDATE_SEEN,
    { input },
  );
  const results = resp?.data?.logRcpCandidateSeen?.candidateResults || [];
  return {
    logged: results.length,
    candidates: results.map(r => ({
      candidate_id: r.encryptedJobseekerAccountId,
      event_key: r.eventKey,
      match_id: r.matchId,
    })),
  };
}


// ── Spec 5.3 / 5.4 / 5.5 业务 API 封装 ──────────────────────────────────


// spec 5.3 「分析候选人」按钮 — 取候选人 vs 岗位的匹配可解释性
async function apiIndeedEmployerGetMatchProfile(jobId) {
  if (!jobId) throw new Error('jobId 必填(EmployerJob iri 或 legacyId)');
  let resp;
  try {
    resp = await indeedEmployerGraphQL('MatchProfile', GQL_MATCH_PROFILE, { jobId });
  } catch (e) {
    if (String(e?.message || e).includes('HTTP 400')) {
      throw new Error(
        'MatchProfile 被 Indeed 拒绝(HTTP 400),query 体未在 persisted query 白名单中。' +
        '建议:在 employers.indeed.com 候选人详情页点击"Why we matched"区域一次,让扩展抓到真实请求格式。'
      );
    }
    throw e;
  }
  const m = resp?.data?.getMatchingProfile || {};
  return {
    job_id: m.id || jobId,
    fit_qualities: (m.userFitQualities || []).map(q => ({
      id: q.id, raw_value: q.rawValue, state: q.state,
    })),
  };
}


// spec 5.3 「查看简历」+ 候选人 submission 完整详情
// input 用 FindCandidateSubmissionsInput 形态(见 GQL_LEGACY_FIND_CANDIDATE_SUBMISSION)
async function apiIndeedEmployerGetCandidateSubmission(submissionId, options = {}) {
  if (!submissionId) throw new Error('submission_id 必填');
  const { first = 1 } = options;
  // FindCandidateSubmissions 通过 input.identifiers 指定具体 submission
  const input = {
    identifiers: { submissionIds: [submissionId] },
  };
  const resp = await indeedEmployerGraphQL(
    'LegacyFindCandidateSubmission', GQL_LEGACY_FIND_CANDIDATE_SUBMISSION,
    { input, first },
  );
  const submissions = resp?.data?.findCandidateSubmissions?.candidateSubmissions || [];
  return {
    submissions: submissions.map(s => {
      const d = s.data || {};
      const prof = d.profile || {};
      const job = (d.jobs?.jobs || [])[0] || {};
      const jd = job.jobData || {};
      return {
        id: s.id,
        legacy_id: d.legacyID || '',
        submission_uuid: d.submissionUuid || '',
        created: d.created || null,
        candidate_name: prof.name?.displayName || '',
        candidate_phone: prof.contact?.phoneNumber || '',
        milestone: d.milestone?.milestone?.category || '',
        feedback: (d.feedback?.feedback || []).map(f => ({
          id: f.id, interest_level: f.interestLevel,
        })),
        job: {
          id: jd.id || '',
          legacy_id: jd.legacyId || '',
          job_key: jd.jobKey || '',
          title: jd.title || '',
          location: jd.location?.formatted?.short || '',
          public_url: jd.externalJobPageUrl || (jd.advertisingLocations?.[0]?.publicJobPageUrl) || '',
        },
      };
    }),
    total: submissions.length,
  };
}


// spec 5.4 申请人列表(per-job RCP 匹配)
// 真实抓包变量复杂(showGuidedNudgeV2 / showPremiumFeatures / proctorGroups 等),
// 这里用最小可用 input 集合,字段够 spec 5.4 渲染。
async function apiIndeedEmployerFindApplicants(employerJobId, options = {}) {
  if (!employerJobId) throw new Error('employer_job_id 必填(IRI 形态,从 list_jobs 取 employerJob.id)');
  const {
    dispositions = ['NEW', 'PENDING', 'PHONE_SCREENED', 'INTERVIEWED', 'OFFER_MADE', 'REVIEWED'],
    sortBy = 'APPLY_DATE',     // 'APPLY_DATE' / 'MATCH_SCORE'
    sortOrder = 'DESCENDING',  // 'DESCENDING' / 'ASCENDING'
    createdAfterMs = 0,
    limit = 20,
  } = options;

  const surfaceContext = [
    ...dispositions.map(d => ({ contextKey: 'DISPOSITION', contextPayload: d })),
    { contextKey: 'SORT_BY', contextPayload: sortBy },
    { contextKey: 'SORT_ORDER', contextPayload: sortOrder },
  ];
  if (createdAfterMs > 0) {
    surfaceContext.push({ contextKey: 'CREATEDAFTER', contextPayload: String(createdAfterMs) });
  }

  const input = {
    clientSurfaceName: 'candidate-list-page',
    limit,
    context: { surfaceContext },
    identifiers: { jobIdentifiers: { employerJobId } },
  };

  // V2 query body 不在 Indeed persisted query 白名单中(实测 400)。
  // 自动 fallback 到 V1(GQL_FIND_RCP_MATCHES,与 list_applicants 同 op)
  // 它走的是 OrchestrationMatchesInput 形态,Indeed 已确认接受。
  let resp;
  try {
    resp = await indeedEmployerGraphQL('FindRCPMatches', GQL_FIND_RCP_MATCHES_V2, { input });
  } catch (e) {
    if (!String(e?.message || e).includes('HTTP 400')) throw e;
    // V1 fallback — 复用 apiIndeedEmployerSearchCandidates 的 input 形态
    const v1 = await apiIndeedEmployerSearchCandidates(employerJobId, {
      dispositions, sortBy, sortOrder, limit,
    });
    return {
      rcpRequestId: '',
      searchSessionId: '',
      totalCount: v1.totalCount || 0,
      totalCountWithoutLimit: v1.totalCount || 0,
      _fallback: 'v1_search_candidates',  // 让 backend 知道走了 fallback
      applicants: (v1.candidates || []).map(c => ({
        match_id: '',
        match_type: '',
        submission_id: '',
        legacy_id: c.legacyId || '',
        submission_uuid: c.submissionUuid || '',
        candidate_name: c.name || '',
        milestone: c.milestone || '',
        created: c.appliedAt || null,
      })),
    };
  }
  const m = resp?.data?.findRCPMatches || {};
  const matches = m?.matchConnection?.matches || [];

  // 落 token chain — RCP request id 用作后续 log_candidate_seen 闭环
  if (m.rcpRequestId) {
    tokenStore.setChainToken('indeed_employer', m.rcpRequestId, 'rcpRequestId', m.rcpRequestId);
  }
  if (m.searchSessionId) {
    tokenStore.setChainToken('indeed_employer', m.searchSessionId, 'searchSessionId', m.searchSessionId);
  }

  return {
    rcpRequestId: m.rcpRequestId || '',
    searchSessionId: m.searchSessionId || '',
    totalCount: m.overallMatchCount || 0,
    totalCountWithoutLimit: m.overallMatchCountWithoutLimit || 0,
    applicants: matches.map(x => {
      const cs = x.candidateSubmission || {};
      const cd = cs.data || {};
      const prof = cd.profile || {};
      return {
        match_id: x.matchId?.id || '',
        match_type: x.matchId?.type || '',
        submission_id: cs.id || '',
        legacy_id: cd.legacyID || '',
        submission_uuid: cd.submissionUuid || '',
        candidate_name: prof.name?.displayName || '',
        milestone: cd.milestone?.milestone?.category || '',
        created: cd.created || null,
      };
    }),
  };
}


// spec 5.4 申请人 facet — 提供 disposition / status / sentiment 的 count 分布
// 以及 spec 5.4 自动标记可疑前的辅助数据(如某 milestone 占比异常)
async function apiIndeedEmployerGetApplicantFilters(employerJobId) {
  if (!employerJobId) throw new Error('employer_job_id 必填');
  const baseInput = { identifiers: { jobIdentifiers: { employerJobId } } };
  const resp = await indeedEmployerGraphQL(
    'FindGroupedCandidateSubmissionFilterOptions',
    GQL_FIND_GROUPED_FILTER_OPTIONS,
    {
      filterDataInput: baseInput,
      statusDataInput: baseInput,
      shortlistCountInput: baseInput,
      undecidedCountInput: baseInput,
    },
  );
  const data = resp?.data || {};
  const filt = data.filterData?.filterOptions || {};
  const ms   = data.milestoneData?.filterOptions || {};
  return {
    locations: (filt.locations?.locations || []).map(l => ({
      value: l.value, count: l.count,
    })),
    sentiments: (filt.sentiments?.sentiments || []).map(s => ({
      value: s.value, count: s.count,
    })),
    milestones: (ms.hiringMilestones?.milestoneIds || []).map(m => ({
      value: m.value, count: m.count,
    })),
    shortlist_count: (data.shortlistCountData?.filterOptions?.sentiments?.sentiments || [])
      .filter(s => s.value === 'YES')
      .reduce((a, s) => a + (s.count || 0), 0),
    undecided_count: (data.undecidedCountData?.filterOptions?.sentiments?.sentiments || [])
      .filter(s => s.value === 'UNSET')
      .reduce((a, s) => a + (s.count || 0), 0),
  };
}


// spec 5.4 自动标记可疑 — Indeed 官方风险评估(替代 LLM 启发式)
// contexts: 数组,每项形如 { type: "CANDIDATE_SUBMISSION", id: "<submission_iri>" }
async function apiIndeedEmployerGetRiskAssessment(contexts) {
  if (!Array.isArray(contexts) || contexts.length === 0) {
    throw new Error('contexts 必填(数组,每项 {type, id})');
  }
  const resp = await indeedEmployerGraphQL(
    'getRiskAssessment', GQL_GET_RISK_ASSESSMENT, { contexts },
  );
  const r = resp?.data?.assessRiskForContexts || {};
  return {
    action: r.action || 'UNKNOWN',  // 'BLOCK' / 'ALLOW' / ...
    reason: r.reason || '',
    limit_info: r.limitInfo
      ? { limit: r.limitInfo.limit, remaining: r.limitInfo.remaining }
      : null,
  };
}


// spec 5.5 招聘端会话列表 v2(支持时间窗口过滤)
async function apiIndeedEmployerListConversationsV2(options = {}) {
  const {
    sinceMs = 0,        // 起始时间戳 ms,0 = 不限
    untilMs = 0,        // 结束时间戳 ms,0 = 不限
    employerJobId = '', // 过滤特定岗位
    limit = 20,
  } = options;

  const input = { limit };
  if (sinceMs > 0)  input.minDateTime = new Date(sinceMs).toISOString();
  if (untilMs > 0)  input.maxDateTime = new Date(untilMs).toISOString();
  if (employerJobId) {
    input.scope = [{ key: 'EMPLOYER_JOB_ID', value: employerJobId }];
  }

  const resp = await indeedEmployerGraphQL(
    'FindConversations', GQL_FIND_CONVERSATIONS_V2, { input },
  );
  const conv = resp?.data?.findConversations?.conversations || [];

  return {
    conversations: conv.map(c => {
      const scope = (c.scope || []).reduce((acc, s) => {
        acc[s.key] = s.value; return acc;
      }, {});
      const last = c.lastEvent || {};
      return {
        conversation_id: c.id,
        created: c.creationDateTime,
        context: c.context,
        candidate_key: scope.CANDIDATE_KEY || '',
        job_key: scope.JOB_KEY || '',
        advertiser_key: scope.ADVERTISER_KEY || '',
        last_message_ts: last.publicationDateTime || c.creationDateTime,
        last_message_preview: last.messagePreview || '',
        last_event_type: last.type || '',
        last_event_role: last.author?.role || '',
        participants: (c.participants || []).map(p => ({
          name: p.participantName, role: p.role, removed: p.removed,
        })),
      };
    }),
  };
}


// spec 5.5 单 conversation 完整 thread(回复时取上下文)
async function apiIndeedEmployerGetConversationThread(conversationId) {
  if (!conversationId) throw new Error('conversation_id 必填');
  const resp = await indeedEmployerGraphQL(
    'GetConversationAndEvents', GQL_GET_CONVERSATION_AND_EVENTS_V2,
    { conversationId },
  );
  const c = resp?.data?.conversation || {};
  const scope = (c.scope || []).reduce((acc, s) => {
    acc[s.key] = s.value; return acc;
  }, {});
  return {
    conversation_id: c.id,
    created: c.creationDateTime,
    context: c.context,
    candidate_key: scope.CANDIDATE_KEY || '',
    job_key: scope.JOB_KEY || '',
    last_event: c.lastEvent ? {
      id: c.lastEvent.id,
      author_role: c.lastEvent.author?.role,
      type: c.lastEvent.type,
      message_format: c.lastEvent.messageContentFormat,
      preview: c.lastEvent.messagePreview,
      published_at: c.lastEvent.publicationDateTime,
    } : null,
    participants: (c.participants || []).map(p => ({
      account_key: p.accountKey,
      name: p.participantName,
      role: p.role,
      access_level: p.accessLevel,
      removed: p.removed,
    })),
  };
}


async function apiIndeedEmployerSearchAutocomplete(query, type = 'keyword') {
  const tab = await ensureSiteWorkerTab('indeed_employer');
  if (!tab || !tab.id) throw new Error('indeed_employer Worker Tab 不可用');

  const endpoint = type === 'location'
    ? 'location'
    : 'rez-what';

  const url = `https://autocomplete.indeed.com/api/v0/suggestions/${endpoint}?country=US&language=en&count=10&formatted=1&query=${encodeURIComponent(query)}&useEachWord=false`;

  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: async (fetchUrl) => {
      try {
        const resp = await fetch(fetchUrl, {
          method: 'GET',
          credentials: 'include',
          headers: { 'Accept': 'application/json' },
        });
        if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
        return { ok: true, data: await resp.json() };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },
    args: [url],
  });

  const r = results?.[0]?.result;
  if (!r?.ok) throw new Error(`Autocomplete failed: ${r?.error || 'unknown'}`);

  return {
    suggestions: (r.data || []).map(s => s.suggestion || s),
    type,
    query,
  };
}


// ── Job Posting API ────────────────────────────────────────────────────────

async function apiIndeedEmployerListDraftJobs() {
  const resp = await indeedEmployerGraphQL(
    'findDraftJobPosts',
    GQL_FIND_DRAFT_JOB_POSTS,
    { input: {} },
  );

  const results = resp?.data?.findDraftJobPosts?.results || [];
  return {
    drafts: results.map(r => {
      const d = r.draftJobPost || {};
      return {
        draftJobId: d.id,
        formId: d.jobPostingForm?.id || '',
        title: d.title || '',
        description: d.description ? d.description.substring(0, 200) : '',
        dateCreated: d.dateCreated || '',
      };
    }),
  };
}


async function apiIndeedEmployerGetJobForm(draftJobId) {
  const resp = await indeedEmployerGraphQL(
    'FindJobPostingFormByDraftJobId',
    GQL_FIND_JOB_POSTING_FORM,
    {
      draftJobId,
      includeTitleField: true,
      includeLocationField: true,
      includeDescriptionField: true,
      includePayField: true,
      includeJobTypeField: true,
      includeBenefitsField: true,
      includeHiresNeededField: true,
      includeApplicationSettingsField: true,
      includeWorkingHoursField: false,
      includeJobLocaleField: true,
      includeJobCompanyNameField: false,
      includeOccupationField: false,
      includeAccommodationsContactInfoField: false,
      includeExpectedHireDateField: false,
    },
  );

  const form = resp?.data?.findJobPostingFormByDraftJobId || {};
  const d = form.data || {};

  return {
    formId: form.id || '',
    title: d.title?.data?.title || '',
    location: d.jobLocation?.data?.location || '',
    workLocationType: d.jobLocation?.data?.workLocationType || '',
    roleLocationType: d.jobLocation?.data?.roleLocationType || '',
    description: d.description?.data?.description || '',
    payMin: d.pay?.data?.minimumMinor || null,
    payMax: d.pay?.data?.maximumMinor || null,
    payPeriod: d.pay?.data?.period || '',
    payType: d.pay?.data?.type || '',
    jobTypes: d.jobType?.data?.types || [],
    benefits: d.benefits?.data?.benefits || [],
    hiresNeeded: d.hiresNeeded?.data?.hiresNeeded || null,
    requireResume: d.applicationSettings?.data?.requireResume || null,
    country: d.jobLocale?.data?.country || '',
    language: d.jobLocale?.data?.language || '',
  };
}


async function apiIndeedEmployerUpdateJobForm(formId, patch) {
  const resp = await indeedEmployerGraphQL(
    'PersistJobPostingForm',
    GQL_PERSIST_JOB_POSTING_FORM,
    { input: { id: formId, patch } },
  );

  const result = resp?.data?.JobPostingForm?.persist?.result || {};
  return {
    formId: result.id || formId,
    draftJobId: result.targetEntities?.[0]?.id || '',
  };
}


async function apiIndeedEmployerPublishJob(formId) {
  const resp = await indeedEmployerGraphQL(
    'PublishJobPostingForm',
    GQL_PUBLISH_JOB_POSTING_FORM,
    { input: { id: formId } },
  );

  const publish = resp?.data?.JobPostingForm?.publish || {};

  // Check for errors
  if (publish.errors) {
    throw new Error(`Publish failed: ${JSON.stringify(publish.errors)}`);
  }

  const ej = publish.employerJob || {};
  const jd = ej.jobData || {};
  return {
    employerJobId: ej.id || '',
    jobDataId: jd.id || '',
    legacyId: jd.legacyId || '',
    title: jd.title || '',
    status: jd.status || '',
  };
}


async function apiIndeedEmployerOptimizeJobDescription(draftJobId, title, language = 'en', country = 'US') {
  const resp = await indeedEmployerGraphQL(
    'DraftJobDescriptionOptimization',
    GQL_DRAFT_JOB_DESCRIPTION_OPTIMIZATION,
    {
      ids: [draftJobId],
      jobInputs: { title, language, country },
    },
  );

  const results = resp?.data?.draftJobPosts?.results || [];
  if (!results.length) return { description: '', recommendationId: '' };

  const opt = results[0]?.draftJobPost?.jobOptimization?.autoJobDescription?.jobDescriptionOptimization || {};
  return {
    description: opt.newParam?.jobDescription || '',
    recommendationId: opt.recommendationId || '',
  };
}
