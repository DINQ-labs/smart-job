"""job_common — 跨服务共享纯逻辑模块。

之前 risk_signals.py / risk_detector.py 住在 job-api-gateway/,job-agent-gateway
跨目录 `sys.path.insert(0, '../job-api-gateway')` 反向导入 —— 紧耦合,任一服务
重命名 / 重定位都炸。

本包仅放真正的"两边都用"的纯逻辑(无 IO / 无业务 router):
  - risk_signals:  风控信号注册表 + 处置策略
  - risk_detector: 从工具响应识别风控信号

非目标:
  - 不放 DB schema / async DB helpers(每个服务各自的 db.py 自治)
  - 不放 LLM / prompt 拼装(各服务的 modes/ 自治)
  - 不放 platforms_config 那种 manifest(那是 agent-gateway 自治的)
"""
