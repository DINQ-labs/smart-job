/**
 * linkedin/recruiter-api.js — LinkedIn Recruiter (Talent Solutions) API 封装
 *
 * LinkedIn Recruiter 使用 /talent/ 路径，与普通 LinkedIn (/voyager/) 完全独立。
 * 需要付费 Recruiter Seat 才可使用。
 * 所有请求通过 executeSiteApi('linkedin', ...) 在 Worker Tab MAIN world 执行，
 * Csrf-Token 由 site-executor.js 自动注入。
 *
 * 依赖（全局）：executeSiteApi（core/site-executor.js）
 */

const RECRUITER_SEARCH_DECORATION = [
  'entityUrn',
  'linkedInMemberProfileUrn~(',
    'entityUrn,referenceUrn,anonymized,firstName,lastName,',
    'unobfuscatedFirstName,unobfuscatedLastName,',
    'canSendInMail,headline,industryName,publicProfileUrl,',
    'location(displayName),networkDistance,numConnections,',
    'contactInfo(primaryEmail),',
    'memberPreferences(openToNewOpportunities),',
    'currentPositions*(companyName,title,startDateOn,endDateOn,location),',
    'educations*(schoolName,degreeName,startDateOn,endDateOn),',
    'vectorProfilePicture',
  ')',
  'candidateInsights(candidateSearchInsightsUrn~(positionsInsight,yearsOfExperience,entityUrn))',
  'topFeatures(interested(interestLikelihood))',
].join('');


// ── Recruiter 搜索 ───────────────────────────────────────────────────────

async function apiLinkedinRecruiterSearch(projectUrn, options = {}) {
  const {
    keywords = '',
    titles = [],
    start = 0,
    count = 25,
    sortBy = 'RELEVANCE',
    facets = [],
  } = options;

  // 构建 query 参数
  const titlesList = titles.length > 0
    ? `titles:List(${titles.map(t => `(title:${t})`).join(',')}),titleScope:CURRENT_OR_PAST,`
    : '';
  const facetsList = facets.length > 0
    ? `facets:List(${facets.join(',')})`
    : 'facets:List(TALENT_POOL)';

  let queryParts = [
    `capSearchSortBy:${sortBy}`,
    `project:${projectUrn}`,
    titlesList,
    facetsList,
  ].filter(Boolean).join(',');

  if (keywords) {
    queryParts = `keywords:${encodeURIComponent(keywords)},${queryParts}`;
  }

  const body = [
    `decoration=(${RECRUITER_SEARCH_DECORATION})`,
    `count=${count}`,
    `q=recruiterSearch`,
    `query=(${queryParts})`,
    `start=${start}`,
  ].join('&');

  const raw = await executeSiteApi('linkedin',
    '/talent/search/api/talentRecruiterSearchHits',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
      },
      body,
    },
  );

  const total = raw?.metadata?.total || 0;
  const elements = raw?.elements || [];

  return {
    totalCount: total,
    highlightTerms: raw?.metadata?.highlightTerms || [],
    candidates: elements.map(e => {
      const profile = e.linkedInMemberProfileUrnResolutionResult || {};
      const positions = profile.currentPositions || [];
      const educations = profile.educations || [];

      return {
        entityUrn: e.entityUrn || '',
        profileUrn: profile.referenceUrn || profile.entityUrn || '',
        firstName: profile.unobfuscatedFirstName || profile.firstName || '',
        lastName: profile.unobfuscatedLastName || profile.lastName || '',
        headline: profile.headline || '',
        location: profile.location?.displayName || '',
        industry: profile.industryName || '',
        canSendInMail: profile.canSendInMail || false,
        openToOpportunities: profile.memberPreferences?.openToNewOpportunities || false,
        connections: profile.numConnections || 0,
        networkDistance: profile.networkDistance || '',
        currentPosition: positions[0] ? {
          title: positions[0].title || '',
          company: positions[0].companyName || '',
          location: positions[0].location || '',
        } : null,
        education: educations[0] ? {
          school: educations[0].schoolName || '',
          degree: educations[0].degreeName || '',
        } : null,
        contactEmail: profile.contactInfo?.primaryEmail || '',
        publicProfileUrl: profile.publicProfileUrl || '',
      };
    }),
  };
}


// ── 获取候选人详情 ─────────────────────────────────────────────────────────

async function apiLinkedinRecruiterGetProfile(profileUrn, projectUrn) {
  const decoration = [
    'entityUrn,referenceUrn,anonymized,firstName,lastName,',
    'unobfuscatedFirstName,unobfuscatedLastName,headline,',
    'canSendInMail,contactInfo(primaryEmail,weChat),',
    'currentPositions*(companyName,title,startDateOn,endDateOn,description,location),',
    'educations*(schoolName,degreeName,startDateOn,endDateOn),',
    'workExperience*(companyName,title,startDateOn,endDateOn),',
    'industryName,location(displayName),networkDistance,',
    'numConnections,publicProfileUrl,memberPreferences(openToNewOpportunities),',
    'currentResumePosition',
  ].join('');

  const encodedUrn = encodeURIComponent(profileUrn);
  const raw = await executeSiteApi('linkedin',
    `/talent/api/talentLinkedInMemberProfiles/${encodedUrn}`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
      },
      body: `altkey=urn&decoration=(${decoration})`,
    },
  );

  const positions = raw?.currentPositions || [];
  const edus = raw?.educations || [];
  const work = raw?.workExperience || [];

  return {
    profileUrn: raw?.referenceUrn || raw?.entityUrn || profileUrn,
    firstName: raw?.unobfuscatedFirstName || raw?.firstName || '',
    lastName: raw?.unobfuscatedLastName || raw?.lastName || '',
    headline: raw?.headline || '',
    location: raw?.location?.displayName || '',
    industry: raw?.industryName || '',
    canSendInMail: raw?.canSendInMail || false,
    contactEmail: raw?.contactInfo?.primaryEmail || '',
    openToOpportunities: raw?.memberPreferences?.openToNewOpportunities || false,
    connections: raw?.numConnections || 0,
    publicProfileUrl: raw?.publicProfileUrl || '',
    currentPositions: positions.map(p => ({
      title: p.title || '',
      company: p.companyName || '',
      location: p.location || '',
      from: p.startDateOn || null,
      to: p.endDateOn || null,
      description: p.description || '',
    })),
    education: edus.map(e => ({
      school: e.schoolName || '',
      degree: e.degreeName || '',
      from: e.startDateOn || null,
      to: e.endDateOn || null,
    })),
    workHistory: work.map(w => ({
      title: w.title || '',
      company: w.companyName || '',
      from: w.startDateOn || null,
      to: w.endDateOn || null,
    })),
  };
}


// ── 发送 InMail ──────────────────────────────────────────────────────────

async function apiLinkedinRecruiterSendInMail(recipientProfileUrn, subject, body, options = {}) {
  const {
    hiringProjectUrn = '',
    sourcingChannelUrn = '',
    signature = '',
  } = options;

  const variables = {
    messagePostPayloads: [{
      emailAddress: null,
      primaryPost: {
        attachments: [],
        richFormattedBody: {
          text: body,
          attributes: [],
        },
        channel: 'INMAIL',
        enableCalenderShare: false,
        subject,
      },
      followUpPosts: [],
      hiringProjectUrn: hiringProjectUrn || undefined,
      recipientProfileUrn,
      signature: signature || undefined,
      sourcingChannelUrn: sourcingChannelUrn || undefined,
      visibility: 'PROJECT',
      inMailIntent: 'HIRE_FOR_OWN_COMPANY',
    }],
  };

  const raw = await executeSiteApi('linkedin',
    '/talent/api/graphql',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json; charset=UTF-8',
        'Accept': 'application/vnd.linkedin.normalized+json+2.1',
      },
      body: JSON.stringify({
        variables,
        queryId: 'talentMultiMessagePosts.be0f985141a7cf89d4d410c4e5839e8f',
        includeWebMetadata: true,
      }),
    },
  );

  return {
    sent: true,
    recipientProfileUrn,
    subject,
  };
}


// ── 添加候选人到招聘项目 ──────────────────────────────────────────────────

async function apiLinkedinRecruiterAddToProject(candidateUrn, hiringProjectUrn, sourcingChannelUrn) {
  const raw = await executeSiteApi('linkedin',
    '/talent/api/talentSourcingChannelCandidateCreations',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify({
        elements: [{
          hiringContext: hiringProjectUrn,
          candidate: candidateUrn,
          sourcingChannel: sourcingChannelUrn,
          candidateAddedToANewProject: false,
          copiedFromAnotherProject: false,
        }],
      }),
    },
  );

  const result = raw?.elements?.[0] || {};
  return {
    added: true,
    id: result.id || result.location || '',
  };
}


// ── 列出招聘项目 ─────────────────────────────────────────────────────────

async function apiLinkedinRecruiterListProjects() {
  const decoration = '(entityUrn,hiringProjectMetadata(name,state,created,locationDescription))';
  const raw = await executeSiteApi('linkedin',
    `/talent/api/talentHiringProjects?decoration=${encodeURIComponent(decoration)}&q=contextual&sortCriteria=LAST_ACCESSED`,
    {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
    },
  );

  const elements = raw?.elements || [];
  return {
    projects: elements.map(e => {
      const meta = e.hiringProjectMetadata || {};
      return {
        projectUrn: e.entityUrn || '',
        name: meta.name || '',
        state: meta.state || '',
        location: meta.locationDescription || '',
        created: meta.created || null,
      };
    }),
  };
}


// ── 获取搜索筛选项 ──────────────────────────────────────────────────────

async function apiLinkedinRecruiterSearchFacets(projectUrn, facetTypes = []) {
  const defaultFacets = ['TITLE_SWR', 'TOTAL_YEARS_OF_EXPERIENCE', 'BING_GEO', 'CURRENT_COMPANY', 'SKILL'];
  const facets = facetTypes.length > 0 ? facetTypes : defaultFacets;

  const variables = `(query:(facets:List(${facets.join(',')}),hiringProjects:List((entity:${projectUrn}))))`;
  const queryId = 'talentRecruiterFacets.01234567890abcdef';

  const raw = await executeSiteApi('linkedin',
    `/talent/api/graphql?includeWebMetadata=true&variables=${encodeURIComponent(variables)}&queryId=${queryId}`,
    {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
    },
  );

  // Parse facet results
  const data = raw?.data || raw || {};
  return { facets: data };
}
