# smart-job · 三大核心能力 / Three Core Capabilities

> 多平台(BOSS直聘 / LinkedIn / Indeed)求职—招聘自动化系统。
> A multi-platform (BOSS Zhipin / LinkedIn / Indeed) job-seeking & recruiting automation system.

**一句话 / In one line:** Agent 懂你要什么 → 调度层把任务变成可控流水线 → 扩展在你自己的浏览器里真实落地。
The agent understands your intent → the scheduling layer turns it into a controllable pipeline → the extension executes it for real, inside your own browser.

> 本文据当前代码核实编写,只列真实实现的能力。
> Written and verified against the current codebase — only shipped capabilities are listed.

---

## 中文

### 🧩 浏览器扩展 —— 在你自己的浏览器里,真实地干活

**定位:不伪造请求、不靠脆弱页面脚本、平台改接口也不用重发版。**

- **真实登录态执行** —— 全系统唯一接触招聘平台的组件。所有请求都在用户自己的浏览器、用自己的 Cookie 和真实指纹发出,与手动操作无异;后端从不在服务器侧伪造平台流量。
- **四层降级阶梯** —— ① 抓包捕获平台真实 API → ② 把 API 固化成可复用命令(之后零成本复用)→ ③ 没有对应 API 才退到 DOM 操作 → ④ DOM 也读不懂才上图像识别。可靠性优先、成本递增,绝大多数动作走最快最稳的 API 路径(①②)。
- **抓包逆向,对页面透明** —— 基于 Chrome DevTools Protocol 旁路捕获平台真实 API 流量,不注入脚本、不改页面行为,把摸清的接口沉淀成可复用命令。
- **令牌链** —— 平台 API 那串"列表→详情→会话→发消息"的有时效安全令牌,用有向依赖图建模:缺失/过期自动回补,无需人工关心调用顺序。
- **隐藏 Worker Tab + 拟人节奏** —— 每个平台一个后台工作标签页,真实 `fetch` + 按操作类型数秒级随机抖动限速,模拟人类节奏,降低风控触发。
- **动态命令 · 云端热更** —— 平台改接口时,后端把新命令以安全 JSON 模板经 WebSocket 推下即时生效,不重发扩展;模板只认占位符、不收原始 JS,杜绝代码注入。
- **自动填表** —— 把简历智能填进 Workday / Greenhouse / Lever 等任意 ATS 申请表:跨 iframe 探测字段、LLM 匹配(后端不可用时本地关键词兜底)、上报前自动脱敏 PII。
- **求职 / 招聘双模** —— 同一套代码、同一个扩展,既服务找工作的求职者,也服务找人的招聘者。

### ⚙️ 调度 —— 把不可靠的平台操作,变成可控、可观测、可扩容的流水线

**定位:资源池化 + 分布式编排 + 拟人限速 + 全链路审计。**

- **浏览器槽位池** —— 带状态机(空闲/忙/清理)的槽位管理,按平台限容、FIFO 排队、超时自动回收;槽位释放时强制登出清态,多用户之间数据零串扰。
- **会话隧道** —— 扩展经单条 WebSocket 接入,按"(用户, 角色)"维度管理会话、踢掉重复登录;请求/响应用 `req_id` 精确配对;求职端与招聘端两个扩展可并存。
- **三模式代理池** —— 轮询 / 随机 / 粘性(同浏览器复用同代理)三种分配策略可选。
- **限速 · 配额 · 执行守卫** —— 每会话按操作类型(搜索/详情/打招呼)1~8 秒随机抖动限速;每日配额硬约束(如 100 次投递 / 20 次触达,北京时间零点重置);敏感操作前置守卫拦截。
- **任务编排引擎** —— 长任务拆成"模板 → 平台专属步骤流水线 → 逐条目执行",支持批量打招呼、批量简历筛选;进度经 SSE 实时回报,运维可后台干预。
- **分布式协调** —— Redis 撑起跨实例:全局并发轮次计数(默认 30)、任务租约(60s,防多实例抢跑)、跨实例取消/恢复信号;Redis 不可用自动降级单实例,服务不中断。
- **风控信号驱动** —— 平台摩擦(验证码/限流/登录失效/配额满)统一抽象成信号,按策略自动处理:自动重试 / 暂停转人工 / 跳过当前条目 / 终止任务。
- **全链路审计** —— 每条命令、每次槽位分配、每个任务进度都落库,管理后台经实时 WebSocket 看板可观测。

### 🤖 Agent —— 一句话下达意图,它自己选模式、调工具、跑批量

**定位:对话式驱动 + 模式感知 + 流式体验 + 工具化执行。**

- **对话式自动化** —— 用自然语言说需求("搜北京的项目经理职位""给这几个打招呼"),Agent 自动调工具完成搜索、评估、沟通、投递。
- **推理主循环 + 流式输出** —— "推理→调工具→取结果→再推理"多轮循环,全程以 SSE 推流:文本增量、思考过程、工具调用、职位卡片、操作按钮实时可见。
- **六大模式系统** —— 搜索 / 评估 / 投递 / 面试准备 / 对比 / 招聘,按关键词与工具使用倾向自动判定意图,模式可在一轮对话中途升级。
- **约 210 个 MCP 工具** —— 覆盖三大平台的求职端与招聘端,经 FastMCP 标准协议暴露;Claude Code 等任意 MCP 客户端可直连(本仓库已带 `.mcp.json`)。
- **扩展思考** —— 开启 extended thinking,复杂决策的推理过程可流式展开查看。
- **模型自动降级** —— 主模型过载时自动切到备用模型,对话不中断。
- **简历与个性化,完全本地** —— 简历 PDF/DOCX 在服务内解析、缓存、版本化,不调任何外部服务;求职偏好与招聘画像独立持久化。
- **跨 worker 会话** —— 会话历史经 Redis 跨实例共享、空闲自动回收,水平扩容无缝。

---

## English

### 🧩 Browser Extension — real work, inside your own browser

**No forged requests, no brittle page scripts, no re-release when a platform changes its API.**

- **Real logged-in execution** — the only component that touches job platforms. Every request goes out from the user's own browser, with their own cookies and real fingerprint — indistinguishable from manual use. The backend never forges platform traffic server-side.
- **Four-tier fallback ladder** — ① capture real platform API traffic → ② freeze the API into a reusable command (zero-cost to reuse thereafter) → ③ fall back to DOM operations only when there is no API → ④ resort to vision only when the DOM is unreadable. Reliability first — the vast majority of actions take the fast, stable API path (① ②).
- **Transparent capture** — uses the Chrome DevTools Protocol to passively capture real platform API traffic; injects no script, changes nothing on the page, and distills the discovered APIs into reusable commands.
- **Token chain** — the chain of time-limited security tokens platform APIs require (list → detail → session → message) is modeled as a directed dependency graph; missing or expired tokens are backfilled automatically.
- **Hidden Worker Tab + human-like pacing** — a background Worker Tab per platform issues real `fetch` calls with randomized-jitter rate limiting by operation type, mimicking human rhythm to reduce anti-abuse triggers.
- **Hot-updatable dynamic commands** — when a platform changes its API, the backend pushes new commands as safe JSON templates over WebSocket, live — no extension re-release. Templates take only placeholders, never raw JS — no code injection.
- **Form auto-fill** — fills resume data into any ATS application form (Workday, Greenhouse, Lever, …): detects fields across iframes, matches via LLM (local keyword fallback), and scrubs PII before upload.
- **Dual seeker / recruiter mode** — one codebase, one extension, serving both job seekers and recruiters.

### ⚙️ Scheduling — turning unreliable platform actions into a controllable, observable, scalable pipeline

**Resource pooling + distributed orchestration + human-like pacing + a full audit trail.**

- **Browser slot pool** — slots managed by a state machine (idle / busy / cleaning), with per-platform capacity, FIFO queuing and idle auto-eviction; on release a slot is force-logged-out and wiped — zero data bleed between users.
- **Session tunnel** — extensions connect over a single WebSocket; sessions are keyed by (user, role), duplicate logins are kicked, and requests/responses are correlated by `req_id`; seeker and recruiter extensions can coexist.
- **Three-mode proxy pool** — round-robin / random / sticky (same proxy reused per browser).
- **Rate limiting · quota · execution guard** — per-session jitter rate limiting (1–8 s) by operation type; hard daily quotas (e.g. 100 applications / 20 contacts, reset at local midnight); a guard intercepts sensitive operations up front.
- **Task orchestration engine** — long tasks become "template → platform-specific step pipeline → per-item execution," supporting bulk outreach and bulk resume screening; progress streams live over SSE and operators can intervene from the console.
- **Distributed coordination** — Redis powers cross-instance operation: a global concurrent-turn counter (default 30), task leases (60 s, preventing two instances running the same task), and cross-instance cancel/resume signals; without Redis it degrades to single-instance automatically.
- **Risk-signal driven** — platform friction (captcha / rate limit / logout / quota) is unified into signals handled by strategy: auto-retry / pause-for-human / skip-item / abort.
- **End-to-end audit** — every command, slot assignment and task step is persisted; the admin console observes it all over a live WebSocket dashboard.

### 🤖 Agent — state your intent in one sentence; it picks the mode, calls the tools, runs the batch

**Conversation-driven + mode-aware + streamed + tool-executed.**

- **Conversational automation** — state your need in natural language ("search project-manager jobs in Beijing," "say hi to these"); the agent calls tools to search, evaluate, message and apply.
- **Reasoning loop + streaming** — a multi-turn "reason → call tool → take result → reason" loop; the whole process streams over SSE — text deltas, thinking, tool calls, job cards and action buttons, all in real time.
- **Six-mode system** — search / evaluate / apply / interview / compare / recruiter; intent is detected from keywords and tool-usage affinity, and the mode can upgrade mid-conversation.
- **~210 MCP tools** — covering the seeker and recruiter sides of all three platforms, exposed over the standard FastMCP protocol; any MCP client (e.g. Claude Code) can connect directly — this repo ships a ready `.mcp.json`.
- **Extended thinking** — with extended thinking on, the reasoning behind complex decisions can be streamed and expanded.
- **Automatic model fallback** — if the primary model is overloaded, it switches to a backup model without breaking the conversation.
- **Fully local resume & personalization** — resume PDFs/DOCX are parsed, cached and versioned inside the service, calling no external service; job preferences and recruiter profiles are persisted separately.
- **Cross-worker sessions** — session history is shared across instances via Redis and idle-swept; horizontal scaling is seamless.
