/**
 * 求职者端 Job 命令
 *
 * 包含：search_jobs, get_job_detail, start_chat, send_message,
 *       get_chat_history, mark_job_interest, get_recommend_jobs,
 *       get_job_card, get_job_history, get_friend_list, get_geek_job,
 *       get_resume_baseinfo, get_resume_expect, get_resume_status,
 *       get_deliver_list, get_interview_data
 *
 * Token 链（jobs 命名空间）：
 *   listSecurityId → detailSecurityId → chatSecurityId
 *
 * 依赖（全局）：tokenStore, apiSearchJobs, apiGetJobDetail, apiFriendAdd,
 *   apiEnterSession, apiSendMessage, apiGetChatHistory, apiGeekMarkJobInterest,
 *   apiGetRecommendJobs, apiGetJobCard, apiGetJobHistory,
 *   apiGetFriendList, apiGetGeekJob, apiGetResumeBaseinfo, apiGetResumeExpect,
 *   apiGetResumeStatus, apiGetDeliverList, apiGetInterviewData,
 *   apiGeekFilterByLabel, apiGeekGetBossData, apiGetWsEndpoints, apiMsgHistoryPull,
 *   extOk
 */

// ── 辅助：解析聊天所需 token（chat 阶段） ────────────────────────────────

function _resolveChatTokens(encrypt_job_id, security_id) {
  const tokens = tokenStore.getJobTokens(encrypt_job_id);
  const sid = security_id || (tokens && tokens.chatSecurityId);
  const bossId = tokens && (tokens.encryptBossId || tokens.bossId);
  if (!sid) throw new Error(`找不到 ${encrypt_job_id} 的 chatSecurityId，请先调用 start_chat`);
  if (!bossId) throw new Error(`找不到 ${encrypt_job_id} 的 bossId`);
  if (tokenStore.isStale(encrypt_job_id)) {
    console.warn(`[boss-api-ext] ${encrypt_job_id} token 已超过 TTL，chatSecurityId 可能失效，建议重新执行 search_jobs → start_chat`);
  }
  return { sid, bossId };
}

// ── 搜索职位（批量捕获 listSecurityId） ──────────────────────────────────

defineCommand({
  path: 'boss/search_jobs',
  description: '搜索职位，批量捕获 listSecurityId',
  produces: { chain: 'jobs', stage: 'list' },
  handler: async ({ keyword, city = 101010100, page = 1, extra = {} } = {}) => {
    if (!keyword) throw new Error('keyword 必填');
    const raw = await apiSearchJobs(keyword, city, page, extra);
    const jobList = raw && raw.zpData && raw.zpData.jobList;
    if (Array.isArray(jobList) && jobList.length > 0) {
      await tokenStore.storeJobListTokens(jobList);
    }
    return extOk({
      raw,
      tokens_captured: jobList ? jobList.map(j => j.encryptJobId).filter(Boolean) : [],
    });
  },
});

// ── 获取职位详情（消费 list，捕获 detail） ────────────────────────────────

defineCommand({
  path: 'boss/get_job_detail',
  description: '获取职位详情，消费 listSecurityId，捕获 detailSecurityId',
  requires: { chain: 'jobs', stage: 'list', keyParam: 'encrypt_job_id' },
  produces: { chain: 'jobs', stage: 'detail' },
  handler: async ({ encrypt_job_id, security_id } = {}) => {
    if (!encrypt_job_id) throw new Error('encrypt_job_id 必填');
    let sid = security_id;
    let lid = '';
    if (!sid) {
      const tokens = tokenStore.getJobTokens(encrypt_job_id);
      sid = tokens && tokens.listSecurityId;
      lid = (tokens && tokens.lid) || '';
    }
    if (!sid) throw new Error(`找不到 ${encrypt_job_id} 的 listSecurityId，请先调用 search_jobs`);
    const raw = await apiGetJobDetail(sid, lid);
    if (raw && raw.zpData && raw.zpData.securityId) {
      const bossId = raw.zpData.encryptBossId || raw.zpData.bossId || '';
      await tokenStore.storeDetailToken(encrypt_job_id, raw.zpData.securityId, bossId);
    }
    return extOk({ raw, tokens_captured: ['detail_security_id'] });
  },
});

// ── 开启聊天（消费 detail，捕获 chat） ───────────────────────────────────

defineCommand({
  path: 'boss/start_chat',
  description: '向招聘方发起联系，消费 detailSecurityId，捕获 chatSecurityId',
  requires: { chain: 'jobs', stage: 'detail', keyParam: 'encrypt_job_id' },
  produces: { chain: 'jobs', stage: 'chat' },
  handler: async ({ encrypt_job_id, security_id } = {}) => {
    if (!encrypt_job_id) throw new Error('encrypt_job_id 必填');
    let sid = security_id;
    if (!sid) {
      const tokens = tokenStore.getJobTokens(encrypt_job_id);
      sid = tokens && tokens.detailSecurityId;
      if (!sid) {
        const listSid = tokens && tokens.listSecurityId;
        if (!listSid) throw new Error(`找不到 ${encrypt_job_id} 的任何 securityId，请先调用 search_jobs`);
        console.warn(`[boss-api-ext] start_chat: ${encrypt_job_id} 缺少 detailSecurityId，自动调用 get_job_detail`);
        // 自动解析：调用 get_job_detail 获取 detail token
        const detailCmd = getCommand('boss/get_job_detail');
        if (!detailCmd) throw new Error('内部错误：boss/get_job_detail 命令未注册');
        await detailCmd.handler({ encrypt_job_id });
        const updated = tokenStore.getJobTokens(encrypt_job_id);
        sid = updated && updated.detailSecurityId;
        if (!sid) throw new Error(`获取职位详情后仍无 detailSecurityId`);
      }
    }

    const friendRaw = await apiFriendAdd(sid, encrypt_job_id);
    let chatSid = friendRaw && friendRaw.zpData && friendRaw.zpData.securityId;
    if (chatSid) {
      await tokenStore.storeChatToken(encrypt_job_id, chatSid);
    }

    if (friendRaw && friendRaw.code !== 0) {
      const dialog = (friendRaw.zpData && friendRaw.zpData.bizData && friendRaw.zpData.bizData.chatRemindDialog) || null;
      return extOk({
        raw: { friend_add: friendRaw, session_enter: null },
        tokens_captured: [],
        warn: friendRaw.message || '开聊受限',
        dialog_title: dialog && dialog.title,
        dialog_content: dialog && dialog.content,
      });
    }

    const sessionRaw = await apiEnterSession(chatSid);
    return extOk({
      raw: { friend_add: friendRaw, session_enter: sessionRaw },
      tokens_captured: ['chat_security_id'],
      greeting: friendRaw.zpData && friendRaw.zpData.greeting,
    });
  },
});

// ── 发送消息（消费 chat token） ───────────────────────────────────────────

defineCommand({
  path: 'boss/send_message',
  description: '向招聘方发送消息（自动 enterSession 激活会话,消费 chatSecurityId）',
  requires: { chain: 'jobs', stage: 'chat', keyParam: 'encrypt_job_id' },
  handler: async ({ encrypt_job_id, content, security_id, encrypt_uid } = {}) => {
    if (!content) throw new Error('content 必填');

    // 解析 chatSecurityId / encryptJobId / bossId(三种入参方式):
    //   (a) 已知 encrypt_job_id —— 老路径,从 tokenStore.jobs[eid] 取
    //   (b) 只知 encrypt_uid (= encryptFriendId,从 friendList 进入)—— 反查 lookupChatTokenByBoss
    //   (c) 显式传 security_id —— 优先用
    let sid = security_id;
    let eid = encrypt_job_id;
    let bossId = '';

    if (eid) {
      const tokens = tokenStore.getJobTokens(eid);
      if (!sid) sid = tokens?.chatSecurityId;
      bossId = tokens?.encryptBossId || tokens?.bossId;
    }
    if (!sid && encrypt_uid) {
      const t = tokenStore.lookupChatTokenByBoss(encrypt_uid);
      if (t) {
        sid = t.chatSecurityId;
        eid = t.encryptJobId;
        bossId = encrypt_uid;
      }
    }
    if (!bossId && encrypt_uid) bossId = encrypt_uid;

    if (!sid) {
      throw new Error('chat_token_not_captured: 未找到 chatSecurityId,请先调 boss_geek_filter_by_label 让扩展批量捕获 token,或让用户在 Boss 浏览器里打开和该 boss 的对话页');
    }
    if (!bossId) throw new Error('找不到对应的 bossId(encryptBossId / encryptFriendId)');
    if (!eid) throw new Error('找不到对应的 encrypt_job_id');

    // **关键**(2026-04-28 抓包反推):Boss page 在打开会话时一定会先调 geekEnter
    // 激活会话,之后 sendMsg 才合法。我们的旧路径直接 sendMsg 会被 Boss 返回
    // code 121"请求不合法",误以为对方未接受。这里在第一次 send 前自动 enter,
    // 之后用 sessionEntered flag 缓存避免重复浪费 RT。
    const tok = tokenStore.getJobTokens(eid) || {};
    if (!tok.sessionEntered) {
      try {
        await apiEnterSession(sid);
        await tokenStore.markSessionEntered(eid);
      } catch (e) {
        console.warn('[boss-api-ext] enterSession 失败,继续 sendMsg(可能已激活):', e?.message || e);
      }
    }

    const raw = await apiSendMessage(bossId, sid, content);
    return extOk({ raw });
  },
});

// ── 获取聊天记录（消费 chat token） ──────────────────────────────────────

defineCommand({
  path: 'boss/get_chat_history',
  description: '获取与招聘方的聊天记录，消费 chatSecurityId',
  requires: { chain: 'jobs', stage: 'chat', keyParam: 'encrypt_job_id' },
  handler: async ({ encrypt_job_id, max_msg_id = '', security_id } = {}) => {
    if (!encrypt_job_id) throw new Error('encrypt_job_id 必填');
    const { sid, bossId } = _resolveChatTokens(encrypt_job_id, security_id);
    const raw = await apiGetChatHistory(bossId, sid, max_msg_id);
    return extOk({ raw });
  },
});

// ── 按 boss 反查 chat token —— "查看和某 boss 历史"路径 ────────────────────
// boss_geek_filter_by_label 的 friendList 项只有 encryptFriendId(== encryptBossId),
// 没有 encryptJobId / chatSecurityId。当用户在 Boss 浏览器里打开和某 boss 的对话
// 页时,Boss 前端会调 friend/add 接口,扩展 intercept 把 (encryptJobId, securityId)
// 写入 tokenStore.jobs[encryptJobId](encryptBossId 字段在 storeDetailToken /
// storeJobListTokens 时建立)。本命令暴露按 encryptBossId 反查 lookup,后端的
// boss_get_chat_history MCP 工具在 encrypt_job_id / security_id 都为空时调用本
// 命令拿到双键值,再调 get_chat_history。
defineCommand({
  path: 'boss/lookup_chat_token',
  description: '按 encrypt_uid (encryptBossId / encryptFriendId) 反查 tokenStore 里捕获的 chatSecurityId + encryptJobId',
  handler: async ({ encrypt_uid } = {}) => {
    if (!encrypt_uid) throw new Error('encrypt_uid 必填');
    const t = tokenStore.lookupChatTokenByBoss(encrypt_uid);
    if (!t) {
      return extOk({
        found: false,
        hint: '尚未在 Boss 浏览器里打开过和该 boss 的对话页 —— 请引导用户打开 Boss 消息页 → 点击该对话,扩展会自动 intercept friend/add 响应捕获令牌。',
      });
    }
    return extOk({
      found: true,
      encrypt_job_id: t.encryptJobId,
      chat_security_id: t.chatSecurityId,
      captured_at: t.capturedAt,
    });
  },
});

// ── 收藏 / 不感兴趣（消费 list token） ───────────────────────────────────

defineCommand({
  path: 'geek/mark_job_interest',
  description: '标记职位感兴趣或不感兴趣（flag=1: 感兴趣, flag=0: 不感兴趣）',
  requires: { chain: 'jobs', stage: 'list', keyParam: 'job_id' },
  handler: async ({ job_id, security_id, flag = 1, expect_id = '', tag = 4, lid = '' } = {}) => {
    if (!job_id) throw new Error('job_id 必填');
    let sid = security_id;
    let jobLid = lid;
    if (!sid) {
      if (tokenStore.isStale(job_id)) {
        throw new Error(`${job_id} 的 listSecurityId 不存在或已过期（>5分钟），请先调用 boss/search_jobs 重新搜索`);
      }
      const tokens = tokenStore.getJobTokens(job_id);
      sid = tokens && tokens.listSecurityId;
      jobLid = jobLid || (tokens && tokens.lid) || '';
    }
    if (!sid) throw new Error(`找不到 ${job_id} 的 listSecurityId，请先调用 boss/search_jobs`);

    const extraHeaders = {};
    if (tokenStore.session.bossToken) extraHeaders['token'] = tokenStore.session.bossToken;
    if (tokenStore.session.zpStoken) extraHeaders['zp_token'] = tokenStore.session.zpStoken;

    const raw = await apiGeekMarkJobInterest(sid, job_id, Number(flag), expect_id, tag, jobLid, extraHeaders);
    const json = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (json?.code !== 0) {
      const hint = json?.code === 121 ? '（listSecurityId 已失效，请重新调用 boss/search_jobs 并立即收藏）' : '';
      throw new Error(`${json?.message || '操作失败'}${hint}`);
    }
    return extOk({ job_id, flag: Number(flag), collected: Number(flag) === 1 });
  },
});

// ── 推荐职位 ──────────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/get_recommend_jobs',
  description: '获取推荐职位列表',
  handler: async ({ page = 1 } = {}) => {
    return extOk({ raw: await apiGetRecommendJobs(page), page });
  },
});

defineCommand({
  path: 'boss/get_job_card',
  description: '获取职位卡片详情',
  handler: async ({ security_id, lid = '' } = {}) => {
    if (!security_id) throw new Error('security_id 必填');
    return extOk({ raw: await apiGetJobCard(security_id, lid) });
  },
});

defineCommand({
  path: 'boss/get_job_history',
  description: '获取浏览过的职位历史',
  handler: async ({ page = 1 } = {}) => {
    return extOk({ raw: await apiGetJobHistory(page), page });
  },
});

// ── 社交 / 互动 ───────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/get_friend_list',
  description: '获取好友（已沟通）列表（旧接口，前端已切到 geek_filter_by_label）',
  handler: async () => extOk({ raw: await apiGetFriendList() }),
});

// ── 求职者消息中心（2026-04 抓包反推：geekFilterByLabel + getBossData） ───

defineCommand({
  path: 'boss/geek_filter_by_label',
  description: '求职者侧消息中心列表，按 tab 筛选（labelId: 0=全部 1=新招呼 2=仅沟通 3=有交换 4=有面试 5=不感兴趣）',
  handler: async ({ label_id = 0, encrypt_system_id = '', name = '' } = {}) => {
    // Stage 1:filter by label → 稀疏 friendList(只有 friendId / encryptFriendId / updateTime)
    const stage1 = await apiGeekFilterByLabel(label_id, encrypt_system_id, name);
    const friendList = stage1?.zpData?.friendList || [];

    if (!friendList.length) {
      return extOk({
        raw: stage1,
        label_id: Number(label_id),
        friends: [],
        tokens_captured: [],
      });
    }

    // Stage 2(2026-04-28 抓包反推):用 friendIds 批量拉完整详情。
    // 真正的 chatSecurityId / encryptJobId / encryptBossId 在这一步才齐。
    // Boss page 也是这样两阶段调,我们对齐它的行为。
    const friendIds = friendList.map(f => f.friendId).filter(Boolean);
    let fullList = [];
    let tokensCaptured = 0;
    let stage2Failed = false;
    try {
      const stage2 = await apiGetGeekFriendListBatch(friendIds);
      fullList = stage2?.zpData?.result || [];

      // 把每个 friend 的 (encryptJobId, securityId, encryptBossId) 写 tokenStore,
      // 让下游 boss_get_chat_history / boss_geek_get_boss_data 通过
      // lookupChatTokenByBoss(encryptUid) 反查命中,无需 LLM 显式传 security_id。
      for (const f of fullList) {
        if (f && f.encryptJobId && f.securityId && f.encryptBossId) {
          await tokenStore.storeChatToken(f.encryptJobId, f.securityId, f.encryptBossId);
          tokensCaptured++;
        }
      }
    } catch (e) {
      console.warn('[boss-api-ext] stage 2 batch friend list failed:', e?.message || e);
      stage2Failed = true;
      // 降级:用 stage 1 sparse 数据兜底,至少让 agent 看到 friendId / encryptFriendId,
      // LLM 渲染时只能给"详情待加载",但比 friends: [] 让用户以为列表为空好。
      fullList = friendList.map(f => ({
        encryptFriendId: f.encryptFriendId,
        friendId: f.friendId,
        updateTime: f.updateTime,
        waterLevel: f.waterLevel,
        _stage2_failed: true,
      }));
    }

    return extOk({
      raw: stage1,
      label_id: Number(label_id),
      friends: fullList,                            // 给 agent 完整渲染(失败时降级)
      stage2_failed: stage2Failed || undefined,     // 显式标记,prompt 据此调整文案
      tokens_captured: tokensCaptured ? [`chat_security_id × ${tokensCaptured}`] : [],
    });
  },
});

defineCommand({
  path: 'boss/geek_get_boss_data',
  description: '求职者进入聊天前拉 boss 元信息 + 关联职位（含 encryptJobId、salary）。security_id 留空时由 ext 反查 tokenStore(geek_filter_by_label 已自动写入);成功后顺手 enterSession 激活会话,后续 sendMsg 直接成功。',
  handler: async ({ boss_id, security_id = '', boss_source = 0 } = {}) => {
    if (!boss_id) throw new Error('boss_id 必填（encryptBossId / encryptFriendId）');
    let sid = security_id;
    if (!sid) {
      // 反查 tokenStore:geek_filter_by_label 调用过后,boss_id 对应的 chatSecurityId
      // 应该已经写入 jobs[encryptJobId].chatSecurityId(by encryptBossId 索引)
      const t = tokenStore.lookupChatTokenByBoss(boss_id);
      sid = t && t.chatSecurityId;
      if (!sid) {
        throw new Error('chat_token_not_captured: 未找到该 boss 的 chatSecurityId。请先调 boss_geek_filter_by_label 让扩展批量捕获 token,或让用户在 Boss 浏览器里打开和该 boss 的对话页。');
      }
    }
    const raw = await apiGeekGetBossData(boss_id, sid, boss_source);

    // 顺手激活 session(对齐 Boss page 行为:打开会话时调 geekEnter)。
    // 这样下游 sendMsg 不必再 enter。失败不阻塞。
    const ejid = raw?.zpData?.data?.encryptJobId;
    if (ejid) {
      try {
        await apiEnterSession(sid);
        await tokenStore.markSessionEntered(ejid);
      } catch (e) {
        console.warn('[boss-api-ext] geek_get_boss_data 顺手 enterSession 失败:', e?.message || e);
      }
    }

    return extOk({ raw });
  },
});

// ── 主动激活聊天会话(独立命令,供需要时调用)────────────────────────────
// Boss page 打开会话时会自动 enterSession,我们补齐:
// - 用户主动想"打开和某 boss 的对话"但暂不发消息时
// - boss_send_message 已自动 enter,这个命令通常不需要直接被 LLM 调
defineCommand({
  path: 'boss/enter_chat_session',
  description: '激活与某 boss 的聊天会话(对齐 Boss page 打开会话时的 geekEnter POST)',
  handler: async ({ encrypt_uid, encrypt_job_id, security_id = '' } = {}) => {
    let sid = security_id;
    let eid = encrypt_job_id;
    if (eid && !sid) {
      sid = tokenStore.getJobTokens(eid)?.chatSecurityId;
    }
    if (!sid && encrypt_uid) {
      const t = tokenStore.lookupChatTokenByBoss(encrypt_uid);
      if (t) { sid = t.chatSecurityId; eid = t.encryptJobId; }
    }
    if (!sid) throw new Error('chat_token_not_captured: 找不到 chatSecurityId,请先调 boss_geek_filter_by_label');
    const raw = await apiEnterSession(sid);
    if (eid) await tokenStore.markSessionEntered(eid);
    return extOk({ raw, encrypt_job_id: eid });
  },
});

defineCommand({
  path: 'boss/get_ws_endpoints',
  description: '获取 Boss WebSocket 服务器列表（实时推送入口）',
  handler: async () => extOk({ raw: await apiGetWsEndpoints() }),
});

defineCommand({
  path: 'boss/msg_history_pull',
  description: '离线消息增量补拉（WS 重连后用）',
  handler: async ({ type = 0, last_id = 0, secret_id = '' } = {}) => {
    return extOk({ raw: await apiMsgHistoryPull(type, last_id, secret_id) });
  },
});

defineCommand({
  path: 'boss/get_geek_job',
  description: '获取求职者投递的职位（自己视角）',
  handler: async ({ security_id } = {}) => {
    if (!security_id) throw new Error('security_id 必填');
    return extOk({ raw: await apiGetGeekJob(security_id) });
  },
});

// ── 个人中心 ──────────────────────────────────────────────────────────────

defineCommand({
  path: 'boss/get_resume_baseinfo',
  description: '获取简历基本信息',
  handler: async () => extOk({ raw: await apiGetResumeBaseinfo() }),
});

defineCommand({
  path: 'boss/get_resume_expect',
  description: '获取简历期望岗位信息',
  handler: async () => extOk({ raw: await apiGetResumeExpect() }),
});

defineCommand({
  path: 'boss/get_resume_status',
  description: '获取简历状态',
  handler: async () => extOk({ raw: await apiGetResumeStatus() }),
});

defineCommand({
  path: 'boss/get_deliver_list',
  description: '获取投递记录列表',
  handler: async ({ page = 1 } = {}) => {
    return extOk({ raw: await apiGetDeliverList(page), page });
  },
});

defineCommand({
  path: 'boss/get_interview_data',
  description: '获取面试数据',
  handler: async () => extOk({ raw: await apiGetInterviewData() }),
});
