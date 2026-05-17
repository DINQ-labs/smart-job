# 贡献指南

欢迎提 Issue 与 Pull Request。

## 开发环境

见 [README.md](README.md) 的「快速开始」（Docker）与「本地开发」两节。

## 代码约定

- **Python 服务**：跟随各服务现有风格；改动后跑该服务的 `pytest`（如有 `tests/`）。
- **管理后台 admin**：提交前 `pnpm build` 必须通过（含 `vue-tsc` 类型检查）。
- **浏览器扩展**：改动后在 `chrome://extensions` 重新加载验证。
- **用户可见文案**一律走 i18n：admin 用 `vue-i18n`（`packages/admin/src/i18n/`），
  扩展用自带 i18n（`extensions/job-seeker/lib/i18n/`）；中、英两份都要补齐。

## 提交

- 不要提交 `.env`、密钥（`*.pem`）、`.venv/`、`node_modules/`、构建产物、日志。
- Commit message 简洁说明「做了什么 + 为什么」，中英文皆可。

## Pull Request

- 一个 PR 聚焦一件事。
- 附上验证方式：构建输出 / 测试结果 / 截图。

## 免责声明

本项目用于学习与研究。使用者需自行确保遵守目标平台的服务条款及所在地法律法规。
