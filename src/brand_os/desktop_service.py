"""为唯一桌面客户端提供读取与人工评审用例。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict

from .domain import Actor, ActorKind, CommandContext, ProposalReview, ReviewAction
from .ports import LocalAccessStorePort
from .sqlite_base import ProjectNotFound


DESKTOP_PROJECT_VIEW_SCHEMA_VERSION = "desktop-project-view.v1"
DESKTOP_EVIDENCE_VIEW_SCHEMA_VERSION = "desktop-evidence-view.v1"
DESKTOP_TASK_PACKET_VIEW_SCHEMA_VERSION = "desktop-task-packet-view.v1"
DESKTOP_PROPOSAL_VIEW_SCHEMA_VERSION = "desktop-proposal-view.v1"
DESKTOP_PROPOSAL_REVIEW_SCHEMA_VERSION = "desktop-proposal-review.v1"
DESKTOP_PROPOSAL_REVIEW_RESULT_SCHEMA_VERSION = "desktop-proposal-review-result.v1"


class DesktopServiceError(RuntimeError):
    """表示桌面请求不符合版本化用例契约。"""


class DesktopProjectService:
    """隔离桌面读取与 Fox 人工动作，不复用 MCP 工具分发表。"""

    def __init__(
        self,
        store: LocalAccessStorePort,
        project_id: str,
        *,
        reviewer_id: str = "Fox",
    ) -> None:
        if not project_id.strip():
            raise ValueError("project_id 不能为空")
        if reviewer_id != "Fox":
            raise ValueError("当前本地验证只允许 Fox 执行人工评审")
        self.store = store
        self.project_id = project_id
        self.reviewer = Actor(ActorKind.HUMAN, reviewer_id)

    def get_project_view(self) -> Mapping[str, object]:
        """装配桌面首屏所需的最小业务视图。"""

        project = dict(self.store.get_project(self.project_id))
        current_state = list(self.store.get_current_state(self.project_id))
        decisions = list(self.store.list_decisions(self.project_id))
        open_questions = list(self.store.list_open_questions(self.project_id))
        sources = list(
            self.store.list_source_versions(self.project_id, current_only=True)
        )
        known_gaps = list(self.store.list_source_gaps(self.project_id))
        proposals = list(self.store.list_proposals(self.project_id))
        runtime_tasks = list(self.store.list_runtime_tasks(self.project_id))
        task_packets = list(self.store.list_task_packets(self.project_id))
        proposal_counts = Counter(str(item["status"]) for item in proposals)
        return {
            "schema_version": DESKTOP_PROJECT_VIEW_SCHEMA_VERSION,
            "project": {
                "project_id": project["project_id"],
                "name": project["name"],
                "state_version": project["version"],
                "updated_at": project["updated_at"],
                "store_schema_version": self.store.schema_version,
            },
            "authority": {
                "current": "local_sqlite",
                "approval_actor": "Fox",
                "agent_can_approve": False,
            },
            "summary": {
                "current_state_count": len(current_state),
                "decision_count": len(decisions),
                "open_question_count": len(open_questions),
                "current_source_count": len(sources),
                "known_gap_count": len(known_gaps),
                "proposal_count": len(proposals),
                "pending_proposal_count": proposal_counts["proposed"],
                "runtime_task_count": len(runtime_tasks),
                "task_packet_count": len(task_packets),
            },
            "current_state": current_state,
            "decisions": decisions,
            "open_questions": open_questions,
            "sources": sources,
            "known_gaps": known_gaps,
            "proposals": proposals,
            "runtime_tasks": runtime_tasks,
            "task_packets": task_packets,
        }

    def get_evidence(self, evidence_ref: str) -> Mapping[str, object]:
        """按稳定引用返回证据链；未确认结果保持原样。"""

        self._require_text(evidence_ref, "evidence_ref")
        return {
            "schema_version": DESKTOP_EVIDENCE_VIEW_SCHEMA_VERSION,
            "project_id": self.project_id,
            "evidence": self.store.resolve_evidence_ref(
                self.project_id, evidence_ref
            ),
        }

    def get_task_packet(self, packet_id: str) -> Mapping[str, object]:
        """读取既有不可变 Packet，不允许桌面临时改写。"""

        self._require_text(packet_id, "packet_id")
        return {
            "schema_version": DESKTOP_TASK_PACKET_VIEW_SCHEMA_VERSION,
            "project_id": self.project_id,
            "packet": self.store.get_task_packet(self.project_id, packet_id),
        }

    def get_proposal(self, proposal_id: str) -> Mapping[str, object]:
        """读取 Proposal 当前值及其完整审计历史。"""

        self._require_text(proposal_id, "proposal_id")
        proposal = next(
            (
                item
                for item in self.store.list_proposals(self.project_id)
                if item["proposal_id"] == proposal_id
            ),
            None,
        )
        if proposal is None:
            raise ProjectNotFound(f"未找到 Proposal {proposal_id}")
        return {
            "schema_version": DESKTOP_PROPOSAL_VIEW_SCHEMA_VERSION,
            "project_id": self.project_id,
            "proposal": proposal,
            "history": self.store.get_proposal_history(
                self.project_id, proposal_id
            ),
        }

    def review_proposal(self, request: Mapping[str, object]) -> Mapping[str, object]:
        """执行已经由 Electron 原生确认框确认的 Fox 人工动作。"""

        allowed = {
            "schema_version",
            "proposal_id",
            "action",
            "reason",
            "replacement_after",
            "expected_version",
            "idempotency_key",
        }
        required = {
            "schema_version",
            "proposal_id",
            "action",
            "reason",
            "expected_version",
            "idempotency_key",
        }
        self._require_only(request, allowed, required)
        if request["schema_version"] != DESKTOP_PROPOSAL_REVIEW_SCHEMA_VERSION:
            raise DesktopServiceError("人工评审 Schema 版本不受支持")
        try:
            action = ReviewAction(self._text(request, "action"))
        except ValueError as exc:
            raise DesktopServiceError("人工评审动作无效") from exc
        expected_version = request["expected_version"]
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise DesktopServiceError("expected_version 必须是整数")
        replacement_after = request.get("replacement_after")
        if replacement_after is not None and not isinstance(
            replacement_after, Mapping
        ):
            raise DesktopServiceError("replacement_after 必须是对象或 null")
        if action is ReviewAction.MODIFY_AND_APPROVE and replacement_after is None:
            raise DesktopServiceError("修改后批准必须提供修改后的内容")
        if action is not ReviewAction.MODIFY_AND_APPROVE and "replacement_after" in request:
            raise DesktopServiceError("只有修改后批准可以提交修改后的内容")
        review = ProposalReview(
            proposal_id=self._text(request, "proposal_id"),
            action=action,
            reason=self._text(request, "reason"),
            replacement_after=replacement_after,
        )
        result = self.store.review_proposal(
            CommandContext(
                self.project_id,
                self.reviewer,
                self._text(request, "idempotency_key"),
                expected_version,
            ),
            review,
        )
        return {
            "schema_version": DESKTOP_PROPOSAL_REVIEW_RESULT_SCHEMA_VERSION,
            "project_id": self.project_id,
            "proposal": self.get_proposal(review.proposal_id)["proposal"],
            "command": asdict(result),
        }

    @staticmethod
    def _require_only(
        value: Mapping[str, object], allowed: set[str], required: set[str]
    ) -> None:
        unknown = set(value) - allowed
        missing = required - set(value)
        if unknown:
            raise DesktopServiceError(
                f"请求包含未声明字段：{', '.join(sorted(unknown))}"
            )
        if missing:
            raise DesktopServiceError(
                f"请求缺少必填字段：{', '.join(sorted(missing))}"
            )

    @staticmethod
    def _require_text(value: str, field: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise DesktopServiceError(f"{field} 不能为空")

    @classmethod
    def _text(cls, value: Mapping[str, object], field: str) -> str:
        selected = value[field]
        cls._require_text(selected, field)  # type: ignore[arg-type]
        return selected.strip()  # type: ignore[union-attr]
