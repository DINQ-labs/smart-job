/**
 * indeed/api.js — Indeed API 封装
 *
 * 所有请求通过 executeSiteApi('indeed', ...) 经 Worker Tab MAIN world 执行。
 * Indeed 匿名搜索无需登录；已保存职位需要 indeed_sess cookie。
 *
 * 依赖（全局）：executeSiteApi（core/site-executor.js）
 */

/**
 * 按 country 返回 Indeed 地区子域。US → www.indeed.com；其他国家 → <code>.indeed.com
 * 用于 /rpc/*, /cmp/*, /viewjob 等强绑定地区的端点。
 */
function indeedRegionOrigin(country) {
  const c = (country || 'US').toUpperCase();
  return c === 'US' ? 'https://www.indeed.com' : `https://${c.toLowerCase()}.indeed.com`;
}

// 向后兼容别名（老代码 & 测试可能仍在用）
const INDEED_ORIGIN = 'https://www.indeed.com';

// ── 跨区域 API 域（与地区子域无关，全球统一） ───────────────────────────────
const INDEED_APIS_ORIGIN = 'https://apis.indeed.com';
const INDEED_APPLY_ORIGIN = 'https://apply.indeed.com';
const INDEED_AUTOCOMPLETE_ORIGIN = 'https://autocomplete.indeed.com';
const INDEED_MYJOBS_ORIGIN = 'https://myjobs.indeed.com';

/**
 * 搜索职位 — 从 SERP HTML（/jobs?q=...&l=...）解析 mosaic-provider-jobcards 数据。
 *
 * 背景：老的 /rpc/jobsearch/v2 JSON API 在部分地区（SG/CA/UK）开始限流或返 401；
 * Indeed 现在主流程走 SSR：浏览器导航到 /jobs 页面，职位列表作为 JSON 内嵌在 HTML
 * script 里。本函数通过 executeSiteApi 的 allowNonJson 选项拿到 HTML，用三级回退
 * 策略解析：
 *   1) window.mosaic.providerData["mosaic-provider-jobcards"] 块  (主路径)
 *   2) 正则匹配 "mosaicProviderJobCardsModel".results 裸数组      (兜底 A)
 *   3) DOM 扫描 data-jk="..." 属性                               (兜底 B, 仅 jobKey)
 *
 * @param {string} query     - 搜索关键词
 * @param {string} location  - 地点（可选）
 * @param {number} start     - 分页偏移（每页 10 条）
 * @param {string} country   - 用户所在地区码，默认 US。SG/UK/CA/IN/AU 走对应子域。
 * @returns {Promise<{jobs: Array, total: number, _via?: string, _parse_error?: string}>}
 */
async function apiIndeedSearchJobs(query, location = '', start = 0, country = 'US', filters = {}) {
  const params = new URLSearchParams({ q: query });
  if (location) params.set('l', location);
  if (start) params.set('start', String(start));
  // from 参数必须跟 start 语义匹配,否则 Indeed 判为爬虫:
  //   - 首次搜索 (start=0): 来自首页 searchOnHP
  //   - 翻页 (start>0): 来自 SERP 自身 searchOnDesktopSerp
  params.set('from', start > 0 ? 'searchOnDesktopSerp' : 'searchOnHP');
  // 筛选器参数 — Indeed SERP 的 URL query 直接驱动 SSR 结果
  if (filters && typeof filters === 'object') {
    // jt: fulltime / parttime / contract / internship / temporary
    if (filters.jobType) params.set('jt', String(filters.jobType));
    // fromage: 1 / 3 / 7 / 14  (days since posted)
    if (filters.fromage) params.set('fromage', String(filters.fromage));
    // radius: miles (0 / 5 / 10 / 15 / 25 / 50 / 100)
    if (filters.radius != null && filters.radius !== '') {
      params.set('radius', String(filters.radius));
    }
    // salary estimate (美区 annual): sr=80000 表示 $80k+
    if (filters.salaryMin) params.set('sr', String(filters.salaryMin));
    // experience level: entry_level / mid_level / senior_level
    if (filters.experience) params.set('explvl', String(filters.experience));
    // sort: 'date' 按时间倒序; 默认空=按相关度
    if (filters.sort) params.set('sort', String(filters.sort));
  }
  const resp = await executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/jobs?${params}`,
    {
      method: 'GET',
      headers: { 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' },
      allowNonJson: true,
    });
  const html = (resp && resp._html) || '';
  return _parseIndeedSerpHtml(html);
}


/**
 * RecordJobSeen GraphQL mutation — 反爬伪装。
 * 抓包里真实用户每看一个职位都会 POST 一次,我们 batch 分析时顺便发一下,
 * 让网络特征更接近真实用户。失败静默,不影响主流程。
 *
 * @param {string} trackingKey - 来自 search 结果的 jobResultTrackingKey
 * @param {string} country     - 'US' / 'SG' / 'CN' 等
 * @param {object} [opts]      - { isSponsored, href } 可选补充 serpFields
 */
async function apiIndeedRecordJobSeen(trackingKey, country = 'US', opts = {}) {
  if (!trackingKey) return null;
  const body = JSON.stringify({
    operationName: 'RecordJobSeen',
    variables: {
      input: {
        jobResultTrackingKeys: [trackingKey],
        additionalInfo: {
          serpFields: {
            isMobileRequest: false,
            isSponsored: !!opts.isSponsored,
            country,
            ...(opts.href ? { href: opts.href } : {}),
          },
        },
      },
    },
    query: 'mutation RecordJobSeen($input: RecordJobSeenInput!) {\n'
         + '  recordJobSeen(input: $input) {\n'
         + '    ctk\n'
         + '    __typename\n'
         + '  }\n'
         + '}',
  });
  try {
    return await executeSiteApi('indeed',
      `${indeedRegionOrigin(country)}/graphql`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
  } catch (_) {
    return null;
  }
}


/**
 * 从 mosaic-provider-jobcards 的 result 对象里提取薪资文本。
 * Indeed 同时用多种字段表达薪资,容错多条路径取第一个非空:
 *   salarySnippet.text:           "$128,700 - $196,200 a year"
 *   salarySnippet.currency+text:  "USD $80,000 a year"
 *   extractedSalary:              {min, max, type}             内部字段
 *   estimatedSalary.formattedRange: "..."                       估算范围(加前缀标注)
 *   formattedSalary:              已格式化字符串
 *   salary:                       老字段,偶尔出现
 */
function _extractIndeedSalaryText(r) {
  if (!r || typeof r !== 'object') return '';
  if (r.salarySnippet && typeof r.salarySnippet === 'object' && r.salarySnippet.text) {
    return String(r.salarySnippet.text);
  }
  if (typeof r.formattedSalary === 'string' && r.formattedSalary) {
    return r.formattedSalary;
  }
  if (r.extractedSalary && typeof r.extractedSalary === 'object') {
    const es = r.extractedSalary;
    const unit = (es.type || 'year').toLowerCase();
    if (es.min != null && es.max != null) {
      return `$${Number(es.min).toLocaleString()} - $${Number(es.max).toLocaleString()} /${unit}`;
    }
    if (es.min != null || es.max != null) {
      const v = es.min != null ? es.min : es.max;
      return `$${Number(v).toLocaleString()} /${unit}`;
    }
  }
  if (r.estimatedSalary && typeof r.estimatedSalary === 'object') {
    const est = r.estimatedSalary;
    if (est.formattedRange) return `预估 ${est.formattedRange}`;
    if (est.min != null && est.max != null) {
      return `预估 $${Number(est.min).toLocaleString()} - $${Number(est.max).toLocaleString()}`;
    }
  }
  if (typeof r.salary === 'string' && r.salary) return r.salary;
  return '';
}


/** 把 mosaic-provider-jobcards 的 result 记录压成统一形状。 */
function _shapeIndeedJob(r) {
  if (!r || typeof r !== 'object') return null;
  const jobKey = r.jobkey || r.jobKey || r.jk || '';
  if (!jobKey) return null;
  const location = r.formattedLocation
    || r.location
    || (r.jobLocationCity
        ? [r.jobLocationCity, r.jobLocationState, r.jobLocationCountry].filter(Boolean).join(', ')
        : '');
  return {
    jobKey,
    title: r.title || r.displayTitle || r.jobtitle || '',
    company: r.company || r.companyName || '',
    location,
    salary: _extractIndeedSalaryText(r),
    trackingKey: r.jobResultTrackingKey || r.trackingKey || r.jrTk || '',
  };
}


/**
 * 从 Indeed SERP HTML 提取职位列表，三级回退。
 * 返回 { jobs, total, _via }；失败时 jobs=[] 并在 _parse_error 里给出原因。
 */
function _parseIndeedSerpHtml(html) {
  if (typeof html !== 'string' || html.length < 1000) {
    return { jobs: [], total: 0, _parse_error: 'empty_or_too_short' };
  }
  // 极端情况：挑战页 / 验证码页（没有任何职位数据标记）
  if (/cf-turnstile|cf-chl-widget|captcha\.indeed/i.test(html)
      && !/"jobkey"|"mosaicProviderJobCardsModel"/.test(html)) {
    return { jobs: [], total: 0, _parse_error: 'cloudflare_challenge' };
  }

  // ── 策略 1：window.mosaic.providerData 注入块 ──────────────
  try {
    const m = html.match(
      /window\.mosaic\.providerData\s*\[\s*["']mosaic-provider-jobcards["']\s*\]\s*=\s*(\{[\s\S]*?\});\s*(?:window\.|<\/script>)/
    );
    if (m && m[1]) {
      const data = JSON.parse(m[1]);
      const results = data?.metaData?.mosaicProviderJobCardsModel?.results
        || data?.metaData?.jobcardsMosaicResults?.results
        || [];
      const jobs = results.map(_shapeIndeedJob).filter(Boolean);
      if (jobs.length) {
        return { jobs, total: jobs.length, _via: 'mosaic-provider-jobcards' };
      }
    }
  } catch (_) { /* fallthrough */ }

  // ── 策略 2：正则匹配裸 results 数组 ─────────────────────────
  try {
    const m = html.match(
      /"mosaicProviderJobCardsModel"\s*:\s*\{[^{}]*?"results"\s*:\s*(\[[\s\S]*?\])\s*,\s*"[a-zA-Z_]/
    );
    if (m && m[1]) {
      const results = JSON.parse(m[1]);
      const jobs = results.map(_shapeIndeedJob).filter(Boolean);
      if (jobs.length) {
        return { jobs, total: jobs.length, _via: 'regex-mosaicProviderJobCardsModel' };
      }
    }
  } catch (_) { /* fallthrough */ }

  // ── 策略 3：DOM 扫描 data-jk 兜底 ──────────────────────────
  const jobKeys = new Set();
  const re = /data-jk=["']([a-f0-9]{8,})["']/gi;
  let match;
  while ((match = re.exec(html)) !== null) jobKeys.add(match[1]);
  if (jobKeys.size > 0) {
    const jobs = Array.from(jobKeys).map(jk => ({
      jobKey: jk, title: '', company: '', location: '', trackingKey: '',
    }));
    return { jobs, total: jobs.length, _via: 'dom-datajk', _partial: true };
  }

  return { jobs: [], total: 0, _parse_error: 'no_pattern_matched' };
}

/**
 * 获取职位详情 — 从 /viewjob?jk=... 的 HTML 里用三级策略解析结构化数据。
 *
 * 背景：Indeed 没有稳定的 JSON detail API 可用；`/m/basecamp/viewjob?viewtype=embeddedhtml`
 * 和 `/rpc/viewjob` 都返回 HTML（或被限流）。完整 SERP HTML 里反而带有 schema.org
 * JobPosting JSON-LD（标准协议，Indeed 不轻易改），所以优先走它，再回退到
 * window._initialData / #jobDescriptionText DOM。
 *
 * @param {string} jobKey - Indeed 职位唯一 Key（jk 参数）
 * @param {string} country - 用户所在地区码，默认 US
 * @returns {Promise<{jobKey, title?, description?, company?, formattedLocation?, salary?,
 *                    employmentType?, datePosted?, _via?, _parse_error?, raw_schema?,
 *                    raw_initial?}>}
 */
async function apiIndeedGetJobDetail(jobKey, country = 'US') {
  if (!jobKey) throw new Error('jobKey 必填');
  // spa=1 会让 Indeed 返回 SPA 骨架(几乎无 SSR 数据); 明确不传,拿完整 SSR HTML。
  const resp = await executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/viewjob?jk=${encodeURIComponent(jobKey)}&from=vjs&vjs=1`,
    {
      method: 'GET',
      headers: { 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' },
      allowNonJson: true,
    });
  const html = (resp && resp._html) || '';
  return _parseIndeedDetailHtml(html, jobKey);
}


/** 把 schema.org JobPosting.baseSalary 对象压成一行人类可读薪资字符串。 */
function _formatSchemaSalary(bs) {
  if (!bs || typeof bs !== 'object') return '';
  const v = bs.value || bs;
  if (!v || typeof v !== 'object') return '';
  const cur = bs.currency || v.currency || '';
  const unit = String(v.unitText || 'YEAR').toLowerCase();
  if (v.minValue != null && v.maxValue != null) {
    return `${cur} ${v.minValue} - ${v.maxValue} /${unit}`.trim();
  }
  if (v.value != null) {
    return `${cur} ${v.value} /${unit}`.trim();
  }
  return '';
}


/** HTML entity decode — 最小实现,只处理最常见的几个。 */
function _decodeHtmlEntities(s) {
  if (typeof s !== 'string') return '';
  return s
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(parseInt(code, 10)));
}


/** 把 HTML 片段转纯文本(保持段落/换行)。 */
function _htmlToText(html) {
  if (typeof html !== 'string') return '';
  return _decodeHtmlEntities(
    html
      .replace(/<script[\s\S]*?<\/script>/gi, '')
      .replace(/<style[\s\S]*?<\/style>/gi, '')
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/(?:p|li|h[1-6]|div|section|article)>/gi, '\n')
      .replace(/<li[^>]*>/gi, '• ')
      .replace(/<[^>]+>/g, '')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n\s*\n\s*\n+/g, '\n\n')
  ).trim();
}


/**
 * 从 Indeed viewjob HTML 抽职位详情。五级回退(越靠前越稳定全量):
 *   1) schema.org JSON-LD JobPosting
 *   2) mosaic-provider-jobview 注入块 (Indeed 现代 SSR 的常见位置)
 *   3) window._initialData.jobInfoWrapperModel.jobInfoModel
 *   4) DOM 类名扫描 (jobsearch-JobInfoHeader-title + companyName + jobDescriptionText)
 *   5) 单独 #jobDescriptionText (只有 description,最后兜底)
 *
 * 失败时返回 _parse_error 并附上诊断信息(HTML 长度 + 检测到的 marker)。
 */
function _parseIndeedDetailHtml(html, jobKey) {
  if (typeof html !== 'string' || html.length < 500) {
    return { jobKey, _parse_error: 'empty_or_too_short', _html_length: (html || '').length };
  }
  if (/cf-turnstile|cf-chl-widget|captcha\.indeed/i.test(html)
      && !/JobPosting|jobDescriptionText|mosaic-provider/.test(html)) {
    return { jobKey, _parse_error: 'cloudflare_challenge', _html_length: html.length };
  }

  // ── 策略 1: schema.org JSON-LD JobPosting ───────────────────
  try {
    const re = /<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
    let m;
    while ((m = re.exec(html)) !== null) {
      let ld;
      try { ld = JSON.parse(m[1].trim()); } catch (_) { continue; }
      const cands = Array.isArray(ld) ? ld : [ld];
      const pick = cands.find(x => {
        const t = x && x['@type'];
        return t === 'JobPosting' || (Array.isArray(t) && t.includes('JobPosting'));
      });
      if (!pick) continue;
      const loc = pick.jobLocation || {};
      const addr = (loc.address) || {};
      const formattedLocation = loc.name
        || [addr.streetAddress, addr.addressLocality, addr.addressRegion, addr.addressCountry]
           .filter(Boolean).join(', ');
      const description = pick.description ? _decodeHtmlEntities(pick.description) : '';
      return {
        jobKey,
        title: pick.title || '',
        description,
        company: (pick.hiringOrganization && pick.hiringOrganization.name) || '',
        formattedLocation,
        salary: _formatSchemaSalary(pick.baseSalary),
        employmentType: pick.employmentType || '',
        datePosted: pick.datePosted || '',
        validThrough: pick.validThrough || '',
        _via: 'schema-org-jobposting',
        raw_schema: pick,
      };
    }
  } catch (_) { /* fallthrough */ }

  // ── 策略 2: mosaic-provider-jobview 注入块 ──────────────────
  try {
    const m = html.match(
      /window\.mosaic\.providerData\s*\[\s*["'](?:mosaic-provider-jobview|mosaic-provider-jobsearch-vj)["']\s*\]\s*=\s*(\{[\s\S]*?\});\s*(?:window\.|<\/script>)/
    );
    if (m && m[1]) {
      const data = JSON.parse(m[1]);
      const info = data?.jobInfoWrapperModel?.jobInfoModel
        || data?.metaData?.jobInfoWrapperModel?.jobInfoModel
        || data?.mosaicData?.jobInfoWrapperModel?.jobInfoModel
        || {};
      const desc = info?.jobDescriptionSectionModel || {};
      const loc = info?.jobLocationModel || {};
      const salaryInfo = info?.salaryInfoModel || info?.salaryGuideModel || {};
      const salary = salaryInfo?.salaryText || salaryInfo?.formattedSalary || '';
      if (info?.jobTitleModel || desc?.jobDescriptionText || desc?.jobDescriptionHtml) {
        const descText = desc?.jobDescriptionText
          || (desc?.jobDescriptionHtml ? _htmlToText(desc.jobDescriptionHtml) : '');
        return {
          jobKey,
          title: info?.jobTitleModel?.jobTitle || info?.jobTitle || '',
          description: descText,
          company: info?.companyModel?.companyName || info?.companyName || '',
          formattedLocation: loc?.formattedAddress || loc?.jobLocation || '',
          salary,
          _via: 'mosaic-provider-jobview',
          raw_mosaic: info,
        };
      }
    }
  } catch (_) { /* fallthrough */ }

  // ── 策略 3: window._initialData ─────────────────────────────
  try {
    const m = html.match(
      /window\._initialData\s*=\s*(\{[\s\S]*?\});\s*(?:window\.|<\/script>|\(function)/
    );
    if (m && m[1]) {
      const data = JSON.parse(m[1]);
      const info = data?.jobInfoWrapperModel?.jobInfoModel
        || data?.jobInfoModel
        || {};
      const desc = info?.jobDescriptionSectionModel || {};
      const loc = info?.jobLocationModel || {};
      if (info?.jobTitleModel || desc?.jobDescriptionText || desc?.jobDescriptionHtml) {
        const descText = desc?.jobDescriptionText
          || (desc?.jobDescriptionHtml ? _htmlToText(desc.jobDescriptionHtml) : '');
        return {
          jobKey,
          title: info?.jobTitleModel?.jobTitle || info?.jobTitle || '',
          description: descText,
          company: info?.companyModel?.companyName || info?.companyName || '',
          formattedLocation: loc?.formattedAddress || loc?.jobLocation || '',
          _via: 'window._initialData',
          raw_initial: info,
        };
      }
    }
  } catch (_) { /* fallthrough */ }

  // ── 策略 4: DOM 结构化抽取(标题 + 公司 + 描述 + 薪资) ──────
  try {
    const titleM = html.match(
      /<h1[^>]*class=["'][^"']*jobsearch-JobInfoHeader-title[^"']*["'][^>]*>([\s\S]*?)<\/h1>/i
    ) || html.match(/<h1[^>]*data-testid=["']jobsearch-JobInfoHeader-title["'][^>]*>([\s\S]*?)<\/h1>/i);
    const companyM = html.match(
      /<div[^>]*data-(?:company-name|testid)=["'](?:true|[^"']*companyName[^"']*)["'][^>]*>([\s\S]*?)<\/div>/i
    ) || html.match(/<a[^>]*data-testid=["'][^"']*companyName[^"']*["'][^>]*>([\s\S]*?)<\/a>/i);
    const locationM = html.match(
      /<div[^>]*data-testid=["'][^"']*jobLocation[^"']*["'][^>]*>([\s\S]*?)<\/div>/i
    );
    const salaryM = html.match(
      /<div[^>]*id=["']salaryInfoAndJobType["'][^>]*>([\s\S]*?)<\/div>/i
    ) || html.match(/<span[^>]*class=["'][^"']*salary-snippet[^"']*["'][^>]*>([\s\S]*?)<\/span>/i);
    const descM = html.match(
      /<div[^>]*id=["']jobDescriptionText["'][^>]*>([\s\S]*?)<\/div>\s*(?=<(?:div|section|footer))/i
    );
    const description = descM ? _htmlToText(descM[1]) : '';
    const title = titleM ? _htmlToText(titleM[1]) : '';
    const company = companyM ? _htmlToText(companyM[1]) : '';
    // 至少拿到 title 才算"结构化"成功; 只有 description 的情况交给策略 5 处理。
    if (title && description && description.length > 20) {
      return {
        jobKey,
        title,
        description,
        company,
        formattedLocation: locationM ? _htmlToText(locationM[1]) : '',
        salary: salaryM ? _htmlToText(salaryM[1]) : '',
        _via: 'dom-jobsearch-classes',
        _partial: !company,
      };
    }
  } catch (_) { /* fallthrough */ }

  // ── 策略 5: 单独 #jobDescriptionText 兜底 ──────────────────
  const descOnlyM = html.match(
    /<div[^>]*id=["']jobDescriptionText["'][^>]*>([\s\S]*?)(?=<(?:\/div>\s*<(?:section|footer|script|div[^>]*class=["'][^"']*jobsearch-[^"']*RelatedLinks)|\/body>))/i
  );
  if (descOnlyM) {
    const stripped = _htmlToText(descOnlyM[1]);
    if (stripped.length > 20) {
      return {
        jobKey,
        description: stripped,
        _via: 'dom-jobDescriptionText',
        _partial: true,
      };
    }
  }

  // 诊断信息:告诉上游页面里都有哪些 marker,方便未来排障。
  const markers = {
    has_jsonld: /application\/ld\+json/.test(html),
    has_jobposting_type: /"@type"\s*:\s*"JobPosting"/.test(html),
    has_mosaic: /mosaic-provider/.test(html),
    has_initial_data: /window\._initialData/.test(html),
    has_job_desc_div: /id=["']jobDescriptionText["']/.test(html),
    has_header_title: /jobsearch-JobInfoHeader-title/.test(html),
    has_captcha: /cf-turnstile|captcha/.test(html),
    first_tag: (html.match(/<([a-z][a-z0-9]*)/i) || [])[1] || '',
  };
  return {
    jobKey,
    _parse_error: 'no_pattern_matched',
    _html_length: html.length,
    _markers: markers,
  };
}

/**
 * 投递前置：为 jk 生成 smartapply URL + iaUid
 * 响应: { result: { applyUrl, iaUid, ... } }
 * @param {string} jk         - Indeed 职位 jobKey
 * @param {string} jobCountry - 国家码（SG / US / CN 等）
 * @param {string} tk         - tracking key（可选，从 SERP cookie 取，缺省服务端降级）
 * @param {string} vjtk       - viewjob tracking key（可选）
 * @param {string} ctk        - client tracking key（可选）
 */
async function apiIndeedPrepareApply(jk, jobCountry = 'US', tk = '', vjtk = '', ctk = '') {
  const qs = new URLSearchParams();
  if (tk) qs.set('tk', tk);
  if (vjtk) qs.set('vjtk', vjtk);
  if (ctk) qs.set('ctk', ctk);
  qs.set('jk', jk);
  const jobUrl = `https://www.indeed.com/viewjob?jk=${encodeURIComponent(jk)}`;
  const body = {
    iip: true,
    isGql: true,
    isApi: false,
    source: 'idd',
    hl: 'en',
    co: jobCountry,
    spn: false,
    jobUrl,
    jobCountry,
    jk,
    newTab: false,
    noButtonUI: true,
  };
  return executeSiteApi('indeed',
    `${INDEED_APPLY_ORIGIN}/api/v1/env?${qs}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify(body),
    });
}

/**
 * 查询某 jk 是否已投递
 * 依赖: 先调 apiIndeedPrepareApply 取 iaUid
 * 响应: { result: { applied, appliedMs }, success }
 */
async function apiIndeedCheckApplied(jk, iaUid, jobCountry = 'US') {
  const jobUrl = `https://www.indeed.com/viewjob?jk=${encodeURIComponent(jk)}`;
  return executeSiteApi('indeed',
    `${INDEED_APPLY_ORIGIN}/api/v1/appliedStatus`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        iaUid,
        isGql: true,
        src: 'idd',
        jk,
        jobUrl,
        jobCountry,
      }),
    });
}

/**
 * 查询某搜索是否已订阅 job alert
 * 响应: { subscriptions: [{ q, l, subscriptionState }] } — state 为 ACTIVE / NOT_FOUND
 */
async function apiIndeedSearchJobAlert(keywords, location, country = 'US', locale = 'en_US') {
  return executeSiteApi('indeed',
    `${INDEED_APIS_ORIGIN}/jobseeker/jobalerts/v1/jobalerts/search`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clientApplication: 'jasx',
        co: country,
        locale,
        subscriptionStates: [],
        filters: [{ q: keywords, l: location }],
      }),
    });
}

/**
 * 创建邮件 job alert 订阅
 * 响应: { subscriptionState: "ACTIVE", subscriptionId: "..." }
 */
async function apiIndeedCreateJobAlert(keywords, location, email,
                                       country = 'US', locale = 'en_US',
                                       searchParams = '', clientTk = '') {
  return executeSiteApi('indeed',
    `${INDEED_APIS_ORIGIN}/jobseeker/jobalerts/v1/jobalerts/`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        q: keywords,
        l: location,
        nl: '',
        email,
        clientApplication: 'jasx',
        clientTk,
        co: country,
        locale,
        position: 'DesktopSERPJobAlertPopup',
        forcedGroups: '',
        searchParams,
      }),
    });
}

/**
 * 输入自动补全
 * @param {string} type     - 'what' | 'location' | 'cmp-what-with-top-companies'
 * @param {string} query    - 用户输入的片段
 * @param {string} country  - 国家码
 * @param {string} language - 语言码
 * @param {string} location - 仅对 'cmp-what-with-top-companies' 生效，作为 where 过滤（如 remote）
 */
async function apiIndeedAutocomplete(type, query, country = 'US', language = 'en', location = '') {
  const qs = new URLSearchParams({
    country,
    language,
    count: '10',
    formatted: '1',
    query,
    useEachWord: 'false',
    page: 'homepage',
    showAlternateSuggestions: 'false',
    rich: 'true',
  });
  if (type === 'cmp-what-with-top-companies') {
    qs.set('types', 'cmp-what-with-top-companies');
    qs.set('counts', '10');
    qs.set('merged', 'true');
    qs.set('grouped', 'false');
    qs.set('page', 'serp');
    if (location) qs.set('where', location);
  }
  return executeSiteApi('indeed',
    `${INDEED_AUTOCOMPLETE_ORIGIN}/api/v0/suggestions/${encodeURIComponent(type)}?${qs}`,
    { method: 'GET', headers: { 'Accept': 'application/json' } });
}

/**
 * 未读会话数（消息红点）
 * 响应: { data: { getUnreadConversationCount: { unreadConversationCount: N } } }
 */
async function apiIndeedUnreadMessages() {
  const body = {
    operationName: 'GetUnreadConversationCount',
    query: `
            query GetUnreadConversationCount($input: GetUnreadConversationCountInput!) {
                getUnreadConversationCount(input: $input) {
                    unreadConversationCount
                }
            }
        `,
    variables: {
      input: {
        conversationFilter: {
          excludedLabels: ['JS_MSG_FOLDER/ARCHIVE', 'JS_MSG_FOLDER/SPAM'],
        },
      },
    },
  };
  return executeSiteApi('indeed',
    `${INDEED_APIS_ORIGIN}/graphql`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
}

/**
 * 保存/取消保存 职位 — 两个 action 共用同一端点
 * @param {string} jk        - 职位 jobKey
 * @param {'save'|'unsave'} action
 * @param {string} csrfToken - 从 cookie `indeedcsrftoken` 取
 * @param {string} country   - 用户所在地区码，默认 US
 */
async function apiIndeedTransitionJobState(jk, action, csrfToken, country = 'US') {
  const qs = new URLSearchParams({
    client: 'mobile',
    cause: 'statepicker',
    preserveTimestamp: 'false',
    jobKey: jk,
    originPage: 'viewjob',
    action,
    indeedcsrftoken: csrfToken,
  });
  return executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/rpc/log/myjobs/transition_job_state?${qs}`,
    { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
}

/**
 * 隐藏/撤销隐藏 职位
 * @param {string} jk
 * @param {boolean} undo   - false=隐藏, true=撤销隐藏
 * @param {string} country
 */
async function apiIndeedDislikeJob(jk, undo = false, country = 'US') {
  const qs = new URLSearchParams({
    pageId: 'jasx',
    jobKey: jk,
    undo: String(!!undo),
  });
  return executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/rpc/dislikeJob?${qs}`,
    { method: 'POST' });
}

/**
 * 公司名搜索补全
 * @param {string} query
 * @param {number} caret  - 光标位置，默认等于 query 长度
 * @param {string} country
 */
async function apiIndeedSearchCompanies(query, caret = -1, country = 'US') {
  const qs = new URLSearchParams({
    q: query,
    caret: String(caret < 0 ? query.length : caret),
  });
  return executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/cmp/_ws/what?${qs}`,
    { method: 'GET', headers: { 'Accept': 'application/json' } });
}

/**
 * 公司上下文下的职位详情（比 /viewjob 精简）
 * @param {string} jobKey
 * @param {string} country
 * @param {boolean} loggedIn
 */
async function apiIndeedGetCompanyJobs(jobKey, country = 'US', loggedIn = true) {
  const qs = new URLSearchParams({
    jobKey,
    loggedIn: loggedIn ? '1' : '0',
  });
  return executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/cmp/-/rpc/fetch-jobs?${qs}`,
    { method: 'GET', headers: { 'Accept': 'application/json' } });
}

/**
 * 检查是否已关注该公司（免费用户"公司有新岗位时提醒我"）
 * @param {string} companyName - 公司显示名，如 "Ministry of Education Singapore"
 * @param {string} companyId   - 公司 ID（抓包里叫 cid）
 * @param {string} country
 */
async function apiIndeedFollowCompanyCheck(companyName, companyId, country = 'US') {
  const qs = new URLSearchParams({
    a: 'check',
    q: `company:"${companyName}"`,
    followCompany: companyId,
    app: 'acme',
  });
  return executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/rpc/jobalert?${qs}`,
    { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
}

/**
 * 读取用户的 JSSD (Job Seeker Structured Data) 偏好：
 * minimumPay / locations / relocation / maximumCommute / 工作时间 等
 * @param {string} country - 用户所在地区，影响 GraphQL 端点的 co 参数
 */
/**
 * 列出用户的会话（消息中心）
 * @param {string} country   - 'US'/'SG'/'UK' 等
 * @param {string} locale    - 'en-US'/'zh-SG' 等，不传则后端默认
 * @param {'inbox'|'archive'|'spam'} folder - 默认 inbox
 * @param {number} last      - 每页条数，默认 30
 * @param {string} cursor    - 上一页的 endCursor，用于翻页
 */
async function apiIndeedListConversations(country = 'US', locale = '',
                                          folder = 'inbox', last = 30, cursor = '') {
  const variables = { last, includeRequireResponse: true };
  if (folder === 'archive') {
    variables.includedLabels = ['JS_MSG_FOLDER/ARCHIVE'];
  } else if (folder === 'spam') {
    variables.includedLabels = ['JS_MSG_FOLDER/SPAM'];
  } else {
    // inbox = 除去 archive/spam
    variables.excludedLabels = ['JS_MSG_FOLDER/ARCHIVE', 'JS_MSG_FOLDER/SPAM'];
  }
  if (cursor) variables.before = cursor;

  const body = {
    operationName: 'GetConversations',
    variables,
    query: `query GetConversations($last: Int = 10, $before: String, $first: Int, $after: String, $includedLabels: [String!], $excludedLabels: [String!], $contexts: [ConversationContext!], $scope: [ConversationScopeInput!], $includeRequireResponse: Boolean = false) {
  findConversations(
    input: {filter: {includedLabels: $includedLabels, excludedLabels: $excludedLabels, contexts: $contexts, scope: $scope}}
    last: $last
    before: $before
    first: $first
    after: $after
  ) {
    edges {
      node { ...ConversationPreviewFields __typename }
      cursor
      __typename
    }
    pageInfo { startCursor endCursor hasNextPage hasPreviousPage __typename }
    __typename
  }
}

fragment ConversationPreviewFields on Conversation {
  id __typename creationDateTime context
  scope { key value __typename }
  lastEvent {
    author { accountKey externalParticipantId role __typename }
    type subType messageContentFormat messagePreview publicationDateTime __typename
  }
  participants { accountKey externalParticipantId participantName removed role __typename }
  userReadsInfo { unreadCount __typename }
  userLabelInfo { labels __typename }
  locks { primary reason timestamp endUserDisplayText isRemovable __typename }
  job {
    key displayTitle normalizedTitle title
    employer {
      key name
      dossier { images { squareLogoUrls { url64 __typename } __typename } __typename }
      __typename
    }
    location { formatted { short __typename } __typename }
    sourceEmployerName __typename
  }
  userContext {
    participantKey
    requireResponse @include(if: $includeRequireResponse) { required timestamp __typename }
    __typename
  }
  jobIsConfirmedFraudulent
}`,
  };
  const qs = new URLSearchParams({ co: (country || 'US').toUpperCase() });
  if (locale) qs.set('locale', locale);
  return executeSiteApi('indeed',
    `${INDEED_APIS_ORIGIN}/graphql?${qs}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
}

/**
 * 查询用户在线状态偏好（对招聘方是否显示在线）
 */
async function apiIndeedGetOnlineStatus(country = 'US', locale = '') {
  const body = {
    operationName: 'GetOnlineStatusPreference',
    variables: {},
    query: `query GetOnlineStatusPreference {
  onlineActivityPreferences {
    onlineStatusPreference { isEnabled __typename }
    __typename
  }
}`,
  };
  const qs = new URLSearchParams({ co: (country || 'US').toUpperCase() });
  if (locale) qs.set('locale', locale);
  return executeSiteApi('indeed',
    `${INDEED_APIS_ORIGIN}/graphql?${qs}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
}

// ── 反爬行为上报（内部 helper，不暴露为命令） ───────────────────────────────
//
// Indeed 正常用户浏览 SERP 时浏览器会自动发 RecordJobSeen / RecordJobTimeInViewport。
// 我们在 search_jobs / get_job_detail 成功后 fire-and-forget 调用，
// 让行为指纹靠近真实用户、降低风控升级概率。失败完全静默。

/**
 * 上报曝光的 tracking keys。
 * @param {string[]} trackingKeys - 从 search 结果中提取的 jobResultTrackingKey
 * @param {string}   serpTk       - 可选 applicationTrackingKey / serpTrackingKey
 */
function recordJobSeen(trackingKeys, serpTk = '') {
  if (!Array.isArray(trackingKeys) || trackingKeys.length === 0) return;
  const body = {
    operationName: 'RecordJobSeen',
    variables: {
      input: {
        jobResultTrackingKeys: trackingKeys,
        additionalInfo: {
          serpFields: {
            applicationTrackingKey: serpTk || '',
            serpTrackingKey: serpTk || '',
            adId: [],
            isMobileRequest: false,
            isSponsored: false,
            country: 'US',
          },
        },
      },
    },
    query: `mutation RecordJobSeen($input: RecordJobSeenInput!) {
  recordJobSeen(input: $input) { ctk __typename }
}`,
  };
  // fire-and-forget
  try {
    Promise.resolve(executeSiteApi('indeed', `${INDEED_APIS_ORIGIN}/graphql`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })).catch(() => {});
  } catch (_) { /* 静默 */ }
}

/**
 * 上报每个 jobKey 在视口的停留时长。
 * @param {Array<{jobKey:string,durationMilliseconds:number}>} jobInfos
 * @param {string} searchTk - 可选 searchTrackingKey / surfaceTrackingKey
 */
function recordJobTimeInViewport(jobInfos, searchTk = '') {
  if (!Array.isArray(jobInfos) || jobInfos.length === 0) return;
  const body = {
    operationName: 'RecordJobTimeInViewport',
    variables: {
      input: {
        isMobile: false,
        jobInfos,
        searchProvider: 'search',
        searchTrackingKey: searchTk || '',
        surface: 'serp',
        surfaceTrackingKey: searchTk || '',
      },
    },
    query: `mutation RecordJobTimeInViewport($input: RecordJobTimeInViewportInput!) {
  recordJobTimeInViewport(input: $input) { ctk __typename }
}`,
  };
  try {
    Promise.resolve(executeSiteApi('indeed', `${INDEED_APIS_ORIGIN}/graphql`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })).catch(() => {});
  } catch (_) { /* 静默 */ }
}

/**
 * 同岗位竞品公司职位推荐（SERP 右侧 "Jobs at similar companies"）。
 * @param {string} jobKey  - 当前职位 jobKey
 * @param {number} limit   - 返回条数
 * @param {string} country - 地区码
 */
async function apiIndeedGetCompetitorJobs(jobKey, limit = 15, country = 'US') {
  const qs = new URLSearchParams({ jobKey, limit: String(limit) });
  return executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/m/getcompetitorsjobs?${qs}`,
    { method: 'GET', headers: { 'Accept': 'application/json' } });
}

/**
 * 某 (keywords, location) 组合在时间窗内的新增职位数。
 * @param {string} keywords - 搜索关键词（可空）
 * @param {string} location - 地点（必填，如 'remote' / 'San Francisco'）
 * @param {string} fromage  - 'last'(自上次) / '1' / '3' / '7' / '14' 天
 * @param {string} country  - 地区码
 */
async function apiIndeedGetNewJobsCount(keywords, location, fromage = 'last', country = 'US') {
  const qs = new URLSearchParams({
    q: keywords || '',
    l: location,
    ts: String(Date.now()),
    fromage,
  });
  return executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/m/newjobs?${qs}`,
    { method: 'GET', headers: { 'Accept': 'application/json' } });
}

/**
 * 读取当前登录用户的简历数据（自己的），用于驱动 indeed/apply 自动填表。
 * 响应: { data: { jobSeekerProfile: { profile: { accountKey, defaultInfo, resume, ... } } } }
 */
async function apiIndeedGetResumeSection(country = 'US') {
  const body = {
    operationName: 'ResumeSection',
    variables: {},
    query: `query ResumeSection {
  jobSeekerProfile {
    profile {
      ...profileContactInfo
      resume {
        ...resumeContactInfo
        ...resumeSectionFields
      }
      fraudMetadata { state }
    }
  }
}

fragment profileContactInfo on JobSeekerProfile {
  accountKey
  defaultInfo {
    contactInformation {
      firstName lastName
      location {
        address city admin1 postalCode country
        unknownLocation formattedLocation
        latitude longitude geocodePrecision
      }
      phoneNumber
    }
  }
}

fragment resumeContactInfo on JobSeekerProfileResume {
  id locale firstName lastName
  firstNameFurigana lastNameFurigana
  phoneNumber showPhoneNumber headline
  location {
    address address2 city admin1 postalCode country
    formattedLocation unknownLocation
    latitude longitude geocodePrecision
  }
  resumeType wordCount
  employmentEligibilities { employmentEligibility id }
}

fragment resumeSectionFields on JobSeekerProfileResume {
  id state resumeType dateModified wordCount
}`,
  };
  const co = (country || 'US').toUpperCase();
  return executeSiteApi('indeed',
    `${INDEED_APIS_ORIGIN}/graphql?co=${co}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
}

async function apiIndeedGetPreferences(country = 'US') {
  const body = {
    operationName: 'jssd_preferences',
    variables: { input: { queryFilter: { dataCategories: ['CONFIRMED_BY_USER'] } } },
    query: `query jssd_preferences($input: JobSeekerProfileStructuredDataInput) {
  jobSeekerProfileStructuredData(input: $input) {
    preferences {
      id
      minimumPay { amount currency id salaryType __typename }
      relocation { ableToRelocate __typename }
      maximumCommute { timeMinutes collectionTime __typename }
      locations { id __typename }
      attributeMetadata { dataCategory __typename }
      __typename
    }
    __typename
  }
  jobSeekerProfileStructuredDataPreferenceAttributesByCustomClass {
    negativePreferenceAttributesByCustomClass {
      customClassSuid customClassLabel
      attributes { label suid __typename }
      __typename
    }
    positivePreferenceAttributesByCustomClass {
      customClassSuid customClassLabel
      attributes { label suid __typename }
      __typename
    }
    __typename
  }
}`,
  };
  const co = (country || 'US').toUpperCase();
  return executeSiteApi('indeed',
    `${INDEED_APIS_ORIGIN}/graphql?co=${co}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
}

// ── My Jobs 系列（已申请 / 面试 / 状态更新） ────────────────────────────────

/**
 * 用户已申请职位列表（My Jobs → Applied tab 数据源）。
 * @param {number} startMs   - 过滤起始时间戳（ms），0 表示不限
 * @param {string} fromParam - 埋点来源，默认 'app-tracker'
 */
async function apiIndeedListAppliedJobs(startMs = 0, fromParam = 'app-tracker') {
  const qs = new URLSearchParams({ type: 'POST_APPLY', from: fromParam });
  if (startMs > 0) qs.set('applyUpdateStartTime', String(startMs));
  return executeSiteApi('indeed',
    `${INDEED_MYJOBS_ORIGIN}/api/v1/appStatusJobs?${qs}`,
    { method: 'GET', headers: { 'Accept': 'application/json' } });
}

/**
 * 用户面试列表（含邀请/确认/取消的所有状态）。
 * @param {string[]} statuses - 默认 JS_CONFIRM, JS_CANCEL, EMP_CANCEL, EMP_INVITE
 * @param {string[]} formats  - 默认 IN_PERSON, PHONE, THIRD_PARTY_VIDEO, INDEED_VIDEO
 * @param {number}   startMs  - 过滤起始时间戳（ms）
 */
async function apiIndeedListInterviews(statuses, formats, startMs = 0) {
  const qs = new URLSearchParams({
    statuses: (statuses && statuses.length ? statuses : ['JS_CONFIRM', 'JS_CANCEL', 'EMP_CANCEL', 'EMP_INVITE']).join(','),
    formats: (formats && formats.length ? formats : ['IN_PERSON', 'PHONE', 'THIRD_PARTY_VIDEO', 'INDEED_VIDEO']).join(','),
    from: 'app-tracker',
  });
  if (startMs > 0) qs.set('start', String(startMs));
  return executeSiteApi('indeed',
    `${INDEED_MYJOBS_ORIGIN}/api/v1/interviews?${qs}`,
    { method: 'GET', headers: { 'Accept': 'application/json' } });
}

/**
 * 更新已申请职位的状态（手动标记 NOT_INTERESTED / OFFER / INTERVIEWING 等）。
 * PATCH myjobs/api/v1/savedJobs/{jk}?indeedcsrftoken=X
 * @param {string} jk            - 职位 jobKey
 * @param {string} csrfToken     - indeedcsrftoken cookie
 * @param {object} statePayload  - {statuses, cause, prev*, new*}
 */
async function apiIndeedUpdateJobAppStatus(jk, csrfToken, statePayload) {
  const qs = new URLSearchParams({
    from: 'app-tracker',
    indeedcsrftoken: csrfToken,
  });
  return executeSiteApi('indeed',
    `${INDEED_MYJOBS_ORIGIN}/api/v1/savedJobs/${encodeURIComponent(jk)}?${qs}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(statePayload),
    });
}

// ── 全站通知红点（区别于消息红点） ──────────────────────────────────────────

async function apiIndeedGetNotificationsCount(country = 'US') {
  const qs = new URLSearchParams({ client: 'gnav', from: 'gnav-util-homepage' });
  return executeSiteApi('indeed',
    `${indeedRegionOrigin(country)}/notifications/api/1/getNotificationsCount?${qs}`,
    { method: 'GET', headers: { 'Accept': 'application/json' } });
}

// ── 邮件偏好 / 未读 Offer 数 ────────────────────────────────────────────────

async function apiIndeedGetEmailPreferences() {
  const body = {
    operationName: 'GetEmailPreferences',
    variables: {},
    query: `query GetEmailPreferences {
  emailPreferences {
    preferences { category isEnabled }
    emailChannelOptedOut
  }
}`,
  };
  return executeSiteApi('indeed',
    `${INDEED_APIS_ORIGIN}/graphql`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
}

async function apiIndeedGetUnreadOffersCount() {
  const body = {
    operationName: 'GetUnreadOffersCount',
    variables: {},
    query: `query GetUnreadOffersCount {
  getUnreadOffersCount { count }
}`,
  };
  return executeSiteApi('indeed',
    `${INDEED_APIS_ORIGIN}/graphql`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
}
