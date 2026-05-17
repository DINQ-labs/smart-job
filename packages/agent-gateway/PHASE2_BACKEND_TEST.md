# Phase 2 Backend (per-platform sessions) Test Plan

## 上线步骤

1. **Reset DB schema (开发期一次性)**
   ```bash
   cd job-agent-gateway
   python3 -c "import asyncio, db; asyncio.run(db.reset_schema())"
   ```
   会 DROP & RECREATE 三张表(agent_conv_sessions / events / messages),加新列 role + platform。

2. **重启 agent-gateway**
   ```bash
   uvicorn server:app --reload --port 8769
   ```

3. **检查启动日志**:`init_db OK` + 各 mode 加载成功。

## Live curl 测试矩阵 (B7)

每个 (role, platform) 组合都要发一条消息验证 SSE 通,记录 token usage + cache hit rate。

```bash
GW=http://127.0.0.1:8769

# 6 个组合
for role in jobseeker recruiter; do
  for platform in boss linkedin indeed; do
    echo "--- $role / $platform ---"
    curl -N -X POST "$GW/agent/sse?user_id=test_$role&role=$role&platform=$platform" \
      -H "Content-Type: application/json" \
      -d '{"text":"你好"}' 2>&1 | head -20
    sleep 1
  done
done
```

预期:每个组合都返 SSE 流,connected → text_delta → message_end,无 500/400。

## 关键验证点

### B1 — DB schema
```sql
SELECT column_name, is_nullable
FROM information_schema.columns
WHERE table_name='agent_conv_sessions'
ORDER BY ordinal_position;
```
应该看到 role / platform 都是 NOT NULL,主键含 (user_id, role, platform) UNIQUE。

```sql
SELECT user_id, role, platform, COUNT(*)
FROM agent_conv_sessions
GROUP BY user_id, role, platform;
```
每个 (user, role, platform) 三元组应只有 1 行。

### B2 — Session 隔离
同一 user_id 同时跑 boss + linkedin 各发一条:
```bash
curl -X POST "$GW/agent/sse?user_id=u1&role=jobseeker&platform=boss" -d '{"text":"找北京 PM"}' &
curl -X POST "$GW/agent/sse?user_id=u1&role=jobseeker&platform=linkedin" -d '{"text":"find SF SWE"}' &
```
两个 SSE 流并行,各自的 history 互不干扰。

```bash
curl "$GW/agent/sse/sessions" | jq
```
应看到 2 行,role/platform 不同。

### B3 — Tool filtering
观察 agent_loop.py:957 区域 log,boss 调用看到的工具列表:
```
[boss_*, cross_platform_*]
```
不应有 linkedin_* 或 indeed_* 工具。

### B5 — Prompt size reduction
日志或 inspect 看 system prompt 长度:
- search/boss session: ~18k chars
- search/linkedin session: ~23k chars
- recruiter/boss session: ~11k chars

vs 之前的 25k / 21k(全量),节省 27% / 47%。

### B6 — candidate_list_card
recruiter session 调 boss_search_candidates / linkedin_search_candidates,
观察 SSE 流应有:
```
event: candidate_list_card
data: {"type":"candidate_list_card","platform":"boss","scanned":N,"matched":M,"candidates":[...]}
```

## Funnel 指标
```bash
curl "$GW/admin/metrics/funnel?days=1" | jq
```
应看到 welcome / role_selected / first_search 各有计数,**含 role 维度**。
