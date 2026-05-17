/**
 * Indeed 命令
 *
 * 包含：indeed/search_jobs, indeed/get_job_detail
 *
 * 令牌链（indeed_jobs 命名空间）：
 *   search（searchToken）→ detail（viewToken）
 *
 * 依赖（全局）：tokenStore, apiIndeedSearchJobs, apiIndeedGetJobDetail, extOk
 */

// ── 辅助：读取 Indeed CSRF token（写操作所需） ────────────────────────────
async function indeedReadCsrfToken() {
  // Indeed 把 csrf 放在 .indeed.com 域的 cookie `indeedcsrftoken` 中
  const [byUrl, byDomain] = await Promise.all([
    chrome.cookies.getAll({ url: 'https://www.indeed.com', name: 'indeedcsrftoken' }),
    chrome.cookies.getAll({ domain: '.indeed.com', name: 'indeedcsrftoken' }),
  ]);
  const csrf = (byUrl[0]?.value) || (byDomain[0]?.value) || '';
  if (!csrf) throw new Error('indeedcsrftoken cookie 未找到，请先在浏览器访问任一 Indeed 页面');
  return csrf;
}

// ── 登录状态检测 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed/check_login',
  description: 'Indeed 检查登录状态（检测 PPID cookie + 解析用户信息）',
  handler: async () => {
    // PPID 是 Indeed 的 JWT 身份令牌，登录后设置在 .indeed.com 域
    // url + domain 双查：规避 CHIPS 分区 / 子域差异导致的漏判
    const [byUrl, byDomain] = await Promise.all([
      chrome.cookies.getAll({ url: 'https://www.indeed.com', name: 'PPID' }),
      chrome.cookies.getAll({ domain: '.indeed.com', name: 'PPID' }),
    ]);
    const ppid = (byUrl[0]?.value) || (byDomain[0]?.value) || '';
    if (!ppid) {
      return extOk({ logged_in: false, email: '', userId: '' });
    }

    // PPID 是标准 JWT，解析 payload 获取用户信息
    let email = '', userId = '';
    try {
      const parts = ppid.split('.');
      if (parts.length >= 2) {
        const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
        email = payload.email || '';
        userId = payload.sub || '';
      }
    } catch (_) {}

    // 上报 site_status 给网关，与 Boss/LinkedIn 对齐（便于按 app_user_id 定位会话）
    try {
      if (typeof reportSiteStatus === 'function') {
        reportSiteStatus('indeed', userId);
      }
    } catch (_) {}

    return extOk({ logged_in: true, email, userId });
  },
});

// ── 搜索职位（捕获 search 阶段 token） ───────────────────────────────────

defineCommand({
  path: 'indeed/search_jobs',
  description: 'Indeed 搜索职位（SERP HTML 解析 mosaic-provider-jobcards），批量捕获 jobKey',
  produces: { chain: 'indeed_jobs', stage: 'search' },
  handler: async ({
    query, location = '', start = 0, country = 'US',
    // 筛选器参数（全可选）— URL query 驱动 SERP SSR 的 jt/fromage/radius/sr/explvl/sort
    job_type = '', fromage = '', radius = '',
    salary_min = '', experience = '', sort = '',
  } = {}) => {
    if (!query) throw new Error('query 必填');
    const filters = {};
    if (job_type) filters.jobType = job_type;
    if (fromage) filters.fromage = fromage;
    if (radius !== '' && radius != null) filters.radius = radius;
    if (salary_min) filters.salaryMin = salary_min;
    if (experience) filters.experience = experience;
    if (sort) filters.sort = sort;

    const parsed = await apiIndeedSearchJobs(query, location, start, country, filters);

    const jobs = (parsed?.jobs || []).filter(j => j && j.jobKey);

    // 存入 tokenStore 供后续 get_job_detail 关联 trackingKey 触发 RecordJobSeen
    for (const j of jobs) {
      tokenStore.setChainToken('indeed_jobs', j.jobKey, 'searchToken', j.jobKey, {
        title: j.title,
        company: j.company,
        location: j.location,
        trackingKey: j.trackingKey || '',
      });
    }

    // 反爬：异步上报曝光（新 SERP 路径下 trackingKey 可能不全，有几个报几个）
    const trackingKeys = jobs.map(j => j.trackingKey).filter(Boolean);
    if (trackingKeys.length > 0) recordJobSeen(trackingKeys, '');

    return extOk({
      jobs,
      total: jobs.length,
      tokens_captured: jobs.map(j => j.jobKey),
      source: parsed?._via || 'unknown',
      ...(parsed?._parse_error ? { parse_error: parsed._parse_error } : {}),
      filters_applied: filters,
    });
  },
});

// ── 获取职位详情 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed/get_job_detail',
  description: 'Indeed 获取职位详情（viewjob HTML 解析 schema.org / _initialData / DOM 三级回退）',
  requires: { chain: 'indeed_jobs', stage: 'search', keyParam: 'job_key' },
  handler: async ({ job_key, country = 'US' } = {}) => {
    if (!job_key) throw new Error('job_key 必填');
    const t0 = Date.now();
    const parsed = await apiIndeedGetJobDetail(job_key, country);
    tokenStore.setChainToken('indeed_jobs', job_key, 'viewToken', job_key);

    // 反爬：异步上报视口停留时长（用真实请求耗时，避免伪造规整数字被风控识别）
    const duration = Math.max(1000, Date.now() - t0);
    recordJobTimeInViewport([{ jobKey: job_key, durationMilliseconds: duration }]);

    // 反爬伪装：真实用户每次点击职位都会 POST RecordJobSeen GraphQL。
    // 从 tokenStore 里查本次搜索时存下的 trackingKey,有就 fire-and-forget 发一次,
    // 让我们的批量分析流量特征接近真实用户浏览。
    try {
      const chain = tokenStore.getChainToken('indeed_jobs', job_key, 'searchToken');
      const trackingKey = chain && chain.meta && chain.meta.trackingKey;
      if (trackingKey) {
        apiIndeedRecordJobSeen(trackingKey, country).catch(() => {});
      }
    } catch (_) { /* ignore */ }

    return extOk({
      raw: parsed,
      source: parsed?._via || 'unknown',
      ...(parsed?._parse_error ? { parse_error: parsed._parse_error } : {}),
    });
  },
});

// ── Apply 表单自动填写 ──────────────────────────────────────────────────

defineCommand({
  path: 'indeed/apply',
  description: 'Indeed 自动投递（多步表单填写，混合模式）',
  handler: async ({ jobId, profileData = {}, country = 'US' } = {}) => {
    if (!jobId) throw new Error('jobId 必填');

    // 导航到职位页面并切到前台
    const jobUrl = `${indeedRegionOrigin(country)}/viewjob?jk=${jobId}`;
    const tab = await navigateSiteWorkerTab('indeed', jobUrl);
    await waitForSiteTabLoad(tab.id, 15000);
    await new Promise(r => setTimeout(r, 1500));

    const result = await indeedApply(tab.id, jobId, profileData);
    return extOk(result);
  },
});

defineCommand({
  path: 'indeed/get_apply_form',
  description: 'Indeed 获取申请表单结构（不填写，只预览）',
  handler: async ({ jobId, country = 'US' } = {}) => {
    if (!jobId) throw new Error('jobId 必填');

    const jobUrl = `${indeedRegionOrigin(country)}/viewjob?jk=${jobId}`;
    const tab = await navigateSiteWorkerTab('indeed', jobUrl);
    await waitForSiteTabLoad(tab.id, 15000);
    await new Promise(r => setTimeout(r, 1500));

    // 点击 Apply 按钮打开表单
    const clickResult = await executeFormActionOnTab(tab.id, formClickElement,
      '[data-testid="indeedApplyButton"], .indeed-apply-button, #indeedApplyButton',
      8000
    );
    if (!clickResult.ok) {
      return extOk({ fields: [], buttons: {}, error: 'Apply 按钮未找到' });
    }
    await new Promise(r => setTimeout(r, 1000));

    const structure = await indeedGetApplyForm(tab.id);
    return extOk(structure);
  },
});

defineCommand({
  path: 'indeed/fill_fields',
  description: 'Indeed 按指令填写表单字段（用于处理 unresolved_fields）',
  handler: async ({ actions = [] } = {}) => {
    if (!actions || actions.length === 0) throw new Error('actions 必填');

    const tab = await ensureSiteWorkerTab('indeed');
    if (!tab || !tab.id) throw new Error('Indeed 标签页不可用');

    const result = await indeedFillFields(tab.id, actions);
    return extOk(result);
  },
});

// ── 投递前置：获取 smartapply URL + iaUid ────────────────────────────────

defineCommand({
  path: 'indeed/prepare_apply',
  description: 'Indeed 投递前置：为 jk 生成 smartapply URL 和 iaUid（后续 check_applied 依赖）',
  handler: async ({ jk, job_country = 'US', tk = '', vjtk = '', ctk = '' } = {}) => {
    if (!jk) throw new Error('jk 必填');
    const raw = await apiIndeedPrepareApply(jk, job_country, tk, vjtk, ctk);
    return extOk({
      raw,
      ia_uid: raw?.result?.iaUid || '',
      apply_url: raw?.result?.applyUrl || '',
    });
  },
});

// ── 查询 jk 是否已投递 ─────────────────────────────────────────────────────

defineCommand({
  path: 'indeed/check_applied',
  description: 'Indeed 查询某 jk 是否已投递（批量投递前去重用）',
  handler: async ({ jk, ia_uid, job_country = 'US' } = {}) => {
    if (!jk) throw new Error('jk 必填');
    if (!ia_uid) throw new Error('ia_uid 必填（先调 indeed/prepare_apply 取 iaUid）');
    const raw = await apiIndeedCheckApplied(jk, ia_uid, job_country);
    return extOk({
      raw,
      applied: !!raw?.result?.applied,
      applied_ms: raw?.result?.appliedMs || 0,
    });
  },
});

// ── Job Alert 订阅：查询 + 创建 ────────────────────────────────────────────

defineCommand({
  path: 'indeed/search_job_alert',
  description: 'Indeed 查询某 q+l 搜索是否已订阅邮件 alert',
  handler: async ({ keywords, location = '', country = 'US', locale = 'en_US' } = {}) => {
    if (!keywords) throw new Error('keywords 必填');
    const raw = await apiIndeedSearchJobAlert(keywords, location, country, locale);
    const sub = (raw?.subscriptions || [])[0] || {};
    return extOk({
      raw,
      subscription_state: sub.subscriptionState || 'UNKNOWN',
      subscribed: sub.subscriptionState === 'ACTIVE',
    });
  },
});

defineCommand({
  path: 'indeed/create_job_alert',
  description: 'Indeed 为 q+l 搜索创建邮件 job alert 订阅（返回 subscriptionId）',
  handler: async ({ keywords, location = '', email,
                    country = 'US', locale = 'en_US',
                    search_params = '', client_tk = '' } = {}) => {
    if (!keywords) throw new Error('keywords 必填');
    if (!email) throw new Error('email 必填');
    const raw = await apiIndeedCreateJobAlert(keywords, location, email,
      country, locale, search_params, client_tk);
    return extOk({
      raw,
      subscription_id: raw?.subscriptionId || '',
      subscription_state: raw?.subscriptionState || 'UNKNOWN',
    });
  },
});

// ── 输入建议 ──────────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed/autocomplete',
  description: 'Indeed 输入建议（type=what/location/cmp-what-with-top-companies）',
  handler: async ({ type = 'what', query, country = 'US', language = 'en', location = '' } = {}) => {
    if (!query) throw new Error('query 必填');
    const ALLOWED = ['what', 'location', 'cmp-what-with-top-companies'];
    if (!ALLOWED.includes(type)) {
      throw new Error(`type 必须为 ${ALLOWED.join('/')}，收到 ${type}`);
    }
    const raw = await apiIndeedAutocomplete(type, query, country, language, location);
    // cmp-what-with-top-companies 返回数组结构：[{suggestion, matches, payload}, ...]
    let list = [];
    if (Array.isArray(raw)) list = raw;
    else list = raw?.suggestions || raw?.items || raw?.list || [];
    const suggestions = list
      .map(s => (typeof s === 'string' ? s : (s?.suggestion || s?.value || s?.text || s?.name || '')))
      .filter(Boolean);
    return extOk({ raw, suggestions });
  },
});

// ── 未读消息计数 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'indeed/unread_messages',
  description: 'Indeed 未读会话数（GraphQL GetUnreadConversationCount）',
  handler: async () => {
    const raw = await apiIndeedUnreadMessages();
    const count = raw?.data?.getUnreadConversationCount?.unreadConversationCount ?? 0;
    return extOk({ raw, unread_count: count });
  },
});

// ── 保存 / 取消保存 职位 ─────────────────────────────────────────────────

defineCommand({
  path: 'indeed/save_job',
  description: 'Indeed 保存职位到 My Jobs（需要登录 + indeedcsrftoken cookie）',
  handler: async ({ jk, country = 'US' } = {}) => {
    if (!jk) throw new Error('jk 必填');
    const csrf = await indeedReadCsrfToken();
    const raw = await apiIndeedTransitionJobState(jk, 'save', csrf, country);
    return extOk({ raw, action: 'save', jk });
  },
});

defineCommand({
  path: 'indeed/unsave_job',
  description: 'Indeed 取消保存职位',
  handler: async ({ jk, country = 'US' } = {}) => {
    if (!jk) throw new Error('jk 必填');
    const csrf = await indeedReadCsrfToken();
    const raw = await apiIndeedTransitionJobState(jk, 'unsave', csrf, country);
    return extOk({ raw, action: 'unsave', jk });
  },
});

// ── 隐藏 / 撤销隐藏 职位 ─────────────────────────────────────────────────

defineCommand({
  path: 'indeed/dislike_job',
  description: 'Indeed 隐藏/不喜欢职位（从搜索结果过滤掉）',
  handler: async ({ jk, country = 'US' } = {}) => {
    if (!jk) throw new Error('jk 必填');
    const raw = await apiIndeedDislikeJob(jk, false, country);
    return extOk({ raw, action: 'dislike', jk });
  },
});

defineCommand({
  path: 'indeed/undislike_job',
  description: 'Indeed 撤销隐藏职位',
  handler: async ({ jk, country = 'US' } = {}) => {
    if (!jk) throw new Error('jk 必填');
    const raw = await apiIndeedDislikeJob(jk, true, country);
    return extOk({ raw, action: 'undislike', jk });
  },
});

// ── 公司搜索 / 公司页职位详情 / 关注公司检查 ────────────────────────────

defineCommand({
  path: 'indeed/search_companies',
  description: 'Indeed 公司名补全（用于查找 Apple / Meta 等公司）',
  handler: async ({ query, caret, country = 'US' } = {}) => {
    if (!query) throw new Error('query 必填');
    const raw = await apiIndeedSearchCompanies(query, caret ?? -1, country);
    const list = Array.isArray(raw) ? raw : (raw?.suggestions || raw?.companies || raw?.items || []);
    return extOk({ raw, suggestions: list });
  },
});

defineCommand({
  path: 'indeed/get_company_jobs',
  description: 'Indeed 公司页上下文下的职位详情（/cmp/-/rpc/fetch-jobs，结构比 viewjob 精简）',
  handler: async ({ jobKey, country = 'US', loggedIn = true } = {}) => {
    if (!jobKey) throw new Error('jobKey 必填');
    const raw = await apiIndeedGetCompanyJobs(jobKey, country, loggedIn);
    const job = raw?.jobData?.results?.[0]?.job || null;
    return extOk({ raw, job });
  },
});

defineCommand({
  path: 'indeed/follow_company_check',
  description: 'Indeed 检查是否已关注某公司（返回 alertCode；非 null 表示已订阅）',
  handler: async ({ company_name, company_id, country = 'US' } = {}) => {
    if (!company_name) throw new Error('company_name 必填');
    if (!company_id) throw new Error('company_id 必填');
    const raw = await apiIndeedFollowCompanyCheck(company_name, company_id, country);
    const code = raw?.alertCode ?? null;
    return extOk({ raw, alert_code: code, followed: !!code && code !== 'null' });
  },
});

// ── 消息中心：列出会话 + 在线状态偏好 ───────────────────────────────────

defineCommand({
  path: 'indeed/list_conversations',
  description: 'Indeed 列出用户会话（消息中心），支持 folder 过滤 inbox/archive/spam',
  handler: async ({ country = 'US', locale = '', folder = 'inbox', last = 30, cursor = '' } = {}) => {
    const raw = await apiIndeedListConversations(country, locale, folder, last, cursor);
    const conn = raw?.data?.findConversations;
    const edges = conn?.edges || [];
    const conversations = edges.map(e => {
      const n = e?.node || {};
      return {
        id: n.id,
        cursor: e.cursor,
        created_at: n.creationDateTime,
        context: n.context,
        last_message: {
          preview: n?.lastEvent?.messagePreview || '',
          type: n?.lastEvent?.type,
          author_role: n?.lastEvent?.author?.role,
          timestamp: n?.lastEvent?.publicationDateTime,
        },
        participants: (n.participants || []).map(p => ({
          name: p.participantName, role: p.role, removed: p.removed,
        })),
        unread: n?.userReadsInfo?.unreadCount || 0,
        labels: n?.userLabelInfo?.labels || [],
        job: n?.job ? {
          key: n.job.key, title: n.job.title || n.job.displayTitle,
          company: n.job?.employer?.name, location: n.job?.location?.formatted?.short,
        } : null,
      };
    });
    return extOk({
      raw,
      conversations,
      total: conversations.length,
      page_info: conn?.pageInfo || {},
    });
  },
});

defineCommand({
  path: 'indeed/get_online_status',
  description: 'Indeed 查询用户在线状态偏好（对招聘方是否可见在线）',
  handler: async ({ country = 'US', locale = '' } = {}) => {
    const raw = await apiIndeedGetOnlineStatus(country, locale);
    const enabled = !!raw?.data?.onlineActivityPreferences?.onlineStatusPreference?.isEnabled;
    return extOk({ raw, is_enabled: enabled });
  },
});

// ── 用户求职偏好（JSSD） ─────────────────────────────────────────────────

defineCommand({
  path: 'indeed/get_preferences',
  description: 'Indeed 读取用户求职偏好（最低薪资、地点、通勤、工作时间等）',
  handler: async ({ country = 'US' } = {}) => {
    const raw = await apiIndeedGetPreferences(country);
    const pref = raw?.data?.jobSeekerProfileStructuredData?.preferences?.[0] || {};
    const attrs = raw?.data?.jobSeekerProfileStructuredDataPreferenceAttributesByCustomClass || {};
    return extOk({
      raw,
      minimum_pay: pref?.minimumPay || null,
      relocation: pref?.relocation || null,
      maximum_commute: pref?.maximumCommute || null,
      locations: pref?.locations || [],
      positive_attributes: attrs?.positivePreferenceAttributesByCustomClass || [],
      negative_attributes: attrs?.negativePreferenceAttributesByCustomClass || [],
    });
  },
});

// ── SERP 周边：竞品职位 + 新增职位数 ────────────────────────────────────

defineCommand({
  path: 'indeed/get_competitor_jobs',
  description: 'Indeed 获取同岗位/同公司竞品公司的职位推荐',
  requires: { chain: 'indeed_jobs', stage: 'search', keyParam: 'job_key' },
  handler: async ({ job_key, limit = 15, country = 'US' } = {}) => {
    if (!job_key) throw new Error('job_key 必填');
    const raw = await apiIndeedGetCompetitorJobs(job_key, limit, country);
    const model = raw?.transitionalJobCardsModel || {};
    const jobs = model.recommendedJobs || model.competitorJobs || model.jobCards || [];
    return extOk({
      raw,
      jobs,
      tracking_key: raw?.rjpTrackingKey || '',
    });
  },
});

defineCommand({
  path: 'indeed/get_new_jobs_count',
  description: 'Indeed 查询 (keywords,location) 在时间窗内的新增职位数（提醒策略用）',
  handler: async ({ keywords = '', location, fromage = 'last', country = 'US' } = {}) => {
    if (!location) throw new Error('location 必填');
    const raw = await apiIndeedGetNewJobsCount(keywords, location, fromage, country);
    const body = raw?.body || {};
    return extOk({
      raw,
      new_count: body?.newCount ?? 0,
      total_count: body?.totalCount ?? 0,
      query_key: body?.key || '',
    });
  },
});

// ── 用户自己的简历（驱动 indeed/apply 自动填表） ─────────────────────────

defineCommand({
  path: 'indeed/get_resume_section',
  description: 'Indeed 读取当前登录用户的简历（联系信息/地点/简历列表），用于自动投递前取资料',
  handler: async ({ country = 'US' } = {}) => {
    const raw = await apiIndeedGetResumeSection(country);
    const profile = raw?.data?.jobSeekerProfile?.profile || {};
    const contact = profile?.defaultInfo?.contactInformation || {};
    const resumes = Array.isArray(profile?.resume) ? profile.resume : (profile?.resume ? [profile.resume] : []);
    return extOk({
      raw,
      account_key: profile?.accountKey || '',
      contact: {
        first_name: contact?.firstName || '',
        last_name: contact?.lastName || '',
        phone_number: contact?.phoneNumber || '',
        location: contact?.location || null,
      },
      resumes,
      has_resume: resumes.length > 0,
    });
  },
});

// ── My Jobs: 已申请职位 / 面试 / 状态更新 ─────────────────────────────────

defineCommand({
  path: 'indeed/list_applied_jobs',
  description: 'Indeed 列出用户已申请职位（My Jobs Applied tab）',
  handler: async ({ start_ms = 0, from_param = 'app-tracker' } = {}) => {
    const raw = await apiIndeedListAppliedJobs(start_ms, from_param);
    const body = raw?.body || {};
    const jobs = (body?.appStatusJobs || []).map(j => ({
      job_key: j.jobKey || '',
      app_tk: j.appTk || '',
      job_title: j.jobTitle || '',
      job_url: j.jobUrl || '',
      location: j.location || '',
      company: j?.company?.name || '',
      company_logo: j?.company?.logoUrl || '',
      withdrawn: !!j.withdrawn,
      expired: !!j.jobExpired,
      reported: !!j.jobReported,
      fraudulent: !!j.jobFraudulent,
      statuses: j.statuses || null,
    }));
    return extOk({ raw, jobs, total: jobs.length });
  },
});

defineCommand({
  path: 'indeed/list_interviews',
  description: 'Indeed 列出用户面试（含邀请/确认/取消的所有状态，按时间筛）',
  handler: async ({ statuses = [], formats = [], start_ms = 0 } = {}) => {
    const raw = await apiIndeedListInterviews(statuses, formats, start_ms);
    const interviews = raw?.body?.interviews || [];
    return extOk({ raw, interviews, total: interviews.length });
  },
});

defineCommand({
  path: 'indeed/update_job_app_status',
  description: 'Indeed 更新已申请职位状态（NOT_INTERESTED / INTERESTED / OFFER / INTERVIEWING 等）',
  handler: async ({ jk, state_payload, cause = 'api update' } = {}) => {
    if (!jk) throw new Error('jk 必填');
    if (!state_payload) throw new Error('state_payload 必填（完整 statuses + prev*/new* 结构）');
    const csrf = await indeedReadCsrfToken();
    const payload = { cause, ...state_payload };
    const raw = await apiIndeedUpdateJobAppStatus(jk, csrf, payload);
    return extOk({ raw, jk, success: raw?.success === true });
  },
});

// ── 全站通知红点 + 邮件偏好 + 未读 offer ────────────────────────────────

defineCommand({
  path: 'indeed/get_notifications_count',
  description: 'Indeed 全站通知红点（右上角小铃铛，区别于消息红点）',
  handler: async ({ country = 'US' } = {}) => {
    const raw = await apiIndeedGetNotificationsCount(country);
    return extOk({
      raw,
      authenticated: !!raw?.authenticated,
      new_count: raw?.newCount ?? 0,
      should_show_new: !!raw?.shouldShowNew,
    });
  },
});

defineCommand({
  path: 'indeed/get_email_preferences',
  description: 'Indeed 邮件订阅偏好（各 category 是否订阅）',
  handler: async () => {
    const raw = await apiIndeedGetEmailPreferences();
    const prefs = raw?.data?.emailPreferences?.preferences || [];
    return extOk({
      raw,
      preferences: prefs.map(p => ({ category: p.category, is_enabled: !!p.isEnabled })),
      email_channel_opted_out: !!raw?.data?.emailPreferences?.emailChannelOptedOut,
    });
  },
});

defineCommand({
  path: 'indeed/get_unread_offers_count',
  description: 'Indeed 未读招聘方 offer 数（不同于消息 unread_messages）',
  handler: async () => {
    const raw = await apiIndeedGetUnreadOffersCount();
    const count = raw?.data?.getUnreadOffersCount?.count ?? 0;
    return extOk({ raw, unread_offers_count: count });
  },
});
