/*
 * _shared/design.js — DESIGN.md token 的 JS 镜像(给 content script 注入 UI 用).
 *
 * Content script 在站点 DOM 上注入 widget(如详情页右侧 AI 浮动按钮 .plat-ai-float),
 * 没法 link sidepanel/styles/tokens.css.这里把同一套 token 暴露成 JS 常量,
 * 让 injection-widgets.js 直接拼 inline style 时用,保持视觉一致.
 *
 * 跟 tokens.css 同步:tokens.css 改 hex,这里也得改一行.唯一 SoT 是 DESIGN.md,
 * 改完两处一起改是约定的代价.
 */
(function (root) {
  'use strict';
  const T = Object.freeze({
    // 中性
    slate900: '#0f172a', slate800: '#1e293b', slate700: '#334155', slate600: '#475569',
    slate500: '#64748b', slate400: '#94a3b8', slate300: '#cbd5e1', slate200: '#e2e8f0',
    slate100: '#f1f5f9', slate50:  '#f8fafc',
    gray50:   '#fafafa', surface2: '#e8ecf0', white: '#ffffff',
    // 语义
    blue50:  '#eff6ff', blue200:  '#bfdbfe', blue600:  '#2563eb',
    green50: '#f0fdf4', green200: '#bbf7d0', green600: '#16a34a',
    amber50: '#fffbeb', amber200: '#fde68a', amber600: '#d97706',
    red50:   '#fef2f2', red200:   '#fecaca', red600:   '#dc2626',
    // 阴影
    shadowCard:  '0 8px 32px rgba(15,23,42,0.12)',
    shadowModal: '0 4px 16px rgba(15,23,42,0.20)',
    // 字体
    fontSans: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  });

  /** 取分数色组(high/mid/low). */
  function scoreStyles(score) {
    const s = Number(score || 0);
    if (s >= 70) return { bg: T.green50, border: T.green200, fg: T.green600, level: 'high' };
    if (s >= 40) return { bg: T.amber50, border: T.amber200, fg: T.amber600, level: 'mid'  };
    return       { bg: T.red50,   border: T.red200,   fg: T.red600,   level: 'low'  };
  }

  root.DQ_DESIGN = { T, scoreStyles };
  // CJS 兜底(虽然 content script 不走 require,但写 lib 时方便)
  if (typeof module !== 'undefined' && module.exports) module.exports = { T, scoreStyles };
})(typeof window !== 'undefined' ? window : globalThis);
