# 平台交互工作流程

> 本文说明 smart-job 如何与招聘平台（BOSS直聘 / LinkedIn / Indeed）交互、自动化操作。
> 核心是一套**分层降级策略**：能用 API 就用 API，不行才退到 DOM，再不行才用图像。
> 系统整体架构见 [ARCHITECTURE.md](ARCHITECTURE.md)；扩展与后端组件细节见
> [EXTENSION.md](EXTENSION.md)、[BACKEND.md](BACKEND.md)。

## 1. 总体策略：四层降级阶梯

招聘平台没有公开 API，页面结构会变、还有风控。系统不押注单一手段，而是按
**「可靠性优先、成本递增」**排了四层，优先走上层，缺失或失败才降级：

```
        可靠性 / 速度       手段                              成本
  ┌──────────────────────────────────────────────────────────────┐
  │ ① 抓包             捕获平台真实 API 流量                      低 │  一次性
  │ ② 反编译 API→命令   把抓到的 API 固化成可复用命令 / 工具       低 │  之后零成本复用
  ├──────────────────────────────────────────────────────────────┤
  │ ③ DOM 操作         快照 DOM → 选可点击元素 → 点击             中 │  每次重新定位
  │ ④ 图像识别 + 点击   截图 → 识别 → 按坐标定位                   高 │  每次调视觉模型
  └──────────────────────────────────────────────────────────────┘
        优先级：①② ＞ ③ ＞ ④
```

- **①②（API 路径）**：把一个平台动作「做成」一条 API 命令是一次性投入，之后每次
  调用都是直接 fetch，**最快、最稳、最省 token**。系统绝大多数动作走这条路。
- **③（DOM 路径）**：没有对应 API、或动作本质是纯 UI 操作（点按钮、切 tab）时，
  退到 DOM：快照页面 → 定位元素 → 点击。
- **④（图像路径）**：DOM 也不可靠时（canvas 渲染、强混淆、深 shadow DOM）的兜底。
  **目前仅用于表单字段识别（见 §5），通用的「看屏幕点坐标」尚在规划中。**

> 一句话：**抓一次 API，用一辈子；抓不到才看 DOM；DOM 看不懂才看图。**

## 2. 层级① — API 抓包

让扩展在用户正常浏览招聘网站时，**旁路捕获**平台前端发出的 API 请求与响应。

```
用户浏览 zhipin.com / linkedin.com …
   │
   ▼  MAIN world content script hook window.fetch
拦截器捕获请求/响应（Boss: /wapi/   LinkedIn: /voyager/api/、/graphql）
   │
   ▼  postMessage（MAIN → ISOLATED）→ background
扩展上报 → agent-gateway 的 capture 端点（POST /api/capture）
   │
   ▼  持久化
api_capture_sessions（会话）+ api_capture_requests（逐条请求/响应）
   │
   ▼  POST /api/sessions/{id}/analyze（可选）
调 LLM 分析这批请求，产出接口说明
```

- **拦截点**：`extensions/job-seeker/content/{boss,linkedin,indeed}/interceptor_main.js`
  在 MAIN world hook `window.fetch`；`content/shared/interceptor.js` 做 MAIN↔ISOLATED 中转。
- **抓什么**：method、URL、请求头/体、响应状态/体，以及响应里的安全令牌
  （Boss 的 `securityId`、LinkedIn 的 `trackingId` 等）。
- **存到哪**：`api_capture_sessions`（名称、页面 URL、请求数、分析状态/结果）
  + `api_capture_requests`（逐条请求/响应明细）。
- **后端**：`packages/agent-gateway/capture_router.py`。
- **查看**：管理后台「API Capture」页可浏览会话、触发分析。

> 现状：Boss、LinkedIn 抓包完整；Indeed 同构。

## 3. 层级② — 反编译 API → 命令

把抓到、分析过的 API「固化」成系统可反复调用的**命令**。这一步是 API 路径的关键 ——
做一次，之后该平台动作就有了稳定、零发现成本的工具。

**命令的两种形态**

| 形态 | 存放 | 适用 |
|---|---|---|
| **动态命令** | YAML 配置，存 DB，运行时注册 | 新接口 / 易变接口；改配置即生效，**无需重新发布扩展** |
| **静态工具** | 编译进 api-gateway 代码 | 已稳定、高频的核心动作 |

**动态命令生命周期**

```
抓包分析结果 → 写成 YAML（packages/api-gateway/dynamic-commands/）
   │
   ▼  git push → validate 校验 → 合并 → 同步脚本上推 DB
DB 成为运行时真相源
   │
   ├─▼ api-gateway 启动：dynamic_mcp_registry 读 DB → 每条命令注册成 MCP 工具
   └─▼ WebSocket 广播：扩展收到最新命令注册表
   │
   ▼  长期稳定后
promote_to_static.py 把 YAML 命令晋升为静态代码
```

- **YAML 描述**：`path`、`description`、`mcp`（参数 schema）、`requires`/`produces`
  （令牌链依赖，见 [EXTENSION.md](EXTENSION.md)）、`requestBuilder`（URL/body 模板，
  支持 `{{body.field}}` 占位）、`metadata`（来源抓包 session 等）。
- **关键文件**：`packages/api-gateway/dynamic-commands/`（YAML 真相源）、
  `dynamic_mcp_registry.py`（注册成 MCP 工具）、`dynamic_command_state.py`、
  `commands.py`（执行）。

> 现状：Boss 动态命令 YAML 齐备；LinkedIn / Indeed 目前以静态工具为主，动态 YAML
> 尚未补齐。「抓包分析结果 → 自动生成 YAML」仍是人工环节，自动化在规划中。

## 4. 层级③ — DOM tree 操作

当某个动作**没有对应 API**（或本质就是点按钮、切 tab 这类纯 UI 操作）时，退到 DOM：
让扩展把页面结构回传，Agent 据此定位并点击。

**工具（MCP，各平台前缀 `boss_` / `linkedin_` / `indeed_`）**

| 工具 | 作用 |
|---|---|
| `*_get_dom_snapshot` | 回传 DOM 树（按深度 / 节点数截断），用于「看懂」页面 |
| `*_get_clickables` | 回传所有可点击元素：`idx` + `selector` + `text` + `rect` + 快照 ID |
| `*_click_by_idx` | 用 快照ID + idx 精确点击 |
| `*_click_by_text` | 按可见文本点击（多匹配用 nth 选第几个） |
| `*_wait_for` | 等某元素出现再继续 |
| `*_navigate_to` | 把工作标签页导航到指定 URL（host 白名单） |

**流程**

```
Agent 调 *_get_clickables → 扩展遍历 DOM → 返回 [{idx,selector,text,rect}] + snapshot_id
   │  （快照带短 TTL ~5s 的内存缓存，键 snap_<uuid>，不持久化以防拿到过期 DOM）
   ▼
Agent 选定要点的 idx → 调 *_click_by_idx(snapshot_id, idx)
   │
   ▼  内置兜底：
       selector 命中  → 点击（clicked_via: selector）
       selector 失效  → 用 text 再匹配点击（clicked_via: text_fallback）
       两者都失败     → 报错
```

- **关键文件**：扩展端 `extensions/job-seeker/lib/ext-core/commands/dom.js`；
  后端工具定义 `packages/api-gateway/mcp_tools_boss.py` 等，执行在 `commands.py`。

> 现状：三个平台均完整可用，含 selector → text 的点击兜底。

## 5. 层级④ — 图像识别 + 点击

DOM 也读不懂时（canvas 绘制、强混淆、深 shadow DOM）的最后兜底：截图 → 识别 → 点击。

**当前能力（已实现）**

- **截图**：扩展 `chrome.tabs.captureVisibleTab()` 抓当前可见区域，转 dataURL。
- **表单字段识别**：`packages/agent-gateway/autofill_router.py` 的 `/autofill/ocr`
  端点收截图，调**视觉模型**识别表单字段 —— 返回每个字段的 label、类型、
  **归一化 bbox（0~1）**、是否必填、选项；autofill 子系统据此定位并填写。
- **二维码截图**：登录场景把扫码二维码截图回传。

**尚未实现（规划中）**

- ❌ 通用的「截全图 → 视觉理解 → 按坐标点任意元素」—— 目前图像识别**只服务表单填写**，
  还不是 §1 阶梯里通用的点击兜底。
- ❌ 通用 OCR 文字识别、图像模板匹配。
- ❌ 验证码（CAPTCHA）自动识别 —— 命中时按风控策略暂停、转人工。

> 现状：**部分实现**。「截图 + 视觉模型」链路已通，但只用在 autofill 表单字段检测；
> 把它扩展成 DOM 失败后的通用点击兜底，是这一层的下一步。

## 6. 降级决策

对某个平台动作，如何选层：

```
该动作有没有 API 命令（静态工具 / 动态 YAML）?
   ├─ 有 ───────────────► 层级①② 直接调 API 命令          ← 默认、绝大多数动作
   └─ 没有
        └─ 能用 DOM 定位吗?
             ├─ 能 ──────► 层级③ get_clickables → click_by_idx
             │               └ selector 失效 → text 兜底
             └─ 不能 ────► 层级④ 截图 + 视觉识别
                             （目前仅表单场景成熟）
```

- 接入**新平台动作**的推荐路径：先用层级③把流程跑通，同时**抓包**（层级①）；
  待 API 摸清，固化成命令（层级②），后续即走最优的 API 路径。
- 风控信号（验证码、限流、登录失效）在任一层被识别后，沿调用链上抛，由任务引擎
  按 [job_common](../packages/job_common/) 的策略处理（重试 / 暂停 / 跳过 / 终止）。

## 7. 现状与规划

| 层级 | 状态 | 说明 |
|---|---|---|
| ① API 抓包 | ✅ 完整 | Boss / LinkedIn 完整，Indeed 同构 |
| ② 反编译 → 命令 | ✅ 框架完整 | Boss 动态 YAML 齐；LinkedIn / Indeed 以静态工具为主，动态 YAML 待补；抓包→YAML 自动化在规划中 |
| ③ DOM 操作 | ✅ 完整 | 三平台可用，含 selector → text 兜底 |
| ④ 图像识别 + 点击 | 🟡 部分 | 截图 + 表单字段 OCR 已通；通用视觉点击兜底、通用 OCR、验证码识别均规划中 |
