"""
UserAgentSession — SSE 对话会话数据类。

Phase 2 (per-platform sessions):
  会话主键现在是 (user_id, role, platform) 三元组,而非单一 user_id。
  同一用户对同一平台的对话历史延续,跨平台互相隔离。
  SseSessionManager 用 SessionKey 索引,Redis 存储用复合 key 区分。
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import NamedTuple

import config


class SessionKey(NamedTuple):
    """Per-platform session 三元组主键。

    role: 'jobseeker' | 'recruiter' (extension 硬钉)
    platform: 'boss' | 'linkedin' | 'indeed' (前端 sub-tab)
    """
    user_id: str
    role: str
    platform: str

    def __str__(self) -> str:
        return f"{self.user_id}/{self.role}/{self.platform}"


# ── Global turn semaphore (shared by all SSE sessions) ───────────────────────
# Decouples "how many users can connect" from "how many LLM turns run at once".
global_turn_semaphore = asyncio.Semaphore(config.CONCURRENT_TURNS)


@dataclass
class UserAgentSession:
    user_id: str
    messages: list[dict] = field(default_factory=list)  # 累积对话历史（多轮）
    task: asyncio.Task | None = None     # 当前 agent 轮次 task（可 abort）
    ext_session_id: str = ""            # 绑定的扩展 session_id（""=自动）
    app_user_id: str = ""               # boss 账号标识（pool 模式）
    last_active_at: float = field(default_factory=time.time)  # 最后活跃时间戳
    created_at: float = field(default_factory=time.time)      # session 创建时间
    current_tool: str | None = None     # 当前正在调用的 tool（running 时有值）
    db_session_id: int | None = None    # agent_conv_sessions.id（Postgres）
    turn_index: int = 0                 # 当前对话轮次（每条 user_message +1）
    role_type: str = ""                 # 会话身份：'jobseeker' | 'recruiter'
    entry_type: str = ""                # 入口类型：'search' | 'assistant'
    title_set: bool = False             # 是否已从首条消息自动写入标题
    stable_browser_id: str = ""         # 扩展的稳定 browser_id，用于会话断线后自动恢复
    search_session_id: str = ""         # 当前搜索会话ID，用于标记本次搜索缓存的职位
    platform: str = "boss"              # 当前操作的职位平台：boss | linkedin | indeed | ...
    user_tier: str = ""                 # 用户订阅等级：free | pro | premium
    request_id: str = ""                # Go gateway 注入的 X-Request-ID（跨服务追踪）
    linkedin_session_id: str = ""       # LinkedIn session_id
    indeed_session_id: str = ""         # Indeed session_id
    current_mode: str = "search"        # 当前激活的 mode（含 mid-turn 升级后的结果）
    last_turn_tool_usage: set[str] = field(default_factory=set)  # 上一轮 turn 用过的工具名（Phase 2 tool_usage_hint）
    language: str = ""                  # 用户语言偏好：'zh' | 'en' | ''

    def trim_messages(self) -> None:
        """Trim history to MAX_MESSAGES, keeping first 2 + last N."""
        max_msgs = config.MAX_MESSAGES
        if len(self.messages) <= max_msgs:
            return
        self.messages = self.messages[:2] + self.messages[-(max_msgs - 2):]
