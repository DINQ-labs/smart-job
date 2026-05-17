# dynamic-commands/ — 云端动态命令的 source of truth

每个 yaml 一条命令或一条 chain。**这里是真相，DB 只是缓存。**

## 目录结构

```
dynamic-commands/
  _meta.yaml             # 全局元数据（schema_version 等）
  _examples/             # 参考 yaml（不入 lint，不入 sync）
  boss/<cmd_name>.yaml   # zhipin 命令
  linkedin/<cmd_name>.yaml
  indeed/<cmd_name>.yaml
  chains/<chain_name>.yaml  # 自定义 token chain 定义
```

`_examples/` 里的 yaml 不参与 lint / sync。例如 `promoted_recruiter_chat_list.yaml`
用作"已升 static 的命令长什么样"的留档参考，不会真推到扩展。

## 工作流

1. **写**：从 `api-anaylzer/` 看抓包 → 写新 yaml（或 Phase 3 用 analyzer 自动生成草稿）
2. **lint**：`python scripts/validate_dynamic.py` 必须 0 error 才能合并
3. **commit**：PR review → merge 到 main
4. **推送**：`python scripts/sync_dynamic_to_db.py --gateway https://testapi.dinq.me --token $ADMIN_PASSWORD` 把所有 yaml 拼成一个 config_update 推到 gateway，gateway 再广播到所有连接的扩展
5. **监控**：admin 面板 `/dynamic-commands` 看历史 + ack；命令日志页可按 `is_dynamic` 过滤
6. **晋升**：跑 30 天稳定后 `python scripts/promote_to_static.py boss/<cmd>` 生成 PR 草稿，把 yaml 的实现搬到扩展 + 网关静态代码

## yaml schema（最小集）

```yaml
path: boss/recruiter_chat_list   # 必填，唯一，正则 ^(boss|linkedin|indeed)/[a-z][a-z0-9_]+$
description: ≥10 字符的命令说明（agent 看 description 决定何时调用）

# Phase 2 才用：MCP tool 暴露元数据
mcp:
  name: boss_recruiter_chat_list   # 正则 ^(boss|linkedin|indeed)_[a-z][a-z0-9_]+$
  params:
    - {name: label_id, type: int,  default: 0,  required: false}
    - {name: sort,     type: str,  default: "", required: false}

# token chain 关系（Phase 3 后完整）
requires: null   # 或 {chain: jobs, stage: detail, keyParam: encrypt_job_id}
produces: null   # 或 {chain: jobs, stage: list, extractor: {type: jsonpath_list, path: ..., entity_field: ...}}

# 实际请求模板（必填）
requestBuilder:
  method: POST
  url: /wapi/zprelation/friend/filterByLabel
  contentType: form
  body:
    labelId: "{{body.label_id}}"
    encJobId: ""
    sort: "{{body.sort}}"

# 维护元数据（必填）
metadata:
  owner: hyh
  source_capture: rec_1777123250260   # 抓包链路追溯（可选）
  added_at: 2026-04-26
  promoted_at: null    # 升 static 时填
  retired_at: null     # 退役时填
```

## 反模式

- **不要**直接在 admin 面板贴 JSON 推 prod —— 那只能用于 staging / 灰度调试
- **不要**修改 dist 后忘了把 yaml 也加进来 —— git 是真相
- **不要**让 yaml 里的命令"永远是 dynamic" —— 30 天稳定该升 static
