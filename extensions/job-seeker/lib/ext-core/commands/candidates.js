/**
 * 招聘方 Candidates 命令
 *
 * 包含：search_candidates, auto_suggest, get_candidate_detail, contact_candidate,
 *       geek_info, boss_enter, boss_history, resume_preview, resume_download,
 *       filter_label, accept_exchange, my_job_list, rec_job_list,
 *       geek_list, rec_geek_list, contact_list, view_geek_info
 *
 * Token 链（geeks 命名空间）：
 *   securityId（search） → detailSecurityId → chatSecurityId（contact）
 *
 * 依赖（全局）：tokenStore, extOk, apiBossXxx 系列函数（api.js）
 */

// ── 辅助：标准化 geekCard 并存 token ─────────────────────────────────────

/**
 * F4：从 geekCard 或父级对象里尝试提取推荐匹配分。
 * Boss 不同版本 / 不同接口（rec/geek/list、search、contact_list）字段名不
 * 统一，做一次防御性扫描：只要命中任何一个数值字段就保留，否则返回 null。
 * 网关层据此批量 upsert 到 recruiter_geek_interests.match_score。
 */
function _extractMatchScore(gc, outerItem = {}) {
  const candidates = [
    gc.matchScore, gc.match_score,
    gc.score, gc.jobScore, gc.weight, gc.matchWeight,
    gc.similarity, gc.recScore, gc.relScore,
    outerItem.matchScore, outerItem.match_score,
    outerItem.score, outerItem.recScore,
  ];
  for (const v of candidates) {
    if (typeof v === 'number' && Number.isFinite(v)) return v;
    if (typeof v === 'string') {
      const n = Number(v);
      if (Number.isFinite(n) && v.trim() !== '') return n;
    }
  }
  return null;
}

function _normalizeGeekCard(gc, outerItem = {}) {
  const encryptGeekId  = gc.encryptGeekId || gc.encGeekId || outerItem.encryptGeekId || '';
  const geekId         = gc.geekId || gc.geekUserId || 0;
  const securityId     = gc.securityId || '';
  const expectId       = gc.expectId || 0;
  const encryptJobId   = gc.encryptJobId || outerItem.encryptJobId || '';
  const lid            = gc.lid || outerItem.lid || '';
  const name           = gc.geekName || gc.name || '';
  const matchScore     = _extractMatchScore(gc, outerItem);

  if (securityId) {
    tokenStore.storeGeekToken(encryptGeekId || String(geekId), {
      uid: String(geekId),
      encryptUid:    encryptGeekId,
      securityId,
      encryptJobId,
      lid,
      plainExpectId: String(expectId),
    });
  }

  return {
    encryptGeekId,
    geekId,
    securityId,
    expectId,
    encryptJobId,
    lid,
    name,
    matchScore,  // null 表示本次响应里找不到匹配分
    salary:       gc.salary || gc.salaryDesc || '',
    city:         gc.expectLocationName || gc.city || '',
    workYear:     gc.geekWorkYear || gc.workYear || '',
    degree:       gc.geekDegree || gc.degreeName || gc.degree || '',
    currentWork:  (gc.geekWork || gc.middleContent)?.content || (gc.geekWork || gc.middleContent)?.name || '',
    school:       gc.geekEdu?.school || gc.geekEdu?.name || '',
    activeDesc:   outerItem.activeTimeDesc || gc.activeDesc || '',
    applyStatus:  outerItem.applyStatus ?? gc.applyStatus ?? -1,
    isFriend:     outerItem.isFriend ?? 0,
  };
}

// ── 搜索候选人（批量捕获 geek search securityId） ────────────────────────

defineCommand({
  path: 'boss/search_candidates',
  description: '搜索候选人，批量捕获 geek search securityId',
  produces: { chain: 'geeks', stage: 'search' },
  handler: async ({ encrypt_job_id, keywords = '', city = -1, page = 1, filters = {} } = {}) => {
    if (!encrypt_job_id) throw new Error('encrypt_job_id 必填（招聘方自己的职位加密 ID）');
    const raw = await apiBossSearchGeeks(encrypt_job_id, keywords, city, page, filters);
    const rawGeeks = raw?.zpData?.geekList || raw?.zpData?.geeks || [];
    const geeks = rawGeeks.map(g => {
      const gc = g.geekCard || g;
      const encryptGeekId = gc.encryptGeekId || gc.encGeekId || g.encryptGeekId || '';
      const securityId    = gc.securityId || '';
      const geekId        = gc.geekId || gc.geekUserId || 0;
      const name          = gc.geekName || gc.name || '';
      const salary        = gc.salary || gc.salaryDesc || '';
      const encryptExpectId = gc.encryptExpectId || '';
      const encryptJobId    = gc.encryptJobId || encrypt_job_id;
      const lid             = gc.lid || '';

      if (encryptGeekId && securityId) {
        tokenStore.storeGeekToken(encryptGeekId, {
          uid: String(geekId),
          encryptUid:   encryptGeekId,
          securityId,
          encryptExpectId,
          encryptJobId,
          lid,
        });
      }
      return {
        encryptGeekId, geekId, securityId, encryptExpectId, encryptJobId, lid,
        name, salary,
        city:        gc.expectLocationName || gc.city || '',
        workYear:    gc.geekWorkYear || gc.workYear || '',
        degree:      gc.geekDegree || gc.degreeName || '',
        currentWork: (gc.geekWork || gc.middleContent)?.content || (gc.geekWork || gc.middleContent)?.name || '',
        school:      gc.geekEdu?.school || gc.geekEdu?.name || '',
        activeDesc:  g.activeTimeDesc || gc.activeDesc || '',
        applyStatus: g.applyStatus ?? gc.applyStatus ?? -1,
      };
    });
    return extOk({
      geeks,
      hasMore:    raw?.zpData?.hasMore    ?? false,
      totalCount: raw?.zpData?.totalCount ?? raw?.zpData?.totalSize ?? geeks.length,
      page:       raw?.zpData?.page       ?? page,
      raw,
    });
  },
});

// ── 候选人自动补全 ────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/auto_suggest',
  description: '候选人搜索关键词自动补全',
  handler: async ({ query, encrypt_job_id } = {}) => {
    if (!query) throw new Error('query 必填');
    if (!encrypt_job_id) throw new Error('encrypt_job_id 必填');
    const raw = await apiBossAutoSuggest(query, encrypt_job_id);
    const items = raw?.zpData?.itemList || [];
    return extOk({ suggestions: items.map(i => i.name), raw });
  },
});

// ── 候选人详情（消费 search，捕获 detail） ───────────────────────────────

defineCommand({
  path: 'boss/get_candidate_detail',
  description: '获取候选人详情，消费 search securityId，捕获 detailSecurityId',
  requires: { chain: 'geeks', stage: 'search', keyParam: 'encrypt_uid' },
  produces: { chain: 'geeks', stage: 'detail' },
  handler: async ({ encrypt_uid, security_id } = {}) => {
    const sid = security_id || tokenStore.getGeekToken(encrypt_uid)?.securityId;
    if (!sid) throw new Error('security_id 必填（或传 encrypt_uid 并先调用 boss/search_candidates）');

    const raw = await apiBossSearchGeekInfo(sid);
    const zp  = raw?.zpData || {};

    const encryptGeekId   = zp.encryptGeekId   || encrypt_uid || '';
    const encryptExpectId = zp.encryptExpectId  || '';
    const encryptJobId    = zp.encryptJobId     || '';
    const detailSecurityId = zp.securityId      || '';

    if (encryptGeekId) {
      tokenStore.storeGeekToken(encryptGeekId, {
        encryptUid: encryptGeekId, detailSecurityId, encryptExpectId, encryptJobId,
      });
    }

    const base = zp.geekDetail?.geekBaseInfo || {};
    return extOk({
      encryptGeekId, encryptExpectId, encryptJobId, detailSecurityId,
      name:          base.name || '',
      gender:        base.gender ?? -1,
      ageDesc:       base.ageDesc || '',
      workYearDesc:  base.workYearDesc || '',
      degree:        base.degreeCategory || '',
      activeDesc:    base.activeTimeDesc || '',
      applyStatus:   base.applyStatusContent || '',
      searchChatCardCostCount: zp.searchChatCardCostCount ?? 0,
      sendFreeGeek:  zp.sendFreeGeek ?? 0,
      alreadyInterested: zp.alreadyInterested ?? 0,
      raw,
    });
  },
});

// ── 主动联系候选人（消费 detail → bossEnter） ─────────────────────────────

defineCommand({
  path: 'boss/contact_candidate',
  description: '主动联系候选人（自动获取 detail token 并进入会话）',
  requires: { chain: 'geeks', stage: 'search', keyParam: 'encrypt_uid' },
  handler: async ({ encrypt_uid, job_id, security_id } = {}) => {
    const searchSid = security_id || tokenStore.getGeekToken(encrypt_uid)?.securityId;
    if (!searchSid && !encrypt_uid) throw new Error('encrypt_uid 或 security_id 必填');

    let encryptGeekId   = encrypt_uid || '';
    let encryptExpectId = tokenStore.getGeekToken(encrypt_uid)?.encryptExpectId || '';
    let encryptJobId    = job_id || tokenStore.getGeekToken(encrypt_uid)?.encryptJobId || '';
    let detailSid       = tokenStore.getGeekToken(encrypt_uid)?.detailSecurityId || '';

    if (searchSid && !detailSid) {
      const detailRaw = await apiBossSearchGeekInfo(searchSid);
      const zp = detailRaw?.zpData || {};
      encryptGeekId   = zp.encryptGeekId   || encryptGeekId;
      encryptExpectId = zp.encryptExpectId  || encryptExpectId;
      encryptJobId    = job_id || zp.encryptJobId || encryptJobId;
      detailSid       = zp.securityId       || '';
      if (encryptGeekId) {
        tokenStore.storeGeekToken(encryptGeekId, {
          encryptUid: encryptGeekId, detailSecurityId: detailSid, encryptExpectId, encryptJobId,
        });
      }
    }

    if (!encryptGeekId)   throw new Error('无法获取 encryptGeekId，请先调用 boss_get_candidate_detail');
    if (!encryptJobId)    throw new Error('无法获取 encryptJobId，请通过 job_id 参数指定招聘方职位');
    if (!detailSid)       throw new Error('无法获取 detail securityId，请先调用 boss_get_candidate_detail');
    if (!encryptExpectId) throw new Error('无法获取 encryptExpectId，请先调用 boss_get_candidate_detail');

    const enterRaw = await apiBossEnterSession(encryptGeekId, encryptExpectId, encryptJobId, detailSid);
    return extOk({ raw: enterRaw, encryptGeekId, encryptJobId });
  },
});

// ── 获取 Geek 基础信息（旧接口） ──────────────────────────────────────────

defineCommand({
  path: 'boss/geek_info',
  description: '获取候选人基础信息（旧接口，捕获 search 级 securityId）',
  handler: async ({ uid, security_id } = {}) => {
    if (!uid) throw new Error('uid 必填');
    if (!security_id) throw new Error('security_id 必填');
    const raw = await apiBossGetGeekInfo(uid, security_id);
    const d = raw?.zpData?.data;
    if (d?.encryptUid) {
      tokenStore.storeGeekToken(d.encryptUid, {
        uid: String(d.uid || uid),
        encryptUid: d.encryptUid,
        encryptExpectId: d.encryptExpectId || '',
        encryptJobId: String(d.jobId || ''),
        securityId: d.securityId || '',
      });
    }
    return extOk({ raw });
  },
});

// ── 进入招聘方与候选人的会话 ─────────────────────────────────────────────

defineCommand({
  path: 'boss/boss_enter',
  description: '进入招聘方与候选人的聊天会话',
  handler: async ({ encrypt_uid, encrypt_job_id, security_id, encrypt_expect_id } = {}) => {
    if (!encrypt_uid) throw new Error('encrypt_uid 必填');
    if (!encrypt_job_id) throw new Error('encrypt_job_id 必填');
    const tok = tokenStore.getGeekToken(encrypt_uid);
    const sid = security_id || tok?.securityId;
    const expectId = encrypt_expect_id || tok?.encryptExpectId;
    if (!sid) throw new Error('security_id 未找到，请先调用 boss/geek_info');
    if (!expectId) throw new Error('encrypt_expect_id 未找到，请先调用 boss/geek_info');
    const raw = await apiBossEnterSession(encrypt_uid, expectId, encrypt_job_id, sid);
    return extOk({ raw });
  },
});

// ── 招聘方聊天记录 ────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/boss_history',
  description: '获取招聘方与候选人的聊天记录',
  handler: async ({ uid, encrypt_uid, max_msg_id = '0', count = 20, page = 1 } = {}) => {
    let plainUid = uid;
    if (!plainUid && encrypt_uid) plainUid = tokenStore.getGeekToken(encrypt_uid)?.uid;
    if (!plainUid) throw new Error('uid 必填（或传 encrypt_uid 并先调用 boss/geek_info）');
    const raw = await apiBossGetChatHistory(plainUid, max_msg_id, count, page);
    return extOk({ raw });
  },
});

// ── 简历预览 ──────────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/resume_preview',
  description: '检查是否有权限预览候选人简历',
  handler: async ({ encrypt_uid, authority_id } = {}) => {
    if (!encrypt_uid) throw new Error('encrypt_uid 必填');
    const authId = authority_id || tokenStore.getGeekToken(encrypt_uid)?.authorityId;
    if (!authId) throw new Error('authority_id 必填（来源尚不明确，需继续抓包）');
    const raw = await apiResumePreviewCheck(encrypt_uid, authId);
    const z = raw?.zpData;
    if (z?.encryptGeekId) {
      tokenStore.storeGeekToken(z.encryptGeekId, { authorityId: z.encryptAuthorityId || authId });
    }
    return extOk({ raw });
  },
});

// ── 简历下载（返回 base64 PDF） ───────────────────────────────────────────

defineCommand({
  path: 'boss/resume_download',
  description: '下载候选人简历（返回 base64 PDF）',
  handler: async ({ encrypt_uid, authority_id, timestamp } = {}) => {
    if (!encrypt_uid) throw new Error('encrypt_uid 必填');
    const authId = authority_id || tokenStore.getGeekToken(encrypt_uid)?.authorityId;
    if (!authId) throw new Error('authority_id 必填（需先调用 boss/resume_preview）');
    const dataUrl = await apiDownloadResume(encrypt_uid, authId, timestamp || Date.now());
    return extOk({ base64_pdf: dataUrl });
  },
});

// ── 标签筛选 ──────────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/filter_label',
  description: '按标签筛选候选人（per-job：encrypt_job_id 必填，对应某职位下的标签筛选）',
  handler: async ({ label_id, encrypt_job_id, sort } = {}) => {
    if (label_id == null) throw new Error('label_id 必填');
    if (!encrypt_job_id) throw new Error('encrypt_job_id 必填（招聘官自己的职位ID）');
    const raw = await apiFilterByLabel(label_id, encrypt_job_id, sort || '');
    return extOk({ raw });
  },
});

defineCommand({
  path: 'boss/recruiter_chat_list',
  description: '招聘方全局聊天列表（filterByLabel + encJobId="" 不绑定职位）',
  handler: async ({ label_id, sort } = {}) => {
    if (label_id == null) throw new Error('label_id 必填');
    const raw = await apiFilterByLabel(label_id, '', sort || '');
    return extOk({ raw });
  },
});

// ── 接受简历交换 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/accept_exchange',
  description: '接受候选人的简历交换请求',
  handler: async ({ message_id, security_id } = {}) => {
    if (!message_id) throw new Error('message_id 必填');
    if (!security_id) throw new Error('security_id 必填');
    const raw = await apiAcceptExchange(message_id, security_id);
    return extOk({ raw });
  },
});

// ── 招聘方职位列表 ────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/my_job_list',
  description: '获取招聘方自己发布的职位列表',
  handler: async ({ page = 1, type = 0, search_str = '' } = {}) => {
    const raw = await apiBossGetMyJobList(page, type, search_str);
    const zpData = raw?.zpData || {};
    const jobs = (zpData.data || []).map(j => ({
      encryptJobId:    j.encryptJobId || j.encryptId || '',
      jobName:         j.jobName || '',
      city:            j.city || j.locationName || '',
      cityCode:        String(j.location || ''),
      areaCode:        String(j.position || ''),
      salaryDesc:      j.salaryDesc || '',
      lowSalary:       j.lowSalary || 0,
      highSalary:      j.highSalary || 0,
      salaryMonth:     j.salaryMonth || 12,
      experienceName:  j.experienceName || '',
      degreeName:      j.degreeName || '',
      jobTypeName:     j.jobTypeName || '',
      jobStatus:       j.jobStatus ?? 0,
      jobAuditStatus:  j.jobAuditStatus ?? 0,
      viewCount:       j.viewCount || 0,
      concatCount:     j.concatCount || 0,
      addTime:         j.addTime || 0,
      updateTime:      j.updateTime || 0,
    }));
    return extOk({
      jobs, page: zpData.page || page, pageSize: zpData.pageSize || 20,
      hasMore: zpData.hasMore || false, totalSize: zpData.totalSize || jobs.length, raw,
    });
  },
});

defineCommand({
  path: 'boss/rec_job_list',
  description: '获取招聘方推荐职位列表',
  handler: async () => {
    const raw = await apiBossGetRecJobList();
    const onlineJobList = (raw?.zpData?.onlineJobList || []).map(j => ({
      encryptJobId: j.encryptId || '',
      jobName:      j.jobName || '',
      salaryDesc:   j.salaryDesc || '',
      positionCode: String(j.positionCode || ''),
    }));
    return extOk({ onlineJobList, raw });
  },
});

// ── 候选人互动列表 ────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/geek_list',
  description: '获取招聘方的候选人互动列表',
  handler: async ({ tag = 2, status = 2, geek_apply_status = -1, chat_status = -1, jobid = -1, page = 1 } = {}) => {
    const raw = await apiBossGetGeekList(tag, status, geek_apply_status, chat_status, jobid, page);
    const rawList = raw?.zpData?.geekList || [];
    const geeks = rawList.map(item => _normalizeGeekCard(item.geekCard || item, item));
    return extOk({
      geeks, hasMore: raw?.zpData?.hasMore ?? false,
      totalCount: raw?.zpData?.totalCount ?? geeks.length, page, tag, raw,
    });
  },
});

defineCommand({
  path: 'boss/chatted_jobs',
  description: '获取有沟通记录的职位列表（快速定位有候选人互动的职位）',
  handler: async () => {
    const raw = await apiBossChattedJobList();
    const jobs = (Array.isArray(raw?.zpData) ? raw.zpData : []).map(j => ({
      encryptJobId: j.encryptJobId || '',
      jobName: j.jobName || '',
      salaryDesc: j.salaryDesc || '',
      address: j.address || '',
      jobId: j.jobId || 0,
      jobOnlineStatus: j.jobOnlineStatus ?? 0,
    }));
    return extOk({ jobs, total: jobs.length, raw });
  },
});

defineCommand({
  path: 'boss/rec_geek_list',
  description: '获取招聘方推荐候选人列表',
  handler: async ({ job_id, page = 1, filters = {} } = {}) => {
    if (!job_id) throw new Error('job_id 必填（招聘方职位 ID）');
    const raw = await apiBossRecGeekList(job_id, page, filters);
    const rawList = raw?.zpData?.geekList || [];
    const geeks = rawList.map(item => _normalizeGeekCard(item.geekCard || item, item));
    return extOk({
      geeks, hasMore: raw?.zpData?.hasMore ?? false,
      totalCount: raw?.zpData?.totalCount ?? geeks.length, page, raw,
    });
  },
});

defineCommand({
  path: 'boss/contact_list',
  description: '获取招聘方联系过的候选人列表',
  handler: async ({ filter = {}, page = 1, source = 2 } = {}) => {
    const raw = await apiBossContactList(filter, page, source);
    const rawList = raw?.zpData?.cardList || [];
    const geeks = rawList.map(item => _normalizeGeekCard(item.geekCard || item, item));
    return extOk({
      geeks, hasMore: raw?.zpData?.hasMore ?? false,
      totalCount: raw?.zpData?.totalCount ?? geeks.length, page, raw,
    });
  },
});

defineCommand({
  path: 'boss/view_geek_info',
  description: '查看候选人详情页（v2 接口）',
  handler: async ({ encrypt_jid, expect_id, security_id, lid = '', entrance = 2 } = {}) => {
    if (!encrypt_jid) throw new Error('encrypt_jid 必填（geekCard.encryptJobId）');
    if (!expect_id) throw new Error('expect_id 必填（geekCard.expectId，正整数）');
    if (!security_id) throw new Error('security_id 必填（geekCard.securityId）');
    const raw = await apiBossViewGeekInfoV2(encrypt_jid, expect_id, security_id, lid, entrance);
    return extOk({ raw });
  },
});

// ── HR 快捷回复短语 / 主动交换电话微信 ───────────────────────────────────

defineCommand({
  path: 'boss/get_quick_replies',
  description: 'Boss 招聘方拉取自定义的快捷回复短语模板列表（聊天框常用语）',
  handler: async () => {
    const raw = await apiBossGetQuickReplies();
    const list = Array.isArray(raw?.zpData) ? raw.zpData : [];
    return extOk({ raw, replies: list, total: list.length });
  },
});

defineCommand({
  path: 'boss/exchange_request',
  description: 'Boss 招聘方主动向候选人发起交换电话/微信（区别于 accept_exchange 被动接受）',
  handler: async ({ security_id, type = 4 } = {}) => {
    if (!security_id) throw new Error('security_id 必填（来自 boss_enter / chat 上下文的 securityId）');
    const raw = await apiBossExchangeRequest(security_id, type);
    return extOk({
      raw,
      blocked: !!raw?.blocked,
      alert_type: raw?.alertType ?? 0,
      type,
      success: !raw?.blocked && (raw?.request?.code === 0),
    });
  },
});

// ── 聊天回复屏蔽预检（批量联系前过滤） ─────────────────────────────────────
defineCommand({
  path: 'boss/check_reply_block',
  description: 'Boss 招聘方预检某候选人是否屏蔽了回复（批量打招呼前过滤，省配额 + 防风控）',
  handler: async ({ encrypt_jid, encrypt_exp_id, security_id } = {}) => {
    if (!encrypt_jid) throw new Error('encrypt_jid 必填（招聘方自己的 encryptJobId）');
    if (!encrypt_exp_id) throw new Error('encrypt_exp_id 必填（候选人 encryptExpId）');
    if (!security_id) throw new Error('security_id 必填（聊天级 securityId）');
    const raw = await apiBossCheckReplyBlock(encrypt_jid, encrypt_exp_id, security_id);
    const reply = raw?.zpData?.replyBlock;
    // replyBlock 可能是 bool 或含状态的对象：
    //   true / { blocked: true }         → 已被屏蔽, 不要再联系
    //   false / null / { blocked: false } → 未屏蔽, 可以联系
    const blocked = reply === true
      || (reply && typeof reply === 'object' && (reply.blocked === true || reply.status === 1));
    return extOk({
      raw,
      blocked: !!blocked,
      reply_block: reply ?? null,
      hunter_call_chat_limit: raw?.zpData?.hunterCallChatLimit ?? null,
    });
  },
});
