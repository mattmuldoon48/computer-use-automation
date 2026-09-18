"""Public configuration and private UI data have different projection authority."""

import asyncio
import json
from io import StringIO

import pytest

from tests.support import BINDING, INPUTS, artifact, observation, policy
from ui_capability.compiler import DeterministicCompiler
from ui_capability.contracts import (
    Equals,
    FailureCode,
    Fill,
    FrameScope,
    InputRef,
    ObservedTarget,
    PublicLiteral,
    RoleLocator,
    TargetSpec,
    Visible,
)
from ui_capability.demo import load_demo
from ui_capability.discovery import Discovery
from ui_capability.errors import RuntimeFault
from ui_capability.evidence import EvidenceSink
from ui_capability.profiles import Profile
from ui_capability.provider import ModelDecision
from ui_capability.session import Session
from ui_capability.surfaces.fake import FakeSurface


def derived(observed):
    return observed.model_copy(
        update={
            "targets": tuple(
                target.model_copy(update={"derived": True}) for target in observed.targets
            )
        }
    )


def ceiling(cap):
    trusted = policy(cap)
    return trusted.model_copy(
        update={"approved_literals": (*trusted.approved_literals, BINDING.entry_route)}
    )


@pytest.mark.parametrize("nickname", ["a", "Review"])
def test_schema_valid_public_collisions_compile_without_rewriting_labels(nickname):
    inputs = {**INPUTS, "nickname": nickname}
    bundle = load_demo("http://127.0.0.1:8765")
    assert bundle.artifact.goal.inputs["nickname"].accepts(nickname)
    DeterministicCompiler(
        bundle.artifact, bundle.policy, inputs, reviewed_targets=bundle.profile.targets
    )
    cap = artifact()
    compiler = DeterministicCompiler(cap, ceiling(cap), inputs, reviewed_targets=cap.targets)
    compiler.record(
        Fill(target="nickname_field", value=InputRef(name="nickname")),
        derived(observation(cap, inputs)),
        derived(observation(cap, inputs, filled=True)),
    )
    compiled = compiler.finish(live=False, run_id="privacy_fixture")
    assert compiled.steps[0].action.value == InputRef(name="nickname")
    assert compiled.steps[0].postconditions[0].value == InputRef(name="nickname")
    assert compiled.targets["review_button"].locator.name == PublicLiteral(value="Review")
    assert compiler.project_value("Review", target="review_button", field="text") == PublicLiteral(
        value="Review"
    )
    assert compiler.project_value(nickname, target="nickname_field", field="value") == InputRef(
        name="nickname"
    )


def test_equal_private_values_preserve_positional_identity_and_drop_ambiguous_text():
    cap = artifact()
    inputs = {**INPUTS, "nickname": INPUTS["member_id"]}
    compiler = DeterministicCompiler(cap, ceiling(cap), inputs, reviewed_targets=cap.targets)
    compiler.record(
        Fill(target="nickname_field", value=InputRef(name="nickname")),
        derived(observation(cap, inputs)),
        derived(observation(cap, inputs, filled=True)),
    )
    assert compiler.project_value(
        inputs["member_id"], target="member_id", field="text"
    ) == InputRef(name="member_id")
    assert compiler.project_value(inputs["nickname"], target="nickname", field="text") == InputRef(
        name="nickname"
    )
    assert compiler.project_value(
        inputs["nickname"], target="nickname_field", field="value"
    ) == InputRef(name="nickname")
    assert compiler.project_value(inputs["nickname"]) is None


@pytest.mark.parametrize("private", ["a", "Review", "000042"])
def test_scalar_approval_cannot_publish_embedded_runtime_text_or_locator(private):
    cap = artifact()
    inputs = {**INPUTS, "nickname": private}
    embedded = f"Private customer: {private}"
    trusted = ceiling(cap)
    promoted = trusted.model_copy(
        update={"approved_literals": (*trusted.approved_literals, embedded)}
    )
    compiler = DeterministicCompiler(cap, promoted, inputs, reviewed_targets=cap.targets)
    assert compiler.project_value(embedded) is None
    assert compiler.project_value(embedded, target="review_button", field="text") is None
    dynamic = ObservedTarget(
        ref="private_dynamic_link",
        spec=TargetSpec(
            frame=FrameScope(kind="main"),
            locator=RoleLocator(role="link", name=PublicLiteral(value=embedded)),
        ),
        role="link",
        control="link",
        visible=True,
        text=embedded,
        derived=True,
        grounding="visible_role",
    )
    observed = derived(observation(cap, inputs)).model_copy(update={"targets": (dynamic,)})
    with pytest.raises(RuntimeFault) as error:
        compiler.target_name(dynamic, observed)
    assert error.value.code == FailureCode.POLICY_DENIED
    # Adding both a caller target and its scalar approval still does not change
    # the independent profile's public structural authority.
    malicious = cap.model_copy(update={"targets": {**cap.targets, "private_link": dynamic.spec}})
    with pytest.raises(RuntimeFault) as error:
        DeterministicCompiler(malicious, promoted, inputs, reviewed_targets=cap.targets)
    assert error.value.code == FailureCode.POLICY_DENIED


def test_unknown_private_route_is_not_promoted_by_scalar_approval():
    cap = artifact()
    private_route = f"/prepare/{INPUTS['member_id']}"
    trusted = ceiling(cap)
    trusted = trusted.model_copy(
        update={"approved_literals": (*trusted.approved_literals, private_route)}
    )
    compiler = DeterministicCompiler(cap, trusted, dict(INPUTS), reviewed_targets=cap.targets)
    assert compiler.public_route(BINDING.entry_route)
    assert not compiler.public_route(private_route)
    with pytest.raises(RuntimeFault) as error:
        compiler.preconditions(
            Fill(target="nickname_field", value=InputRef(name="nickname")),
            derived(observation(cap, INPUTS)).model_copy(update={"route": private_route}),
        )
    assert error.value.code == FailureCode.POLICY_DENIED


def test_caller_literal_predicate_cannot_declassify_dynamic_control_text():
    cap = artifact()
    embedded = f"PRIVATE_RUNTIME={INPUTS['nickname']}"
    promoted = cap.model_copy(
        update={
            "final_checks": (
                *cap.final_checks,
                Equals(
                    kind="text_equals",
                    target="review_button",
                    value=PublicLiteral(value=embedded),
                ),
            )
        }
    )
    compiler = DeterministicCompiler(
        promoted, ceiling(promoted), dict(INPUTS), reviewed_targets=cap.targets
    )
    assert compiler.project_value(embedded, target="review_button", field="text") is None


class FinishActor:
    """A request-capture test fixture, never a real model or human operator."""

    live = False

    def __init__(self):
        self.call_count = 0
        self.records = []
        self.requests = []

    async def propose(self, request):
        self.call_count += 1
        self.requests.append(request)
        observed = request["observation"]
        return ModelDecision(
            observation_id=observed["observation_id"],
            ownership_epoch=observed["ownership_epoch"],
            kind="finish",
            target_ref=None,
            input_name=None,
            destination=None,
            milliseconds=None,
            reason=None,
        )


@pytest.mark.parametrize("nickname", ["a", "Review", "000042"])
def test_provider_and_evidence_boundary_drops_dynamic_data_even_if_approved(nickname):
    cap = artifact()
    inputs = {**INPUTS, "nickname": nickname}
    embedded = f"PRIVATE_RUNTIME={nickname}"
    trusted = ceiling(cap)
    trusted = trusted.model_copy(
        update={"approved_literals": (*trusted.approved_literals, embedded)}
    )
    observed = derived(observation(cap, inputs)).model_copy(
        update={"visible_text": (embedded, nickname)}
    )
    observed = observed.model_copy(
        update={
            "targets": tuple(
                target.model_copy(update={"text": embedded})
                if target.ref == "review_button"
                else target
                for target in observed.targets
            )
        }
    )
    actor = FinishActor()
    audit = StringIO()
    surface = FakeSurface(BINDING, [observed])
    discovery = Discovery(
        cap,
        Profile(
            profile_id="privacy_fixture",
            binding=BINDING,
            targets=cap.targets,
            terminal_checks=(Visible(target="review"),),
        ),
        Session(surface, trusted, EvidenceSink(audit)),
        actor,
        frozenset({"prepare"}),
    )
    outcome = asyncio.run(discovery.run(inputs))
    assert outcome.result.code == FailureCode.POSTCONDITION_FAILED
    assert actor.call_count == 1 and not surface.actions
    projected = {target["ref"]: target for target in actor.requests[0]["observation"]["targets"]}
    assert projected["nickname"]["text"] == {"kind": "input_ref", "name": "nickname"}
    assert projected["member_id"]["text"] == {"kind": "input_ref", "name": "member_id"}
    assert "text" not in projected["review_button"]
    assert projected["review_button"]["descriptor"]["locator"]["name"] == {
        "kind": "literal",
        "value": "Review",
    }
    assert embedded not in json.dumps(actor.requests) + audit.getvalue()
    assert outcome.artifact is None
