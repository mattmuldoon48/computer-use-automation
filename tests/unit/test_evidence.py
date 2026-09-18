import json
from io import StringIO

import pytest
from pydantic import ValidationError

from ui_capability.contracts import (
    BusinessOutcome,
    Failure,
    FailureCode,
    Intervention,
    Metadata,
    Ownership,
    PublicLiteral,
    Success,
)
from ui_capability.errors import RuntimeFault
from ui_capability.evidence import EvidenceSink

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
