/**
 * LinkedIn 命令
 *
 * 包含：linkedin/check_login, linkedin/search_people, linkedin/get_profile,
 *       linkedin/connect, linkedin/send_message, linkedin/search_jobs
 *
 * 令牌链（linkedin_people 命名空间）：
 *   search（trackingId）→ profile（profileId）→ contact（conversationUrn）
 *
 * 依赖（全局）：tokenStore, apiLinkedinSearchPeople, apiLinkedinGetProfile,
 *   apiLinkedinConnect, apiLinkedinSendMessage, apiLinkedinSearchJobs, extOk
 */

// ── 登录状态检测 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin/check_login',
  description: 'LinkedIn 检查登录状态（检测 li_at cookie，并返回 memberId/name）',
  handler: async (params = {}) => {
    // force_reset=true 时(用户开了 VPN 后从 chat chip 触发重试),先清扩展侧
    // region-blocked 缓存 —— 否则 5 分钟 TTL 内任何 LinkedIn 工具调用都会被
    // 短路返回 region_blocked: true,用户以为 VPN 没生效。
    if (params && params.force_reset === true) {
      try {
        if (typeof self.clearSiteRegionBlocked === 'function') {
          self.clearSiteRegionBlocked('linkedin');
          console.log('[linkedin] force_reset:已清 region-blocked 缓存');
        }
      } catch (e) {
        console.warn('[linkedin] force_reset 清缓存失败:', e?.message || e);
      }
    }

    // 用 url 参数查 cookie 比 domain 可靠（能穿透 partitioned / 子域 情况）
    // 两种写法都查一遍，避免 Chrome 版本/分区差异导致漏判
    const [byUrl, byDomain] = await Promise.all([
      chrome.cookies.getAll({ url: 'https://www.linkedin.com', name: 'li_at' }),
      chrome.cookies.getAll({ domain: '.linkedin.com', name: 'li_at' }),
    ]);
    const cookieOk = (byUrl.length > 0 && !!byUrl[0].value)
                   || (byDomain.length > 0 && !!byDomain[0].value);

    // 尝试调用 /me，成功则强证据已登录（即使 cookie 查不到，也承认为登录）
    let memberId = '';
    let name = '';
    let publicIdentifier = '';
    let apiOk = false;
    let regionBlocked = false;
    let regionBlockedReason = '';
    try {
      const me = await apiLinkedinGetMe();
      const mp = me?.miniProfile || me?.plainId && me || {};
      memberId = String(me?.plainId || mp?.objectUrn?.match(/(\d+)$/)?.[1] || '').trim();
      publicIdentifier = String(mp?.publicIdentifier || me?.miniProfile?.publicIdentifier || '').trim();
      const first = mp?.firstName || me?.miniProfile?.firstName || '';
      const last = mp?.lastName || me?.miniProfile?.lastName || '';
      name = `${first} ${last}`.trim();
      apiOk = !!(memberId || name || publicIdentifier);
    } catch (e) {
      const msg = String(e?.message || e);
      if (msg.includes('REGION_BLOCKED')) {
        // site-executor 检测到 linkedin.com 被重定向(大陆地区 / 公司内网),
        // 5 分钟内不再自动开 tab。结构化告诉上游(agent / 前端),并给用户开
        // VPN 后从 popup 手动重试的提示。
        regionBlocked = true;
        regionBlockedReason = msg.replace(/^.*REGION_BLOCKED.*?:\s*/, '');
      } else {
        console.warn('[linkedin] /me 请求失败:', msg);
      }
    }

    if (regionBlocked) {
      return extOk({
        logged_in: false,
        region_blocked: true,
        reason: regionBlockedReason,
        hint: 'LinkedIn 在当前网络环境无法访问(可能被重定向到 linkedin.cn / HTTP 451)。请使用 VPN 后,从扩展 popup 点击"重新尝试 LinkedIn"按钮。',
      });
    }

    const loggedIn = apiOk || cookieOk;
    if (!loggedIn) return extOk({ logged_in: false });

    try {
      if (typeof reportSiteStatus === 'function') {
        reportSiteStatus('linkedin', memberId);
      }
    } catch (_) {}

    // 落 mailbox URN 到 tokenStore — 后续 messaging 工具(list_conversations
    // / list_inbox_counts / precheck_compose)默认从这里读,不再每次 _deriveOwnMailboxUrn
    let selfMailboxUrn = '';
    try {
      selfMailboxUrn = await _deriveOwnMailboxUrn();
      if (selfMailboxUrn) {
        tokenStore.setChainToken('linkedin_messaging', memberId || 'self', 'mailboxUrn', selfMailboxUrn);
      }
    } catch (e) {
      console.warn('[linkedin] selfMailboxUrn 派生失败:', e?.message || e);
    }

    return extOk({ logged_in: true, memberId, name, publicIdentifier, selfMailboxUrn });
  },
});

// ── 搜索人才（捕获 search 阶段 token） ───────────────────────────────────

defineCommand({
  path: 'linkedin/search_people',
  description: 'LinkedIn 搜索候选人(worker tab + DOM 抓取,适配 SDUI 新架构)',
  produces: { chain: 'linkedin_people', stage: 'search' },
  handler: async ({ keywords, count = 10, start = 0, network = '' } = {}) => {
    if (!keywords) throw new Error('keywords 必填');

    // LinkedIn 把 People Search 整体迁移到 SDUI/RSC 二进制 protobuf 架构
    // (capture_rec_17774346 验证),旧 voyagerSearchDashByAll GraphQL 已死。
    // 改走 worker tab 加载 web UI 同款 URL → 等 SPA hydrated → MAIN-world
    // querySelectorAll 抓人员卡。LinkedIn UI 看到啥我们就拿到啥。
    const params = new URLSearchParams({
      keywords,
      origin: 'GLOBAL_SEARCH_HEADER',
    });
    // network filter: F=1 度好友, S=2 度, O=3+/外网。recruiter 关心 1+2 度多。
    if (network) params.set('network', `["${network}"]`);
    // start>0 时翻页(LinkedIn 用 page 参数, 1-indexed, 每页 ~10)
    if (start > 0) params.set('page', String(Math.floor(start / 10) + 1));

    const url = `${LINKEDIN_ORIGIN}/search/results/people/?${params.toString()}`;

    // ensureSiteWorkerTab + chrome.tabs.update 走 background tab,但 LinkedIn
    // 的 SDUI hydration 在 background 会被 Chrome 节流。临时切 active 让它
    // 加载完;scrape 完后让用户自然把焦点切回去。
    const tab = await ensureSiteWorkerTab('linkedin');
    if (!tab || !tab.id) throw new Error('LinkedIn Worker Tab 不可用');
    await chrome.tabs.update(tab.id, { url, active: true });
    await waitForSiteTabLoad(tab.id, 20000);

    // SPA hydration 通常在 load 完后 1-3s 才把人员卡渲染到 DOM,scraper 内
    // 部已轮询 + 多版本兼容选择器,timeout 给 12s 足够 first paint。
    const scraped = await executeSiteFormAction(
      'linkedin', _scrapeLinkedinPeopleResults, count, 12000,
    );

    const people = (scraped?.people || []).slice(0, count);

    // 写入 token chain(下游 connect/send_message 通过 memberUrn 查 publicId)
    const chainEntries = people.map((p) => ({
      entityKey: p.memberUrn,
      field: 'trackingId',
      value: p.trackingId || p.publicId, // SDUI 新版没 trackingId,用 publicId 占位
      meta: {
        publicId: p.publicId,
        name: p.name,
        headline: p.headline,
        location: p.location,
        degree: p.degree,
      },
    }));
    if (chainEntries.length > 0) {
      await tokenStore._batchSetChainTokens('linkedin_people', chainEntries);
    }

    // _debug 仅在 scraper 失败时(scraped.debug 非空)透传,成功响应不带 debug
    // —— 避免 LLM 把 debug 字段当成不可信信号
    const out = {
      people,
      total: people.length,
      tokens_captured: people.map((p) => p.memberUrn),
    };
    if (scraped?.debug) out._debug = scraped.debug;
    return extOk(out);
  },
});

// ── 获取个人资料 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin/get_profile',
  description: 'LinkedIn 获取成员个人资料，消费 search token，捕获 profile token',
  requires: { chain: 'linkedin_people', stage: 'search', keyParam: 'member_urn' },
  produces: { chain: 'linkedin_people', stage: 'profile' },
  handler: async ({ member_urn, public_id } = {}) => {
    if (!member_urn && !public_id) throw new Error('member_urn 或 public_id 必填');
    const pid = public_id
      || tokenStore.getChainToken('linkedin_people', member_urn, 'publicId')?.value;
    if (!pid) throw new Error(`找不到 ${member_urn} 的 publicId，请先调用 linkedin/search_people`);

    const raw = await apiLinkedinGetProfile(pid);
    const profile = raw?.elements?.[0] || {};
    const profileId = profile.entityUrn || member_urn;
    // 审核补丁 #R2：await 持久化，避免 SW 重启后 profileId 丢失。
    await tokenStore.setChainToken('linkedin_people', member_urn, 'profileId', profileId);
    return extOk({ profile, tokens_captured: ['profileId'] });
  },
});

// ── 发送连接请求 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin/connect',
  description: 'LinkedIn 发送连接请求，需先 search_people 获取 trackingId',
  requires: { chain: 'linkedin_people', stage: 'search', keyParam: 'member_urn' },
  handler: async ({ member_urn, message = '' } = {}) => {
    if (!member_urn) throw new Error('member_urn 必填');
    const trackingId = tokenStore.getChainToken('linkedin_people', member_urn, 'trackingId')?.value || '';
    const raw = await apiLinkedinConnect(member_urn, trackingId, message);
    return extOk({ raw });
  },
});

// ── 获取连接程度 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin/get_connection_degree',
  description: 'LinkedIn 获取与目标成员的连接程度（1st/2nd/3rd/None）',
  requires: { chain: 'linkedin_people', stage: 'search', keyParam: 'member_urn' },
  handler: async ({ member_urn, public_id } = {}) => {
    if (!member_urn && !public_id) throw new Error('member_urn 或 public_id 必填');
    const pid = public_id
      || tokenStore.getChainToken('linkedin_people', member_urn, 'publicId')?.value;
    if (!pid) throw new Error(`找不到 publicId，请先调用 linkedin/search_people 或传入 public_id`);
    const raw = await apiLinkedinGetConnectionDegree(pid);
    const degreeVal = raw?.distance?.value ?? null;
    const connectionType = raw?.memberRelationship?.connectionType || null;
    return extOk({ degree: degreeVal, connectionType, raw });
  },
});

// ── 发送站内消息 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin/send_message',
  description: 'LinkedIn 发送站内消息（InMail 或 1 度好友消息）',
  requires: { chain: 'linkedin_people', stage: 'search', keyParam: 'member_urn' },
  handler: async ({ member_urn, body, subject = '' } = {}) => {
    if (!member_urn) throw new Error('member_urn 必填');
    if (!body) throw new Error('body 必填');

    // Degree check — warn (not block) if non-1st-degree connection (InMail credit risk)
    let warn = null;
    try {
      const pid = tokenStore.getChainToken('linkedin_people', member_urn, 'publicId')?.value;
      if (pid) {
        const networkInfo = await apiLinkedinGetConnectionDegree(pid);
        const degreeVal = networkInfo?.distance?.value;
        if (typeof degreeVal === 'number' && degreeVal > 1) {
          warn = `${degreeVal}nd-degree connection — this will consume an InMail credit. Proceed with intent.`;
        }
      }
    } catch (_) {}

    const raw = await apiLinkedinSendMessage(member_urn, body, subject);
    return extOk({ raw, ...(warn ? { warn } : {}) });
  },
});

// ── 搜索职位 ──────────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin/search_jobs',
  description: 'LinkedIn 搜索职位',
  produces: { chain: 'linkedin_jobs', stage: 'search' },
  handler: async ({
    keywords,
    // 新：页码/每页；旧：start/count — 两种都接（上游 job-api-gateway 传的是 page/pageSize）
    page = null, pageSize = null,
    start = null, count = null,
    // 新：数字 geo id；旧：location（保留兼容，只要是纯数字就视作 geoId）
    geoLocationId = '', location = '',
  } = {}) => {
    if (!keywords) throw new Error('keywords 必填');

    const pageSizeN = Number(pageSize) > 0 ? Number(pageSize) : (Number(count) > 0 ? Number(count) : 25);
    const startN = Number(start) >= 0
      ? Number(start)
      : (Number(page) > 0 ? (Number(page) - 1) * pageSizeN : 0);
    // location 若是数字串视为 geoId；文本值（"United States" 等）忽略
    const geoId = (geoLocationId || (/^\d+$/.test(location || '') ? location : '')).trim();

    const raw = await apiLinkedinSearchJobs(keywords, geoId, startN, pageSizeN);

    // 响应形如：{ data:{ elements:[{ jobCardUnion:{ "*jobPostingCard": urn } }] }, included:[...] }
    const elements = raw?.data?.elements || [];
    const included = raw?.included || [];
    const byUrn = {};
    for (const it of included) {
      if (it?.entityUrn) byUrn[it.entityUrn] = it;
    }

    const jobs = [];
    // 审核补丁 #R2：收集后 batch await，避免 fire-and-forget 丢 chain token。
    const chainEntries = [];
    for (const el of elements) {
      const cardUrn = el?.jobCardUnion?.['*jobPostingCard'];
      if (!cardUrn) continue;
      const card = byUrn[cardUrn] || {};
      const jobPostingUrn = card['*jobPosting'] || card.jobPosting || '';
      const posting = byUrn[jobPostingUrn] || {};
      const companyUrn = posting['*primaryDescription'] || posting['*companyDetails'] || '';
      const company = byUrn[companyUrn] || {};
      // jobId 埋在 urn 形如 urn:li:fsd_jobPostingCard:(4322939058,JOBS_SEARCH)
      const jobId = (cardUrn.match(/\((\d+)\s*,/) || [])[1] || '';
      if (!jobId) continue;

      const job = {
        jobId,
        jobUrn: jobPostingUrn || `urn:li:fsd_jobPosting:${jobId}`,
        title: card.jobPostingTitle || posting.title || '',
        companyName: company.name || card.primaryDescription?.text || '',
        location: card.secondaryDescription?.text || '',
        listedAt: posting.listedAt || null,
      };
      jobs.push(job);
      chainEntries.push({
        entityKey: jobId,
        field: 'searchToken',
        value: jobId,
        meta: { title: job.title, companyName: job.companyName },
      });
    }
    if (chainEntries.length > 0) {
      await tokenStore._batchSetChainTokens('linkedin_jobs', chainEntries);
    }

    return extOk({ jobs, total: jobs.length, tokens_captured: jobs.map(j => j.jobId) });
  },
});

// ── 单职位详情（含 JD 正文，分析职位匹配度的核心数据源）──────────────────
//
// 抓包决策（capture_rec_17774285）：
//   - search 列表 (voyagerJobsDashJobCards) 只返回 title/company/city/listedAt,
//     没有 description/salary/skills —— 单靠列表给 _score_jobs_with_llm 评分
//     效果差。
//   - voyagerJobsDashJobPostings GraphQL 一次返回 JD 正文 + companyDetails +
//     formattedLocation + employmentStatus + jobFunctions + workplaceTypes +
//     industryV2Taxonomy + originalListedAt + companyApplyUrl，正是分析所需。
//
// 后端 linkedin_commands._extract_linkedin_detail_fields 已经按 raw.included[]
// 里 $type=*JobPosting 的对象抽 description/formattedLocation,这里直接把
// LinkedIn 原始响应整体放进 raw 即可,不用前端拍平 —— 复用现有缓存逻辑。

defineCommand({
  path: 'linkedin/get_job_detail',
  description: 'LinkedIn 查看单职位详情（JD 正文 / 公司 / 地点 / 雇佣类型 / 行业，缺 queryId 时自动 bootstrap）',
  handler: async ({ jobId } = {}) => {
    if (!jobId) throw new Error('jobId 必填');
    const idStr = String(jobId);

    // 自动 bootstrap：第一次调用时 queryId 通常没捕获。降级策略：静默
    // navigate Worker Tab 到 LinkedIn 职位首页 → LinkedIn 自家 JS 渲染推荐
    // 列表时会为前 N 条职位批量 fire voyagerJobsDashJobPostings GraphQL →
    // MAIN-world interceptor 把 queryId 写入 chrome.storage.local → 我们
    // 等到后重试。整个过程对用户透明,首次慢 5-15 秒,之后 7 天 TTL 内瞬时。
    //
    // 抓包验证(capture_rec_17774285): /flagship-web/jobs/ 加载后 ~6s 内连发
    // 8 次 voyagerJobsDashJobPostings,而 /jobs/view/{id}/ 是 SSR/RSC 路径
    // 浏览器端不发 GraphQL —— 早期版本用错了 URL 导致 bootstrap 永远超时。
    let raw;
    try {
      raw = await apiLinkedinGetJobDetail(idStr);
    } catch (err) {
      const msg = String(err && err.message || err);
      if (!msg.includes('尚未捕获')) throw err;  // 仅 queryId 缺失走 bootstrap

      // 1) 静默 ensure + navigate Worker Tab(不抢用户焦点)
      const tab = await ensureSiteWorkerTab('linkedin');
      if (!tab || !tab.id) throw new Error('LinkedIn Worker Tab 不可用,请先确认已登录');
      await chrome.tabs.update(tab.id, { url: 'https://www.linkedin.com/jobs/' });
      await waitForSiteTabLoad(tab.id, 20000);

      // 2) 轮询 15 秒等 queryId 落盘。LinkedIn 推荐列表 GraphQL 通常在 page
      //    load 后 2-6 秒批量发起,留足余量。命中立即继续。
      const entry = await _waitForLinkedinQueryId('voyagerJobsDashJobPostings', 15000);
      if (!entry) {
        // 失败时附带诊断:列出当前 storage 里所有 queryId,帮排查是登录态丢
        // 还是 LinkedIn 改了请求模式
        let captured = [];
        try {
          const stored = await chrome.storage.local.get('linkedin_queryIds');
          captured = Object.keys(stored.linkedin_queryIds || {});
        } catch (_) {}
        const diag = captured.length
          ? `已捕获其它 queryId: ${captured.slice(0, 5).join(', ')}${captured.length > 5 ? '...' : ''}`
          : '当前 storage 里 0 个 LinkedIn queryId(MAIN-world 拦截器可能未注入)';
        throw new Error(
          `LinkedIn queryId 自动 bootstrap 失败 — 请确认已登录 LinkedIn 并刷新扩展。诊断: ${diag}`,
        );
      }

      // 3) 重试。此时 queryId 已就位,正常 1 次请求拿到详情
      raw = await apiLinkedinGetJobDetail(idStr);
    }

    // 从 included[] 摘 JobPosting / Geo / Company,拍平一份 friendly view
    // 给后端 / LLM 直接消费;raw 整体保留供 _extract_linkedin_detail_fields 兜底。
    const included = raw?.included || [];
    let posting = null;
    let geo = null;
    let company = null;
    for (const it of included) {
      if (!it || !it.$type) continue;
      if (it.$type === 'com.linkedin.voyager.dash.jobs.JobPosting') posting = it;
      else if (it.$type === 'com.linkedin.voyager.dash.common.Geo') geo = it;
      else if (it.$type === 'com.linkedin.voyager.dash.organization.Company') company = it;
    }

    const description = posting?.description?.text || '';
    const companyName = posting?.companyDetails?.name || company?.name || '';
    const formattedLocation = geo?.defaultLocalizedName || geo?.abbreviatedLocalizedName || '';
    const workplaceTypes = Array.isArray(posting?.workplaceTypes) ? posting.workplaceTypes : [];
    const employmentStatus = (posting?.['*employmentStatus'] || '').replace(/^urn:li:fsd_employmentStatus:/, '');

    return extOk({
      jobId: String(jobId),
      // raw 字段保留三层信息,后端已经按 raw.description.text / raw.included[] 解析
      raw: {
        description: { text: description },
        formattedLocation,
        companyDetails: { name: companyName },
        workplaceTypes,
        employmentStatus,
        title: posting?.title || '',
        listedAt: posting?.listedAt || posting?.originalListedAt || null,
        applyUrl: posting?.companyApplyUrl || '',
        jobFunctions: posting?.jobFunctions || [],
        included,
      },
    });
  },
});

// ── Easy Apply 表单自动填写 ──────────────────────────────────────────────

defineCommand({
  path: 'linkedin/apply',
  description: 'LinkedIn Easy Apply 自动投递（多步表单填写，混合模式）',
  handler: async ({ jobId, profileData = {} } = {}) => {
    if (!jobId) throw new Error('jobId 必填');

    // 导航到职位页面并切到前台（LinkedIn 模态框需要可见标签页）
    const jobUrl = `https://www.linkedin.com/jobs/view/${jobId}/`;
    const tab = await navigateSiteWorkerTab('linkedin', jobUrl);
    await waitForSiteTabLoad(tab.id, 15000);
    await new Promise(r => setTimeout(r, 1500)); // 等待页面 JS 完成渲染

    const result = await linkedinEasyApply(tab.id, jobId, profileData);
    return extOk(result);
  },
});

defineCommand({
  path: 'linkedin/get_apply_form',
  description: 'LinkedIn 获取 Easy Apply 表单结构（不填写，只预览）',
  handler: async ({ jobId } = {}) => {
    if (!jobId) throw new Error('jobId 必填');

    // 导航到职位页面
    const jobUrl = `https://www.linkedin.com/jobs/view/${jobId}/`;
    const tab = await navigateSiteWorkerTab('linkedin', jobUrl);
    await waitForSiteTabLoad(tab.id, 15000);
    await new Promise(r => setTimeout(r, 1500));

    // 点击 Easy Apply 按钮打开模态框
    const clickResult = await executeFormActionOnTab(tab.id, formClickElement,
      '.jobs-apply-button--top-card, .jobs-s-apply button, button[aria-label*="Easy Apply"]',
      8000
    );
    if (!clickResult.ok) {
      return extOk({ fields: [], buttons: {}, error: 'Easy Apply 按钮未找到' });
    }
    await new Promise(r => setTimeout(r, 1000));

    const structure = await linkedinGetApplyForm(tab.id);
    return extOk(structure);
  },
});

defineCommand({
  path: 'linkedin/fill_fields',
  description: 'LinkedIn 按指令填写表单字段（用于处理 unresolved_fields）',
  handler: async ({ actions = [] } = {}) => {
    if (!actions || actions.length === 0) throw new Error('actions 必填');

    // 获取当前 LinkedIn Worker Tab
    const tab = await ensureSiteWorkerTab('linkedin');
    if (!tab || !tab.id) throw new Error('LinkedIn 标签页不可用');

    const result = await linkedinFillFields(tab.id, actions);
    return extOk(result);
  },
});

// ══════════════════════════════════════════════════════════════════════════
// Messaging（新 Messenger 框架 — 读列表/读消息/回复/标记已读/Mailbox/Presence）
// ══════════════════════════════════════════════════════════════════════════

// 从当前登录用户派生 primary mailbox URN。
// **mailbox URN 必须是 base64 FSD 形态**(`urn:li:fsd_profile:ACoA...`),不是
// 数字 memberId 形态。Messenger GraphQL(messengerConversations / MailboxCounts)
// 只接受 base64 URN;数字 URN 部分老接口仍兼容,但抓包(2026-04-28)实测 base64
// 是当前主路径。
//
// 字段优先级:
//   1) miniProfile.dashEntityUrn        ← 新版 FSD URN(2024+ /me 都返这个)
//   2) miniProfile.entityUrn 替换前缀   ← 老版 fs_miniProfile:ACoA... → fsd_profile:ACoA...
//   3) urn:li:fsd_profile:{numeric}     ← 兜底(部分接口仍接受)
async function _deriveOwnMailboxUrn() {
  const me = await apiLinkedinGetMe();
  const mp = me?.miniProfile || {};
  // ① 新 FSD URN(优先)
  if (typeof mp.dashEntityUrn === 'string' && mp.dashEntityUrn.startsWith('urn:li:fsd_profile:')) {
    return mp.dashEntityUrn;
  }
  // ② entityUrn 替换前缀(fs_miniProfile → fsd_profile)
  if (typeof mp.entityUrn === 'string' && mp.entityUrn.includes(':ACoA')) {
    return mp.entityUrn.replace(/urn:li:[a-z_]+:/i, 'urn:li:fsd_profile:');
  }
  // ③ 兜底:数字 memberId 形态
  const mid = String(
    me?.plainId
      || mp?.objectUrn?.match(/(\d+)$/)?.[1]
      || '',
  ).trim();
  if (!mid) throw new Error('无法确定当前登录的 LinkedIn memberId(请确认已登录 linkedin.com)');
  return `urn:li:fsd_profile:${mid}`;
}

defineCommand({
  path: 'linkedin/list_conversations',
  description: 'LinkedIn 读消息会话列表(Messenger GraphQL,支持 mailbox 切换 + sync token 增量)。**老路径**,新代码请用 list_conversations_filtered',
  handler: async ({ mailbox_urn, sync_token = '', count = 20 } = {}) => {
    if (!mailbox_urn) mailbox_urn = await _deriveOwnMailboxUrn();
    const raw = await apiLinkedinListConversations(mailbox_urn, sync_token, count);
    const coll = raw?.data?.messengerConversationsBySyncToken
               || raw?.data?.data?.messengerConversationsBySyncToken
               || {};
    const elements = coll?.elements || [];
    const metadata = coll?.metadata || {};
    const convs = elements.map(c => ({
      conversation_urn: c?.entityUrn || c?.backendUrn || '',
      last_activity_ms: c?.lastActivityAt || 0,
      unread_count: c?.unreadCount || 0,
      participants_urns: (c?.conversationParticipants || []).map(p => p?.hostIdentityUrn || p?.participantType?.member?.entityUrn || ''),
      title: c?.title?.text || c?.subject || '',
    }));
    return extOk({
      raw, conversations: convs, total: convs.length,
      new_sync_token: metadata.newSyncToken || '',
      deleted_urns: metadata.deletedUrns || [],
    });
  },
});

// ── 2026-04-29 抓包对齐 Boss 求职 Step 2B ──────────────────────────────────

defineCommand({
  path: 'linkedin/list_inbox_counts',
  description: 'LinkedIn 主收件箱分类未读计数(对齐 Boss geek_message_center_summary)',
  handler: async ({ mailbox_urn = '' } = {}) => {
    if (!mailbox_urn) mailbox_urn = await _deriveOwnMailboxUrn();
    const raw = await apiLinkedinGetMailboxCounts(mailbox_urn);
    const coll = raw?.data?.messengerMailboxCountsByMailbox
               || raw?.data?.data?.messengerMailboxCountsByMailbox
               || {};
    const elements = coll?.elements || [];
    const counts = {};
    for (const e of elements) {
      if (e && e.category) counts[e.category] = e.unreadConversationCount || 0;
    }
    return extOk({ raw, counts, mailbox_urn });
  },
});

defineCommand({
  path: 'linkedin/list_conversations_filtered',
  description: 'LinkedIn 按标签筛选会话(对齐 Boss geek_filter_by_label):支持 categories / read / 1 度好友 / 翻页',
  produces: { chain: 'linkedin_messaging', stage: 'conversation', keyParam: 'conversationUrn' },
  handler: async ({
    mailbox_urn = '',
    categories = 'PRIMARY_INBOX',     // 逗号分隔的多 category, 如 'PRIMARY_INBOX,JOB'
    count = 20,
    read = '',                          // '' / 'true' / 'false'
    first_degree_connections = '',      // '' / 'true' / 'false'
    next_cursor = '',
  } = {}) => {
    if (!mailbox_urn) mailbox_urn = await _deriveOwnMailboxUrn();

    const cats = typeof categories === 'string'
      ? categories.split(',').map(s => s.trim()).filter(Boolean)
      : (Array.isArray(categories) ? categories : ['PRIMARY_INBOX']);
    const _bool = (v) => v === '' ? null : (v === 'true' || v === true);

    const raw = await apiLinkedinListConversationsBySearchCriteria(mailbox_urn, {
      categories: cats,
      count,
      read: _bool(read),
      firstDegreeConnections: _bool(first_degree_connections),
      nextCursor: next_cursor,
    });

    const coll = raw?.data?.messengerConversationsBySearchCriteria
               || raw?.data?.data?.messengerConversationsBySearchCriteria
               || {};
    const elements = coll?.elements || [];
    const conversations = elements.map(c => ({
      conversation_urn: c?.entityUrn || c?.backendUrn || '',
      conversation_url: c?.conversationUrl || '',
      title: c?.title?.text || c?.titleV2?.text || '',
      headline: c?.shortHeadlineText?.text || c?.headlineText?.text || '',
      last_activity_ms: c?.lastActivityAt || 0,
      last_read_ms: c?.lastReadAt || 0,
      unread_count: c?.unreadCount || 0,
      read: !!c?.read,
      group_chat: !!c?.groupChat,
      categories: c?.categories || [],
      participants_urns: (c?.conversationParticipants || []).map(p =>
        p?.hostIdentityUrn || p?.entityUrn || ''
      ).filter(Boolean),
    }));

    // 落 conversationUrn → 后续 reply / precheck_compose 反查命中
    for (const c of conversations) {
      if (c.conversation_urn) {
        tokenStore.setChainToken('linkedin_messaging', c.conversation_urn, 'conversationUrn', c.conversation_urn);
      }
    }

    // LinkedIn 翻页用 paging.next.cursor 编码(base64 of `<offset>&<count>`)
    const paging = coll?.paging || {};
    const nextCursorOut = paging?.next?.cursor || '';

    return extOk({
      raw,
      conversations,
      total: conversations.length,
      hasMore: !!nextCursorOut,
      next_cursor: nextCursorOut,
      mailbox_urn,
    });
  },
});

defineCommand({
  path: 'linkedin/precheck_compose',
  description: 'LinkedIn 发消息前的预检:能否发?是否被拉黑?是否需要 InMail?(LinkedIn 独有)',
  handler: async ({ recipient_urn, conversation_urn = '', type = 'REPLY' } = {}) => {
    if (!recipient_urn) throw new Error('recipient_urn 必填(urn:li:fsd_profile:ACoA... 形态)');
    const raw = await apiLinkedinPrecheckCompose(recipient_urn, conversation_urn, type);
    const ctx = raw?.data?.data?.messagingDashComposeViewContextsByRecipients?.elements?.[0]
             || raw?.data?.messagingDashComposeViewContextsByRecipients?.elements?.[0]
             || {};
    return extOk({
      raw,
      can_send: !ctx.showBlockedFooter && !ctx.trustInterventionPage,
      blocked: !!ctx.showBlockedFooter,
      trust_intervention: !!ctx.trustInterventionPage,
      show_subject: !!ctx.showSubjectField,
      invitation_text: ctx.invitationText || null,
      header_text: ctx.headerText || null,
      footer_text: ctx.footerText || null,
    });
  },
});

defineCommand({
  path: 'linkedin/get_conversation_messages',
  description: 'LinkedIn 读取某会话的历史消息',
  handler: async ({ conversation_urn, count = 20 } = {}) => {
    if (!conversation_urn) throw new Error('conversation_urn 必填');
    const raw = await apiLinkedinGetConversationMessages(conversation_urn, count);
    const batch = raw?.data?.messengerMessagesBySyncTokensInBatch || [];
    const elements = (batch[0]?.elements) || [];
    const messages = elements.map(m => ({
      message_urn: m?.entityUrn || '',
      sender_urn: m?.sender?.hostIdentityUrn || m?.senderUrn || '',
      text: m?.body?.text || '',
      delivered_at: m?.deliveredAt || 0,
    }));
    return extOk({ raw, messages, total: messages.length });
  },
});

defineCommand({
  path: 'linkedin/list_mailboxes',
  description: 'LinkedIn 列出所有 mailbox（普通收件箱 / Recruiter / Page admin 收件箱）',
  handler: async () => {
    const raw = await apiLinkedinListMailboxes();
    const elements = raw?.data?.data?.messagingDashAffiliatedMailboxesAll?.elements
                  || raw?.data?.messagingDashAffiliatedMailboxesAll?.elements
                  || [];
    const mailboxes = elements.map(e => {
      const mb = e?.mailbox || {};
      const kind = mb.recruiter ? 'recruiter'
                 : mb.pageMailbox ? 'page'
                 : mb.primary ? 'primary'
                 : 'unknown';
      const data = mb.recruiter || mb.pageMailbox || mb.primary || {};
      return {
        kind,
        headline: data?.headline || '',
        unread_count_total: data?.unreadCountTotal ?? 0,
      };
    });
    return extOk({ raw, mailboxes, total: mailboxes.length });
  },
});

defineCommand({
  path: 'linkedin/mark_messages_seen',
  description: 'LinkedIn 标记所有消息为已读（清除消息红点）',
  handler: async ({ until_ms = 0 } = {}) => {
    const raw = await apiLinkedinMarkAllMessagesAsSeen(until_ms);
    return extOk({ raw, success: true });
  },
});

defineCommand({
  path: 'linkedin/reply_to_conversation',
  description: 'LinkedIn 在已有会话中回复（区别于 send_message 发起新 InMail）',
  handler: async ({ conversation_urn, mailbox_urn, text, origin_token = '' } = {}) => {
    if (!conversation_urn) throw new Error('conversation_urn 必填');
    if (!text) throw new Error('text 必填');
    if (!mailbox_urn) mailbox_urn = await _deriveOwnMailboxUrn();
    const raw = await apiLinkedinReplyToConversation(conversation_urn, mailbox_urn, text, origin_token);
    return extOk({
      raw,
      message_urn: raw?.value?.entityUrn || '',
      conversation_urn,
    });
  },
});

defineCommand({
  path: 'linkedin/get_user_presence',
  description: 'LinkedIn 查询用户在线状态（available / lastActiveAt / instantlyReachable）',
  handler: async ({ profile_urns = [] } = {}) => {
    if (!Array.isArray(profile_urns) || profile_urns.length === 0) {
      throw new Error('profile_urns 必填（非空数组）');
    }
    const raw = await apiLinkedinGetUserPresence(profile_urns);
    const results = raw?.data?.results || raw?.results || {};
    const statuses = {};
    for (const [urn, s] of Object.entries(results)) {
      statuses[urn] = {
        available: !!s?.available,
        last_active_at: s?.lastActiveAt || 0,
        instantly_reachable: !!s?.instantlyReachable,
      };
    }
    return extOk({ raw, statuses });
  },
});

defineCommand({
  path: 'linkedin/get_my_email_handles',
  description: 'LinkedIn 获取当前用户的邮箱列表（驱动 Apply 自动填表，对标 indeed/get_resume_section）',
  handler: async ({ primary_only = true } = {}) => {
    const raw = await apiLinkedinGetMyEmailHandles(primary_only);
    const included = raw?.data?.included || raw?.included || [];
    const emails = [];
    for (const item of included) {
      const email = item?.handleDetailUnion?.emailAddress?.emailAddress
                 || item?.emailAddress
                 || '';
      if (email) {
        emails.push({
          email,
          is_primary: !!(item?.primary || item?.isPrimary),
          is_verified: !!(item?.verified || item?.isVerified),
        });
      }
    }
    return extOk({ raw, emails, total: emails.length });
  },
});
