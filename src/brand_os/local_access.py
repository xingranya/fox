"""为本地 CLI、MCP 和 Agent 运行时提供同一受控应用服务。"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence

from .domain import Actor, ActorKind, CommandContext, ProposalDraft
from .ports import LocalAccessStorePort
from .runtime_adapters import build_mcp_adapter_config, get_runtime_adapter
from .sqlite_base import ProjectNotFound
from .task_packets import AgentRunRequest


LOCAL_AI_ACCESS_SCHEMA_VERSION = "local-ai-access.v1"
MCP_TOOL_NAMES = (
    "project_get_state",
    "task_get_packet",
    "evidence_get",
    "decision_list",
    "open_question_list",
    "proposal_create",
    "proposal_get",
    "system_doctor",
    "project_verify",
)
FORBIDDEN_AI_OPERATIONS = (
    "proposal_approve",
    "proposal_modify_and_approve",
    "proposal_reject",
    "proposal_reopen",
    "task_switch_mode",
    "direct_sql",
    "source_hard_delete",
    "secret_read",
    "workspace_file_read",
)
PROPOSAL_INPUT_FIELDS = {
    "proposal_id",
    "proposal_kind",
    "classification",
    "subject_id",
    "before",
    "after",
    "reason",
    "impact_scope",
    "evidence_refs",
    "supersedes_proposal_id",
    "source_meeting_item_id",
    "valid_from",
    "valid_until",
    "expected_version",
    "idempotency_key",
}
PROPOSAL_REQUIRED_FIELDS = {
    "proposal_id",
    "proposal_kind",
    "classification",
    "after",
    "reason",
    "impact_scope",
    "evidence_refs",
    "expected_version",
    "idempotency_key",
}


class LocalAccessError(RuntimeError):
    """表示本地 AI 入口请求违反应用契约。"""


class ToolNotAllowed(LocalAccessError):
    """表示调用方请求了白名单以外的工具。"""


class ModelNotAllowed(LocalAccessError):
    """表示模型不在当前 Task Packet 的允许范围内。"""


class LocalAIService:
    """把项目范围、AI 身份和允许用例固定在本地应用层。"""

    def __init__(
        self,
        store: LocalAccessStorePort,
        project_id: str,
        *,
        caller_id: str,
        caller_kind: ActorKind = ActorKind.WORKFLOW,
    ) -> None:
        if not project_id.strip():
            raise ValueError("project_id 不能为空")
        if caller_kind is ActorKind.HUMAN:
            raise ValueError("非交互 CLI/MCP 不能声明为人工操作者")
        self.store = store
        self.project_id = project_id
        self.actor = Actor(caller_kind, caller_id)
        self.runtime_actor = Actor(ActorKind.SYSTEM, "brand-os-runtime")

    def invoke(self, tool_name: str, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """通过固定分发表执行工具，不接受反射式方法调用。"""

        operations = {
            "project_get_state": self.project_get_state,
            "task_get_packet": self.task_get_packet,
            "evidence_get": self.evidence_get,
            "decision_list": self.decision_list,
            "open_question_list": self.open_question_list,
            "proposal_create": self.proposal_create,
            "proposal_get": self.proposal_get,
            "system_doctor": self.system_doctor,
            "project_verify": self.project_verify,
        }
        operation = operations.get(tool_name)
        if operation is None:
            raise ToolNotAllowed(f"工具未开放：{tool_name}")
        return operation(arguments)

    def project_get_state(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """读取当前状态投影及其项目版本。"""

        self._reject_arguments(arguments)
        state = list(self.store.get_current_state(self.project_id))
        return {
            "schema_version": "project-state-view.v1",
            "project_id": self.project_id,
            "state_version": self.store.get_project_version(self.project_id),
            "items": state,
        }

    def task_get_packet(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """读取已由受控流程生成的 Packet，不允许 AI 临时装配。"""

        self._require_only(arguments, {"packet_id", "layer"}, {"packet_id"})
        packet_id = self._text(arguments, "packet_id")
        layer = arguments.get("layer")
        if layer is None or layer == "FULL":
            return self.store.get_task_packet(self.project_id, packet_id)
        if not isinstance(layer, str) or layer.upper() not in {"L0", "L1", "L2", "L3", "L4"}:
            raise LocalAccessError("layer 必须是 FULL 或 L0-L4")
        return self.store.get_task_packet_layer(self.project_id, packet_id, layer)

    def evidence_get(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """按稳定引用回源，无法确认时原样返回未确认。"""

        self._require_only(arguments, {"evidence_ref"}, {"evidence_ref"})
        return self.store.resolve_evidence_ref(
            self.project_id, self._text(arguments, "evidence_ref")
        )

    def decision_list(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """读取当前决定，历史内容只能显式请求。"""

        self._require_only(arguments, {"include_inactive"}, set())
        include_inactive = self._boolean(arguments, "include_inactive", False)
        return {
            "schema_version": "decision-list.v1",
            "project_id": self.project_id,
            "items": list(
                self.store.list_decisions(
                    self.project_id, include_inactive=include_inactive
                )
            ),
        }

    def open_question_list(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """读取当前开放问题，默认排除失效内容。"""

        self._require_only(arguments, {"include_inactive"}, set())
        include_inactive = self._boolean(arguments, "include_inactive", False)
        return {
            "schema_version": "open-question-list.v1",
            "project_id": self.project_id,
            "items": list(
                self.store.list_open_questions(
                    self.project_id, include_inactive=include_inactive
                )
            ),
        }

    def proposal_create(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """创建待 Fox 确认的 Proposal，不提供任何审批动作。"""

        self._require_only(arguments, PROPOSAL_INPUT_FIELDS, PROPOSAL_REQUIRED_FIELDS)
        after = arguments["after"]
        before = arguments.get("before")
        evidence_refs = arguments["evidence_refs"]
        expected_version = arguments["expected_version"]
        if not isinstance(after, Mapping):
            raise LocalAccessError("after 必须是对象")
        if before is not None and not isinstance(before, Mapping):
            raise LocalAccessError("before 必须是对象或 null")
        if not isinstance(evidence_refs, Sequence) or isinstance(
            evidence_refs, (str, bytes)
        ) or not all(isinstance(value, str) for value in evidence_refs):
            raise LocalAccessError("evidence_refs 必须是字符串数组")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise LocalAccessError("expected_version 必须是整数")
        draft = ProposalDraft(
            proposal_id=self._text(arguments, "proposal_id"),
            proposal_kind=self._text(arguments, "proposal_kind"),
            classification=self._text(arguments, "classification"),
            subject_id=self._optional_text(arguments, "subject_id"),
            before=before,
            after=after,
            reason=self._text(arguments, "reason"),
            impact_scope=self._text(arguments, "impact_scope"),
            evidence_refs=tuple(evidence_refs),
            supersedes_proposal_id=self._optional_text(
                arguments, "supersedes_proposal_id"
            ),
            source_meeting_item_id=self._optional_text(
                arguments, "source_meeting_item_id"
            ),
            valid_from=self._optional_text(arguments, "valid_from"),
            valid_until=self._optional_text(arguments, "valid_until"),
        )
        context = CommandContext(
            self.project_id,
            self.actor,
            self._text(arguments, "idempotency_key"),
            expected_version,
        )
        result = self.store.create_proposal(context, draft)
        return {
            "schema_version": "proposal-create-result.v1",
            "project_id": self.project_id,
            "proposal_id": draft.proposal_id,
            "status": "proposed",
            "changes_current_state": False,
            "command": asdict(result),
        }

    def proposal_get(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """读取 Proposal 当前状态，不返回审批命令。"""

        self._require_only(arguments, {"proposal_id"}, {"proposal_id"})
        proposal_id = self._text(arguments, "proposal_id")
        proposal = next(
            (
                value
                for value in self.store.list_proposals(self.project_id)
                if value["proposal_id"] == proposal_id
            ),
            None,
        )
        if proposal is None:
            raise ProjectNotFound(f"未找到 Proposal {proposal_id}")
        return {
            "schema_version": "proposal-view.v1",
            "project_id": self.project_id,
            "proposal": proposal,
        }

    def system_doctor(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """返回不含路径、密钥和原文的本地健康信息。"""

        self._reject_arguments(arguments)
        quick_check = self.store.quick_check()
        return {
            "schema_version": "local-doctor.v1",
            "status": "ok" if quick_check else "error",
            "project_id": self.project_id,
            "store_schema_version": self.store.schema_version,
            "sqlite_quick_check": quick_check,
            "transport": "stdio",
            "allowed_tools": list(MCP_TOOL_NAMES),
            "forbidden_operations": list(FORBIDDEN_AI_OPERATIONS),
            "provider_credentials_read": False,
        }

    def project_verify(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        """核对状态和证据概况，不把空数据误报成业务通过。"""

        self._reject_arguments(arguments)
        state = list(self.store.get_current_state(self.project_id))
        decisions = list(self.store.list_decisions(self.project_id))
        open_questions = list(self.store.list_open_questions(self.project_id))
        unconfirmed = [
            value["item_id"]
            for value in (*decisions, *open_questions)
            if value.get("evidence_status") != "confirmed"
        ]
        quick_check = self.store.quick_check()
        return {
            "schema_version": "project-verification.v1",
            "project_id": self.project_id,
            "verified": quick_check and not unconfirmed,
            "state_version": self.store.get_project_version(self.project_id),
            "current_state_count": len(state),
            "decision_count": len(decisions),
            "open_question_count": len(open_questions),
            "unconfirmed_item_ids": unconfirmed,
            "sqlite_quick_check": quick_check,
            "note": (
                "结构校验通过；业务质量仍由黄金集和 Fox 人工评审决定"
                if quick_check and not unconfirmed
                else "存在未确认的证据或存储完整性问题"
            ),
        }

    def start_agent_run(
        self,
        *,
        packet_id: str,
        packet_hash: str,
        runtime_name: str,
        runtime_version: str,
        model_id: str,
        model_version: str,
        run_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        """把 Codex/Claude 运行绑定到既有 Packet，不接受私有上下文。"""

        profile = get_runtime_adapter(runtime_name)
        packet = self.store.get_task_packet(self.project_id, packet_id)
        validation = self.store.validate_task_packet(self.project_id, packet_id)
        if not validation["valid"]:
            raise LocalAccessError("Task Packet 校验失败，不能启动 Agent")
        allowlist = packet["runtime_policy"]["model_allowlist"]
        if model_id not in allowlist:
            raise ModelNotAllowed(f"模型不在本任务允许范围内：{model_id}")
        request = AgentRunRequest(
            run_id=run_id,
            packet_id=packet_id,
            expected_packet_hash=packet_hash,
            runtime_id=profile.runtime_id,
            runtime_version=runtime_version,
            model_id=model_id,
            model_version=model_version,
            idempotency_key=idempotency_key,
        )
        return self.store.record_agent_run(
            self.project_id, self.runtime_actor, request
        )

    def adapter_config(
        self,
        runtime_name: str,
        *,
        workspace_root: Path,
        database_path: Path | None = None,
        command: str = "brand-os",
    ) -> Mapping[str, object]:
        """返回 Codex/Claude 的同源 stdio MCP 配置。"""

        return build_mcp_adapter_config(
            runtime_name,
            workspace_root=workspace_root,
            project_id=self.project_id,
            database_path=database_path,
            command=command,
        )

    def _reject_arguments(self, arguments: Mapping[str, object]) -> None:
        self._require_only(arguments, set(), set())

    def _require_only(
        self,
        arguments: Mapping[str, object],
        allowed: set[str],
        required: set[str],
    ) -> None:
        unknown = sorted(set(arguments) - allowed)
        missing = sorted(required - set(arguments))
        if unknown:
            raise LocalAccessError(f"包含未声明参数：{', '.join(unknown)}")
        if missing:
            raise LocalAccessError(f"缺少必填参数：{', '.join(missing)}")

    def _text(self, arguments: Mapping[str, object], field: str) -> str:
        value = arguments.get(field)
        if not isinstance(value, str) or not value.strip():
            raise LocalAccessError(f"{field} 必须是非空字符串")
        return value

    def _optional_text(
        self, arguments: Mapping[str, object], field: str
    ) -> str | None:
        value = arguments.get(field)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise LocalAccessError(f"{field} 必须是非空字符串或 null")
        return value

    def _boolean(
        self, arguments: Mapping[str, object], field: str, default: bool
    ) -> bool:
        value = arguments.get(field, default)
        if not isinstance(value, bool):
            raise LocalAccessError(f"{field} 必须是布尔值")
        return value
