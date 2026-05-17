/**
 * sidepanel-shared/platforms-config.js — 前端单一 platform manifest
 *
 * 跟后端 platforms_config.PLATFORMS dict 同步(初期 hardcode;后续可改为后端
 * GET /platforms 下发,前端启动拉一次缓存)。
 *
 * 加新平台(如 51job / Liepin)只需:
 *   1. 在这里加一个 entry
 *   2. 后端 platforms_config.PLATFORMS 加同样 entry
 *   3. 后端 modes/cells.py 加 2 个 cell(jobseeker × myplat / recruiter × myplat)
 *   4. ext manifest content_scripts 加 myplat 域名块
 *   5. 写 myplat 站点的 API client / interceptor / chain
 *
 * 暴露:
 *   window.DQ.platforms[id] → { label, labelEn, short, siteUrl, emoji }
 *   window.DQ.listPlatforms() → ['boss', 'linkedin', 'indeed', ...]
 *   window.DQ.platformLabel(id, lang?) → '中文名' / 'English'(自动 fallback id)
 */
(function () {
  'use strict';

  window.DQ = window.DQ || {};
  window.DQ.platforms = {
    boss: {
      label: 'Boss直聘',
      labelEn: 'Boss直聘',
      short: 'BZ',
      siteUrl: 'https://www.zhipin.com/',
      emoji: '🇨🇳',
    },
    linkedin: {
      label: 'LinkedIn',
      labelEn: 'LinkedIn',
      short: 'IN',
      siteUrl: 'https://www.linkedin.com/',
      emoji: '🌐',
    },
    indeed: {
      label: 'Indeed',
      labelEn: 'Indeed',
      short: 'ID',
      siteUrl: 'https://www.indeed.com/',
      emoji: '🌎',
    },
  };

  window.DQ.listPlatforms = function () {
    return Object.keys(window.DQ.platforms);
  };

  window.DQ.platformLabel = function (id, lang) {
    const p = window.DQ.platforms[id];
    if (!p) return id || '';
    return (String(lang || '').toLowerCase().startsWith('en')) ? p.labelEn : p.label;
  };
})();
