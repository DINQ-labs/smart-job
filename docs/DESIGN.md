# smart-job 设计规范 (DESIGN.md)

> 来源：`https://dinq-product.vercel.app/plugin-ui-v3.html` 的 inline CSS 与渲染稿，2026-05-14 抽取。
> 用途：作为浏览器扩展 (`job-seeker-ext`)、管理后台 (`job-api-admin`)、以及任何新增前端的设计单一来源 (Single Source of Truth)。
> 风格基线：**Tailwind Slate 中性 + 语义色**（Blue/Green/Amber/Red 主线 600 强调），无品牌色冲突，无圆形 logo 主义，"功能优先 + 状态化"。

---

## 0. 设计原则

从 v3 设计稿推断出的隐性原则：

1. **状态化优先于装饰** —— 颜色被严格保留给"状态语义"（成功/警告/异常/中性/品牌动作），不做无意义点缀。
2. **深色 Header + 浅色 Body** —— 强对比的分层；Header 用 `slate-900` 系（近黑），内容区用 `slate-50` 系（近白）。
3. **粗字号收口** —— 全站只用 600 / 700 / 800 三个字重，**没有 regular**——决定了视觉密度偏高、信息密度优先的气质。
4. **圆角分层** —— 微圆 (3-7px) 给标签/小按钮；中圆 (8-12px) 给卡片/输入框；大圆 (16-20px) 给手机外壳/Hero 卡。
5. **极简阴影** —— 全站仅 3 种 shadow，避免 elevation 噪声。
6. **气泡用非对称圆角** —— AI 气泡 `3px 12px 12px 12px`，用户气泡 `12px 3px 12px 12px`——尾巴在角上，不画 SVG。
7. **图标用 emoji + 单色 SVG 混搭** —— 顶部 Tab 用富色 emoji（💬 📋 ⭐ 👤），功能内用单色 line。
8. **批量 / 单次清晰对偶** —— 每个能批量执行的操作都同时有「单次」入口和「批量条」入口。

---

## 1. 色板 Color Tokens

### 1.1 中性色 (Slate, Tailwind 对齐)

| Token | Hex | 用途 |
|---|---|---|
| `slate-900` | `#0f172a` | 主深色背景（Header / Tab 栏 / 主按钮 / 价格卡） |
| `slate-800` | `#1e293b` | 深色面板次级背景、深色 hover |
| `slate-700` | `#334155` | 主深色文字、深色 chip 文字 |
| `slate-600` | `#475569` | 二级文字（深底上的副标题、浅底上的主体文字） |
| `slate-500` | `#64748b` | 三级文字、占位符 |
| `slate-400` | `#94a3b8` | 静默状态图标、辅助说明文字 |
| `slate-300` | `#cbd5e1` | 默认边框、分隔线 |
| `slate-200` | `#e2e8f0` | 弱边框、disabled 边框 |
| `slate-100` | `#f1f5f9` | 浅色背景区块（章节分隔条带） |
| `slate-50`  | `#f8fafc` | 卡片底、输入框 disabled 底 |
| `gray-50`  | `#fafafa` | 整体页面底色（章节间灰条） |
| `surface-2` | `#e8ecf0` | slate-100 与 slate-200 之间的自定义浅灰；用于精细分隔 |
| `white`     | `#ffffff` | 卡片底、内容承载 |

### 1.2 语义色（统一 50 / 200 / 600 三档）

| 语义 | Bg `-50` | Border `-200` | Solid `-600` | 何时用 |
|---|---|---|---|---|
| **Primary** (Blue) | `#eff6ff` | `#bfdbfe` | `#2563eb` | 主行动按钮、链接、激活态、信息提示 |
| **Success** (Green) | `#f0fdf4` | `#bbf7d0` | `#16a34a` | 完成、高匹配度、Pro 解锁、积极提示 |
| **Warning** (Amber) | `#fffbeb` | `#fde68a` | `#d97706` | 进行中、中匹配、配额预警、需确认 |
| **Danger** (Red)   | `#fef2f2` | `#fecaca` | `#dc2626` | 异常、需验证、超限、风控阻断 |

> 命名等于 Tailwind 同位色，可直接 `bg-blue-50 border-blue-200 text-blue-600` 套用。

### 1.3 透明叠加（深色面板上专用）

| Token | Value | 用途 |
|---|---|---|
| `dark-line` | `rgba(255,255,255,0.06)` | 深色面板上的微分割线（top highlight） |
| `dark-line-2` | `rgba(255,255,255,0.08)` | 深色面板上的标准 border |
| `dark-line-3` | `rgba(255,255,255,0.10)` | 深色面板上的中等 border |
| `dark-line-4` | `rgba(255,255,255,0.15)` | 深色面板上 hover/激活 border |
| `shadow-soft` | `rgba(15,23,42,0.12)` | 大阴影颜色 |
| `shadow-card` | `rgba(15,23,42,0.20)` | 中阴影颜色 |

### 1.4 不在本系统中的色

明确**禁止引入**：
- 任何 purple / pink / orange / teal / cyan / lime
- 任何饱和度 > 80% 的强色（除红色危险态）
- 渐变色（设计稿全程无线性/径向渐变）

---

## 2. 字体与排版 Typography

### 2.1 字体栈

```css
font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
```

系统原生栈，**不引入 web font**，保证扩展启动 0 网络字体请求。

### 2.2 字号 Scale

| Token | px | 用途 |
|---|---|---|
| `text-micro` | 10 | 极小角标（badge、匹配度数字内的"分"） |
| `text-tiny`  | 11 | 标签副文、credit 计数 |
| `text-xs`    | 12 | 辅助说明、列表副标、chip 文字 |
| `text-sm`    | 13 | 输入框默认、按钮默认、卡片副文 |
| `text-base`  | 14 | 正文段落（设计稿事实默认） |
| `text-md`    | 15 | 段落强调、列表主标 |
| `text-lg`    | 16 | 卡片标题 |
| `text-lg+`   | 17 | 模块标题 |
| `text-xl`    | 18 | 卡片大标题 |
| `text-2xl`   | 20 | 章节内小标 |
| `text-3xl`   | 22 | 章节标题 |
| `text-4xl`   | 26 | 页面 H2 |
| `text-5xl`   | 32 | 页面 H1 / 章节大标 |
| `text-display` | 48 | 首页/营销超大标 |

### 2.3 字重

```
600 = 中粗（默认正文强调）
700 = 粗（卡片标题）
800 = 加粗（H1 / 章节大标 / 关键数字）
```

**没有 regular (400 / 500)**——是这套设计的辨识度之一。新增组件时直接默认 600。

### 2.4 行高

```
1.3  超紧凑标题（H1/H2）
1.4  卡片标题
1.5  正文（默认）
1.6  长段落
1.7  对话气泡（提高可读性）
```

---

## 3. 间距 Spacing

8px base，但**保留 2/4/6/10/14 等细粒度补位**（实测频次最高的反而是 10/8/12/14/6，比纯 4 倍数更密）。

| Token | px | 主要场景 |
|---|---|---|
| `space-px` | 1 | hairline border |
| `space-0.5` | 2 | 图标贴文 |
| `space-1` | 4 | 文字与图标间距 |
| `space-1.5` | 6 | chip 内左右 padding |
| `space-2` | 8 | 卡片内元素间距 |
| `space-2.5` | 10 | 卡片 padding（最常用，36 次） |
| `space-3` | 12 | 卡片 padding 大、垂直列表 gap |
| `space-3.5` | 14 | 卡片外边距 |
| `space-4` | 16 | 卡片 padding XL、区段 gap |
| `space-5` | 20 | 大卡片 padding |
| `space-6` | 24 | 章节内间距 |
| `space-8` | 32 | 章节标题与内容 gap |
| `space-10` | 40 | 大区段分隔 |
| `space-12` | 48 | 顶部 hero 间距 |
| `space-16` | 64 | 章节间最大间距 |

---

## 4. 圆角 Radius

| Token | px | 用途 |
|---|---|---|
| `rounded-xs` | 3 | 标签、极小 chip、score 角标 |
| `rounded-sm` | 4 | 小按钮、小输入框 |
| `rounded` | 6 | 标准按钮 |
| `rounded-md` | 8 | 列表项、二级卡片 |
| `rounded-lg` | 10 | 输入框、Tab 项 |
| `rounded-xl` | 12 | **主卡片默认** |
| `rounded-2xl` | 16 | Hero 卡片、Modal |
| `rounded-3xl` | 20 | 手机外壳、超大容器 |
| `rounded-full` | 50% | 头像、圆点、状态指示器 |

**对话气泡专用**（非对称）：

```css
/* AI 气泡（左侧） */
border-radius: 3px 12px 12px 12px;

/* 用户气泡（右侧） */
border-radius: 12px 3px 12px 12px;
```

---

## 5. 阴影 Elevation

**全站仅 3 级**——多用 border 不用 shadow 是这套设计的纪律。

| Token | Value | 用途 |
|---|---|---|
| `shadow-line` | `0 1px 0 rgba(255,255,255,0.06)` | 深色面板上层之间的高光分隔（不是真阴影） |
| `shadow-card` | `0 8px 32px rgba(15,23,42,0.12)` | 卡片/卡片悬浮态 |
| `shadow-modal` | `0 4px 16px rgba(15,23,42,0.2)` | 模态/浮动 panel |

---

## 6. 组件库 Components

按 CSS 类前缀分组（来自源稿 216 个 class）。新增组件请沿用前缀命名。

### 6.1 框架 / 容器

| 前缀 | 含义 | 关键类 |
|---|---|---|
| `topbar-*` | 顶栏导航（文档站用） | `.topbar`, `.topbar-title`, `.topbar-links`, `.topbar-link` |
| `chapter-*` | 章节标题块 | `.chapter`, `.chapter-label`, `.chapter-title` |
| `screen-*` | 屏幕分组容器 | `.screen-group`, `.screen-label`, `.screen-sub` |
| `ph-*` | 手机外壳（mockup chrome） | `.ph`, `.ph-logo`, `.ph-badge`, `.ph-close`, `.ph-left` |

### 6.2 Onboarding 引导

`ob-*`（35 个类，最大模块）—— 用于身份选择、平台选择、登录、平台验证、简历上传、AI 分析进度。
关键类：`.ob-cta`, `.ob-cta-btn`, `.ob-back`, `.ob-desc`, `.ob-divider`, `.ob-footer`。

### 6.3 欢迎页轮播

`wc-*`（20 个类）—— 4 张轮播卡。
关键类：`.wc-dots`, `.wc-dot`, `.wc-counter`, `.wc-arrow`, `.wc-cta`, `.wc-footer`。

### 6.4 AI 对话

`chat-*`（12 个类）—— 主交互区域。
| 类 | 含义 |
|---|---|
| `.chat-bubble` | 消息气泡（用 § 4 非对称圆角） |
| `.chat-context-banner` | **置顶不滚动的上下文感知条**（v3 关键改动） |
| `.chat-hints` / `.chat-hint` | AI 主动提示条（9 种场景） |
| `.chat-ai-ico` | AI 头像图标 |
| `.chat-input-box` | 输入框容器 |

### 6.5 任务

`task-*`（20 个类）+ `tc-*`（6 个，任务确认卡）—— 完整生命周期：
| 状态 | 类 | 视觉 |
|---|---|---|
| 确认 | `.tc-header`, `.tc-row`, `.tc-rows`, `.tc-btn-primary`, `.tc-btn-ghost`, `.tc-btns` | 中性灰底 + Primary 按钮 |
| 进行中 | `.task-actions`, `.task-act-btn` | Amber 进度条 |
| 异常 | `.task-alert`, `.task-alert-head`, `.task-alert-msg`, `.task-alert-btn` | Red `bg-red-50 border-red-200` |
| 完成 | （沿用 task- 容器 + Green chip） | Green 提示 |

### 6.6 收藏 / Bookmark

`bm-*`（18 个类）—— Tab 3 主体。
关键类：`.bm-item`, `.bm-item-head`, `.bm-item-ico`, `.bm-item-actions`, `.bm-filter-btn`, `.bm-act`。

### 6.7 我的 / Settings

`my-*`（14 个类）—— Tab 4。
关键类：`.my-avatar`, `.my-avatar-row`, `.my-name`, `.my-email`, `.my-edit`, `.my-list`。

### 6.8 平台注入 UI（content script）

`plat-*`（19 个类）+ `li-*`（候选人/职位列表卡片，3 个）+ `ccb-*`（候选人卡角标，2 个）。
| 类 | 含义 |
|---|---|
| `.plat-ai-float` | 详情页右侧浮动 AI 分析按钮 |
| `.plat-ai-btn` | 浮动按钮主体 |
| `.plat-bar` / `.plat-bar-dot` / `.plat-bar-text` | 列表页顶部"批量操作条" |
| `.plat-batch-bar` | 批量操作工具栏 |
| `.li-card` / `.li-card-img` / `.li-match-badge` | 列表卡片 + AI 匹配角标 |
| `.ccb-ico` / `.ccb-score` | 候选人卡片角标内的图标+分数 |

### 6.9 浮标 / 右下角图标

`float-*`（3 个类）—— 5 种状态（默认/激活/进行中/待处理/展开）。
关键类：`.float-wrapper`, `.float-ico`, `.float-ico-label`。

### 6.10 微件 Chips / Score / Empty / Tabs

| 前缀 | 用法 | 变体 |
|---|---|---|
| `chip-*` | 通用标签 | `.chip-blue` `.chip-green` `.chip-amber` `.chip-red` `.chip-dark` |
| `score-*` | 匹配分角标 | `.score-high`(green) `.score-mid`(amber) `.score-low`(red) |
| `empty-*` | 空状态 | `.empty-state`, `.empty-ico`, `.empty-title`, `.empty-sub`, `.empty-btn` |
| `pt-*` | Panel Tab 栏（4 个 Tab） | `.pt`, `.pt-tab`, `.pt-tab-ico` |
| `pi-*` | Page Info（页面状态条） | `.pi`, `.pi-btn`, `.pi-field` |

---

## 7. 状态语义对照表（最关键的一张表）

| 状态 | Chip | Score | Task | 浮标态 | 配色 |
|---|---|---|---|---|---|
| **激活 / Primary 动作** | `.chip-blue` | — | — | 激活 | Blue 600/200/50 |
| **完成 / 成功 / 高匹配** | `.chip-green` | `.score-high` | 完成 | — | Green 600/200/50 |
| **进行中 / 待确认 / 中匹配** | `.chip-amber` | `.score-mid` | 执行中 | 进行中 | Amber 600/200/50 |
| **异常 / 需验证 / 低匹配 / 超限** | `.chip-red` | `.score-low` | 需验证 | 待处理 | Red 600/200/50 |
| **静默 / 中性** | `.chip-dark` | — | 确认 | 默认 | Slate 700/300 |

> 这张表是设计的"指纹"：所有反馈型 UI 都必须对应这五种状态之一，不要发明新色。

---

## 8. 动效 Motion

| 场景 | 时长 / 曲线 | 来源 |
|---|---|---|
| Header Tab 切换底色 | `0.2s` ease | v3 改动说明 |
| 浮标"呼吸光晕" | `2s` infinite | v3 改动说明 |
| 进度旋转环（任务执行中） | `1.2s` linear infinite | v3 改动说明 |
| AI 提示条出现 | `0.15s` ease-out fade+slide | 推断 |
| Chip / 卡片 hover | `0.12s` ease | 推断 |

约束：**没有 spring，没有 0.4s+ 的入场动画**——克制是设计意图。

---

## 9. 内容 / 文案语气 Voice

从设计稿正文文案可见的语气特征：
- 简体中文为主，**英文专有名词不翻译**（Pro / Credit / Onboarding / Boss / LinkedIn / Indeed）
- 数字优先，强调"几次/几人/几天"具象量化（`50职位/次`、`100人/次`、`20cr/天`）
- **不用感叹号**，不用 emoji 装饰文案
- AI 提示条采用"主动建议 + 一键操作"句式："要我帮你..."、"是否需要..."

---

## 10. 与现有代码的映射 (Where to apply)

| 目标代码位置 | 用本规范做什么 |
|---|---|
| `job-seeker-ext/sidepanel.html` + `sidepanel.js` (114KB) | Tab 栏 (`pt-*`)、AI 对话 (`chat-*`)、任务卡 (`task-*` / `tc-*`)、收藏 (`bm-*`)、我的 (`my-*`) 全部按本规范重落 |
| `job-seeker-ext/ext_shared/content/*.js` 注入 | 列表卡角标 (`li-match-badge` / `ccb-score`)、批量条 (`plat-batch-bar`)、浮动 AI 按钮 (`plat-ai-float`) |
| `job-seeker-ext/popup.html` / `options.html` | 沿用色板与字号 scale；popup 适合用 `screen-group` 容器；options 走表单 (`pi-field`) |
| `job-api-admin` (Vue3) | 整套规范直接 1:1 落 Tailwind 配置（见 §11） |
| `job-evolve-agent/server.py` 提供的 Vue SPA | 沿用 chip / score / chapter 系统化 UI |

---

## 11. Tailwind 落地配置

`tailwind.config.js` 推荐配置（适用于 admin / Vue 项目）：

```js
module.exports = {
  theme: {
    extend: {
      colors: {
        // 直接复用 Tailwind 内置 slate / blue / green / amber / red 即可
        surface: {
          0: '#ffffff',
          50: '#fafafa',
          100: '#f8fafc',
          150: '#f1f5f9',
          200: '#e8ecf0',
        },
      },
      borderRadius: {
        'xs': '3px',
        DEFAULT: '6px',
        'md': '8px',
        'lg': '10px',
        'xl': '12px',
        '2xl': '16px',
        '3xl': '20px',
      },
      fontSize: {
        'micro': ['10px', '1.3'],
        'tiny':  ['11px', '1.4'],
      },
      fontWeight: { normal: 600, medium: 700, bold: 800 }, // 屏蔽 regular
      boxShadow: {
        card:  '0 8px 32px rgba(15,23,42,0.12)',
        modal: '0 4px 16px rgba(15,23,42,0.20)',
      },
      fontFamily: {
        sans: ['-apple-system','BlinkMacSystemFont',"'Segoe UI'",'Roboto','sans-serif'],
      },
    },
  },
}
```

扩展端（无打包工具）：建议把上述 token 写成 CSS 变量 `:root { --color-slate-900: #0f172a; ... }`，组件类使用 `var(--xxx)`。

---

## 12. 不在本规范内 / 待补

- **图标集**：源稿混用 emoji（💬 📋 ⭐ 👤 🚀 🎁）+ 单色 SVG (✓ ✗ ⚠ 等)，暂未抽出统一 icon library。建议使用 lucide-icons（line 系，免费、~24px 默认）作为补全。
- **暗黑模式**：源稿无暗色版本，仅深色 chrome。如要扩展，请基于 slate 反转生成。
- **国际化字号**：当前 size scale 在英文长词下可能溢出；未来 LinkedIn 等英文界面注入需要额外验证。
- **可访问性 (a11y)**：源稿对比度未标，但 slate-900 on white 远超 AAA；slate-500 on white 接近 AA 边界，**长段落正文请勿使用 slate-500 以下**。
- **打印 / PDF 样式**：暂无。

---

## 13. 变更管理

- 本文档与 `https://dinq-product.vercel.app/plugin-ui-v3.html` 保持一致——后者升级 v4 时，须重抽 CSS token 表，更新色板、字号、新增组件前缀。
- 新组件命名沿用 `XX-` 前缀模式（2-4 字母语义缩写）。
- 提交 PR 修改本文档时，要求附带受影响截图。

---

*文档版本：v1 / 2026-05-14 / 抽自 plugin-ui-v3.html inline CSS + 渲染稿*
