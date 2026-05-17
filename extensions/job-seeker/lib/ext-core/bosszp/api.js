/**
 * Boss直聘 API 调用函数（求职者端）。
 * 所有请求通过 executeBossApi() 在 Worker Tab 的 MAIN world 中执行，
 * 自动携带真实 Cookie 和浏览器指纹，无需任何签名逆向。
 *
 * 令牌链：
 *   search → jobList[].securityId (listSecurityId)
 *   ↓
 *   job/detail.json?securityId=listSecurityId → zpData.securityId (detailSecurityId)
 *   ↓
 *   friend/add.json?securityId=detailSecurityId → zpData.securityId (chatSecurityId)
 *   ↓
 *   zpchat/session/geekEnter (securityId=chatSecurityId)
 *   ↓
 *   historyMsg / sendMsg
 */

// 依赖: executor.js (executeBossApi, executeBossApiBinary), tokens.js (tokenStore)

// ── QR 码直接生成接口（不依赖页面截图）────────────────────────────────────

/**
 * 获取 QR 登录唯一 ID。
 *   POST /wapi/zppassport/captcha/randkey
 *   无需 body，返回 { zpData: { qrId: "..." } }
 * 注意：该接口是 POST，不是 GET。
 */
async function apiGetQrId() {
  return executeBossApi('/wapi/zppassport/captcha/randkey', { method: 'POST' });
}

/**
 * 获取 QR 码图片（二进制 PNG），返回 data:image/png;base64,... 字符串。
 *   GET /wapi/zpweixin/qrcode/getqrcode?content={qrId}&w=200&h=200
 * 注意：
 *   - 参数名是 content（不是 uuid/id/qrId）
 *   - w/h 参数必须携带，否则服务端返回尺寸偏小的图片（12KB → 4KB）
 *   - 响应体是 PNG 二进制，不是 JSON
 */
async function apiGetQrCodeImage(qrId) {
  return executeBossApiBinary(`/wapi/zpweixin/qrcode/getqrcode?content=${encodeURIComponent(qrId)}&w=200&h=200`);
}

// ─────────────────────────────────────────────────────────────────────────────

/** 获取会话令牌 wt2 */
async function apiInitSession() {
  return executeBossApi('/wapi/zppassport/get/wt', { method: 'GET' });
}

/** 获取用户信息（检查登录状态） */
async function apiGetUserInfo() {
  return executeBossApi(`/wapi/zpuser/wap/getUserInfo.json?_=${Date.now()}`, { method: 'GET' });
}

/**
 * 搜索职位列表
 * @param {string} keyword
 * @param {string|number} city - 城市代码，北京=101010100
 * @param {number} page
 * @param {object} extra - 额外筛选参数（experience, degree, salary, position 等）
 */
async function apiSearchJobs(keyword, city = 101010100, page = 1, extra = {}) {
  const extraStr = Object.fromEntries(Object.entries(extra).map(([k, v]) => [k, String(v)]));
  const body = new URLSearchParams({
    scene: '1',
    query: keyword,
    city: String(city),
    page: String(page),
    pageSize: '30',
    encryptExpectId: '',
    ...extraStr,
  });
  return executeBossApi(`/wapi/zpgeek/search/joblist.json?_=${Date.now()}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body: body.toString(),
  });
}

/**
 * 获取职位详情
 * @param {string} securityId - listSecurityId（来自搜索结果）
 */
async function apiGetJobDetail(securityId, lid = '') {
  const params = new URLSearchParams({ securityId });
  if (lid) params.set('lid', lid);
  return executeBossApi(`/wapi/zpgeek/job/detail.json?${params}`, {
    method: 'GET',
  });
}

/**
 * 发起聊天（friend/add）
 * @param {string} securityId - detailSecurityId（来自职位详情）
 * @param {string} encryptJobId - 职位加密ID（jobId 参数，真实浏览器必传）
 * @param {string} sessionId - 通常为空
 */
async function apiFriendAdd(securityId, encryptJobId = '', sessionId = '') {
  let url = `/wapi/zpgeek/friend/add.json?securityId=${encodeURIComponent(securityId)}`;
  if (encryptJobId) url += `&jobId=${encodeURIComponent(encryptJobId)}`;
  return executeBossApi(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body: new URLSearchParams({ sessionId }).toString(),
  });
}

/**
 * 进入聊天会话
 * @param {string} securityId - chatSecurityId（来自 friend/add 响应）
 * @param {string|number} jobSource - 通常为 1
 * @param {string|number} k810 - 新版 Boss 校验位，默认 0；2026-04 抓包中固定为 0
 */
async function apiEnterSession(securityId, jobSource = 1, k810 = 0) {
  return executeBossApi('/wapi/zpchat/session/geekEnter', {
    method: 'POST',
    skipToken: true,  // 真实请求无 token header，仅靠 Cookie
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body: new URLSearchParams({
      securityId,
      jobSource: String(jobSource),
      k810: String(k810),
    }).toString(),
  });
}

/**
 * 拉取聊天历史
 * @param {string} bossId - encryptBossId
 * @param {string} securityId - chatSecurityId
 * @param {string} maxMsgId - 分页游标，首次为空（也可以传 0）
 * @param {number} page - 页码（从 1 开始），抓包中显式带
 * @param {number} c - 单页条数，抓包中固定 20
 * @param {number} src - 来源标记，抓包中固定 0
 */
async function apiGetChatHistory(bossId, securityId, maxMsgId = '', page = 1, c = 20, src = 0) {
  const params = new URLSearchParams({
    bossId,
    securityId,
    maxMsgId: String(maxMsgId || 0),
    page: String(page),
    c: String(c),
    src: String(src),
  });
  return executeBossApi(`/wapi/zpchat/geek/historyMsg?${params}`, { method: 'GET' });
}

/**
 * 求职者端：按 label 拉取消息列表（消息中心主接口）
 * @param {number|string} labelId - 0=全部, 1=新招呼, 2=仅沟通, 3=有交换, 4=有面试, 5=不感兴趣, -1=系统侧默认
 * @param {string} encryptSystemId - 抓包中可有可无（labelId=0/-1 时常带）
 * @param {string} name - tab 中文名（如"全部"/"仅沟通"），可选，仅做服务端日志用
 */
async function apiGeekFilterByLabel(labelId, encryptSystemId = '', name = '') {
  const params = new URLSearchParams({ labelId: String(labelId) });
  if (encryptSystemId) params.set('encryptSystemId', encryptSystemId);
  if (name) params.set('name', name);
  return executeBossApi(`/wapi/zprelation/friend/geekFilterByLabel?${params}`, { method: 'GET' });
}

/**
 * 求职者端：进入聊天前拉 boss + 关联职位元信息
 * @param {string} bossId - encryptBossId / encryptFriendId
 * @param {string} securityId - chatSecurityId
 * @param {number|string} bossSource - 0
 */
async function apiGeekGetBossData(bossId, securityId, bossSource = 0) {
  const params = new URLSearchParams({ bossId, securityId, bossSource: String(bossSource) });
  return executeBossApi(`/wapi/zpchat/geek/getBossData?${params}`, { method: 'GET' });
}

/** WebSocket 服务器列表（实时消息推送入口） */
async function apiGetWsEndpoints() {
  return executeBossApi('/wapi/zpchat/config/ws', { method: 'GET' });
}

/**
 * 离线消息增量补拉
 * @param {number|string} type - 抓包中固定 0
 * @param {string|number} lastId - 上次拉到的 messageId（first call 用 0）
 * @param {string} secretId - 服务端给的拉取凭证（来自 WS 握手或上一次 pull）
 */
async function apiMsgHistoryPull(type = 0, lastId = '0', secretId = '') {
  const params = new URLSearchParams({
    type: String(type),
    lastId: String(lastId || 0),
  });
  if (secretId) params.set('secretId', secretId);
  return executeBossApi(`/wapi/zpmsg/history/pull?${params}`, { method: 'GET' });
}

/**
 * 发送消息
 * @param {string} bossId - encryptBossId
 * @param {string} securityId - chatSecurityId
 * @param {string} content - 消息内容
 */
async function apiSendMessage(bossId, securityId, content) {
  return executeBossApi('/wapi/zpchat/geek/sendMsg', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body: new URLSearchParams({ bossId, securityId, content, type: '1' }).toString(),
  });
}

// ── 求职者个人中心 & 职位发现（boss-cli 补全） ─────────────────────────────

/** 推荐职位列表 */
async function apiGetRecommendJobs(page = 1) {
  return executeBossApi(`/wapi/zpgeek/pc/recommend/job/list.json?page=${page}`, { method: 'GET' });
}

/**
 * 职位卡片（轻量详情，不触发完整 token 链）
 * @param {string} securityId
 * @param {string} lid
 */
async function apiGetJobCard(securityId, lid = '') {
  const params = new URLSearchParams({ securityId, ...(lid ? { lid } : {}) });
  return executeBossApi(`/wapi/zpgeek/job/card.json?${params}`, { method: 'GET' });
}

/** 浏览历史（最近看过的职位） */
async function apiGetJobHistory(page = 1) {
  return executeBossApi(`/wapi/zpgeek/history/joblist.json?page=${page}`, { method: 'GET' });
}

/** 简历基本信息 */
async function apiGetResumeBaseinfo() {
  return executeBossApi('/wapi/zpgeek/resume/baseinfo/query.json', { method: 'GET' });
}

/** 求职期望 */
async function apiGetResumeExpect() {
  return executeBossApi('/wapi/zpgeek/resume/expect/query.json', { method: 'GET' });
}

/** 简历投递状态 */
async function apiGetResumeStatus() {
  return executeBossApi('/wapi/zpgeek/resume/status.json', { method: 'GET' });
}

/** 已投递职位列表 */
async function apiGetDeliverList(page = 1) {
  return executeBossApi(`/wapi/zprelation/resume/geekDeliverList?page=${page}`, { method: 'GET' });
}

/** 面试邀请数据 */
async function apiGetInterviewData() {
  return executeBossApi('/wapi/zpinterview/geek/interview/data.json', { method: 'GET' });
}

/**
 * @deprecated 老接口签名已不可用 —— Boss 实际是 POST + friendIds body。
 * 保留仅为防止外部 import 报错;新代码用 apiGetGeekFriendListBatch。
 */
async function apiGetFriendList() {
  return executeBossApi('/wapi/zprelation/friend/getGeekFriendList.json', { method: 'GET' });
}

/**
 * 批量拉好友完整详情(2026-04-28 抓包反推):配合 geekFilterByLabel,
 * Stage1 拿 encryptFriendId / friendId(稀疏列表)→ Stage2 用 friendIds 数组
 * 拉完整 (name, title, brandName, encryptJobId, encryptBossId, securityId, lastMsg, ...)。
 *
 * @param {number[]|string[]} friendIds  数字/字符串 friendId 列表
 * @returns Boss 原响应:{ code, zpData: { result: [...] } },每项含 securityId
 *          (chatSecurityId 级)+ encryptJobId + encryptBossId,可直接喂 tokenStore。
 */
async function apiGetGeekFriendListBatch(friendIds) {
  if (!Array.isArray(friendIds) || friendIds.length === 0) {
    throw new Error('friendIds 必填(非空数组)');
  }
  const body = `friendIds=${encodeURIComponent(friendIds.join(','))}`;
  return executeBossApi('/wapi/zprelation/friend/getGeekFriendList.json', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
}

/**
 * 互动职位（geekGetJob）
 * @param {string} securityId
 */
async function apiGetGeekJob(securityId) {
  return executeBossApi(`/wapi/zprelation/interaction/geekGetJob?securityId=${encodeURIComponent(securityId)}`, { method: 'GET' });
}

/**
 * 收藏 / 不感兴趣职位（求职者端）
 * @param {string} securityId - listSecurityId（搜索结果中的 securityId）
 * @param {string} jobId      - 职位加密 ID（encryptJobId）
 * @param {0|1}   flag        - 1=收藏/感兴趣，0=不感兴趣/取消收藏
 * @param {string} expectId   - 求职期望 ID（可留空）
 * @param {number} tag        - 标签（默认 4）
 * @param {string} lid        - 搜索结果 lid（可留空）
 */
async function apiGeekMarkJobInterest(securityId, jobId, flag, expectId = '', tag = 4, lid = '', extraHeaders = {}) {
  const ts = Date.now();
  const qs = new URLSearchParams({
    securityId,
    jobId,
    expectId,
    tag: String(tag),
    lid,
    _: String(ts),
  });
  return executeBossApi(`/wapi/zprelation/geekTag/job/interest?${qs}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-Requested-With': 'XMLHttpRequest',
      'Referer': 'https://www.zhipin.com/web/geek/jobs',
      ...extraHeaders,
    },
    body: `flag=${flag}`,
  });
}

// ── 招聘官端（Boss 视角）─────────────────────────────────────────────────

async function apiBossGetGeekInfo(uid, securityId) {
  const p = new URLSearchParams({ uid: String(uid), geekSource: '0', securityId });
  return executeBossApi(`/wapi/zpjob/chat/geek/info?${p}`, { method: 'GET' });
}

async function apiBossEnterSession(geekId, expectId, jobId, securityId) {
  return executeBossApi('/wapi/zpchat/session/bossEnter', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body: new URLSearchParams({ geekId, expectId, jobId, securityId }).toString(),
  });
}

async function apiBossGetChatHistory(uid, maxMsgId = '0', count = 20, page = 1) {
  const p = new URLSearchParams({ src: '0', gid: String(uid), maxMsgId: String(maxMsgId), c: String(count), page: String(page) });
  return executeBossApi(`/wapi/zpchat/boss/historyMsg?${p}`, { method: 'GET' });
}

async function apiResumePreviewCheck(encryptUid, authorityId) {
  const p = new URLSearchParams({ geekId: encryptUid, id: authorityId, authType: '0' });
  return executeBossApi(`/wapi/zpgeek/resume/boss/preview/check.json?${p}`, { method: 'GET' });
}

async function apiDownloadResume(geekId, authorityId, timestamp) {
  const p = new URLSearchParams({ d: String(timestamp), id: authorityId, authType: '0', previewType: '1', geekId });
  return executeBossApiBinary(`/wflow/zpgeek/download/preview4boss/${encodeURIComponent(geekId)}?${p}`);
}

async function apiFilterByLabel(labelId, encryptJobId, sort = '') {
  return executeBossApi('/wapi/zprelation/friend/filterByLabel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body: new URLSearchParams({ labelId: String(labelId), encJobId: encryptJobId, sort }).toString(),
  });
}

async function apiAcceptExchange(messageId, securityId) {
  return executeBossApi('/wapi/zpchat/exchange/accept', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body: new URLSearchParams({ mid: String(messageId), type: '3', securityId }).toString(),
  });
}

/**
 * 招聘方搜索候选人（Boss 视角）
 * @param {string} encryptJobId - 招聘方自己的职位加密 ID（必填）
 * @param {string} keywords - 搜索关键词（空=不限）
 * @param {number|string} city - 城市代码，-1=全国，101010100=北京
 * @param {number} page - 页码（从 1 开始）
 * @param {object} filters - 额外筛选：gender,experience,salary,age,schoolLevel,degree,activeness 等
 */
async function apiBossSearchGeeks(encryptJobId, keywords = '', city = -1, page = 1, filters = {}) {
  const filterParams = JSON.stringify({
    sortType: 1,
    region: {
      cityCode: city === -1 ? '-1' : String(city),
      cityName: city === -1 ? '全国' : String(city),
      areas: [],
    },
    overSeaWorkExperience: 0,
    overSeaWorkLanguage: 0,
    overSeaWorkWill: 0,
    manageExperience: filters.manageExperience || 0,
  });
  const p = new URLSearchParams({
    page: String(page),
    jobId: encryptJobId,
    keywords: keywords || '',
    city: String(city),
    gender: String(filters.gender ?? -1),
    experience: filters.experience || '-1,-1',
    salary: filters.salary || '-1,-1',
    age: filters.age || '-1,-1',
    schoolLevel: String(filters.schoolLevel ?? -1),
    applyStatus: String(filters.applyStatus ?? -1),
    degree: filters.degree || '201,201',
    switchFreq: '0',
    manageExperience: String(filters.manageExperience || 0),
    geekJobRequirements: '0',
    exchangeResume: '0',
    viewResume: '0',
    firstDegree: '0',
    queryAnd: '0',
    companySearchTypeForHunter: '0',
    source: keywords ? '1' : '4',
    activeness: String(filters.activeness || 0),
    filterParams,
    defaultCondition: '2',
    hasRcd: '0',
    _: String(Date.now()),
  });
  return executeBossApi(`/wapi/zpitem/web/boss/search/geeks.json?${p}`, { method: 'GET' });
}

/**
 * 招聘方搜索关键词自动补全
 * @param {string} query - 输入的关键词前缀
 * @param {string} encryptJobId - 招聘方职位加密 ID
 */
async function apiBossAutoSuggest(query, encryptJobId) {
  const p = new URLSearchParams({ query, encryptJobId, _: String(Date.now()) });
  return executeBossApi(`/wapi/zpitem/web/boss/search/autoSuggest.json?${p}`, { method: 'GET' });
}

// ── 招聘方职位管理 ───────────────────────────────────────────────────────────

/**
 * 获取招聘方自己发布的职位列表
 * @param {number} page - 页码，从 1 开始
 * @param {number} type - 0=全部 5=草稿/待审 6=已过期
 * @param {string} searchStr - 职位名称搜索（空字符串=不过滤）
 */
async function apiBossGetMyJobList(page = 1, type = 0, searchStr = '') {
  const p = new URLSearchParams({
    position: '0',
    type: String(type),
    searchStr,
    comId: '',
    tagIdStr: '',
    page: String(page),
    _: String(Date.now()),
  });
  return executeBossApi(`/wapi/zpjob/job/data/list?${p}`, { method: 'GET' });
}

/**
 * 获取有沟通记录的职位列表（用于快速定位有候选人互动的职位）
 */
async function apiBossChattedJobList() {
  return executeBossApi('/wapi/zpjob/job/chatted/jobList', { method: 'GET' });
}

/**
 * 获取招聘方简化职位列表（供候选人推荐页使用）
 */
async function apiBossGetRecJobList() {
  return executeBossApi(`/wapi/zpjob/job/recJobList?_=${Date.now()}`, { method: 'GET' });
}

/**
 * 获取候选人详情（搜索页版本）
 * 只需 securityId（来自搜索结果），无需 uid。
 * 返回顶层 encryptGeekId / encryptExpectId / encryptJobId / securityId(detail)
 * 用于后续 bossEnter 进入聊天。
 * @param {string} securityId - 候选人搜索页 securityId
 */
async function apiBossSearchGeekInfo(securityId) {
  const p = new URLSearchParams({ securityId, _: String(Date.now()) });
  return executeBossApi(`/wapi/zpitem/web/boss/search/geek/info?${p}`, { method: 'GET' });
}

// ── 招聘方：互动候选人列表 ────────────────────────────────────────────────────

/**
 * 获取互动候选人列表（看过我/沟通过/待反馈）
 * @param {number} tag - 2=看过我的, 4=沟通过的, 8=待反馈的, -1=全部
 * @param {number} status - 与 tag 保持一致
 * @param {number} geekApplyStatus - -1=全部
 * @param {number} chatStatus - -1=全部
 * @param {number|string} jobid - -1=全部职位, 或传 encryptJobId 过滤
 * @param {number} page - 页码
 */
async function apiBossGetGeekList(tag = 2, status = 2, geekApplyStatus = -1, chatStatus = -1, jobid = -1, page = 1) {
  const p = new URLSearchParams({
    'geek-apply-status': String(geekApplyStatus),
    'chat-status': String(chatStatus),
    jobid: String(jobid),
    page: String(page),
    tag: String(tag),
    status: String(status),
  });
  return executeBossApi(`/wapi/zprelation/interaction/bossGetGeek?${p}`, { method: 'GET' });
}

/**
 * 获取招聘方职位推荐牛人列表（/wapi/zpjob/rec/geek/list）
 * @param {string} jobId - 招聘方职位 ID（必填）
 * @param {number} page - 页码（从 1 开始）
 * @param {object} filters - 筛选条件：age, degree, experience, activation, recentNotView, gender, keyword1
 */
async function apiBossRecGeekList(jobId, page = 1, filters = {}) {
  const params = new URLSearchParams({
    jobId,
    page,
    age:           filters.age           ?? '16,-1',
    degree:        filters.degree        ?? '0',
    experience:    filters.experience    ?? '0',
    activation:    filters.activation    ?? '0',
    recentNotView: filters.recentNotView ?? '0',
    gender:        filters.gender        ?? '0',
    keyword1:      filters.keyword1      ?? '',
  });
  return executeBossApi(`/wapi/zpjob/rec/geek/list?${params}`, { method: 'GET' });
}

/**
 * 获取联系人列表（另一视图，含沟通中候选人）
 * @param {object} filter - 筛选条件，如 {"geek-apply-status":-1,"chat-status":-1}
 * @param {number} page
 * @param {number} source - 来源标识，默认 2
 */
async function apiBossContactList(filter = {}, page = 1, source = 2) {
  const p = new URLSearchParams({
    filter: JSON.stringify(filter),
    page: String(page),
    source: String(source),
  });
  return executeBossApi(`/wapi/zprelation/bossTag/contactList?${p}`, { method: 'GET' });
}

/**
 * 获取互动候选人详情 v2（点击详情时调用）
 * @param {string} encryptJid - 职位加密 ID（geekCard.encryptJobId）
 * @param {string|number} expectId - 候选人 expectId（geekCard.expectId，数字）
 * @param {string} securityId - 候选人 securityId（geekCard.securityId）
 * @param {string} lid - geekCard.lid（可选）
 * @param {number} entrance - 入口类型，默认 2
 */
async function apiBossViewGeekInfoV2(encryptJid, expectId, securityId, lid = '', entrance = 2) {
  const p = new URLSearchParams({
    encryptJid,
    expectId: String(expectId),
    securityId,
    lid,
    entrance: String(entrance),
    wayType: '0',
    sourceType: '3',
  });
  return executeBossApi(`/wapi/zpjob/view/geek/info/v2?${p}`, { method: 'GET' });
}

/**
 * 拉取 HR 自定义的快捷回复短语模板列表（聊天框右侧"常用语"）
 * 返回数组：["您好 您的简历很合适...", "请问您目前 base 哪里呀", ...]
 */
async function apiBossGetQuickReplies() {
  return executeBossApi('/wapi/zpchat/setting/replyWord/list', { method: 'GET' });
}

/**
 * 招聘方主动发起交换电话/微信请求（区别于 accept_exchange，后者是接受候选人请求）
 * @param {string} securityId - 来自 boss_enter 响应或 chat 上下文的 securityId
 * @param {number} type       - 请求类型：4=微信，3=电话（按抓包推测，type 字典需进一步确认）
 *
 * 内部逻辑：先调 /wapi/zpchat/exchange/test 预检（避免重复发起 / 风控拦截），
 *           通过后再调 /wapi/zpchat/exchange/request 真正发起。
 */
/**
 * 招聘方聊天回复屏蔽检查：批量联系候选人前预检，避免浪费配额 / 触发风控。
 * @param {string} encryptJid   - 招聘方自己的职位加密 ID（/view/geek/info/v2 等返回过）
 * @param {string} encryptExpId - 候选人 experience 加密 ID（来自推荐 / 搜索结果）
 * @param {string} securityId   - 聊天级 securityId（bossEnter / geek/info 返回的）
 *
 * 返回 zpData 含:
 *   replyBlock            — 是否被屏蔽（对象/bool，具体见响应）
 *   hunterCallChatLimit   — 会话配额信息
 */
async function apiBossCheckReplyBlock(encryptJid, encryptExpId, securityId) {
  const body = new URLSearchParams({
    encryptJid,
    encryptExpId,
    securityId,
  }).toString();
  return executeBossApi('/wapi/zpblock/chat/reply/block/v2', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body,
  });
}

async function apiBossExchangeRequest(securityId, type = 4) {
  const body = new URLSearchParams({ type: String(type), securityId }).toString();
  // Step 1: 预检
  const testRaw = await executeBossApi('/wapi/zpchat/exchange/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body,
  });
  // testRaw.zpData.alertType !== 0 说明被拦截（如：今天已发送过、配额耗尽）
  const alertType = testRaw?.zpData?.alertType ?? 0;
  if (alertType && alertType !== 0) {
    return { test: testRaw, request: null, blocked: true, alertType };
  }
  // Step 2: 实际请求
  const reqRaw = await executeBossApi('/wapi/zpchat/exchange/request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
    body,
  });
  return { test: testRaw, request: reqRaw, blocked: false, alertType: 0 };
}
