"""正式写幂等、乐观锁和冲突差异集成测试。"""

from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet
from psycopg import sql

from brand_os.authorization import (
    ConfidentialityLevel,
    ProjectAction,
    ProjectAuthorizationService,
    ProjectPrincipal,
    PrincipalKind,
)
from brand_os.consistency import (
    ConflictCode,
    ConsistencyAuthorizationError,
    ConsistencyIntegrityError,
    StateChangeKind,
    WriteConsistencyService,
    WriteOutcome,
)
from brand_os.domain import (
    Actor,
    ActorKind,
    CommandContext,
    ProposalDraft,
    ProposalReview,
    ReviewAction,
)
from brand_os.postgresql_authorization import (
    PostgreSQLProjectAuthorizationRepository,
    grant_project_runtime_role,
)
from brand_os.postgresql_consistency import PostgreSQLConflictSnapshotRepository
from brand_os.postgresql_identity import PostgreSQLIdentityRepository
from brand_os.postgresql_store import PostgreSQLCanonicalStore
from brand_os.secret_cipher import FernetSecretCipher
from phase2.postgresql_test_runtime import TemporaryPostgreSQL


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "write-consistency.json"
AUTHORITY_CONTRACT_PATH = ROOT / "contracts" / "phase2" / "postgresql-authority.json"
POSTGRESQL: TemporaryPostgreSQL | None = None
RUNTIME_ROLE = f"brand_os_consistency_{uuid4().hex}"


def setUpModule() -> None:
    """启动隔离 PostgreSQL，并创建无 RLS 旁路权的运行时角色。"""

    global POSTGRESQL
    try:
        POSTGRESQL = TemporaryPostgreSQL()
    except RuntimeError as error:
        raise unittest.SkipTest(str(error)) from error
    POSTGRESQL.start()
    with psycopg.connect(POSTGRESQL.admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOBYPASSRLS"
            ).format(sql.Identifier(RUNTIME_ROLE))
        )


def tearDownModule() -> None:
    """删除测试角色并停止临时 PostgreSQL。"""

    if POSTGRESQL is None:
        return
    with psycopg.connect(POSTGRESQL.admin_dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(RUNTIME_ROLE))
        )
    POSTGRESQL.stop()


class WriteConsistencyContractTest(unittest.TestCase):
    """冻结 F2.6 对客户端和后续 HTTP 层可见的机器语义。"""

    def test_contract_freezes_outcomes_conflicts_and_deferred_scope(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        authority_contract = json.loads(
            AUTHORITY_CONTRACT_PATH.read_text(encoding="utf-8")
        )

        self.assertEqual(contract["schema_version"], "write-consistency.v1")
        self.assertEqual(contract["conflict_schema_version"], "write-conflict.v1")
        self.assertEqual(
            set(contract["outcomes"]),
            {"COMMITTED", "REPLAYED", "CONFLICT"},
        )
        self.assertEqual(
            set(contract["conflict_codes"]),
            {
                "VERSION_MISMATCH",
                "IDEMPOTENCY_KEY_REUSED",
                "RESOURCE_STATE_CHANGED",
            },
        )
        self.assertEqual(contract["http_status"], 409)
        self.assertFalse(contract["requirements"]["last_write_wins"])
        self.assertTrue(
            contract["requirements"]["current_projection_verified_against_events"]
        )
        self.assertFalse(contract["requirements"]["unknown_failures_become_conflicts"])
        self.assertIn("http_route", contract["deferred"])
        self.assertIn("mcp_command_identity", contract["deferred"])
        self.assertFalse(contract["migrates_hongri_data"])
        self.assertEqual(
            authority_contract["write_consistency_contract"],
            "write-consistency.v1",
        )


class FailingProjectionStore(PostgreSQLCanonicalStore):
    """在正式投影写入时注入未知异常。"""

    def _apply_approval_projection(self, *args, **kwargs) -> None:
        raise RuntimeError("模拟正式投影写入失败")


class WriteConsistencyIntegrationTest(unittest.TestCase):
    """用真实 PostgreSQL 验证重试、并发和差异重建。"""

    def setUp(self) -> None:
        assert POSTGRESQL is not None
        self.database_name, self.dsn = POSTGRESQL.create_database()
        self.store = PostgreSQLCanonicalStore(self.dsn)
        self.fox_actor = Actor(ActorKind.HUMAN, "Fox")
        self.fox = ProjectPrincipal(PrincipalKind.EMPLOYEE, "Fox")
        self.store.create_project(
            CommandContext("hongri", self.fox_actor, "project-create", 0),
            "测试项目",
        )
        identity = PostgreSQLIdentityRepository(
            self.dsn,
            cipher=FernetSecretCipher(Fernet.generate_key().decode("ascii")),
        )
        identity.register_employee(
            employee_id="Fox",
            display_name="Fox",
            primary_email="fox@example.test",
            actor=self.fox_actor,
            occurred_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
        )
        authorization_repository = PostgreSQLProjectAuthorizationRepository(self.dsn)
        self.authorization_service = ProjectAuthorizationService(
            authorization_repository
        )
        self.authorization_service.bootstrap_owner(
            self.fox,
            project_id="hongri",
            confidentiality_ceiling=ConfidentialityLevel.P3,
            occurred_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
        )
        grant_project_runtime_role(self.dsn, RUNTIME_ROLE)
        self.runtime_dsn = (
            f"postgresql://{RUNTIME_ROLE}@127.0.0.1:"
            f"{POSTGRESQL.port}/{self.database_name}"
        )
        self.snapshots = PostgreSQLConflictSnapshotRepository(self.runtime_dsn)
        self.service = WriteConsistencyService(self.snapshots)
        self.create_authorization = self.authorization_service.authorize(
            self.fox,
            project_id="hongri",
            action=ProjectAction.PROPOSAL_CREATE,
        )
        self.review_authorization = self.authorization_service.authorize(
            self.fox,
            project_id="hongri",
            action=ProjectAction.PROPOSAL_REVIEW,
        )

    def tearDown(self) -> None:
        assert POSTGRESQL is not None
        POSTGRESQL.drop_database(self.database_name)

    @staticmethod
    def proposal(proposal_id: str, subject_id: str | None = None) -> ProposalDraft:
        resolved_subject = subject_id or f"decision-{proposal_id}"
        return ProposalDraft(
            proposal_id=proposal_id,
            proposal_kind="create",
            classification="DECISION_CANDIDATE",
            subject_id=resolved_subject,
            before=None,
            after={"id": resolved_subject, "statement": f"确认 {proposal_id}"},
            reason="来自测试证据",
            impact_scope="当前测试",
            evidence_refs=(f"evidence:{proposal_id}",),
        )

    def execute_create(
        self,
        context: CommandContext,
        proposal: ProposalDraft,
    ):
        return self.service.execute(
            self.create_authorization,
            context=context,
            command_name="create_proposal",
            operation=lambda: self.store.create_proposal(context, proposal),
            resource_type="proposal",
            resource_id=proposal.proposal_id,
        )

    def create_and_approve(self, proposal_id: str = "proposal-approved") -> None:
        create_context = CommandContext(
            "hongri",
            self.fox_actor,
            f"create-{proposal_id}",
            self.store.get_project_version("hongri"),
        )
        created = self.execute_create(create_context, self.proposal(proposal_id))
        assert created.result is not None
        review_context = CommandContext(
            "hongri",
            self.fox_actor,
            f"approve-{proposal_id}",
            created.result.project_version,
        )
        reviewed = self.service.execute(
            self.review_authorization,
            context=review_context,
            command_name="review_proposal",
            operation=lambda: self.store.review_proposal(
                review_context,
                ProposalReview(
                    proposal_id,
                    ReviewAction.APPROVE,
                    "Fox 明确确认",
                ),
            ),
            resource_type="proposal",
            resource_id=proposal_id,
        )
        self.assertEqual(reviewed.outcome, WriteOutcome.COMMITTED)

    def test_one_hundred_identical_retries_commit_once_then_replay(self) -> None:
        context = CommandContext("hongri", self.fox_actor, "retry-100", 1)
        proposal = self.proposal("proposal-retry")

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(
                executor.map(
                    lambda _: self.execute_create(context, proposal),
                    range(100),
                )
            )

        self.assertEqual(
            [result.outcome for result in results].count(WriteOutcome.COMMITTED),
            1,
        )
        self.assertEqual(
            [result.outcome for result in results].count(WriteOutcome.REPLAYED),
            99,
        )
        event_ids = {result.result.event_id for result in results if result.result}
        self.assertEqual(len(event_ids), 1)
        self.assertEqual(len(self.store.list_events("hongri")), 2)

    def test_two_requests_on_same_version_yield_one_commit_and_one_409(self) -> None:
        barrier = Barrier(2)

        def run(index: int):
            context = CommandContext(
                "hongri",
                self.fox_actor,
                f"concurrent-{index}",
                1,
            )
            proposal = self.proposal(f"proposal-concurrent-{index}")
            barrier.wait()
            return self.execute_create(context, proposal)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, range(2)))

        self.assertEqual(
            {result.outcome for result in results},
            {WriteOutcome.COMMITTED, WriteOutcome.CONFLICT},
        )
        conflict = next(
            result.conflict
            for result in results
            if result.outcome is WriteOutcome.CONFLICT
        )
        assert conflict is not None
        self.assertEqual(conflict.http_status, 409)
        self.assertEqual(conflict.code, ConflictCode.VERSION_MISMATCH)
        self.assertEqual((conflict.expected_version, conflict.current_version), (1, 2))
        self.assertEqual(len(conflict.events), 1)

    def test_reused_idempotency_key_with_other_payload_has_own_code(self) -> None:
        context = CommandContext("hongri", self.fox_actor, "reused-key", 1)
        first = self.execute_create(context, self.proposal("proposal-first"))
        second = self.execute_create(context, self.proposal("proposal-other"))

        self.assertEqual(first.outcome, WriteOutcome.COMMITTED)
        self.assertEqual(second.outcome, WriteOutcome.CONFLICT)
        assert second.conflict is not None
        self.assertEqual(
            second.conflict.code,
            ConflictCode.IDEMPOTENCY_KEY_REUSED,
        )
        self.assertIn("不同请求", second.conflict.reason)
        self.assertEqual(len(self.store.list_events("hongri")), 2)

    def test_stale_version_reports_stable_event_and_formal_state_diff(self) -> None:
        self.create_and_approve()
        stale_context = CommandContext(
            "hongri",
            self.fox_actor,
            "stale-after-approval",
            2,
        )
        proposal = self.proposal("proposal-stale")

        first = self.execute_create(stale_context, proposal)
        second = self.execute_create(stale_context, proposal)

        self.assertEqual(first.outcome, WriteOutcome.CONFLICT)
        self.assertEqual(first.conflict, second.conflict)
        assert first.conflict is not None
        conflict = first.conflict
        self.assertEqual(conflict.code, ConflictCode.VERSION_MISMATCH)
        self.assertEqual((conflict.expected_version, conflict.current_version), (2, 3))
        self.assertEqual(conflict.baseline.item_count, 0)
        self.assertEqual(conflict.current.item_count, 1)
        self.assertEqual(len(conflict.state_changes), 1)
        self.assertEqual(conflict.state_changes[0].kind, StateChangeKind.ADDED)
        self.assertEqual(conflict.events[0].event_type, "PROPOSAL_APPROVED")
        self.assertEqual(conflict.reason, "预期版本 2 已过期，当前版本为 3")
        self.assertFalse(conflict.events_truncated)
        self.assertIsNone(conflict.next_event_version)

    def test_event_page_reports_truncation_and_first_omitted_version(self) -> None:
        self.create_and_approve()
        limited_service = WriteConsistencyService(
            self.snapshots,
            max_conflict_events=1,
        )
        context = CommandContext(
            "hongri",
            self.fox_actor,
            "truncated-events",
            1,
        )
        proposal = self.proposal("proposal-truncated")
        result = limited_service.execute(
            self.create_authorization,
            context=context,
            command_name="create_proposal",
            operation=lambda: self.store.create_proposal(context, proposal),
        )

        assert result.conflict is not None
        self.assertEqual(len(result.conflict.events), 1)
        self.assertTrue(result.conflict.events_truncated)
        self.assertEqual(result.conflict.events[0].project_version, 2)
        self.assertEqual(result.conflict.next_event_version, 3)

    def test_processed_proposal_returns_resource_state_changed(self) -> None:
        self.create_and_approve()
        context = CommandContext(
            "hongri",
            self.fox_actor,
            "approve-again",
            self.store.get_project_version("hongri"),
        )
        result = self.service.execute(
            self.review_authorization,
            context=context,
            command_name="review_proposal",
            operation=lambda: self.store.review_proposal(
                context,
                ProposalReview(
                    "proposal-approved",
                    ReviewAction.APPROVE,
                    "重复确认",
                ),
            ),
            resource_type="proposal",
            resource_id="proposal-approved",
        )

        self.assertEqual(result.outcome, WriteOutcome.CONFLICT)
        assert result.conflict is not None
        self.assertEqual(
            result.conflict.code,
            ConflictCode.RESOURCE_STATE_CHANGED,
        )
        self.assertEqual(
            result.conflict.expected_version,
            result.conflict.current_version,
        )
        self.assertEqual(result.conflict.state_changes, ())

    def test_authorization_must_match_project_action_and_actor_before_write(
        self,
    ) -> None:
        context = CommandContext("hongri", self.fox_actor, "never-written", 1)
        proposal = self.proposal("proposal-never-written")
        wrong_actor = replace(
            context,
            actor=Actor(ActorKind.HUMAN, "Other"),
        )
        cases = (
            (
                replace(self.create_authorization, project_id="other"),
                context,
                "create_proposal",
            ),
            (self.create_authorization, context, "review_proposal"),
            (self.create_authorization, wrong_actor, "create_proposal"),
        )

        for authorization, command_context, command_name in cases:
            with self.subTest(command_name=command_name, context=command_context):
                with self.assertRaises(ConsistencyAuthorizationError):
                    self.service.execute(
                        authorization,
                        context=command_context,
                        command_name=command_name,
                        operation=lambda: self.store.create_proposal(context, proposal),
                    )
        self.assertEqual(len(self.store.list_events("hongri")), 1)

    def test_unknown_transaction_failure_rolls_back_and_is_not_a_fake_conflict(
        self,
    ) -> None:
        create_context = CommandContext(
            "hongri",
            self.fox_actor,
            "create-before-failure",
            1,
        )
        created = self.execute_create(
            create_context,
            self.proposal("proposal-failure"),
        )
        assert created.result is not None
        failing = FailingProjectionStore(self.dsn)
        review_context = CommandContext(
            "hongri",
            self.fox_actor,
            "approve-failure",
            created.result.project_version,
        )
        before_events = len(self.store.list_events("hongri"))

        with self.assertRaisesRegex(RuntimeError, "模拟正式投影写入失败"):
            self.service.execute(
                self.review_authorization,
                context=review_context,
                command_name="review_proposal",
                operation=lambda: failing.review_proposal(
                    review_context,
                    ProposalReview(
                        "proposal-failure",
                        ReviewAction.APPROVE,
                        "不应提交",
                    ),
                ),
            )

        self.assertEqual(len(self.store.list_events("hongri")), before_events)
        self.assertEqual(self.store.get_project_version("hongri"), 2)
        self.assertEqual(self.store.get_current_state("hongri"), [])

    def test_projection_drift_blocks_conflict_report(self) -> None:
        self.create_and_approve()
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                "UPDATE state_items SET payload_json = %s WHERE project_id = %s",
                ('{"id":"broken"}', "hongri"),
            )
        stale_context = CommandContext(
            "hongri",
            self.fox_actor,
            "drift-check",
            2,
        )

        with self.assertRaisesRegex(ConsistencyIntegrityError, "投影"):
            self.execute_create(stale_context, self.proposal("proposal-drift"))


if __name__ == "__main__":
    unittest.main()
