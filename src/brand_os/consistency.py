"""正式写请求的幂等结果、冲突分类和可复核差异契约。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from .authorization import (
    AuthorizationDecision,
    PrincipalKind,
    ProjectAction,
)
from .domain import ActorKind, CommandContext, CommandResult
from .sqlite_base import (
    IdempotencyKeyConflict,
    ResourceConflict,
    VersionConflict,
)

if TYPE_CHECKING:
    from .ports import ConflictSnapshotPort


CONFLICT_SCHEMA_VERSION = "write-conflict.v1"
WRITE_COMMAND_ACTIONS = {
    "register_source": ProjectAction.EVIDENCE_WRITE,
    "import_source_batch": ProjectAction.EVIDENCE_WRITE,
    "ingest_meeting_batch": ProjectAction.WORKING_WRITE,
    "record_candidate": ProjectAction.WORKING_WRITE,
    "add_relation": ProjectAction.WORKING_WRITE,
    "create_proposal": ProjectAction.PROPOSAL_CREATE,
    "review_proposal": ProjectAction.PROPOSAL_REVIEW,
    "reopen_proposal": ProjectAction.PROPOSAL_REVIEW,
}
PRINCIPAL_ACTOR_KINDS = {
    PrincipalKind.EMPLOYEE: ActorKind.HUMAN,
    PrincipalKind.AI: ActorKind.AI,
    PrincipalKind.WORKFLOW: ActorKind.WORKFLOW,
    PrincipalKind.SYSTEM: ActorKind.SYSTEM,
}


class ConflictCode(StrEnum):
    """客户端可稳定处理的正式写冲突类型。"""

    VERSION_MISMATCH = "VERSION_MISMATCH"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    RESOURCE_STATE_CHANGED = "RESOURCE_STATE_CHANGED"


class WriteOutcome(StrEnum):
    """一次正式写调用的三种可观察结果。"""

    COMMITTED = "COMMITTED"
    REPLAYED = "REPLAYED"
    CONFLICT = "CONFLICT"


class StateChangeKind(StrEnum):
    """预期版本与当前版本之间的正式状态变化。"""

    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"


class WriteConsistencyError(RuntimeError):
    """一致性应用服务无法安全完成请求。"""


class ConsistencyAuthorizationError(WriteConsistencyError, PermissionError):
    """授权结果与待执行命令不一致。"""


class ConsistencyIntegrityError(WriteConsistencyError):
    """事件重建结果与当前投影不一致。"""


@dataclass(frozen=True, slots=True)
class FormalStateItem:
    """用于冲突计算的最小正式状态项。"""

    item_type: str
    item_id: str
    payload: Mapping[str, object]
    source_proposal_id: str
    valid_from: str | None
    valid_until: str | None


@dataclass(frozen=True, slots=True)
class StateSnapshotSummary:
    """一个项目版本上的正式状态摘要。"""

    version: int
    available: bool
    item_count: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class FormalStateChange:
    """单个正式状态项的新增、移除或修改。"""

    kind: StateChangeKind
    item_type: str
    item_id: str
    before: FormalStateItem | None
    after: FormalStateItem | None


@dataclass(frozen=True, slots=True)
class ConflictEvent:
    """预期版本之后的一条脱敏事件元数据。"""

    project_version: int
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    actor_kind: str
    actor_id: str
    committed_at: str


@dataclass(frozen=True, slots=True)
class ConflictReport:
    """可直接映射为 HTTP 409 的稳定冲突报告。"""

    schema_version: str
    http_status: int
    code: ConflictCode
    project_id: str
    command_name: str
    idempotency_key: str
    resource_type: str | None
    resource_id: str | None
    expected_version: int
    current_version: int
    reason: str
    baseline: StateSnapshotSummary
    current: StateSnapshotSummary
    state_changes: tuple[FormalStateChange, ...]
    events: tuple[ConflictEvent, ...]
    events_truncated: bool
    next_event_version: int | None


@dataclass(frozen=True, slots=True)
class WriteExecutionResult:
    """把提交、幂等重放和冲突收敛为一个应用层返回值。"""

    outcome: WriteOutcome
    result: CommandResult | None = None
    conflict: ConflictReport | None = None

    def __post_init__(self) -> None:
        if self.outcome is WriteOutcome.CONFLICT:
            if self.conflict is None or self.result is not None:
                raise ValueError("冲突结果必须且只能包含 conflict")
        elif self.result is None or self.conflict is not None:
            raise ValueError("提交或重放结果必须且只能包含 result")


class WriteConsistencyService:
    """在已授权命令外层统一幂等、版本和资源状态冲突。"""

    def __init__(
        self,
        snapshots: ConflictSnapshotPort,
        *,
        max_conflict_events: int = 100,
    ) -> None:
        if max_conflict_events <= 0:
            raise ValueError("max_conflict_events 必须大于 0")
        self.snapshots = snapshots
        self.max_conflict_events = max_conflict_events

    def execute(
        self,
        authorization: AuthorizationDecision,
        *,
        context: CommandContext,
        command_name: str,
        operation: Callable[[], CommandResult],
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> WriteExecutionResult:
        """校验授权后执行一次正式写，并只把预期业务冲突转换为报告。"""

        self._validate_authorization(authorization, context, command_name)
        try:
            result = operation()
        except IdempotencyKeyConflict as error:
            return self._conflict_result(
                authorization,
                context,
                command_name,
                ConflictCode.IDEMPOTENCY_KEY_REUSED,
                str(error),
                resource_type,
                resource_id,
            )
        except VersionConflict as error:
            return self._conflict_result(
                authorization,
                context,
                command_name,
                ConflictCode.VERSION_MISMATCH,
                str(error),
                resource_type,
                resource_id,
            )
        except ResourceConflict as error:
            return self._conflict_result(
                authorization,
                context,
                command_name,
                ConflictCode.RESOURCE_STATE_CHANGED,
                str(error),
                resource_type,
                resource_id,
            )
        outcome = WriteOutcome.REPLAYED if result.replayed else WriteOutcome.COMMITTED
        return WriteExecutionResult(outcome=outcome, result=result)

    def _conflict_result(
        self,
        authorization: AuthorizationDecision,
        context: CommandContext,
        command_name: str,
        code: ConflictCode,
        reason: str,
        resource_type: str | None,
        resource_id: str | None,
    ) -> WriteExecutionResult:
        report = self.snapshots.capture_conflict(
            authorization,
            context=context,
            command_name=command_name,
            code=code,
            reason=reason,
            resource_type=resource_type,
            resource_id=resource_id,
            max_events=self.max_conflict_events,
        )
        return WriteExecutionResult(
            outcome=WriteOutcome.CONFLICT,
            conflict=report,
        )

    @staticmethod
    def _validate_authorization(
        authorization: AuthorizationDecision,
        context: CommandContext,
        command_name: str,
    ) -> None:
        if not command_name.strip():
            raise ValueError("command_name 不能为空")
        required_action = WRITE_COMMAND_ACTIONS.get(command_name)
        if required_action is None:
            raise ConsistencyAuthorizationError(f"未登记正式写命令：{command_name}")
        if authorization.project_id != context.project_id:
            raise ConsistencyAuthorizationError("授权项目与命令项目不一致")
        if authorization.action is not required_action:
            raise ConsistencyAuthorizationError("授权动作与命令要求不一致")
        expected_actor_kind = PRINCIPAL_ACTOR_KINDS.get(authorization.principal.kind)
        if expected_actor_kind is None:
            raise ConsistencyAuthorizationError(
                "MCP 写入必须等待独立命令身份进入版本化领域契约"
            )
        if (
            context.actor.kind is not expected_actor_kind
            or context.actor.actor_id != authorization.principal.principal_id
        ):
            raise ConsistencyAuthorizationError("授权主体与命令操作者不一致")


__all__ = [
    "CONFLICT_SCHEMA_VERSION",
    "ConflictCode",
    "ConflictEvent",
    "ConflictReport",
    "ConsistencyAuthorizationError",
    "ConsistencyIntegrityError",
    "FormalStateChange",
    "FormalStateItem",
    "StateChangeKind",
    "StateSnapshotSummary",
    "WriteConsistencyError",
    "WriteConsistencyService",
    "WriteExecutionResult",
    "WriteOutcome",
]
