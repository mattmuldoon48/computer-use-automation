import json
import warnings
from io import StringIO

import pytest
from pydantic import ValidationError

from tests.support import INPUTS, artifact, observation
from ui_capability.contracts import (
    BusinessOutcome,
    Diagnostic,
    Failure,
    FailureCode,
    InputRef,
    Intervention,
    Metadata,
    Ownership,
    PublicLiteral,
    Success,
)
from ui_capability.errors import RuntimeFault
from ui_capability.evidence import EvidenceSink, diagnostic_projection

CANARY = "private_member_00042_secret_nickname"


def private_metadata() -> Metadata:
    return Metadata(
        run_id=CANARY,
        artifact_hash=CANARY,
        profile_hash=CANARY,
        failing_step=CANARY,
        retry_count=2,
        human_intervened=True,
    )


def test_event_allowlist_drops_nested_values_exceptions_and_spoofed_fields() -> None:
    stream = StringIO()
    sink = EvidenceSink(stream)
    sink.emit(
        "action_rejected",
        code=FailureCode.INVALID_ARGUMENT,
        epoch=3,
        ownership=Ownership.HUMAN,
        nested={"member_id": CANARY, "errors": [CANARY]},
        exception=ValueError(CANARY),
        outputs={"nickname": CANARY},
        url=f"http://localhost/?member_id={CANARY}",
        run_id=CANARY,
    )
    sink.emit(
        "action_intent", code=CANARY, ownership=CANARY, epoch=CANARY, details={"hidden": CANARY}
    )
    persisted = stream.getvalue()
    assert CANARY not in persisted
    assert [json.loads(line) for line in persisted.splitlines()] == [
        {"event": "action_rejected", "code": "INVALID_ARGUMENT", "epoch": 3, "ownership": "HUMAN"},
        {"event": "action_intent"},
    ]


def test_validation_rejections_are_never_serialized_wholesale() -> None:
    stream = StringIO()
    sink = EvidenceSink(stream)
    with pytest.raises(ValidationError) as caught:
        PublicLiteral.model_validate({"value": {"member": CANARY}})
    sink.emit(
        "action_rejected",
        code=FailureCode.INVALID_ARGUMENT,
        error=caught.value,
        rejected_input={"value": CANARY},
    )
    assert CANARY not in stream.getvalue()
    assert json.loads(stream.getvalue())["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize("kind", ["success", "business_outcome", "failure", "intervention"])
def test_result_projection_never_persists_outputs_details_or_metadata_strings(kind: str) -> None:
    stream = StringIO()
    sink = EvidenceSink(stream)
    metadata = private_metadata()
    if kind == "success":
        sink.result(Success(outputs={CANARY: CANARY}, metadata=metadata))
    elif kind == "business_outcome":
        sink.result(BusinessOutcome(code=CANARY, details={CANARY: CANARY}, metadata=metadata))
    elif kind == "failure":
        sink.result(Failure(code=FailureCode.INTERRUPTED, metadata=metadata))
    else:
        sink.result(
            Intervention(
                reason="session_expired",
                step=CANARY,
                session_id=CANARY,
                ownership_epoch=4,
                metadata=metadata,
            )
        )
    persisted = stream.getvalue()
    assert CANARY not in persisted
    event = json.loads(persisted)
    assert event["human_intervened"] is True
    assert event["retry_count"] == 2
    assert event["provider_call_count"] == 0
    assert not ({"outputs", "details", "run_id", "step", "session_id"} & event.keys())


def test_invalid_event_and_result_arguments_fail_without_persisting_values() -> None:
    stream = StringIO()
    sink = EvidenceSink(stream)
    with pytest.raises(RuntimeFault) as event_error:
        sink.emit(CANARY)  # type: ignore[arg-type]
    with pytest.raises(RuntimeFault) as result_error:
        sink.result({"result": CANARY})  # type: ignore[arg-type]
    assert event_error.value.code is FailureCode.INVALID_ARGUMENT
    assert result_error.value.code is FailureCode.INVALID_ARGUMENT
    assert CANARY not in str(event_error.value)
    assert CANARY not in str(result_error.value)
    assert stream.getvalue() == ""


@pytest.mark.parametrize(
    ("factory", "field", "value"),
    [
        ("construct", "stage", CANARY),
        ("copy", "expected", {"secret": CANARY}),
        ("construct", "observed", [CANARY]),
        ("copy", "target_index", True),
        ("construct", "guard_index", -1),
        ("copy", "match_count", CANARY),
        ("construct", "predicate_path", (0, {"secret": CANARY})),
        ("copy", "predicate_path", [0]),
        ("copy", "extra", {"secret": CANARY}),
    ],
)
def test_forged_diagnostics_are_discarded_without_warnings(
    factory: str, field: str, value: object
) -> None:
    fields = {"stage": "action", "expected": "unique_target", "observed": "missing"}
    valid = Diagnostic.model_validate(fields)
    if factory == "construct":
        forged = Diagnostic.model_construct(**{**fields, field: value})
    else:
        forged = valid.model_copy(update={field: value})
    stream = StringIO()
    sink = EvidenceSink(stream)
    result = Failure.model_construct(
        code=FailureCode.TARGET_NOT_FOUND, metadata=private_metadata(), diagnostic=forged
    )
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        assert diagnostic_projection(forged) is None
        sink.result(result)
        sink.emit("action_rejected", diagnostic=forged)
        sink.checkpoint(0, "preconditions", 1, 0, details=(forged,))
    assert not captured
    assert CANARY not in stream.getvalue()
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert all("diagnostic" not in event for event in events)
    assert events[-1]["details"] == []


def test_diagnostic_string_spoofs_never_invoke_custom_serialization() -> None:
    class Spoof(str):
        def __hash__(self) -> int:
            raise AssertionError("untrusted hash invoked")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("untrusted equality invoked")

    valid = Diagnostic(stage="action", expected="unique_target", observed="missing")
    spoofed = valid.model_copy(update={"stage": Spoof(CANARY)})
    stream = StringIO()
    sink = EvidenceSink(stream)
    assert diagnostic_projection(spoofed) is None
    assert diagnostic_projection({"stage": CANARY}) is None
    sink.emit(
        "action_intent",
        purpose=Spoof(CANARY),
        action_kind=Spoof(CANARY),
        effect=CANARY,
        target_index=True,
        step_index=-1,
        diagnostic=spoofed,
    )
    assert json.loads(stream.getvalue()) == {"event": "action_intent"}


def test_snapshot_reports_visible_cardinality_without_semantic_content() -> None:
    cap = artifact()
    original = observation(cap, INPUTS)
    field = next(target for target in original.targets if target.ref == "nickname_field")
    private = field.model_copy(
        update={
            "ref": CANARY,
            "role": CANARY,
            "control": CANARY,
            "text": CANARY,
            "value": CANARY,
        }
    )
    cap = cap.model_copy(
        update={
            "targets": {
                "z_private_" + CANARY: field.spec,
                "a_private_" + CANARY: next(
                    target.spec for target in original.targets if target.spec != field.spec
                ),
            }
        }
    )
    observed = original.model_copy(
        update={
            "targets": (private, private, private.model_copy(update={"visible": False})),
            "origin": CANARY,
            "route": CANARY,
            "destinations": (CANARY,),
            "visible_text": (CANARY,),
        }
    )
    stream = StringIO()
    sink = EvidenceSink(stream)
    assert sink.structure(observed)["target_details_state"] == "unavailable"
    sink.observation(observed, cap, INPUTS, {}, step_index=2)
    structure = sink.structure(observed)
    missing, ambiguous = structure["targets"]
    assert missing["target_index"] == 0 and missing["state"] == "missing"
    assert missing["match_count"] == 0
    assert ambiguous["target_index"] == 1 and ambiguous["state"] == "ambiguous"
    assert ambiguous["match_count"] == 2
    assert ambiguous["controls"]["other"] == 2
    assert ambiguous["value_types"]["string"] == 2
    assert ambiguous["text_present"] and ambiguous["value_present"]
    assert CANARY not in json.dumps(structure) + stream.getvalue()
    stale = sink.structure(observed.model_copy(update={"observation_id": "later"}))
    assert stale["target_details_state"] == "stale" and "targets" not in stale
    ambiguous["controls"]["other"] = 99
    assert sink.structure(observed)["targets"][1]["controls"]["other"] == 2


def test_snapshot_distinguishes_unresolved_bindings_and_bounds_target_records() -> None:
    cap = artifact()
    observed = observation(cap, INPUTS)
    field = cap.targets["nickname_field"].model_copy(update={"section": InputRef(name="nickname")})
    cap = cap.model_copy(update={"targets": {f"target_{index:03}": field for index in range(105)}})
    stream = StringIO()
    sink = EvidenceSink(stream)
    sink.observation(observed, cap, {}, {}, step_index=0)
    structure = sink.structure(observed)
    assert len(structure["targets"]) == 100
    assert structure["omitted_targets"] == 5
    assert all(
        record == {"target_index": index, "state": "unavailable"}
        for index, record in enumerate(structure["targets"])
    )
