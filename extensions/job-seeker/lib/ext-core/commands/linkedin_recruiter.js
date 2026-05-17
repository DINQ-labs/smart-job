/**
 * LinkedIn Recruiter (Talent Solutions) 命令 — 付费 Recruiter Seat 功能。
 *
 * 与普通 LinkedIn 命令 (linkedin.js) 完全独立，使用 /talent/ API。
 *
 * 依赖:
 *   linkedin/recruiter-api.js  (apiLinkedinRecruiter*)
 *   core/registry.js           (defineCommand)
 */

// ── 搜索候选人 ─────────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin_recruiter/search',
  description: 'LinkedIn Recruiter 搜索候选人（需付费 Recruiter Seat）',
  handler: async ({ project_urn, keywords = '', titles, start = 0, count = 25 } = {}) => {
    if (!project_urn) throw new Error('project_urn 必填（从 list_projects 获取）');

    const titlesArr = titles
      ? (typeof titles === 'string' ? titles.split(',').map(t => t.trim()) : titles)
      : [];

    const result = await apiLinkedinRecruiterSearch(project_urn, {
      keywords,
      titles: titlesArr,
      start: Number(start),
      count: Number(count),
    });
    return extOk(result);
  },
});


// ── 获取候选人详情 ─────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin_recruiter/get_profile',
  description: '获取 LinkedIn Recruiter 候选人的详细资料',
  handler: async ({ profile_urn, project_urn = '' } = {}) => {
    if (!profile_urn) throw new Error('profile_urn 必填（从搜索结果获取）');

    const result = await apiLinkedinRecruiterGetProfile(profile_urn, project_urn);
    return extOk(result);
  },
});


// ── 发送 InMail ──────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin_recruiter/send_inmail',
  description: '通过 LinkedIn Recruiter 发送 InMail 消息',
  handler: async ({ recipient_profile_urn, subject, body, hiring_project_urn = '', sourcing_channel_urn = '', signature = '' } = {}) => {
    if (!recipient_profile_urn) throw new Error('recipient_profile_urn 必填');
    if (!subject) throw new Error('subject 必填（邮件主题）');
    if (!body) throw new Error('body 必填（邮件正文）');

    const result = await apiLinkedinRecruiterSendInMail(
      recipient_profile_urn, subject, body,
      { hiringProjectUrn: hiring_project_urn, sourcingChannelUrn: sourcing_channel_urn, signature },
    );
    return extOk(result);
  },
});


// ── 添加候选人到招聘项目 ──────────────────────────────────────────────────

defineCommand({
  path: 'linkedin_recruiter/add_to_project',
  description: '将候选人添加到 LinkedIn Recruiter 招聘项目',
  handler: async ({ candidate_urn, hiring_project_urn, sourcing_channel_urn } = {}) => {
    if (!candidate_urn) throw new Error('candidate_urn 必填');
    if (!hiring_project_urn) throw new Error('hiring_project_urn 必填');
    if (!sourcing_channel_urn) throw new Error('sourcing_channel_urn 必填');

    const result = await apiLinkedinRecruiterAddToProject(
      candidate_urn, hiring_project_urn, sourcing_channel_urn,
    );
    return extOk(result);
  },
});


// ── 列出招聘项目 ─────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin_recruiter/list_projects',
  description: '列出 LinkedIn Recruiter 所有招聘项目',
  handler: async () => {
    const result = await apiLinkedinRecruiterListProjects();
    return extOk(result);
  },
});


// ── 搜索筛选项 ──────────────────────────────────────────────────────────

defineCommand({
  path: 'linkedin_recruiter/search_facets',
  description: '获取 LinkedIn Recruiter 搜索的可用筛选项（职位、经验年限、地区等）',
  handler: async ({ project_urn, facet_types } = {}) => {
    if (!project_urn) throw new Error('project_urn 必填');

    const types = facet_types
      ? (typeof facet_types === 'string' ? facet_types.split(',').map(t => t.trim()) : facet_types)
      : [];

    const result = await apiLinkedinRecruiterSearchFacets(project_urn, types);
    return extOk(result);
  },
});
