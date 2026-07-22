"""从当前状态、开放问题和证据装配 Task Packet 内容。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from .sqlite_runtime_base import SQLiteRuntimeBaseMixin
from .task_packets import (
    BRAND_AGENT_PROTOCOL_VERSION,
    EVIDENCE_QUERY_VERSION,
    MODE_CONTRACTS,
    ROLE_CONTRACTS,
    TASK_PACKET_ASSEMBLY_VERSION,
    TASK_PACKET_SCHEMA_VERSION,
    TAXONOMY_VERSION,
    VETOES,
    WORK_MODE_PROTOCOL_VERSION,
)


CONFIDENTIALITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
APPROVED_BUCKETS = {
    "FACT": "facts",
    "DECISION": "decisions",
    "CONSTRAINT": "constraints",
    "ACTION": "actions",
}
WORKING_BUCKETS = {
    "VIEW": "views",
    "PREFERENCE": "preferences",
    "HYPOTHESIS": "hypotheses",
    "OPTION": "options",
    "TENDENCY": "tendencies",
    "TARGET_DATE": "target_dates",
    "OPEN": "open_questions",
    "PROPOSAL": "proposals",
}


class SQLiteTaskPacketAssemblyMixin(SQLiteRuntimeBaseMixin):
    """只选择任务显式引用的当前内容，并把缺失项写成缺口。"""

    def _assemble_packet_seed(
        self,
        project_id: str,
        task_row: Mapping[str, object],
        *,
        base_state_version: int,
        generated_at: str,
    ) -> dict[str, object]:
        spec = json.loads(str(task_row["spec_json"]))
        context_refs = [
            (str(value["item_type"]).upper(), str(value["item_id"]))
            for value in spec["context_refs"]
        ]
        approved_state = {bucket: [] for bucket in APPROVED_BUCKETS.values()}
        working_state = {bucket: [] for bucket in WORKING_BUCKETS.values()}
        known_gaps: list[dict[str, object]] = []
        evidence_candidates: list[tuple[str, str]] = [
            (str(value["evidence_ref"]), str(value["purpose"]))
            for value in spec["evidence_refs"]
        ]
        selected_refs: list[tuple[str, str]] = []

        decisions = {
            str(value["item_id"]): value
            for value in self.list_decisions(project_id, as_of=generated_at)
        }
        open_questions = {
            str(value["item_id"]): value
            for value in self.list_open_questions(project_id, as_of=generated_at)
        }
        current_state = {
            (str(value["item_type"]).upper(), str(value["item_id"])): value
            for value in self.get_current_state(project_id)
        }
        proposals = {
            str(value["proposal_id"]): value
            for value in self.list_proposals(project_id, status="proposed")
        }

        for item_type, item_id in context_refs:
            value: Mapping[str, object] | None = None
            if item_type == "DECISION":
                value = decisions.get(item_id)
                if value is not None:
                    approved_state["decisions"].append(value)
            elif item_type == "OPEN":
                value = open_questions.get(item_id)
                if value is not None:
                    working_state["open_questions"].append(value)
            elif item_type == "PROPOSAL":
                value = proposals.get(item_id)
                if value is not None:
                    working_state["proposals"].append(value)
            else:
                value = current_state.get((item_type, item_id))
                if value is not None and not self._state_window_is_current(value, generated_at):
                    value = None
                if value is not None:
                    bucket = APPROVED_BUCKETS.get(item_type) or WORKING_BUCKETS.get(item_type)
                    target = approved_state if item_type in APPROVED_BUCKETS else working_state
                    if bucket is not None:
                        target[bucket].append(value)
            if value is None:
                known_gaps.append(
                    self._derived_gap(
                        f"context:{item_type}:{item_id}",
                        f"{item_type}:{item_id} 不在当前有效上下文中，已排除。",
                        "task_context",
                    )
                )
                continue
            selected_refs.append((item_type, item_id))
            evidence_candidates.extend(self._item_evidence_refs(value))

        gap_ids = set(str(value) for value in spec["known_gap_ids"])
        gap_by_id = {
            str(value["gap_id"]): value for value in self.list_source_gaps(project_id)
        }
        for gap_id in sorted(gap_ids):
            gap = gap_by_id.get(gap_id)
            if gap is None:
                known_gaps.append(
                    self._derived_gap(
                        f"gap:{gap_id}",
                        f"任务引用的资料缺口 {gap_id} 不存在。",
                        "source_gap",
                    )
                )
            elif gap["status"] != "RESOLVED":
                known_gaps.append(dict(gap))

        relevant_evidence: list[dict[str, object]] = []
        seen_evidence: set[str] = set()
        ceiling = CONFIDENTIALITY_RANK[str(spec["confidentiality_ceiling"])]
        for evidence_ref, purpose in evidence_candidates:
            if evidence_ref in seen_evidence:
                continue
            seen_evidence.add(evidence_ref)
            resolved = dict(self.resolve_evidence_ref(project_id, evidence_ref))
            source = resolved.get("source")
            confidentiality = source.get("confidentiality") if isinstance(source, dict) else None
            if (
                isinstance(confidentiality, str)
                and confidentiality in CONFIDENTIALITY_RANK
                and CONFIDENTIALITY_RANK[confidentiality] > ceiling
            ):
                known_gaps.append(
                    self._derived_gap(
                        f"confidentiality:{evidence_ref}",
                        f"证据 {evidence_ref} 超出本任务保密级别，未装配。",
                        "runtime_policy",
                    )
                )
                continue
            resolved["purpose"] = purpose
            resolved["confidentiality"] = confidentiality
            relevant_evidence.append(resolved)
            if resolved.get("verification") != "confirmed":
                known_gaps.append(
                    self._derived_gap(
                        f"evidence:{evidence_ref}",
                        str(resolved.get("message") or "证据未确认"),
                        "evidence",
                    )
                )
            if len(relevant_evidence) == int(spec["max_evidence_items"]):
                if len(seen_evidence) < len(evidence_candidates):
                    known_gaps.append(
                        self._derived_gap(
                            "evidence:limit",
                            "相关证据超过本任务上限，其余证据需要按引用打开。",
                            "runtime_policy",
                        )
                    )
                break

        conflicts: list[Mapping[str, object]] = []
        conflict_ids: set[str] = set()
        for item_type, item_id in selected_refs:
            for relation in self.query_relations(
                project_id,
                subject_type=item_type,
                subject_id=item_id,
                relation_types=("conflicts_with",),
                as_of=generated_at,
            ):
                relation_id = str(relation["relation_id"])
                if relation_id not in conflict_ids:
                    conflict_ids.add(relation_id)
                    conflicts.append(relation)

        source_version_count = len(self.list_source_versions(project_id))
        protocol_versions = {
            "brand_agent": BRAND_AGENT_PROTOCOL_VERSION,
            "work_mode": WORK_MODE_PROTOCOL_VERSION,
            "taxonomy": TAXONOMY_VERSION,
            "evidence_query": EVIDENCE_QUERY_VERSION,
            "assembly": TASK_PACKET_ASSEMBLY_VERSION,
        }
        network = str(spec["network"])
        return {
            "schema_version": TASK_PACKET_SCHEMA_VERSION,
            "project_id": project_id,
            "base_state_version": base_state_version,
            "assembly_policy_version": TASK_PACKET_ASSEMBLY_VERSION,
            "protocol_versions": protocol_versions,
            "task": {
                "task_id": task_row["task_id"],
                "goal": spec["goal"],
                "role": task_row["role"],
                "work_mode": task_row["work_mode"],
                "deliverables": spec["deliverables"],
                "non_goals": spec["non_goals"],
            },
            "role_contract": ROLE_CONTRACTS[str(task_row["role"])],
            "mode_contract": {
                **MODE_CONTRACTS[str(task_row["work_mode"])],
                "mode_switch_authority": "Fox",
                "runtime_may_suggest_switch": True,
                "runtime_may_apply_switch": False,
            },
            "runtime_policy": {
                "network": network,
                "allowed_tools": spec["allowed_tools"],
                "model_allowlist": spec["model_allowlist"],
                "confidentiality_ceiling": spec["confidentiality_ceiling"],
                "mode_switch_authority": "Fox",
                "data_externalization": (
                    "requires_explicit_approval" if network == "approved_external" else "deny"
                ),
            },
            "approved_state": approved_state,
            "working_state": working_state,
            "relevant_evidence": relevant_evidence,
            "known_gaps": self._deduplicate_gaps(known_gaps),
            "conflicts": conflicts,
            "output_contract": {
                "schema_ref": spec["output_schema_ref"],
                "proposal_only": True,
                "acceptance_criteria": spec["acceptance_criteria"],
            },
            "vetoes": list(VETOES),
            "context_watermark": {
                "state_version": base_state_version,
                "task_revision": int(task_row["task_revision"]),
                "task_spec_hash": task_row["spec_hash"],
                "source_version_count": source_version_count,
                "selected_context_count": len(selected_refs),
                "selected_evidence_count": len(relevant_evidence),
                "search_index": "not_configured",
            },
        }

    def _item_evidence_refs(
        self, value: Mapping[str, object]
    ) -> list[tuple[str, str]]:
        evidence = value.get("evidence")
        if isinstance(evidence, list):
            return [
                (str(item["evidence_ref"]), "support")
                for item in evidence
                if isinstance(item, dict) and item.get("evidence_ref")
            ]
        refs = value.get("evidence_refs")
        if isinstance(refs, list):
            return [(str(item), "support") for item in refs]
        proposal_id = value.get("source_proposal_id")
        if not isinstance(proposal_id, str):
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT evidence_ref FROM proposal_evidence WHERE proposal_id = ? ORDER BY evidence_ref",
                (proposal_id,),
            ).fetchall()
        return [(str(row["evidence_ref"]), "support") for row in rows]

    def _state_window_is_current(
        self, value: Mapping[str, object], as_of: str
    ) -> bool:
        moment = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        valid_from = value.get("valid_from")
        valid_until = value.get("valid_until")
        if isinstance(valid_from, str) and moment < datetime.fromisoformat(
            valid_from.replace("Z", "+00:00")
        ):
            return False
        return not (
            isinstance(valid_until, str)
            and moment >= datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
        )

    def _derived_gap(self, gap_id: str, description: str, scope: str) -> dict[str, object]:
        return {
            "gap_id": gap_id,
            "status": "KNOWN_SOURCE_GAP",
            "description": description,
            "scope": scope,
            "evidence_ref": None,
        }

    def _deduplicate_gaps(
        self, gaps: Sequence[Mapping[str, object]]
    ) -> list[Mapping[str, object]]:
        by_id: dict[str, Mapping[str, object]] = {}
        for gap in gaps:
            by_id[str(gap["gap_id"])] = gap
        return [by_id[key] for key in sorted(by_id)]
