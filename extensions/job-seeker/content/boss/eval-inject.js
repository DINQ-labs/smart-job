/**
 * zhipin_eval_inject.js — world: ISOLATED, run_at: document_idle
 * 在 zhipin.com/web/geek/jobs* 列表页每条职位旁注入「评估」按钮。
 * 点击 → 把 snapshot 发给 background → 后端 /jobs/evaluate-by-id → 按钮变分数徽章。
 *
 * MVP:从 li DOM 文本读 title/company/salary/tags;encryptJobId 从 a[href*="/job_detail/"]
 * 拿。Boss 改版会破坏 selector,出问题打开 devtools 看 li 结构,改 SELECTORS。
 */
(function () {
  'use strict';

  // 只在求职者职位列表页跑(/web/geek/jobs*)。其他页面(消息列表/详情)不注入。
  if (!/^\/web\/geek\/(jobs|chat|job-recommend)/.test(location.pathname)) return;

  const INJECTED_FLAG = 'data-dq-eval-injected';
  const BTN_CLASS    = 'dq-eval-btn';
  const BADGE_CLASS  = 'dq-eval-badge';

  // ── 样式注入(一次) ──────────────────────────────────────────────────────
  function injectStyle() {
    if (document.getElementById('dq-eval-style')) return;
    const s = document.createElement('style');
    s.id = 'dq-eval-style';
    s.textContent = `
      .${BTN_CLASS} {
        display:inline-flex; align-items:center; gap:4px;
        margin-left:8px; padding:2px 10px;
        background:#0fcfa6; color:#fff; border:none; border-radius:14px;
        font-size:12px; line-height:1.6; cursor:pointer;
        box-shadow:0 1px 2px rgba(15,207,166,0.3);
        transition:background .2s, transform .1s;
      }
      .${BTN_CLASS}:hover  { background:#0bbf95; }
      .${BTN_CLASS}:active { transform:scale(0.96); }
      .${BTN_CLASS}[disabled] { background:#aaa; cursor:wait; }
      .${BTN_CLASS}.dq-need-resume { background:#f59e0b; }
      .${BTN_CLASS}.dq-error       { background:#ef4444; }
      .${BADGE_CLASS} {
        display:inline-flex; align-items:center;
        margin-left:6px; padding:1px 8px;
        border-radius:10px; font-size:12px; font-weight:600;
        background:#eef9f5; color:#0fcfa6; border:1px solid #0fcfa6;
      }
      .${BADGE_CLASS}.dq-score-high { background:#0fcfa6; color:#fff; }
      .${BADGE_CLASS}.dq-score-mid  { background:#fff8e6; color:#d97706; border-color:#f59e0b; }
      .${BADGE_CLASS}.dq-score-low  { background:#fef2f2; color:#dc2626; border-color:#ef4444; }
    `;
    document.head.appendChild(s);
  }

  // ── 提取 li 数据 ────────────────────────────────────────────────────────
  function extractJob(li) {
    // encryptJobId / securityId 从 detail 链接拿(Boss 列表项每条都有 a[href*="job_detail"])
    const link = li.querySelector('a[href*="/job_detail/"]');
    if (!link) return null;
    const href = link.getAttribute('href') || '';
    const m = href.match(/\/job_detail\/([^.?]+)\.html/);
    if (!m) return null;
    const encryptJobId = m[1];
    const url = new URL(href, location.origin);
    const securityId = url.searchParams.get('securityId') || '';
    const lid        = url.searchParams.get('lid') || '';

    // 各字段都用多 selector 兜底(Boss 版本差异)
    const pick = (...sels) => {
      for (const sel of sels) {
        const el = li.querySelector(sel);
        if (el && el.textContent.trim()) return el.textContent.trim();
      }
      return '';
    };
    const title    = pick('.job-name', '.job-title', '[class*="job-name"]', '[class*="job-title"]');
    const salary   = pick('.salary', '[class*="salary"]');
    const company  = pick('.company-name', '.boss-info-attr', '[class*="company-name"]', '[class*="company-text"] a');
    // 标签(经验 / 学历 / 技能) — Boss 一般在 .tag-list 或 .info-desc 里
    const tagEls = li.querySelectorAll('.tag-list li, .info-desc, [class*="info-public"], [class*="job-info"] [class*="tag"]');
    const tags = [];
    tagEls.forEach(el => {
      const t = el.textContent.trim();
      if (t && t.length < 30 && !tags.includes(t)) tags.push(t);
    });

    return { encryptJobId, securityId, lid, title, company, salary, tags };
  }

  // ── 注入按钮 ────────────────────────────────────────────────────────────
  function injectButton(li) {
    if (li.getAttribute(INJECTED_FLAG)) return;
    const job = extractJob(li);
    if (!job) return;
    li.setAttribute(INJECTED_FLAG, '1');

    const btn = document.createElement('button');
    btn.className = BTN_CLASS;
    btn.type = 'button';
    btn.textContent = (typeof t === 'function' ? t('content.evalBtn') : '评估');
    btn.title = (typeof t === 'function' ? t('content.evalBtnTitle') : 'DingQ:对比简历给出匹配度分数');

    const badge = document.createElement('span');
    badge.className = BADGE_CLASS;
    badge.style.display = 'none';

    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      runEvaluate(btn, badge, job);
    });

    // 放在标题旁边(优先);失败放 li 末尾
    const anchor = li.querySelector('.job-name, .job-title, [class*="job-title-text"]')
                 || li.querySelector('a[href*="/job_detail/"]')
                 || li;
    if (anchor && anchor !== li) {
      anchor.insertAdjacentElement('afterend', badge);
      anchor.insertAdjacentElement('afterend', btn);
    } else {
      li.appendChild(btn);
      li.appendChild(badge);
    }
  }

  // ── 调 background 评估 ─────────────────────────────────────────────────
  function runEvaluate(btn, badge, job) {
    btn.disabled = true;
    btn.textContent = (typeof t === 'function' ? t('content.evalLoading') : '评估中…');
    btn.classList.remove('dq-need-resume', 'dq-error');
    console.log('[DQ] eval start:', job.encryptJobId, job.title);

    // 兜底超时：sendMessage callback 因任何原因不触发(SW 冷启动竞态 / handler 丢失 /
    // background fetch hang),45s 自动失败,避免按钮永远停在「评估中…」。
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      btn.disabled = false;
      btn.classList.add('dq-error');
      btn.textContent = (typeof t === 'function' ? t('content.evalRetry') : '重试');
      btn.title = (typeof t === 'function' ? t('content.evalTimeoutTitle') : '请求超时(45s),请确保扩展已重载并重新打开本页');
      console.warn('[DQ] eval timeout for', job.encryptJobId);
    }, 45000);

    chrome.runtime.sendMessage(
      {
        type: 'DQ_EVALUATE_JOB',
        platform: 'boss',
        encrypt_job_id: job.encryptJobId,
        security_id: job.securityId,
        job_snapshot: {
          encryptJobId: job.encryptJobId,
          title:   job.title,
          company: job.company,
          salary:  job.salary,
          tags:    job.tags,
        },
      },
      (resp) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        console.log('[DQ] eval resp:', resp, 'lastError:', chrome.runtime.lastError);
        btn.disabled = false;
        if (chrome.runtime.lastError) {
          btn.classList.add('dq-error');
          btn.textContent = (typeof t === 'function' ? t('content.evalRetry') : '重试');
          btn.title = chrome.runtime.lastError.message || (typeof t === 'function' ? t('content.evalCommErr') : '通信失败');
          return;
        }
        if (!resp || !resp.ok) {
          btn.classList.add('dq-error');
          btn.textContent = (typeof t === 'function' ? t('content.evalRetry') : '重试');
          btn.title = (resp && resp.error) || (typeof t === 'function' ? t('content.evalBackendErr') : '后端错误');
          return;
        }
        if (resp.has_resume === false) {
          btn.classList.add('dq-need-resume');
          btn.textContent = (typeof t === 'function' ? t('content.evalNeedResume') : '请先上传简历');
          btn.title = (typeof t === 'function' ? t('content.evalNeedResumeTitle') : '点击打开 DingQ 助手上传简历');
          btn.onclick = (e) => {
            e.preventDefault(); e.stopPropagation();
            chrome.runtime.sendMessage({ type: 'DQ_OPEN_PROFILE' });
          };
          return;
        }
        const ev = resp.evaluated || {};
        const score = Number(ev.score) || 0;
        btn.textContent = (typeof t === 'function' ? t('content.evalViewDetail') : '查看详情');
        btn.title = (ev.level || '') + (ev.reasons && ev.reasons.length
                                          ? '\n✓ ' + ev.reasons.slice(0, 3).join('\n✓ ')
                                          : '');
        badge.style.display = 'inline-flex';
        badge.textContent = (typeof t === 'function' ? t('content.matchScore', {score}) : score + '分');
        badge.classList.remove('dq-score-high', 'dq-score-mid', 'dq-score-low');
        badge.classList.add(score >= 70 ? 'dq-score-high'
                            : score >= 50 ? 'dq-score-mid' : 'dq-score-low');
        // 点 "查看详情" → 打开 sidepanel 看富卡片
        btn.onclick = (e) => {
          e.preventDefault(); e.stopPropagation();
          chrome.runtime.sendMessage({
            type: 'DQ_SHOW_EVAL_DETAIL',
            evaluated: ev,
            job_snapshot: {
              encryptJobId: job.encryptJobId, title: job.title,
              company: job.company, salary: job.salary, tags: job.tags,
            },
          });
        };
      }
    );
  }

  // ── 扫描 + MutationObserver ───────────────────────────────────────────
  function scan(root) {
    const lis = (root || document).querySelectorAll(
      'li[ka^="job_card_"], li.job-card-wrapper, li.job-card-box, ul.job-list-box > li, .job-list-container li'
    );
    lis.forEach(injectButton);
  }

  function start() {
    injectStyle();
    scan(document);
    const obs = new MutationObserver((mutations) => {
      for (const m of mutations) {
        m.addedNodes.forEach(n => {
          if (n.nodeType === 1) scan(n);
        });
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
