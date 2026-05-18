# 贡献指南

smart-job 是一个开源的 Alpha 项目 —— 它之后长成什么样,由参与的人决定。无论你会不会写代码,都能推动它。

## 参与方式

**作为用户** —— 不需要写一行代码:

- 上手试用,把遇到的 bug 写成 Issue;
- 把「不顺手的地方」「希望它能做的事」写成 Issue 或 Discussion;
- 分享你的真实使用场景 —— 这是我们判断方向最可靠的依据。

**作为贡献者** —— 直接动手:

- 修 bug、加功能、接入新平台;
- 补充或翻译文档;
- 改进管理后台与扩展的交互。

> 路线图见[官网 Roadmap](https://smartjob.top/zh/roadmap)。Alpha 之后没有预设功能清单 —— 方向就是社区的 Issue、讨论与 PR。

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
