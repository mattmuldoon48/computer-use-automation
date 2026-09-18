"""Persist value-free structural audit facts and fixed-vocabulary diagnostics."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal, TextIO, get_args
from uuid import UUID

from .contracts import (
    ActionPurpose,
    BusinessOutcome,
    CapabilityArtifact,
    Contract,
    Diagnostic,
    DiagnosticExpected,
    DiagnosticObserved,
    DiagnosticStage,
    Failure,
    FailureCode,
    Intervention,
    Observation,
    Ownership,
    Scalar,
    Success,
    TerminalResult,
)
from .errors import RuntimeFault
from .policy import Effect
from .values import matches

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
_CONTROLS = ("input", "textarea", "select", "button", "link", "text", "other")
_MAX_SNAPSHOT_TARGETS = 100
_DIAGNOSTIC_ENUMS = {
    "stage": frozenset(get_args(DiagnosticStage)),
    "expected": frozenset(get_args(DiagnosticExpected)),
    "observed": frozenset(get_args(DiagnosticObserved)),
}
_DIAGNOSTIC_COUNTS = (
    "target_index",
    "output_index",
    "guard_index",
    "match_count",
    "expected_count",
)


def diagnostic_projection(value: object) -> dict[str, object] | None:
    """Validate fields before serialization, including models forged without validation."""
    if type(value) is not Diagnostic:
        return None
    fields = vars(value)
    if fields.keys() - Diagnostic.model_fields.keys() or value.__pydantic_extra__:
        return None
    projection: dict[str, object] = {}
    for name, allowed in _DIAGNOSTIC_ENUMS.items():
        field = fields.get(name)
        if type(field) is not str or field not in allowed:
            return None
        projection[name] = field
    for name in _DIAGNOSTIC_COUNTS:
        field = fields.get(name)
        if field is not None:
            if type(field) is not int or field < 0:
                return None
            projection[name] = field
    path = fields.get("predicate_path")
    if type(path) is not tuple or any(type(index) is not int or index < 0 for index in path):
        return None
    projection["predicate_path"] = list(path)
    return projection


def contract_hash(contract: Contract) -> str:
    """Content checksum only; neither proof of execution nor an attestation."""
    canonical = json.dumps(contract.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EvidenceSink:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._context: dict[str, object] = {}
        self._snapshot_id: str | None = None
        self._snapshot: dict[str, object] | None = None

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

    def checkpoint(
        self,
        step_index: int,
        stage: str,
        expected: int,
        matched: int,
        *,
        details: tuple[Diagnostic, ...] = (),
    ) -> None:
        if type(stage) is not str or stage not in _DIAGNOSTIC_ENUMS["stage"]:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        if any(type(value) is not int or value < 0 for value in (step_index, expected, matched)):
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        projection: dict[str, object] = {
            "event": "checkpoint",
            "step_index": step_index,
            "stage": stage,
            "expected_predicates": expected,
            "matched_predicates": matched,
        }
        if type(details) is tuple:
            projection["details"] = [
                safe for detail in details if (safe := diagnostic_projection(detail)) is not None
            ]
        self._write(projection)

    def observation(
        self,
        observation: Observation,
        artifact: CapabilityArtifact,
        inputs: dict[str, Scalar],
        locals_: dict[str, Scalar],
        *,
        step_index: int | None,
        stage: DiagnosticStage = "observation",
    ) -> None:
        if step_index is not None and (type(step_index) is not int or step_index < 0):
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        if type(stage) is not str or stage not in _DIAGNOSTIC_ENUMS["stage"]:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        records: list[dict[str, object]] = []
        names = sorted(artifact.targets)
        for index, name in enumerate(names[:_MAX_SNAPSHOT_TARGETS]):
            record: dict[str, object] = {"target_index": index}
            try:
                found = matches(artifact.targets[name], observation, inputs, locals_)
            except RuntimeFault:
                record["state"] = "unavailable"
                records.append(record)
                continue
            count = len(found)
            record.update(
                match_count=count,
                state="missing" if count == 0 else "unique" if count == 1 else "ambiguous",
                controls={
                    kind: sum(
                        (target.control if target.control in _CONTROLS else "other") == kind
                        for target in found
                    )
                    for kind in _CONTROLS
                },
                value_types={
                    kind: sum(type(target.value) is value_type for target in found)
                    for kind, value_type in (("string", str), ("integer", int), ("boolean", bool))
                },
                text_present=any(
                    type(target.text) is str and bool(target.text) for target in found
                ),
                value_present=any(
                    type(target.value) in (int, bool)
                    or (type(target.value) is str and bool(target.value))
                    for target in found
                ),
                derived_present=any(target.derived is True for target in found),
            )
            records.append(record)
        self._snapshot_id = observation.observation_id
        self._snapshot = {
            "targets": records,
            "omitted_targets": max(0, len(names) - _MAX_SNAPSHOT_TARGETS),
        }
        projection = {
            "event": "observation",
            "stage": stage,
            **self.structure(observation),
        }
        if step_index is not None:
            projection["step_index"] = step_index
        self._write(projection)

    def structure(self, observation: Observation | None) -> dict[str, object]:
        if observation is None:
            return {"state": "unavailable"}
        # No text, form values, destinations, query parameters, refs, or target names.
        projection: dict[str, object] = {
            "state": "observed",
            "target_count": len(observation.targets),
            "dialog": "unknown" if observation.dialog == "unknown" else "none",
            "controls": {
                kind: sum(target.control == kind for target in observation.targets)
                for kind in ("input", "select", "button", "link", "text")
            },
        }
        if self._snapshot_id == observation.observation_id and self._snapshot is not None:
            projection["target_details_state"] = "current"
            projection.update(deepcopy(self._snapshot))
        else:
            projection["target_details_state"] = (
                "unavailable" if self._snapshot is None else "stale"
            )
        return projection

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
        for name in ("step_index", "action_index", "target_index", "guard_index", "count"):
            value = fields.get(name)
            if type(value) is int and value >= 0:
                projection[name] = value
        for name, allowed in (
            ("purpose", get_args(ActionPurpose)),
            ("action_kind", ("click", "fill", "select", "navigate", "wait")),
        ):
            value = fields.get(name)
            if type(value) is str and value in allowed:
                projection[name] = value
        effect = fields.get("effect")
        if type(effect) is Effect:
            projection["effect"] = effect.value
        diagnostic = diagnostic_projection(fields.get("diagnostic"))
        if diagnostic is not None:
            projection["diagnostic"] = diagnostic
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
            diagnostic = diagnostic_projection(result.diagnostic)
            if diagnostic is not None:
                projection["diagnostic"] = diagnostic
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
