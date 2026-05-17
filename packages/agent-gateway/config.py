import os
from dotenv import load_dotenv

load_dotenv()

# API key: prefer OpenRouter, fallback to Anthropic
_openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
_anthropic_key  = os.getenv("ANTHROPIC_API_KEY", "")

if _openrouter_key:
    API_KEY  = _openrouter_key
    BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
elif _anthropic_key:
    API_KEY  = _anthropic_key
    BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "")
else:
    raise RuntimeError("请在 .env 中设置 OPENROUTER_API_KEY 或 ANTHROPIC_API_KEY")

# Keep legacy names for backwards compat
ANTHROPIC_API_KEY  = API_KEY
ANTHROPIC_BASE_URL = BASE_URL

JOB_API_GATEWAY_URL = os.getenv("JOB_API_GATEWAY_URL") or os.getenv("BOSS_API_GATEWAY_URL", "http://127.0.0.1:8767")
AGENT_DB_URL        = os.getenv("AGENT_DB_URL") or os.getenv("DB_POSTGRES_URL") or "postgresql://postgres:password@localhost:5432/boss_gateway"
JOB_API_GATEWAY_MCP = JOB_API_GATEWAY_URL.rstrip("/") + "/mcp"
# Legacy aliases
BOSS_GATEWAY_URL = JOB_API_GATEWAY_URL
BOSS_GATEWAY_MCP = JOB_API_GATEWAY_MCP
MODEL              = os.getenv("AGENT_MODEL", "claude-opus-4-6")
FALLBACK_MODEL     = os.getenv("FALLBACK_MODEL", "z-ai/glm-5-turbo")
PORT               = int(os.getenv("AGENT_GW_PORT", "8769"))
MAX_SESSIONS       = int(os.getenv("MAX_SESSIONS", "20"))
MAX_TOKENS         = int(os.getenv("MAX_TOKENS", "8096"))

# ── Extended thinking / reasoning（v1.7 新增）────────────────────────────────
# Standard extended thinking：Claude 会先做一段 reasoning 再产出 text。
# - ENABLE_THINKING=true：所有用户对话开启
# - THINKING_BUDGET_TOKENS：reasoning 段最大 tokens（不超过 max_tokens）
# 关闭：把 ENABLE_THINKING 设 false 即整条链路退回旧行为，0 改动差错。
ENABLE_THINKING        = os.getenv("ENABLE_THINKING", "true").lower() == "true"
THINKING_BUDGET_TOKENS = int(os.getenv("THINKING_BUDGET_TOKENS", "8000"))

# 最低匹配度阈值(0-100):job 的 match_percent < 此值时,LLM 自然语言**不推荐**,
# 改为提示用户换关键词 / 调城市 / 重新搜索。前端 job_list_card 仍渲染全部结果
# 让用户自由浏览。三平台(Boss/LinkedIn/Indeed)search prompt 共用此阈值。
MIN_MATCH_PERCENT      = int(os.getenv("MIN_MATCH_PERCENT", "50"))
TOOL_CALL_TIMEOUT  = float(os.getenv("TOOL_CALL_TIMEOUT", "60"))   # 单次工具调用超时（秒）
TURN_TIMEOUT       = float(os.getenv("TURN_TIMEOUT", "600"))        # 整轮 agent 推理超时（秒）
LOG_DIR            = os.getenv("AGENT_LOG_DIR", "logs")
LOG_LEVEL          = os.getenv("AGENT_LOG_LEVEL", "INFO")
DEBUG_MODE         = os.getenv("AGENT_DEBUG_MODE", "false").lower() == "true"

# ── Redis ────────────────────────────────────────────────────────────────────
REDIS_URL          = os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0")

# ── Concurrency ──────────────────────────────────────────────────────────────
# MAX_SESSIONS: how many users can connect (connection layer)
# CONCURRENT_TURNS: how many agent turns run simultaneously (compute layer)
CONCURRENT_TURNS   = int(os.getenv("CONCURRENT_TURNS", "30"))

# ── Session lifecycle ────────────────────────────────────────────────────────
SESSION_IDLE_TTL   = int(os.getenv("SESSION_IDLE_TTL", "1800"))   # 30 min idle → cleanup
MAX_MESSAGES       = int(os.getenv("MAX_MESSAGES", "60"))          # trim history beyond this
WORKER_COUNT       = int(os.getenv("WORKER_COUNT", "1"))           # uvicorn workers (Phase 7)

RESUME_UPLOAD_DIR  = os.getenv("RESUME_UPLOAD_DIR", "uploads/resumes")
RESUME_PARSE_MODEL = os.getenv("RESUME_PARSE_MODEL", MODEL)
RESUME_MAX_SIZE_MB = int(os.getenv("RESUME_MAX_SIZE_MB", "10"))
SCORING_MODEL      = os.getenv("SCORING_MODEL", MODEL)  # 职位匹配评分用的模型（可设为较小模型降低成本）

# 简历管理完全本地化：不再调用任何外部简历服务（dinq-server / 内部聚合器已移除）。

# Proxy: local addresses bypass proxy
for _k in ("NO_PROXY", "no_proxy"):
    _cur = os.environ.get(_k, "")
    _bypass = "127.0.0.1,localhost,::1"
    os.environ[_k] = f"{_cur},{_bypass}" if _cur else _bypass
