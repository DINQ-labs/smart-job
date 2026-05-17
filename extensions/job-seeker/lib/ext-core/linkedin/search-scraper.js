/**
 * linkedin/search-scraper.js — Worker Tab MAIN-world DOM 抓取
 *
 * 背景: LinkedIn 把 People Search 整体迁移到 SDUI/RSC 二进制 protobuf 架构
 * (capture_rec_17774346 验证),旧 voyagerSearchDashByAll GraphQL 在新版 web UI
 * 零调用,我们靠 stale queryId 直接打 endpoint 持续返空集。
 *
 * 替代方案: 让 worker tab 加载 https://www.linkedin.com/search/results/people/
 * (跟用户在 LinkedIn UI 上做的搜索完全一致),等 SDUI 渲染完 → 在 MAIN world
 * 里 querySelectorAll 抓人员卡片。LinkedIn 自家 web UI 怎么给用户显示我们就
 * 怎么读。
 *
 * v1.6.23 选择器策略变更:LinkedIn 2026 SDUI 用 WAI-ARIA roles + 哈希 class。
 * 前者稳定(可访问性合规要求),后者每次 redeploy 都换。所以放弃 class 选择器,
 * 全部用 ARIA roles + URL pattern 锚定:
 *   - 屏幕容器: [data-sdui-screen*="SearchResultsPeople"]
 *   - 列表容器: [role="list"]
 *   - 单条卡片: [role="listitem"](内含至少一条 a[href*="/in/"])
 *   - 姓名: svg[aria-label] (头像) > [aria-label] 兜底
 *   - 文本: 卡内 <p> 元素按 DOM 顺序 = [headline, location, current_job, mutuals]
 *
 * 这里所有函数都通过 chrome.scripting.executeScript 注入到 worker tab,
 * **不能**依赖任何 Service Worker 全局变量(包括 chrome.storage 也不能用)。
 * 全部状态通过 args 进/return 出。
 */

/**
 * 在 LinkedIn 人员搜索页(SPA hydrated 后)轮询抓取人员卡片。
 *
 * @param {number} targetCount  - 抓到这个数量就提前返(避免抓全页 100+ 太慢)
 * @param {number} timeoutMs    - 总超时(SPA hydration + scroll + scrape 总和)
 * @returns {Promise<{people:[],total:number,scraped_from:string,debug:object}>}
 */
function _scrapeLinkedinPeopleResults(targetCount, timeoutMs) {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    let attempts = 0;

    const tick = () => {
      attempts += 1;

      // ── 锚定主搜索结果容器(SDUI screen 标识,跨改版稳定) ─────────────
      const screen = document.querySelector('[data-sdui-screen*="SearchResultsPeople"]')
        || document.querySelector('[data-sdui-screen*="search.SearchResults"]')
        || document.querySelector('main')
        || document.body;

      // ── 找所有 listitem,过滤含 /in/ 链接的(纯人员卡,排除推广位/分页器)
      const allItems = [...screen.querySelectorAll('[role="listitem"]')];
      const cards = allItems.filter((it) => it.querySelector('a[href*="/in/"]'));

      // 老/中版 fallback(class 仍存在的旧账号)
      if (cards.length === 0) {
        const legacy = [
          'li.reusable-search__result-container',
          'li[data-chameleon-result-urn]',
          'div[data-chameleon-result-urn]',
        ];
        for (const sel of legacy) {
          screen.querySelectorAll(sel).forEach((el) => cards.push(el));
        }
      }

      if (cards.length === 0 && Date.now() < deadline) {
        return setTimeout(tick, 500);
      }

      const people = [];
      const seen = new Set();

      for (const card of cards) {
        // publicId: LinkedIn URL schema 永久不变量,/in/{publicId}
        const a = card.querySelector('a[href*="/in/"]');
        if (!a) continue;
        const href = a.getAttribute('href') || '';
        const m = href.match(/\/in\/([^/?#]+)/);
        const publicId = m ? decodeURIComponent(m[1]) : '';
        if (!publicId || seen.has(publicId)) continue;
        seen.add(publicId);

        // memberUrn: SDUI 不暴露,fallback 重建。
        //
        // ⚠ 已知技术债:`urn:li:member:${publicId}` 是**伪造的 URN scheme** —
        // LinkedIn 真正的 member URN 是 `urn:li:fsd_profile:ACoA{20+ rand chars}`,
        // 我们 fabricate 的形态没有 ACoA 前缀,任何 strict-parse URN 的下游
        // 工具(如未来的 linkedin_recruiter_*)会拒。当前 connect/send_message/
        // get_profile 都通过 chain.meta.publicId 走,所以"暂时"无害。
        // 加严格 URN 解析的下游工具时,把这里改成 null + 让消费方走 publicId 兜底。
        const memberUrn = card.getAttribute('data-chameleon-result-urn')
          || a.getAttribute('data-test-app-aware-link-urn')
          || `urn:li:member:${publicId}`;

        // ── 姓名:三层 fallback ─────────────────────────────────────
        // 1. 头像 svg[aria-label="姓名"](LinkedIn 给无头像账号的占位 svg 总有 aria-label)
        // 2. 卡顶部第一个 aria-label(头像 img)
        // 3. 第一个非空 <p>/<span> 文本(裁掉"• 2 度"等装饰后缀)
        let name = '';
        const avatarSvg = card.querySelector('svg[aria-label]');
        if (avatarSvg) name = (avatarSvg.getAttribute('aria-label') || '').trim();
        if (!name) {
          const labeledImg = card.querySelector('img[alt]');
          if (labeledImg) name = (labeledImg.getAttribute('alt') || '').trim();
        }
        if (!name) {
          // 兜底:取 a 的第一行可见文本
          const txt = (a.textContent || '').trim().split(/\n|·|•/)[0].trim();
          if (txt && !txt.toLowerCase().includes('view') && !txt.includes('加为好友')) name = txt;
        }

        // ── 卡内文本块按 DOM 顺序抽:[headline, location, mutuals]──────
        // SDUI 把不同字段都用 <p> 渲染,顺序稳定:
        //   [0] = 姓名(已经从 aria-label 拿了)
        //   [1] = headline(职位)
        //   [2] = 地点
        //   [3] = "目前就职:..." / 简介
        //   [N] = 共同好友
        const textBlocks = [];
        const ps = card.querySelectorAll('p');
        for (const p of ps) {
          const t = (p.textContent || '').trim().replace(/\s+/g, ' ');
          if (t && t.length < 200) textBlocks.push(t);
        }
        // 兜底:若 <p> 不够,扫描卡内所有 直接文本节点容器(<div>/<span>)
        if (textBlocks.length < 2) {
          const divs = card.querySelectorAll('div, span');
          for (const d of divs) {
            // 只取 leaf-ish:子节点不含 div(否则就是 wrapper)
            if (d.querySelector('div, p, a')) continue;
            const t = (d.textContent || '').trim().replace(/\s+/g, ' ');
            if (t && t.length < 200 && !textBlocks.includes(t)) textBlocks.push(t);
          }
        }

        // 把姓名 / 度数 / "加为好友" 这种纯按钮文字过滤掉
        const cleanedBlocks = textBlocks.filter((t) => {
          if (!t) return false;
          if (t === name) return false;
          if (/^[12]\s*度\s*\+?$/.test(t)) return false;          // "1 度", "2 度", "3 度+"
          if (/^(1st|2nd|3rd\+?)$/i.test(t)) return false;
          if (t.includes('加为好友') || t.toLowerCase() === 'connect') return false;
          if (t.includes('发消息') || t.toLowerCase() === 'message') return false;
          if (t === '关注' || t.toLowerCase() === 'follow') return false;
          return true;
        });

        const headline = cleanedBlocks[0] || '';
        const location = cleanedBlocks[1] || '';
        // mutual / current job 进 metadata 但不直接进顶层字段
        const extra = cleanedBlocks.slice(2).join(' | ');

        // degree:"2 度" 这种从原始 textBlocks 里挑回来
        let degree = '';
        for (const t of textBlocks) {
          const dm = t.match(/^([12])\s*度(\s*\+)?$|^(1st|2nd|3rd\+?)$/i);
          if (dm) { degree = t.trim(); break; }
        }

        if (!name) continue; // 没姓名的卡跳过

        people.push({
          memberUrn,
          publicId,
          name,
          headline,
          location,
          degree,
          extra,
          trackingId: '',
        });
        if (people.length >= targetCount) break;
      }

      // 0 命中 → 继续轮询(SPA 可能还在 lazy-loading)
      if (people.length === 0 && Date.now() < deadline) {
        return setTimeout(tick, 600);
      }

      // 0 命中诊断(同 v1.6.22):便于下次出问题时定位
      let diagnostic = null;
      if (people.length === 0) {
        try {
          const allInLinks = document.querySelectorAll('a[href*="/in/"]').length;
          const listItemCount = document.querySelectorAll('[role="listitem"]').length;
          const sampleAnchorParents = [];
          const anchors = [...document.querySelectorAll('a[href*="/in/"]')].slice(0, 3);
          for (const a of anchors) {
            let cur = a;
            const chain = [];
            for (let i = 0; i < 5 && cur; i++) {
              const cls = (cur.className && typeof cur.className === 'string')
                ? cur.className.split(/\s+/).slice(0, 2).join('.')
                : '';
              const role = cur.getAttribute && cur.getAttribute('role');
              chain.push(`${cur.tagName}${cls ? '.' + cls : ''}${role ? `[role=${role}]` : ''}`);
              cur = cur.parentElement;
            }
            sampleAnchorParents.push(chain.join(' > '));
          }
          diagnostic = {
            inProfileLinks: allInLinks,
            listItemCount,
            sampleAnchorAncestry: sampleAnchorParents,
            title: document.title,
          };
        } catch (e) {
          diagnostic = { diagError: String(e) };
        }
      }

      // 成功时不带 debug 字段,避免 LLM 把 debug 信息当成"结果不可信"信号导致
      // 它放弃下一步 chain handoff(v1.6.23 实测过这个症状)。仅在 0 命中时
      // 把诊断信息塞进去帮排查。
      resolve(people.length > 0
        ? { people, total: people.length, scraped_from: 'dom' }
        : {
          people,
          total: 0,
          scraped_from: 'dom',
          debug: {
            attempts,
            cardsFound: cards.length,
            urlAtScrape: window.location.href,
            ...(diagnostic ? { diagnostic } : {}),
          },
        });
    };

    tick();
  });
}
