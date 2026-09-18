import json

import pytest
from pydantic import TypeAdapter, ValidationError

from tests.support import artifact
from ui_capability.contracts import ActionProposal, BoundValue, CapabilityArtifact, PublicLiteral
from ui_capability.demo import load_demo


def test_fixture_roundtrip_preserves_references_and_provenance():
    original = artifact()
    decoded = CapabilityArtifact.model_validate_json(original.model_dump_json())
    assert decoded == original
    assert decoded.provenance.kind == "test_fixture"
    assert decoded.steps[0].action.value.kind == "input_ref"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(untrusted="payload"),
        lambda d: d.update(schema_version=2),
        lambda d: d["steps"][0]["action"].update(kind="evaluate", code="danger()"),
        lambda d: d["steps"][0]["action"]["value"].update(name="undefined_input"),
        lambda d: d["steps"][0]["action"].update(target="missing_target"),
        lambda d: d["steps"][0]["preconditions"].append({"kind": "python", "expression": "True"}),
        lambda d: d["targets"]["nickname_field"]["locator"].update(kind="xpath", expression="//*"),
        lambda d: d["extractions"].pop("currency"),
        lambda d: d.update(redaction=[]),
        lambda d: d["provenance"].update(kind="live_discovery"),
    ],
)
def test_rejects_malformed_or_incomplete_artifacts(mutation):
    data = artifact().model_dump(mode="json")
    mutation(data)
    with pytest.raises(ValidationError):
        CapabilityArtifact.model_validate_json(json.dumps(data))


def test_proposal_cannot_declare_its_own_permissions():
    with pytest.raises(ValidationError):
        ActionProposal.model_validate_json(
            json.dumps(
                {
                    "observation_id": "current",
                    "ownership_epoch": 0,
                    "action": {"kind": "click", "target": "submit", "effect": "safe"},
                }
            )
        )


def test_bound_value_preserves_leading_zeroes_and_rejects_floats():
    adapter = TypeAdapter(BoundValue)
    assert adapter.validate_json('{"kind":"literal","value":"000042"}').value == "000042"
    with pytest.raises(ValidationError):
        PublicLiteral(value=1.2)


def test_installation_binding_cannot_erase_an_unreviewed_application_version(tmp_path):
    data = load_demo().artifact.model_dump(mode="json")
    data["goal"]["binding"]["version"] = "unreviewed"
    path = tmp_path / "incompatible.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_demo("http://127.0.0.1:8123", path)
