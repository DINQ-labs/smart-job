# smart-job 官网

smart-job 项目的官方网站 —— 营销落地页 + 文档站，中英双语。

## 技术栈

- Next.js 14（App Router）
- React 18 + TypeScript
- Tailwind CSS

## 本地开发

```bash
pnpm install      # 或 npm install
pnpm dev          # http://localhost:3400
```

## 构建

```bash
pnpm build
pnpm start
```

## 结构

```
website/
├── app/
│   ├── layout.tsx              根布局
│   ├── page.tsx                重定向到默认语言
│   └── [locale]/               按语言（zh / en）分段
│       ├── page.tsx            落地页
│       └── docs/[slug]/        文档页（architecture / backend / extension / admin）
├── components/                 页面组件
├── content/docs.ts             文档内容（双语结构化数据）
└── lib/                        i18n 与文案字典
```

文档内容集中在 `content/docs.ts`，落地页文案在 `lib/dictionary.ts`，新增语言或修改
文案只需改这两处。
