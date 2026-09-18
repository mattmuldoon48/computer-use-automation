"""Persist only structural audit facts, never execution values or diagnostics."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, TextIO
from uuid import UUID

from .contracts import (
    BusinessOutcome,
    Contract,
    Failure,
    FailureCode,
    Intervention,
    Observation,
    Ownership,
    Success,
    TerminalResult,
)
from .errors import RuntimeFault

Event = Literal[
    "action_intent",
    "action_verified",
    "action_rejected",
    "ownership",
    "retry",
    "recovery",
    "result",
    "intervention",
    "transport_denied",
]
_EVENTS = frozenset(
    {
        "action_intent",
        "action_verified",
        "action_rejected",
        "ownership",
        "retry",
        "recovery",
        "result",
        "intervention",
        "transport_denied",
    }
)


def contract_hash(contract: Contract) -> str:
    """Content checksum only; neither proof of execution nor an attestation."""
    canonical = json.dumps(contract.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EvidenceSink:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._context: dict[str, object] = {}

    def start(
        self,
        run_id: UUID,
        session_id: UUID,
        artifact: Contract,
        profile: Contract,
        *,
        run_type: Literal[
            "hand_authored_browser_fixture",
            "live_discovery",
            "discovery_simulation",
            "discovered_capability_replay",
        ] = "hand_authored_browser_fixture",
    ) -> None:
        self._context = {
            "run_id": run_id.hex,
            "session_id": session_id.hex,
            "artifact_hash": contract_hash(artifact),
            "profile_hash": contract_hash(profile),
            "run_type": run_type,
        }
        self._write({"event": "run_start"})

    def human_activity(self, kind: str, control: str, frame: str) -> None:
        if kind not in {"click", "change", "navigation"}:
            return
        if control not in {"button", "input", "select", "textarea", "a", "document", "other"}:
            return
        if frame not in {"main", "navigation", "workspace"}:
            return
        self._write({"event": "human_activity", "kind": kind, "control": control, "frame": frame})

    def checkpoint(self, step_index: int, stage: str, expected: int, matched: int) -> None:
        if stage not in {"preconditions", "postconditions", "identity", "terminal", "resume"}:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        if any(type(value) is not int or value < 0 for value in (step_index, expected, matched)):
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        self._write(
            {
                "event": "checkpoint",
                "step_index": step_index,
                "stage": stage,
                "expected_predicates": expected,
                "matched_predicates": matched,
            }
        )

    def structure(self, observation: Observation | None) -> dict[str, object]:
        if observation is None:
            return {"state": "unavailable"}
        # No text, form values, destinations, query parameters, refs, or target names.
        return {
            "state": "observed",
            "target_count": len(observation.targets),
            "dialog": "unknown" if observation.dialog == "unknown" else "none",
            "controls": {
                kind: sum(target.control == kind for target in observation.targets)
                for kind in ("input", "select", "button", "link", "text")
            },
        }

    def _write(self, projection: dict[str, object]) -> None:
        if self._context:
            projection = {**self._context, "time_utc": datetime.now(UTC).isoformat(), **projection}
        self._stream.write(json.dumps(projection, separators=(",", ":")) + "\n")
        self._stream.flush()

    def emit(self, event: Event, **fields: object) -> None:
        # Never stringify rejected arguments, recurse into arbitrary structures,
        # or serialize exceptions (including Pydantic validation errors).
        if type(event) is not str or event not in _EVENTS:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        projection: dict[str, object] = {"event": event}
        code = fields.get("code")
        ownership = fields.get("ownership")
        epoch = fields.get("epoch")
        if type(code) is FailureCode:
            projection["code"] = code.value
        if type(ownership) is Ownership:
            projection["ownership"] = ownership.value
        if type(epoch) is int and epoch >= 0:
            projection["epoch"] = epoch
        self._write(projection)

    def result(self, result: TerminalResult | Intervention) -> None:
        projection: dict[str, object]
        if type(result) is Success:
            projection = {"event": "result", "kind": "success"}
        elif type(result) is BusinessOutcome:
            # Business codes are artifact-authored names, not a fixed vocabulary.
            projection = {"event": "result", "kind": "business_outcome"}
        elif type(result) is Failure:
            projection = {"event": "result", "kind": "failure"}
            if type(result.code) is FailureCode:
                projection["code"] = result.code.value
        elif type(result) is Intervention:
            projection = {"event": "intervention", "state": "awaiting_operator"}
        else:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        metadata = result.metadata
        if type(metadata.step_index) is int and metadata.step_index >= 0:
            projection["step_index"] = metadata.step_index
        if type(metadata.retry_count) is int and metadata.retry_count >= 0:
            projection["retry_count"] = metadata.retry_count
        if type(metadata.provider_call_count) is int and metadata.provider_call_count >= 0:
            projection["provider_call_count"] = metadata.provider_call_count
        if type(metadata.human_intervened) is bool:
            projection["human_intervened"] = metadata.human_intervened
        self._write(projection)
