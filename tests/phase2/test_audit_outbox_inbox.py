"""F2.7 审计、Outbox/Inbox 和后台任务边界集成测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from brand_os.domain import (
    Actor,
    ActorKind,
    CommandContext,
    ProposalDraft,
    ProposalReview,
    ReviewAction,
)
from brand_os.postgresql_authorization import grant_outbox_worker_role
from brand_os.postgresql_store import PostgreSQLCanonicalStore
from phase2.postgresql_test_runtime import TemporaryPostgreSQL


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "contracts" / "phase2" / "audit-outbox-inbox.json"
POSTGRESQL: TemporaryPostgreSQL | None = None


def setUpModule() -> None:
    """整个模块复用一个临时 PostgreSQL 集群。"""

    global POSTGRESQL
    try:
        POSTGRESQL = TemporaryPostgreSQL()
    except RuntimeError as error:
        raise unittest.SkipTest(str(error)) from error
    POSTGRESQL.start()


def tearDownModule() -> None:
    """测试结束后停止临时 PostgreSQL。"""

    if POSTGRESQL is not None:
        POSTGRESQL.stop()


class FailingOutboxStore(PostgreSQLCanonicalStore):
    """在 Outbox 写入点注入失败，验证整个正式事务回滚。"""

    def _enqueue_event_for_consumers(self, *args, **kwargs) -> int:
        raise RuntimeError("模拟 Outbox 写入失败")


class AuditOutboxInboxTest(unittest.TestCase):
    """验证正式事务与派生消费者之间的可靠边界。"""

    def setUp(self) -> None:
        assert POSTGRESQL is not None
        self.database_name, self.dsn = POSTGRESQL.create_database()
        self.store = PostgreSQLCanonicalStore(self.dsn)
        self.fox = Actor(ActorKind.HUMAN, "Fox")
        self.ai = Actor(ActorKind.AI, "codex")
        self.store.create_project(self.context(self.fox, "project-create", 0), "鸿日")

    def tearDown(self) -> None:
        assert POSTGRESQL is not None
        POSTGRESQL.drop_database(self.database_name)

    def context(self, actor: Actor, key: str, version: int | None = None) -> CommandContext:
        return CommandContext(
            "hongri",
            actor,
            key,
            self.store.get_project_version("hongri") if version is None else version,
        )

    @staticmethod
    def proposal() -> ProposalDraft:
        return ProposalDraft(
            proposal_id="proposal-1",
            proposal_kind="create",
            classification="DECISION_CANDIDATE",
            subject_id="decision-positioning",
            before=None,
            after={"id": "decision-positioning", "statement": "采用已确认方向"},
            reason="来自当前会议候选",
            impact_scope="本轮内容",
            evidence_refs=("evidence:meeting-1#12",),
        )

    def create_and_approve(self) -> None:
        created = self.store.create_proposal(
            self.context(self.ai, "proposal-create"), self.proposal()
        )
        self.store.review_proposal(
            self.context(self.fox, "proposal-approve", created.project_version),
            ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
        )

    def test_event_audit_and_outbox_commit_with_approval_and_projection(self) -> None:
        self.create_and_approve()

        events = self.store.list_events("hongri")
        audits = self.store.list_audit_records("hongri")
        outbox = self.store.list_outbox_messages("hongri", consumer_name="default")
        self.assertEqual(len(events), 3)
        self.assertEqual(len(audits), 3)
        self.assertEqual(len(outbox), 3)
        self.assertEqual([row["event_id"] for row in outbox], [row["event_id"] for row in events])
        self.assertEqual(audits[-1]["operation"], "PROPOSAL_APPROVED")
        self.assertEqual(audits[-1]["actor_kind"], "HUMAN")
        self.assertNotIn("采用已确认方向", audits[-1]["details_json"])
        self.assertEqual(self.store.list_human_actions("hongri")[0]["actor_id"], "Fox")
        self.assertEqual(self.store.get_current_state("hongri")[0]["item_type"], "DECISION")

    def test_outbox_failure_rolls_back_event_audit_approval_and_projection(self) -> None:
        created = self.store.create_proposal(
            self.context(self.ai, "proposal-create"), self.proposal()
        )
        before = {
            "version": self.store.get_project_version("hongri"),
            "events": len(self.store.list_events("hongri")),
            "audits": len(self.store.list_audit_records("hongri")),
            "outbox": len(self.store.list_outbox_messages("hongri")),
        }
        failing = FailingOutboxStore(self.dsn)

        with self.assertRaisesRegex(RuntimeError, "模拟 Outbox 写入失败"):
            failing.review_proposal(
                CommandContext("hongri", self.fox, "proposal-approve", created.project_version),
                ProposalReview("proposal-1", ReviewAction.APPROVE, "Fox 明确确认"),
            )

        self.assertEqual(self.store.get_project_version("hongri"), before["version"])
        self.assertEqual(len(self.store.list_events("hongri")), before["events"])
        self.assertEqual(len(self.store.list_audit_records("hongri")), before["audits"])
        self.assertEqual(len(self.store.list_outbox_messages("hongri")), before["outbox"])
        self.assertEqual(self.store.list_human_actions("hongri"), [])
        self.assertEqual(self.store.get_current_state("hongri"), [])

    def test_inbox_deduplicates_replayed_delivery(self) -> None:
        self.store.register_outbox_consumer("search-index")
        messages = self.store.claim_outbox_messages(
            "search-index", "worker-1", limit=1, lease_seconds=60
        )
        calls: list[str] = []

        first = self.store.deliver_outbox_message(
            "search-index",
            messages[0],
            lambda message: calls.append(str(message["event_id"])) or {"indexed": True},
            worker_id="worker-1",
        )
        self.store.replay_outbox_message(
            "search-index", str(messages[0]["message_id"]), worker_id="operator-1"
        )
        replay = self.store.claim_outbox_messages(
            "search-index", "worker-1", limit=1, lease_seconds=60
        )
        second = self.store.deliver_outbox_message(
            "search-index",
            replay[0],
            lambda message: calls.append(str(message["event_id"])),
            worker_id="worker-1",
        )

        self.assertEqual(first["status"], "ACKNOWLEDGED")
        self.assertEqual(second["status"], "REPLAYED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.list_inbox_messages("hongri", consumer_name="search-index")[0]["status"], "PROCESSED")
        self.assertEqual(self.store.get_project_version("hongri"), 1)

    def test_same_aggregate_is_claimed_in_order(self) -> None:
        self.store.register_outbox_consumer("ordered")
        self.create_and_approve()

        first_batch = self.store.claim_outbox_messages("ordered", "worker-1", limit=10)
        proposal_messages = [
            row for row in first_batch if row["aggregate_type"] == "proposal"
        ]
        self.assertEqual([row["aggregate_version"] for row in proposal_messages], [1])
        for message in first_batch:
            self.store.ack_outbox_message(
                "ordered",
                str(message["message_id"]),
                worker_id="worker-1",
                lease_token=str(message["lease_token"]),
            )

        second_batch = self.store.claim_outbox_messages("ordered", "worker-2", limit=10)
        self.assertEqual(
            [row["aggregate_version"] for row in second_batch if row["aggregate_type"] == "proposal"],
            [2],
        )

    def test_retry_dead_letter_and_replay_do_not_break_formal_state(self) -> None:
        self.store.register_outbox_consumer("workflow")
        self.create_and_approve()
        first_batch = self.store.claim_outbox_messages("workflow", "worker-1", limit=10)
        for message in first_batch:
            if message["aggregate_type"] == "project":
                self.store.ack_outbox_message(
                    "workflow",
                    str(message["message_id"]),
                    worker_id="worker-1",
                    lease_token=str(message["lease_token"]),
                )
        proposal_v1 = next(
            row for row in first_batch if row["aggregate_type"] == "proposal"
        )
        retry = self.store.fail_outbox_message(
            "workflow",
            str(proposal_v1["message_id"]),
            error="暂时不可用",
            worker_id="worker-1",
            lease_token=str(proposal_v1["lease_token"]),
            retry_delay_seconds=0,
            max_attempts=2,
        )
        retried = self.store.claim_outbox_messages("workflow", "worker-2", limit=10)
        self.assertEqual(len(retried), 1)
        dead = self.store.fail_outbox_message(
            "workflow",
            str(retried[0]["message_id"]),
            error="仍然失败",
            worker_id="worker-2",
            lease_token=str(retried[0]["lease_token"]),
            retry_delay_seconds=0,
            max_attempts=2,
        )

        self.assertEqual(retry["status"], "RETRY")
        self.assertEqual(dead["status"], "DEAD_LETTER")
        self.assertEqual(self.store.claim_outbox_messages("workflow", "worker-3"), [])
        dead_letters = self.store.list_dead_letters("hongri", unresolved_only=True)
        self.assertEqual(len(dead_letters), 1)
        self.store.replay_dead_letter(
            "workflow",
            dead_letter_id=str(dead_letters[0]["dead_letter_id"]),
            worker_id="operator-1",
        )
        replayed = self.store.claim_outbox_messages("workflow", "worker-3", limit=1)
        result = self.store.deliver_outbox_message(
            "workflow",
            replayed[0],
            lambda message: {"workflow_run": message["event_id"]},
            worker_id="worker-3",
        )

        self.assertEqual(result["status"], "ACKNOWLEDGED")
        self.assertEqual(self.store.get_current_state("hongri")[0]["item_type"], "DECISION")
        self.assertEqual(self.store.get_project_version("hongri"), 3)
        self.assertTrue(
            any(row["audit_type"] == "DEAD_LETTER" for row in self.store.list_audit_records("hongri"))
        )

    def test_outbox_worker_role_has_no_formal_table_write_privilege(self) -> None:
        role_name = f"brand_os_outbox_{uuid4().hex}"
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role_name))
            )
        grant_outbox_worker_role(self.dsn, role_name)

        with psycopg.connect(self.dsn, autocommit=True) as connection:
            formal_write = connection.execute(
                "SELECT has_table_privilege(%s, 'state_items', 'INSERT')",
                (role_name,),
            ).fetchone()[0]
            outbox_write = connection.execute(
                "SELECT has_table_privilege(%s, 'outbox_messages', 'UPDATE')",
                (role_name,),
            ).fetchone()[0]
        self.assertFalse(formal_write)
        self.assertTrue(outbox_write)


class AuditOutboxContractTest(unittest.TestCase):
    """验证机器契约固定 F2.7 边界。"""

    def test_contract_keeps_consumers_derived_and_non_authoritative(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["schema_version"], "audit-outbox.v1")
        self.assertEqual(contract["delivery_semantics"], "at_least_once")
        self.assertTrue(contract["formal_transaction"]["event_audit_outbox_atomic"])
        self.assertTrue(contract["consumer"]["inbox_deduplication"])
        self.assertFalse(contract["consumer"]["may_write_formal_tables"])
        self.assertFalse(contract["consumer"]["may_approve_formal_state"])
        self.assertIn("DEAD_LETTER", contract["outbox_statuses"])


if __name__ == "__main__":
    unittest.main()
