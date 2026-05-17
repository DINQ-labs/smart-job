# Extended Thinking / Reasoning 透出

让用户看到 Claude 的 reasoning 决策过程。Anthropic Standard extended thinking
模式：每轮 text 之前先输出一段 reasoning。

## 启用

```bash
# .env / 环境变量
ENABLE_THINKING=true             # 默认 true
THINKING_BUDGET_TOKENS=8000      # reasoning 段 token 上限，必须 < MAX_TOKENS
```

服务端启动时读取 `config.py:ENABLE_THINKING` 与 `THINKING_BUDGET_TOKENS`。

**关闭：** `ENABLE_THINKING=false` 整条链路退回旧行为，零差错。

## 数据流

```
agent_loop.py
  ├─ messages.stream(thinking={type:'enabled', budget_tokens:N}, ...)
  ├─ 解析 stream event:
  │    content_block_delta + delta.type='thinking_delta' → 流式 reasoning chunk
  │    content_block_delta + delta.type='text_delta'    → 流式正文 chunk
  └─ yield:
       {type:'thinking_delta', delta: chunk}    ← 流式
       {type:'thinking_done',  content: full}   ← 段落结束（一次性）
       {type:'text_delta',     delta: chunk}    ← 之后正文

agent_events.py
  └─ thinking_done → db.log_event(event_type='thinking', content=full, mode=...)

sse_router.py
  └─ _fmt() 直接转发新事件类型，不需改

DINQ_client (前端)
  └─ store.handleStreamEvent:
       case 'thinking_delta': 累积到 isStreaming=true 的 thinking block
       case 'thinking_done':  mark isStreaming=false
  └─ BossMessageBubble 渲染 ThinkingBubble（可折叠面板）
```

## 兼容性

- **Claude Sonnet 3.7+ / Opus 4+** 支持；老模型 API 收到 thinking 参数会 400
- **OpenRouter fallback 模型自动跳过 thinking**：agent_loop.py:907 检测 `_midx > 0 || '/' in model` 时 `_thinking_param = None`
- DB schema **无需迁移**：`agent_conv_events.event_type` 是自由 TEXT 字段

## 成本影响

| Mode | 单轮 reasoning | 单轮额外成本（Opus 4.7）|
|---|---|---|
| 关闭 | 0 | 0 |
| budget=4000 | ~3000 tokens | ~$0.015 |
| budget=8000（默认） | ~6500 tokens | ~$0.030 |

如要按 mode 差异化（如 `evaluate` 给 12000，`casual` 给 4000），改
`agent_loop.py:_thinking_param` 接 `mode_def`，v2 任务。

## 故障排查

| 现象 | 排查 |
|---|---|
| 前端不出现 thinking 面板 | (1) ENABLE_THINKING=true？(2) 模型是 Claude？(3) DINQ_client 已重新部署？|
| API 400 `thinking parameter not supported` | 模型不支持。降级回 false 或换 Claude 4 |
| reasoning 段 token 用尽但回答短 | `budget_tokens` 调小或 `MAX_TOKENS` 调大 |
| DB `agent_conv_events` 没看到 thinking 行 | thinking_done 事件 path：检查 `agent_events.py` 是否被改对 |

## 测试

无 e2e 自动化（要烧真 token）；现有 `tests/` 跑过即可。
真实验证：开 `pnpm dev` 起 DINQ_client，admin/search 主聊天问一句问题，
观察紫色 thinking 折叠面板出现，点击展开看完整 reasoning。
