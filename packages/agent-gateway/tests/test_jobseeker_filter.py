"""Round-3 audit Issue 1+10 修复:_jobseeker_tool_filter 锁定测试。

历史:
- Round-1: tool_filter=None,所有招聘端工具都 leak 到 jobseeker
- Round-2 audit: 全屏蔽所有招聘端工具,造成 mode 切换死循环
- Round-3 当前: info-only 读工具放行,只剔写操作 + 破坏性工具

本测试锁定:必须屏蔽哪些 / 必须放行哪些。后续改动若违反契约会 fail。
"""
from pathlib import Path
import sys

_GW = Path(__file__).resolve().parent.parent
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

from modes.search import _jobseeker_tool_filter  # noqa: E402


def _filter(names):
    """辅助:把工具名列表转成 [{"name": n}, ...] 喂给 filter,返回剩余的 name 集合。"""
    tools = [{"name": n} for n in names]
    return {t["name"] for t in _jobseeker_tool_filter(tools)}


# ── 必须屏蔽(写 / 破坏性) ──


class TestMustBlock:
    """jobseeker mode 下,这些写 / 破坏性工具不能让 LLM 看见 / 调用。"""

    def test_blocks_boss_logout(self):
        # 破坏性 —— 让 LLM 主动登出用户是绝对不行的
        assert "boss_logout" not in _filter(["boss_logout"])

    def test_blocks_boss_recruiter_writes(self):
        for tool in ("boss_contact_candidate", "boss_mark_geek_interest",
                     "boss_accept_exchange"):
            assert tool not in _filter([tool]), f"{tool} 必须屏蔽"

    def test_blocks_linkedin_recruiter_writes(self):
        for tool in ("linkedin_recruiter_send_inmail",
                     "linkedin_recruiter_add_to_project"):
            assert tool not in _filter([tool]), f"{tool} 必须屏蔽"

    def test_blocks_indeed_employer_writes(self):
        for tool in ("indeed_employer_send_message",
                     "indeed_employer_update_candidate_status",
                     "indeed_employer_set_candidate_feedback",
                     "indeed_employer_mark_candidate_viewed"):
            assert tool not in _filter([tool]), f"{tool} 必须屏蔽"

    def test_blocks_indeed_employer_job_publish(self):
        # cross-platform mode 下 _INDEED_NO_GO 不生效,filter 防御性补一遍
        for tool in ("indeed_employer_publish_job",
                     "indeed_employer_update_job_form",
                     "indeed_employer_optimize_job_description"):
            assert tool not in _filter([tool]), f"{tool} 必须屏蔽"

    def test_blocks_indeed_request_compose(self):
        # spec 4.x 求职端无主动消息流程
        assert "indeed_request_compose" not in _filter(["indeed_request_compose"])


# ── 必须放行(info-only / jobseeker 自己的写) ──


class TestMustAllow:
    """这些读工具或 jobseeker-side 写在 jobseeker mode 下必须可调,否则触发死循环
    (历史 bug:Round-2 把 list_jobs / check_login 等都屏了,LLM 无法完成 spec 5.1)。"""

    def test_allows_indeed_employer_read_tools(self):
        # 用户从 jobseeker 切到 employer 的过程中,LLM 必须能读 employer 状态
        for tool in ("indeed_employer_check_login",        # 死循环根源:必须放行
                     "indeed_employer_list_jobs",          # spec 5.1 已发布岗位列表
                     "indeed_employer_search_candidates",   # 读
                     "indeed_employer_get_candidate",
                     "indeed_employer_get_conversations",
                     "indeed_employer_get_conversation_messages",
                     "indeed_employer_get_screening_summary",
                     "indeed_employer_get_screening_answers",
                     "indeed_employer_search_resumes",
                     "indeed_employer_get_talent_engagement",
                     "indeed_employer_get_match_profile",
                     "indeed_employer_get_candidate_submission",
                     "indeed_employer_find_applicants",
                     "indeed_employer_get_applicant_filters",
                     "indeed_employer_get_risk_assessment",
                     "indeed_employer_list_conversations_v2",
                     "indeed_employer_get_conversation_thread",
                     "indeed_employer_log_candidate_seen"):
            assert tool in _filter([tool]), f"{tool} 必须放行(info-only / 反作弊埋点)"

    def test_allows_jobseeker_writes(self):
        # 求职端自己的写操作 —— 是 jobseeker mode 业务流程的一部分,不能屏蔽
        for tool in ("linkedin_apply_job",         # 投递
                     "linkedin_send_message",      # 给招聘经理发消息(spec 4.3)
                     "linkedin_connect",           # connection request(spec 4.3)
                     "linkedin_reply_to_conversation",
                     "linkedin_mark_messages_seen",
                     "indeed_save_job",
                     "indeed_unsave_job",
                     "indeed_dislike_job",
                     "indeed_create_job_alert",
                     "indeed_request_compose"  if False else "linkedin_request_compose"):
            assert tool in _filter([tool]), f"{tool} 必须放行(jobseeker-side 写)"

    def test_allows_boss_jobseeker(self):
        # Boss 求职端核心读+写
        for tool in ("boss_check_login",
                     "boss_search_jobs",
                     "boss_get_job_detail",
                     "boss_start_chat",          # 求职者打招呼
                     "boss_geek_filter_by_label",
                     "boss_get_chat_history",
                     "boss_geek_get_boss_data"):
            assert tool in _filter([tool]), f"{tool} 必须放行(Boss 求职端)"

    def test_allows_check_logins(self):
        # 三平台所有 check_login 都必须可调
        for tool in ("boss_check_login",
                     "linkedin_check_login",
                     "indeed_check_login",
                     "indeed_employer_check_login"):
            assert tool in _filter([tool])


# ── 整体行为 ──


class TestFilterIntegrity:
    def test_empty_input(self):
        assert _filter([]) == set()

    def test_unrecognized_tool_passes(self):
        # filter 不应误伤未知工具
        assert "some_new_tool_xyz" in _filter(["some_new_tool_xyz"])

    def test_mixed_set(self):
        names = ["boss_logout", "boss_check_login", "indeed_employer_send_message",
                 "indeed_employer_check_login", "linkedin_apply_job"]
        result = _filter(names)
        assert "boss_check_login" in result
        assert "indeed_employer_check_login" in result
        assert "linkedin_apply_job" in result
        assert "boss_logout" not in result
        assert "indeed_employer_send_message" not in result
