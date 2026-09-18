"""Safety failures are observable independently of any successful fixture recipe."""

import asyncio
import json
from io import StringIO

import pytest

from tests.support import BINDING, INPUTS, artifact, observation, policy
from ui_capability.compiler import DeterministicCompiler
from ui_capability.contracts import (
    Click,
    FailureCode,
    FrameScope,
    InputRef,
    ObservedTarget,
    PublicLiteral,
    RoleLocator,
    TargetSpec,
    Visible,
)
from ui_capability.discovery import Discovery
from ui_capability.errors import RuntimeFault
from ui_capability.evidence import EvidenceSink
from ui_capability.profiles import Guard, Profile
from ui_capability.provider import ModelDecision, ProviderError
from ui_capability.session import Session
from ui_capability.surfaces.fake import FakeSurface
from ui_capability.values import bind_target


class Actor:
    live = False

    def __init__(self, decide):
        self.decide = decide
        self.call_count = 0
        self.records = []
        self.requests = []

    async def propose(self, request):
        self.call_count += 1
        self.requests.append(request)
        observed = request["observation"]
        payload = {
            "observation_id": observed["observation_id"],
            "ownership_epoch": observed["ownership_epoch"],
            "kind": "finish",
            "target_ref": None,
            "input_name": None,
            "destination": None,
            "milliseconds": None,
            "reason": None,
        }
        payload.update(self.decide(request))
        return ModelDecision(**payload)


def derived(observed):
    return observed.model_copy(
        update={
            "targets": tuple(
                target.model_copy(update={"derived": True}) for target in observed.targets
            )
        }
    )


def setup(actor, *, observed=None, guards=(), update=None):
    cap = artifact()
    ceiling = policy(cap)
    ceiling = ceiling.model_copy(
        update={"approved_literals": ceiling.approved_literals + (BINDING.entry_route,)}
    )
    surface = FakeSurface(
        BINDING, [observed or derived(observation(cap, INPUTS))], on_action=update
    )
    session = Session(surface, ceiling, EvidenceSink(StringIO()))
    profile = Profile(
        profile_id="member_ops",
        binding=BINDING,
        targets=cap.targets,
        terminal_checks=(Visible(target="review"),),
        guards=guards,
    )
    return Discovery(cap, profile, session, actor, frozenset({"prepare"})), surface


def test_stale_epoch_cannot_reach_action_gate():
    actor = Actor(
        lambda request: {"kind": "click", "target_ref": "review_button", "ownership_epoch": 99}
    )
    discovery, surface = setup(actor)
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert outcome.result.code == FailureCode.STALE_OBSERVATION
    assert outcome.artifact is None and not surface.actions
    assert outcome.result.metadata.provider_call_count == 0
    assert actor.call_count == 1


def test_ref_not_in_current_observation_is_not_a_durable_selector():
    actor = Actor(lambda request: {"kind": "click", "target_ref": "obsolete_ref"})
    discovery, surface = setup(actor)
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert outcome.result.code == FailureCode.TARGET_NOT_FOUND
    assert outcome.artifact is None and not surface.actions


def test_finish_does_not_bypass_terminal_verification():
    actor = Actor(lambda request: {})
    discovery, surface = setup(actor)
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert outcome.result.code == FailureCode.POSTCONDITION_FAILED
    assert outcome.artifact is None and not surface.actions


def test_unobserved_authored_target_cannot_be_selected():
    actor = Actor(lambda request: {"kind": "click", "target_ref": "review_button"})
    discovery, surface = setup(actor, observed=observation(artifact(), INPUTS))
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert outcome.result.code == FailureCode.TARGET_NOT_FOUND
    assert actor.requests[0]["observation"]["targets"] == []
    assert outcome.artifact is None and not surface.actions


def test_duplicate_derived_target_fails_before_provider_or_action():
    before = derived(observation(artifact(), INPUTS))
    duplicate = next(target for target in before.targets if target.ref == "review_button")
    before = before.model_copy(
        update={"targets": (*before.targets, duplicate.model_copy(update={"ref": "duplicate"}))}
    )
    actor = Actor(lambda request: {"kind": "click", "target_ref": "review_button"})
    discovery, surface = setup(actor, observed=before)
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert outcome.result.code == FailureCode.AMBIGUOUS_TARGET
    assert outcome.artifact is None and not surface.actions
    assert actor.call_count == 0


def test_click_return_without_semantic_change_cannot_compile():
    actor = Actor(lambda request: {"kind": "click", "target_ref": "review_button"})
    discovery, surface = setup(actor)
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert outcome.result.code == FailureCode.POSTCONDITION_FAILED
    assert outcome.artifact is None
    assert [action.kind for action, _, _ in surface.actions] == ["click"]


def test_unknown_dialog_preempts_even_finish_and_never_calls_provider():
    before = derived(observation(artifact(), INPUTS, review=True)).model_copy(
        update={"dialog": "unknown"}
    )
    actor = Actor(lambda request: {})
    discovery, surface = setup(actor, observed=before)
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert outcome.result.code == FailureCode.INTERRUPTED
    assert outcome.artifact is None and not surface.actions
    assert actor.call_count == 0


def test_recovery_is_not_silently_executed_outside_compiled_trace():
    guard = Guard(
        id="dismiss_banner",
        kind="recovery",
        when=Visible(target="interstitial"),
        action=Click(target="dismiss"),
        checkpoint=(Visible(target="nickname_field"),),
    )
    before = derived(observation(artifact(), INPUTS, extra=("interstitial", "dismiss")))
    actor = Actor(lambda request: {})
    discovery, surface = setup(actor, observed=before, guards=(guard,))
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert outcome.result.code == FailureCode.RECOVERY_EXHAUSTED
    assert outcome.artifact is None and not surface.actions
    assert actor.call_count == 0


def test_public_projection_never_sends_restricted_or_unknown_visible_text():
    before = derived(observation(artifact(), INPUTS)).model_copy(
        update={
            "visible_text": (
                "private-account-canary",
                str(INPUTS["member_id"]),
                str(INPUTS["nickname"]),
            )
        }
    )
    actor = Actor(lambda request: {})
    discovery, _ = setup(actor, observed=before)
    asyncio.run(discovery.run(dict(INPUTS)))
    request = json.dumps(actor.requests[0])
    assert INPUTS["member_id"] not in request and INPUTS["nickname"] not in request
    assert "private-account-canary" not in request
    assert {"kind": "input_ref", "name": "member_id"} in actor.requests[0]["observation"][
        "visible_text"
    ]


@pytest.mark.parametrize("field", ["role", "control"])
def test_provider_projection_excludes_unreviewed_dom_metadata(field):
    before = derived(observation(artifact(), INPUTS))
    before = before.model_copy(
        update={
            "targets": tuple(
                target.model_copy(update={field: "private_dom_metadata_canary"})
                for target in before.targets
            )
        }
    )
    actor = Actor(lambda request: {})
    discovery, surface = setup(actor, observed=before)
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert actor.requests
    assert "private_dom_metadata_canary" not in json.dumps(actor.requests)
    assert outcome.artifact is None
    assert not surface.actions


def test_second_malformed_decision_is_terminal_without_raw_error_repair():
    class Malformed(Actor):
        async def propose(self, request):
            self.call_count += 1
            self.requests.append(request)
            raise ProviderError("malformed_output")

    actor = Malformed(None)
    discovery, surface = setup(actor)
    outcome = asyncio.run(discovery.run(dict(INPUTS)))
    assert outcome.result.code == FailureCode.INTERRUPTED
    assert outcome.result.metadata.provider_call_count == 0
    assert actor.call_count == 2
    assert outcome.artifact is None and not surface.actions


def test_structural_generalization_preserves_input_identity_when_values_collide():
    cap = artifact()
    spec = TargetSpec(
        frame=FrameScope(kind="named_frame", name="workspace"),
        locator=RoleLocator(role="link", name=InputRef(name="member_id")),
    )
    cap = cap.model_copy(update={"targets": {**cap.targets, "member_link": spec}})
    inputs = {**INPUTS, "nickname": INPUTS["member_id"]}
    compiler = DeterministicCompiler(cap, policy(cap), inputs)
    target = ObservedTarget(
        ref="visible_link",
        spec=bind_target(spec, inputs, {}),
        role="link",
        control="link",
        visible=True,
        derived=True,
        grounding="visible_role",
    )
    before = observation(cap, inputs).model_copy(update={"targets": (target,)})
    assert compiler.target_name(target, before) == "member_link"
    assert compiler.template.targets["member_link"].locator.name == InputRef(name="member_id")


def test_sensitive_literal_target_cannot_be_published_even_if_caller_approved_it():
    cap = artifact()
    sensitive = TargetSpec(
        frame=FrameScope(kind="main"),
        locator=RoleLocator(role="link", name=PublicLiteral(value=INPUTS["member_id"])),
    )
    cap = cap.model_copy(update={"targets": {**cap.targets, "unsafe_member_link": sensitive}})
    with pytest.raises(RuntimeFault) as error:
        DeterministicCompiler(cap, policy(cap), dict(INPUTS))
    assert error.value.code == FailureCode.POLICY_DENIED


def test_coincidental_input_text_is_not_a_reusable_click_checkpoint():
    cap = artifact()
    ceiling = policy(cap)
    ceiling = ceiling.model_copy(
        update={"approved_literals": ceiling.approved_literals + (BINDING.entry_route,)}
    )
    compiler = DeterministicCompiler(cap, ceiling, dict(INPUTS))
    before = derived(observation(cap, INPUTS))
    after = before.model_copy(
        update={
            "targets": tuple(
                target.model_copy(update={"text": INPUTS["nickname"]})
                if target.ref == "review_button"
                else target
                for target in before.targets
            )
        }
    )
    with pytest.raises(RuntimeFault) as error:
        compiler.record(Click(target="review_button"), before, after)
    assert error.value.code == FailureCode.POSTCONDITION_FAILED
