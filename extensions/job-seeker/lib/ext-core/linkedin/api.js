/**
 * linkedin/api.js — LinkedIn Voyager API 封装
 *
 * 所有请求通过 executeSiteApi('linkedin', ...) 经 Worker Tab MAIN world 执行。
 * LinkedIn CSRF Token（Csrf-Token: ajax:XXXX）在 site-executor.js 的注入函数中
 * 自动从 Worker Tab 的 document.cookie 读取，无需外部传入。
 *
 * 依赖（全局）：executeSiteApi（core/site-executor.js）
 */

const LINKEDIN_ORIGIN = 'https://www.linkedin.com';

// queryId 缓存 key（chrome.storage.local）
const LINKEDIN_QUERY_IDS_KEY = 'linkedin_queryIds';
const QUERY_ID_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 天
// 当 API 返回 400 且缓存 queryId 年龄超过此阈值时，判定为 LinkedIn 前端部署
// 导致的提前失效（不等 7 天 TTL），立即清理并抛出明确错误。
const QUERY_ID_STALE_AFTER_400_MS = 24 * 60 * 60 * 1000; // 1 天

// ─── Fallback queryIds（首次冷启动 + bootstrap 失败时的兜底）─────────────
//
// LinkedIn voyager queryId 是 LinkedIn 前端 JS 编译时哈希,虽然每次 redeploy
// 会变,但实际生命周期通常以"数周"为单位。从抓包(capture_rec_17774285,
// 2026-04-29)固定的 hash 拿来当 fallback —— 在用户没浏览过 LinkedIn /
// 后台 worker tab 被 Chrome throttling 不发 GraphQL 等场景下,我们仍能
// 用这个 fallback 直接发请求(我们自己的 executeSiteApi 在 MAIN world 跑,
// 不受 background tab throttling 影响)。失败(400)时清理 + 走 bootstrap。
const LINKEDIN_FALLBACK_QUERY_IDS = {
  voyagerJobsDashJobPostings: 'voyagerJobsDashJobPostings.891aed7916d7453a37e4bbf5f1f60de4',
  voyagerJobsDashJobCards:    'voyagerJobsDashJobCards.e2f4bbd51b4b4ed8ad8cb2a3d8177989',
};

/**
 * 把 fallback queryId 写入 storage,后续 _getLinkedinQueryIdEntry 能命中。
 * capturedAt 设为"半个 TTL 之前",留个明显标记区分用户真实捕获 vs fallback。
 */
async function _seedFallbackQueryId(prefix) {
  const fallback = LINKEDIN_FALLBACK_QUERY_IDS[prefix];
  if (!fallback) return null;
  try {
    const stored = await chrome.storage.local.get(LINKEDIN_QUERY_IDS_KEY);
    const ids = stored[LINKEDIN_QUERY_IDS_KEY] || {};
    ids[fallback] = { capturedAt: Date.now(), source: 'fallback' };
    await chrome.storage.local.set({ [LINKEDIN_QUERY_IDS_KEY]: ids });
  } catch (_) {}
  return { queryId: fallback, key: fallback, capturedAt: Date.now() };
}

/**
 * 从 chrome.storage.local 读取已捕获的 queryId 条目（含元信息）。
 * @param {string} prefix - queryId 前缀，如 'voyagerSearchDashByAll'
 * @returns {Promise<{queryId: string, key: string, capturedAt: number}|null>}
 */
async function _getLinkedinQueryIdEntry(prefix) {
  try {
    const stored = await chrome.storage.local.get(LINKEDIN_QUERY_IDS_KEY);
    const ids = stored[LINKEDIN_QUERY_IDS_KEY] || {};
    // 清理超过 7 天的条目
    const now = Date.now();
    let changed = false;
    for (const [k, entry] of Object.entries(ids)) {
      if (now - (entry.capturedAt || 0) > QUERY_ID_TTL_MS) {
        delete ids[k];
        changed = true;
      }
    }
    if (changed) await chrome.storage.local.set({ [LINKEDIN_QUERY_IDS_KEY]: ids });

    // 按前缀匹配最新的 queryId
    const matching = Object.entries(ids)
      .filter(([k]) => k.startsWith(prefix))
      .sort((a, b) => (b[1].capturedAt || 0) - (a[1].capturedAt || 0));
    if (matching.length === 0) return null;
    const [key, entry] = matching[0];
    return { queryId: key, key, capturedAt: entry.capturedAt || 0 };
  } catch (_) {
    return null;
  }
}

/**
 * 兼容旧签名：返回 queryId 字符串。新代码应用 _getLinkedinQueryIdEntry。
 */
async function _getLinkedinQueryId(prefix) {
  const entry = await _getLinkedinQueryIdEntry(prefix);
  return entry ? entry.queryId : null;
}

/**
 * 轮询等待指定 prefix 的 queryId 出现（bootstrap 完成信号）。
 *
 * 用于自动 bootstrap 流程：先静默 navigate Worker Tab 到任意能触发该
 * GraphQL 的 LinkedIn 页面（如 /jobs/view/{id}/ 触发 voyagerJobsDashJobPostings），
 * 然后调用本函数等 interceptor_linkedin_main.js 把 queryId 写入 storage。
 *
 * 实现：500ms 轮询 chrome.storage.local，命中即返回 entry；超时返 null。
 */
async function _waitForLinkedinQueryId(prefix, timeoutMs = 8000, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const entry = await _getLinkedinQueryIdEntry(prefix);
    if (entry) return entry;
    await new Promise(r => setTimeout(r, intervalMs));
  }
  return null;
}

/**
 * 从 chrome.storage.local 中移除指定的 queryId 键（用于 400 失效清理）。
 */
async function _removeLinkedinQueryId(key) {
  try {
    const stored = await chrome.storage.local.get(LINKEDIN_QUERY_IDS_KEY);
    const ids = stored[LINKEDIN_QUERY_IDS_KEY] || {};
    if (key in ids) {
      delete ids[key];
      await chrome.storage.local.set({ [LINKEDIN_QUERY_IDS_KEY]: ids });
    }
  } catch (_) {}
}

/**
 * 当 Voyager API 抛 HTTP 400 且 queryId 年龄超过 1 天时，判定为 LinkedIn 前端
 * 部署导致的 queryId 失效，清理缓存并抛出明确提示；否则原样抛出原错误。
 *
 * @param {Error} err - executeSiteApi 抛出的原始错误
 * @param {{queryId, key, capturedAt}} queryEntry - 本次调用使用的 queryId 条目
 * @param {string} hint - 用户可执行的恢复步骤（如"请在浏览器访问 LinkedIn 搜索页"）
 */
async function _handleLinkedinVoyagerError(err, queryEntry, hint) {
  const msg = String(err && err.message || err);
  if (!msg.includes('HTTP 400')) throw err;
  if (!queryEntry || !queryEntry.capturedAt) throw err;

  const age = Date.now() - queryEntry.capturedAt;
  if (age < QUERY_ID_STALE_AFTER_400_MS) throw err;

  await _removeLinkedinQueryId(queryEntry.key);
  const ageHours = Math.round(age / 3600000);
  throw new Error(
    `LinkedIn queryId 已过期（年龄 ${ageHours}h，通常因 LinkedIn 前端更新导致）— ${hint}`
  );
}

/**
 * Voyager GraphQL 调用统一模板：
 *   1. 取 queryId entry（含 capturedAt），未捕获时抛明确错误
 *   2. 调用 fn(queryId) 执行实际请求
 *   3. 捕到 HTTP 400 + queryId 年龄 > 1 天 → 清理 + 抛"已过期"错误
 *      否则原样抛底层错误
 *
 * 三平台共用 Voyager queryId 失效风险（LinkedIn 前端部署后旧 queryId 400），
 * 本 helper 把 A3 的保护扩展到所有带 queryId 的 Voyager 端点，避免单独
 * 为每个 API 重复 try/catch 样板。
 *
 * @param {string} prefix - queryId 前缀（如 'voyagerSearchDashByAll'）
 * @param {string} captureHint - 用户没捕获 queryId / 已过期时的恢复指引文案
 * @param {(queryId: string) => Promise<any>} fn - 实际 API 调用函数
 */
async function _callVoyagerGuarded(prefix, captureHint, fn) {
  let entry = await _getLinkedinQueryIdEntry(prefix);
  let usedFallback = false;

  // 用户从未浏览过对应页面 → 用硬编码 fallback queryId 直接试。这条路径
  // 救活了"用户没在浏览器手动点过 LinkedIn 职位"的场景:我们自己的 fetch
  // 经 executeSiteApi 在 MAIN world 跑,不受 background tab throttling 影响,
  // 拿到 200 即写入 storage 自愈;拿到 400 则清理 fallback 走真正的 bootstrap。
  if (!entry) {
    entry = await _seedFallbackQueryId(prefix);
    if (!entry) {
      throw new Error(
        `LinkedIn queryId 尚未捕获（prefix=${prefix}）— ${captureHint}`,
      );
    }
    usedFallback = true;
  }

  try {
    return await fn(entry.queryId);
  } catch (err) {
    if (usedFallback) {
      // fallback 也 400/失败 → 立即清掉,让上层走 bootstrap 路径(active tab
      // 加载 + 等用户真实 queryId 落盘)。**不**调 _handleLinkedinVoyagerError
      // 那条以"年龄 > 1 天"为前提的 stale 判定,因为 fallback 是刚 seed 的。
      const msg = String(err && err.message || err);
      if (msg.includes('HTTP 400')) {
        await _removeLinkedinQueryId(entry.key);
        throw new Error(
          `LinkedIn queryId fallback 已过时(LinkedIn 可能 redeploy 了)— ${captureHint}`,
        );
      }
      throw err;
    }
    await _handleLinkedinVoyagerError(err, entry, captureHint);
    throw err;  // 不该到达（_handleLinkedinVoyagerError 会抛）
  }
}

/**
 * 搜索人才（Voyager GraphQL）
 * 需要真实 queryId（由 interceptor_linkedin_main.js 从用户真实 LinkedIn 请求中提取）。
 * 若 queryId 未捕获，抛出明确错误提示用户先浏览一次 LinkedIn 搜索页。
 * @param {string} keywords
 * @param {number} start  - 分页偏移
 * @param {number} count  - 每页数量
 */
async function apiLinkedinSearchPeople(keywords, start = 0, count = 10) {
  return _callVoyagerGuarded(
    'voyagerSearchDashByAll',
    // 错误提示里直接带 markdown 链接，agent 原文透传后前端点一下就能打开 LinkedIn
    // 搜索页触发 queryId 捕获。
    '请[👉 打开 LinkedIn 搜索页（新标签页）](https://www.linkedin.com/search/results/people/?keywords=AI%20researcher)随便搜一次，扩展会自动捕获 queryId；捕获后回来重试即可。',
    (queryId) => {
      const variables = encodeURIComponent(JSON.stringify({
        start,
        count,
        origin: 'GLOBAL_SEARCH_HEADER',
        query: {
          keywords,
          flagshipSearchIntent: 'SEARCH_SRP',
          queryParameters: { resultType: [{ value: ['PEOPLE'] }] },
        },
      }));
      return executeSiteApi('linkedin',
        `${LINKEDIN_ORIGIN}/voyager/api/graphql?variables=${variables}&queryId=${queryId}`,
        { method: 'GET' });
    },
  );
}

/**
 * 获取成员个人资料
 * @param {string} publicId - LinkedIn 公开用户名（URL 中的 /in/{publicId}）
 */
async function apiLinkedinGetProfile(publicId) {
  return executeSiteApi('linkedin',
    `${LINKEDIN_ORIGIN}/voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity=${encodeURIComponent(publicId)}&decorationId=com.linkedin.voyager.dash.deco.identity.profile.WebProfilePage-148`,
    { method: 'GET' });
}

/**
 * 获取当前登录用户信息（用于 check_login 识别 memberId/name）。
 * Voyager /me 端点返回当前会话用户的 miniProfile，不需要任何参数。
 */
async function apiLinkedinGetMe() {
  return executeSiteApi('linkedin',
    `${LINKEDIN_ORIGIN}/voyager/api/me`,
    { method: 'GET' });
}

/**
 * 发送连接请求
 * @param {string} profileUrn  - URN，如 urn:li:fsd_profile:ACoAAA...
 * @param {string} trackingId  - 搜索结果中的 trackingId
 * @param {string} message     - 附言（可选）
 */
async function apiLinkedinConnect(profileUrn, trackingId, message = '') {
  const body = {
    invitee: { inviteeUnion: { memberProfile: { profileUrn } } },
    trackingId,
  };
  if (message) body.message = message;
  return executeSiteApi('linkedin',
    `${LINKEDIN_ORIGIN}/voyager/api/growth/normInvitations`,
    { method: 'POST', body: JSON.stringify(body) });
}

/**
 * 发送站内消息
 * @param {string} memberUrn - 接收方 memberUrn
 * @param {string} body      - 消息正文
 * @param {string} subject   - 主题（InMail 时使用）
 */
async function apiLinkedinSendMessage(memberUrn, body, subject = '') {
  return executeSiteApi('linkedin',
    `${LINKEDIN_ORIGIN}/voyager/api/messaging/conversations`,
    {
      method: 'POST',
      body: JSON.stringify({
        recipients: [{ memberUrn }],
        subject,
        body,
        messageType: 'MEMBER_TO_MEMBER',
      }),
    });
}

/**
 * 获取与目标成员的连接程度（1st/2nd/3rd/None）
 * @param {string} publicId - LinkedIn 公开用户名
 */
async function apiLinkedinGetConnectionDegree(publicId) {
  return executeSiteApi('linkedin',
    `${LINKEDIN_ORIGIN}/voyager/api/identity/profiles/${encodeURIComponent(publicId)}/networkinfo`,
    { method: 'GET' });
}

/**
 * 搜索职位（voyager-web 当前版 REST finder）。
 * 对应 LinkedIn 网页端 /jobs/search 页实际调用：
 *   GET /voyager/api/voyagerJobsDashJobCards?q=jobSearch&query=(...)
 *   Accept: application/vnd.linkedin.normalized+json+2.1
 *
 * @param {string} keywords         搜索关键词
 * @param {string} geoLocationId    数字 geoUrn id（如 '103644278' 美国、'102890883' 中国），空字符串则不加地域过滤
 * @param {number} start            分页偏移
 * @param {number} count            每页条数（LinkedIn 上限约 25）
 */
async function apiLinkedinSearchJobs(keywords, geoLocationId = '', start = 0, count = 25) {
  const locations = geoLocationId
    ? `,locations:List(${encodeURIComponent(geoLocationId)})`
    : '';
  // RestLi 记录：整个 (...) 结构作为 query= 的值，括号与逗号保留原样，keywords 需 encode
  const query = `(origin:JOB_SEARCH_PAGE_QUERY_EXPANSION`
    + `,keywords:${encodeURIComponent(keywords)}`
    + locations
    + `,spellCorrectionEnabled:true)`;
  const url = `${LINKEDIN_ORIGIN}/voyager/api/voyagerJobsDashJobCards`
    + `?decorationId=com.linkedin.voyager.dash.deco.jobs.search.JobSearchCardsCollectionLite-88`
    + `&count=${count}`
    + `&q=jobSearch`
    + `&query=${query}`
    + `&servedEventEnabled=false`
    + `&start=${start}`;
  return executeSiteApi('linkedin', url, {
    method: 'GET',
    // normalized+json+2.1 让响应自动带 included 展开；默认 application/json 会拿到旧扁平结构
    headers: { Accept: 'application/vnd.linkedin.normalized+json+2.1' },
  });
}

/**
 * 拉单条职位详情（含 JD 正文 / 公司 / 地点 / 雇佣类型 / 行业 / workplace types）。
 *
 * 抓包验证（capture_rec_17774285）：voyagerJobsDashJobPostings GraphQL 用
 *   variables=(jobPostingUrn:urn:li:fsd_jobPosting:{id}) 即可拿全。响应里
 *   `included[]` 含一条 $type=com.linkedin.voyager.dash.jobs.JobPosting 的对象,
 *   其中 description.text 就是 JD 正文 —— search 列表只有 title/company/city,
 *   分析职位匹配度必须靠这条端点补 description。
 *
 * queryId 取自 _LINKEDIN_QUERY_PREFIX —— 用户首次打开任意 LinkedIn 职位详情页
 * 即由 interceptor_linkedin_main.js 拦截写入 chrome.storage.local。
 */
async function apiLinkedinGetJobDetail(jobId) {
  return _callVoyagerGuarded(
    'voyagerJobsDashJobPostings',
    '请[👉 打开任意 LinkedIn 职位详情页（新标签页）](https://www.linkedin.com/jobs/)随便点开一条职位，扩展会自动捕获 queryId；捕获后回来重试即可。',
    (queryId) => {
      // 抓包对账(capture_rec_17774285):URN 里的冒号必须 URL-encode 成 %3A,
      // 整体 wrapper 括号保留原样。早期版本只 encodeURIComponent(jobId),
      // colons 直传给 LinkedIn 严格解析后返 HTTP 400 → 误判 fallback queryId
      // 已过时,把好 queryId 清掉了。
      const urn = `urn:li:fsd_jobPosting:${jobId}`;
      const variables = `(jobPostingUrn:${encodeURIComponent(urn)})`;
      return executeSiteApi('linkedin',
        `${LINKEDIN_ORIGIN}/voyager/api/graphql?includeWebMetadata=true&variables=${variables}&queryId=${queryId}`,
        {
          method: 'GET',
          // normalized+json+2.1 让响应自动展开 `included[]`,我们靠它取 JobPosting / Geo / Company
          headers: { Accept: 'application/vnd.linkedin.normalized+json+2.1' },
        });
    },
  );
}

// ══════════════════════════════════════════════════════════════════════════
// Messaging (新 Messenger 框架，区别于老 /messaging/conversations REST)
// ══════════════════════════════════════════════════════════════════════════

/**
 * 列出消息会话（Messenger GraphQL，按 mailbox 分组 + sync token 增量拉取）。
 * @param {string} mailboxUrn - 用户自己的 fsd_profile URN（个人收件箱）或 Recruiter mailbox URN
 * @param {string} syncToken  - 增量同步 token（首次传 ''；从上次响应 metadata.newSyncToken 取）
 * @param {number} count      - 每页数量，默认 20
 */
async function apiLinkedinListConversations(mailboxUrn, syncToken = '', count = 20) {
  return _callVoyagerGuarded(
    'messengerConversations',
    '请[👉 打开 LinkedIn 消息页（新标签页）](https://www.linkedin.com/messaging/)一次，扩展会自动捕获 queryId；捕获后回来重试即可。',
    (queryId) => {
      const params = [
        `mailboxUrn:${mailboxUrn}`,
        syncToken ? `syncToken:${encodeURIComponent(syncToken)}` : '',
        `count:${count}`,
      ].filter(Boolean).join(',');
      const variables = `(${params})`;
      return executeSiteApi('linkedin',
        `${LINKEDIN_ORIGIN}/voyager/api/voyagerMessagingGraphQL/graphql?variables=${variables}&queryId=${queryId}`,
        { method: 'GET' });
    },
  );
}

/**
 * 获取某会话的历史消息（Messenger GraphQL）。
 * @param {string} conversationUrn - urn:li:msg_conversation:(...)
 * @param {number} count           - 历史条数，默认 20
 */
async function apiLinkedinGetConversationMessages(conversationUrn, count = 20) {
  return _callVoyagerGuarded(
    'messengerMessages',
    '请[👉 打开 LinkedIn 消息页（新标签页）](https://www.linkedin.com/messaging/)随便点开一个会话，扩展会自动捕获 queryId；捕获后回来重试即可。',
    (queryId) => {
      const variables = `(criteria:List((conversationUrn:${conversationUrn},count:${count})))`;
      return executeSiteApi('linkedin',
        `${LINKEDIN_ORIGIN}/voyager/api/voyagerMessagingGraphQL/graphql?variables=${variables}&queryId=${queryId}`,
        { method: 'GET' });
    },
  );
}

/**
 * 列出用户所有 mailbox（普通 + Recruiter + Page admin）。
 * 返回的 elements[].mailbox.recruiter / .primary / .pageMailbox 区分类型。
 */
async function apiLinkedinListMailboxes() {
  return _callVoyagerGuarded(
    'voyagerMessagingDashAffiliatedMailboxes',
    '请[👉 打开 LinkedIn 消息页（新标签页）](https://www.linkedin.com/messaging/)一次，扩展会自动捕获 queryId；捕获后回来重试即可。',
    (queryId) => executeSiteApi('linkedin',
      `${LINKEDIN_ORIGIN}/voyager/api/graphql?queryId=${queryId}`,
      { method: 'GET' }),
  );
}

/**
 * 标记所有消息为已读（清除消息红点）。
 * @param {number} untilMs - 时间戳上限（毫秒），只标该时间之前的消息；默认当前时间
 */
async function apiLinkedinMarkAllMessagesAsSeen(untilMs = 0) {
  const until = untilMs > 0 ? untilMs : Date.now();
  return executeSiteApi('linkedin',
    `${LINKEDIN_ORIGIN}/voyager/api/voyagerMessagingDashMessagingBadge?action=markAllMessagesAsSeen`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ until }),
    });
}

/**
 * 在已有会话里回复消息（新 Messenger 框架）。
 * 区别于 apiLinkedinSendMessage（后者 POST /messaging/conversations，用于**发起新 InMail**）。
 * @param {string} conversationUrn - 目标会话 URN（urn:li:msg_conversation:(...)）
 * @param {string} mailboxUrn      - 发送方 mailbox URN（用户自己的 fsd_profile URN）
 * @param {string} text            - 消息文本
 * @param {string} originToken     - 幂等键，建议 crypto.randomUUID()
 */
async function apiLinkedinReplyToConversation(conversationUrn, mailboxUrn, text, originToken) {
  const token = originToken || (globalThis.crypto?.randomUUID?.() || String(Date.now()));
  const body = {
    message: {
      body: { attributes: [], text },
      renderContentUnions: [],
      conversationUrn,
      originToken: token,
    },
    mailboxUrn,
    trackingId: '',
    dedupeByClientGeneratedToken: false,
  };
  return executeSiteApi('linkedin',
    `${LINKEDIN_ORIGIN}/voyager/api/voyagerMessagingDashMessengerMessages?action=createMessage`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
}

// ══════════════════════════════════════════════════════════════════════════
// 2026-04-29 抓包新增:对齐 Boss "查看最近消息" 子流程
// queryId hashes 来自 capture_rec_17773943.json
// ══════════════════════════════════════════════════════════════════════════

/**
 * 取主收件箱的分类未读计数。映射 Boss `geek_message_center_summary`。
 * @param {string} mailboxUrn - 用户自己的 fsd_profile URN(base64 形态)
 * @returns 响应里 elements[].{ category, unreadConversationCount }
 *   category 枚举:PRIMARY_INBOX / SECONDARY_INBOX / JOB
 */
async function apiLinkedinGetMailboxCounts(mailboxUrn) {
  if (!mailboxUrn) throw new Error('mailboxUrn 必填');
  return _callVoyagerGuarded(
    'messengerMailboxCounts',
    '请[👉 打开 LinkedIn 消息页(新标签页)](https://www.linkedin.com/messaging/)一次,扩展会自动捕获 queryId;捕获后回来重试即可。',
    (queryId) => {
      const variables = `(mailboxUrn:${mailboxUrn})`;
      return executeSiteApi('linkedin',
        `${LINKEDIN_ORIGIN}/voyager/api/voyagerMessagingGraphQL/graphql?queryId=${queryId}&variables=${encodeURIComponent(variables)}`,
        { method: 'GET' });
    },
  );
}

/**
 * 按筛选条件列会话(LinkedIn search criteria 路径)。映射 Boss
 * `geek_filter_by_label`。
 *
 * 该 queryId(`messengerConversations.737b...`)是 LinkedIn 网页端
 * 实际用于"标签过滤"的查询。区别于 `apiLinkedinListConversations`(走
 * sync token 增量)。
 *
 * @param {string} mailboxUrn - 必填
 * @param {object} opts
 * @param {string[]} opts.categories - 默认 ['PRIMARY_INBOX']。可选值
 *   PRIMARY_INBOX / SECONDARY_INBOX / JOB
 * @param {number} opts.count - 每页数量,默认 20
 * @param {boolean|null} opts.read - true=只看已读 / false=只看未读 / null=不过滤
 * @param {boolean|null} opts.firstDegreeConnections - true=只看 1 度好友
 * @param {string} opts.nextCursor - 翻页 cursor(从上次响应 paging.next 取)
 */
async function apiLinkedinListConversationsBySearchCriteria(mailboxUrn, opts = {}) {
  if (!mailboxUrn) throw new Error('mailboxUrn 必填');
  const {
    categories = ['PRIMARY_INBOX'],
    count = 20,
    read = null,
    firstDegreeConnections = null,
    nextCursor = '',
  } = opts;

  // 抓包验证的 prefix:`messengerConversations.737b` 比 `messengerConversations`
  // 更精确,避免命中 sync_token 路径(0d5e67) 或 predicateUnions 路径(9501)。
  return _callVoyagerGuarded(
    'messengerConversations.737b',
    '请[👉 打开 LinkedIn 消息页(新标签页)](https://www.linkedin.com/messaging/)然后在左侧切一下 "Focused / Other" 标签,扩展会自动捕获该 queryId。',
    (queryId) => {
      const parts = [
        `categories:List(${categories.join(',')})`,
        `count:${count}`,
        ...(firstDegreeConnections !== null ? [`firstDegreeConnections:${firstDegreeConnections}`] : []),
        `mailboxUrn:${mailboxUrn}`,
        ...(read !== null ? [`read:${read}`] : []),
        ...(nextCursor ? [`nextCursor:${nextCursor}`] : []),
      ];
      const variables = `(${parts.join(',')})`;
      return executeSiteApi('linkedin',
        `${LINKEDIN_ORIGIN}/voyager/api/voyagerMessagingGraphQL/graphql?queryId=${queryId}&variables=${encodeURIComponent(variables)}`,
        { method: 'GET' });
    },
  );
}

/**
 * 打开 compose modal 前的预检 — 能否向该 recipient 发消息?
 * 返回 elements[0].{ showSubjectField, showBlockedFooter, trustInterventionPage,
 *   invitationText, headerText, footerText }。
 *
 * LinkedIn 独有,Boss 没有等价机制(Boss 在 send_message 时才报错)。
 *
 * @param {string} recipientUrn - urn:li:fsd_profile:ACoA... 形态
 * @param {string} conversationUrn - 已有会话则传(REPLY 类型);新发空(NEW 类型)
 * @param {string} type - REPLY / NEW / INMAIL
 */
async function apiLinkedinPrecheckCompose(recipientUrn, conversationUrn = '', type = 'REPLY') {
  if (!recipientUrn) throw new Error('recipientUrn 必填');
  return _callVoyagerGuarded(
    'voyagerMessagingDashComposeViewContexts',
    '请[👉 打开 LinkedIn 消息页(新标签页)](https://www.linkedin.com/messaging/)随便打开一个会话或点 "Compose",扩展会自动捕获该 queryId。',
    (queryId) => {
      const parts = [
        `recipients:List(${recipientUrn})`,
        `type:${type}`,
        ...(conversationUrn ? [`contextEntityUrn:${conversationUrn}`] : []),
      ];
      const variables = `(${parts.join(',')})`;
      return executeSiteApi('linkedin',
        `${LINKEDIN_ORIGIN}/voyager/api/graphql?queryId=${queryId}&variables=${encodeURIComponent(variables)}`,
        { method: 'GET' });
    },
  );
}

/**
 * 查询多个用户的在线状态（available / lastActiveAt / instantlyReachable）。
 * @param {string[]} profileUrns - urn:li:fsd_profile:xxx 数组
 */
async function apiLinkedinGetUserPresence(profileUrns) {
  if (!Array.isArray(profileUrns) || profileUrns.length === 0) {
    throw new Error('profileUrns 必填（非空数组）');
  }
  // ids=List(urn%3Ali%3Afsd_profile%3Axxx,urn%3Ali%3Afsd_profile%3Ayyy)
  const encoded = profileUrns.map(u => encodeURIComponent(u)).join(',');
  const formBody = `ids=List(${encoded})`;
  return executeSiteApi('linkedin',
    `${LINKEDIN_ORIGIN}/voyager/api/messaging/dash/presenceStatuses`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formBody,
    });
}

/**
 * 获取当前登录用户的邮箱列表（用于 apply 自动填表）。
 * @param {boolean} primaryOnly - 仅取主邮箱，默认 true
 */
async function apiLinkedinGetMyEmailHandles(primaryOnly = true) {
  const qs = new URLSearchParams({ q: 'criteria', type: 'EMAIL' });
  if (primaryOnly) qs.set('primary', 'true');
  return executeSiteApi('linkedin',
    `${LINKEDIN_ORIGIN}/voyager/api/voyagerOnboardingDashMemberHandles?${qs}`,
    { method: 'GET' });
}
