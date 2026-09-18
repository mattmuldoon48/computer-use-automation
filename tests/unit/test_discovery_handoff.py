"""Synthetic planner/operator actors; these are not live discovery or human evidence."""

import asyncio
import json
from io import StringIO

import pytest

from tests.support import BINDING, INPUTS, artifact, observation, policy
from tests.unit.test_discovery import Actor, derived
from ui_capability.contracts import (
    ActionProposal,
    Click,
    Failure,
    FailureCode,
    Fill,
    InputRef,
    Intervention,
    Ownership,
    Success,
    Visible,
)
from ui_capability.discovery import Discovery
from ui_capability.errors import RuntimeFault
from ui_capability.evidence import EvidenceSink
from ui_capability.profiles import DiscoveryCheckpoint, Guard, Profile
from ui_capability.replay import Replay
from ui_capability.session import Session
from ui_capability.surfaces.fake import FakeSurface


def handoff_rig(
    *,
    expire=False,
    operator_seconds=30,
    active_seconds=180,
    actor=None,
    action_hook=None,
    reviewed=True,
):
    cap = artifact()
    cap = cap.model_copy(
        update={
            "goal": cap.goal.model_copy(
                update={
                    "budgets": cap.goal.budgets.model_copy(
                        update={
                            "operator_seconds": operator_seconds,
                            "active_seconds": active_seconds,
                        }
                    )
                }
            )
        }
    )
    ceiling = policy(cap)
    ceiling = ceiling.model_copy(
        update={"approved_literals": ceiling.approved_literals + (BINDING.entry_route,)}
    )
    audit = StringIO()
    surface = FakeSurface(BINDING, [derived(observation(cap, INPUTS))])
    filled = False

    async def acted(action, target, value):
        nonlocal filled
        if action_hook is not None:
            await action_hook(action, target, value)
        filled = filled or isinstance(action, Fill)
        surface.observations[:] = [
            derived(
                observation(
                    cap,
                    INPUTS,
                    filled=filled,
                    review=isinstance(action, Click) and not expire,
                    extra=("expired",) if isinstance(action, Click) and expire else (),
                )
            )
        ]

    surface.on_action = acted
    paused = False

    def decide(request):
        nonlocal paused
        if not expire and not paused:
            paused = True
            return {"kind": "intervene", "reason": "unsupported_state"}
        prior = request["prior_action_kinds"]
        if "fill" not in prior:
            return {"kind": "fill", "target_ref": "nickname_field", "input_name": "nickname"}
        if "click" not in prior:
            return {"kind": "click", "target_ref": "review_button"}
        return {}

    actor = actor or Actor(decide)
    profile = Profile(
        profile_id="handoff_fixture",
        binding=BINDING,
        targets=cap.targets,
        terminal_checks=(Visible(target="review"),),
        guards=(
            Guard(
                id="expired",
                kind="intervention",
                when=Visible(target="expired"),
                reason="session_expired",
            ),
        ),
        discovery_checkpoints=(
            DiscoveryCheckpoint(
                id="prepare_restored", restored=(Visible(target="nickname_field"),)
            ),
            DiscoveryCheckpoint(
                id="review_restored",
                action=Click(target="review_button"),
                before=(Visible(target="nickname_field"),),
                restored=(Visible(target="review"),),
                reason="session_expired",
            ),
        )
        if reviewed
        else (),
    )
    session = Session(surface, ceiling, EvidenceSink(audit))
    discovery = Discovery(cap, profile, session, actor, frozenset({"prepare"}))
    return discovery, session, surface, actor, cap, profile, audit


def test_planner_pause_preserves_context_denies_human_automation_and_reobserves():
    async def scenario():
        discovery, session, surface, actor, cap, _, _ = handoff_rig()
        outcome = await discovery.run(dict(INPUTS))
        assert isinstance(outcome.result, Intervention)
        assert outcome.artifact is None and not surface.actions
        old_request = actor.requests[-1]["observation"]
        context = surface.context_id
        await session.takeover()
        current = await session.observe()
        with pytest.raises(RuntimeFault) as denied:
            await session.execute(
                ActionProposal(
                    observation_id=current.observation_id,
                    ownership_epoch=current.ownership_epoch,
                    action=Fill(target="nickname_field", value=InputRef(name="nickname")),
                ),
                cap,
                dict(INPUTS),
                {},
                frozenset({"prepare"}),
            )
        assert denied.value.code == FailureCode.OWNERSHIP_DENIED
        with pytest.raises(RuntimeFault):
            await discovery.run(dict(INPUTS))
        assert session.ownership == Ownership.HUMAN and len(actor.requests) == 1
        outcome = await discovery.resume()
        assert isinstance(outcome.result, Success), outcome.result
        assert surface.context_id == context and outcome.result.metadata.human_intervened
        assert outcome.artifact.provenance.kind == "test_fixture"
        assistance = outcome.artifact.provenance.assistance
        assert assistance.checkpoints == ("prepare_restored",)
        assert assistance.replay_status == "candidate_unverified"
        assert assistance.dependency == "same_session_operator_restoration"
        new_request = actor.requests[1]["observation"]
        assert new_request["ownership_epoch"] > old_request["ownership_epoch"]
        assert new_request["observation_id"] != old_request["observation_id"]
        assert [step.action.kind for step in outcome.artifact.steps] == ["fill", "click"]

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["not_taken_over", "identity", "context", "form_change"])
def test_invalid_resume_keeps_checkpoint_and_human_control(invalid):
    async def scenario():
        discovery, session, surface, actor, cap, _, _ = handoff_rig()
        outcome = await discovery.run(dict(INPUTS))
        assert isinstance(outcome.result, Intervention)
        context = surface.context_id
        original = surface.observations[0]
        if invalid != "not_taken_over":
            await session.takeover()
        if invalid == "identity":
            surface.observations[:] = [derived(observation(cap, INPUTS, wrong_member=True))]
        elif invalid == "context":
            surface.context_id = "different_browser_context"
        elif invalid == "form_change":
            surface.observations[:] = [derived(observation(cap, INPUTS, filled=True))]
        with pytest.raises(RuntimeFault):
            await discovery.resume()
        expected = Ownership.AWAITING_OPERATOR if invalid == "not_taken_over" else Ownership.HUMAN
        assert session.ownership == expected
        assert len(actor.requests) == 1 and not surface.actions
        surface.context_id = context
        surface.observations[:] = [original]
        if invalid == "not_taken_over":
            await session.takeover()
        assert isinstance((await discovery.resume()).result, Success)

    asyncio.run(scenario())


def test_expiry_guard_retains_executed_action_and_its_actual_trace_index():
    async def scenario():
        discovery, session, surface, actor, cap, _, audit = handoff_rig(expire=True)
        initial = await session.observe()
        await session.execute(
            ActionProposal(
                observation_id=initial.observation_id,
                ownership_epoch=initial.ownership_epoch,
                action=Fill(target="nickname_field", value=InputRef(name="nickname")),
            ),
            cap,
            dict(INPUTS),
            {},
            frozenset({"prepare"}),
        )
        outcome = await discovery.run(dict(INPUTS))
        assert isinstance(outcome.result, Intervention)
        assert outcome.result.reason == "session_expired"
        assert actor.call_count == 2
        assert [action.kind for action, _, _ in surface.actions] == ["fill", "fill", "click"]
        await session.takeover()
        with pytest.raises(RuntimeFault) as invalid:
            await discovery.resume()
        assert invalid.value.code == FailureCode.INVALID_RESUME
        assert session.ownership == Ownership.HUMAN
        surface.observations[:] = [derived(observation(cap, INPUTS, filled=True, review=True))]
        outcome = await discovery.resume()
        assert isinstance(outcome.result, Success), outcome.result
        assert [step.action.kind for step in outcome.artifact.steps] == ["fill", "click"]
        assert len(surface.actions) == 3 and actor.call_count == 3
        assert actor.requests[-1]["prior_action_kinds"] == ["fill", "click"]
        verified = [
            json.loads(line)
            for line in audit.getvalue().splitlines()
            if json.loads(line)["event"] == "action_verified"
        ]
        assert [(event["action_index"], event["step_index"]) for event in verified] == [
            (1, 0),
            (2, 1),
        ]

    asyncio.run(scenario())


def test_stale_pre_handoff_proposal_is_rejected_after_resume():
    async def scenario():
        old = None

        def decide(request):
            nonlocal old
            if old is None:
                old = request["observation"]
                return {"kind": "intervene", "reason": "unsupported_state"}
            return {
                "kind": "click",
                "target_ref": "review_button",
                "observation_id": old["observation_id"],
                "ownership_epoch": old["ownership_epoch"],
            }

        discovery, session, surface, _, _, _, _ = handoff_rig(actor=Actor(decide))
        assert isinstance((await discovery.run(dict(INPUTS))).result, Intervention)
        await session.takeover()
        outcome = await discovery.resume()
        assert outcome.result.code == FailureCode.STALE_OBSERVATION
        assert session.ownership == Ownership.ABORTED and not surface.actions

    asyncio.run(scenario())


def test_operator_timeout_revokes_without_waiting_for_resume():
    async def scenario():
        discovery, session, surface, actor, _, _, _ = handoff_rig(operator_seconds=1)
        revoked = asyncio.Event()
        set_ownership = surface.set_ownership

        def observe_ownership(owner):
            set_ownership(owner)
            if owner == Ownership.ABORTED:
                revoked.set()

        surface.set_ownership = observe_ownership
        assert isinstance((await discovery.run(dict(INPUTS))).result, Intervention)
        await session.takeover()
        await asyncio.wait_for(revoked.wait(), timeout=3)
        outcome = await discovery.resume()
        assert outcome.result.code == FailureCode.BUDGET_EXCEEDED
        assert outcome.artifact is None and not surface.actions and actor.call_count == 1

    asyncio.run(scenario())


def test_explicit_abort_is_terminal_during_operator_pause():
    async def scenario():
        discovery, session, surface, _, _, _, _ = handoff_rig()
        assert isinstance((await discovery.run(dict(INPUTS))).result, Intervention)
        await session.takeover()
        outcome = await discovery.abort()
        assert isinstance(outcome.result, Failure)
        assert outcome.result.code == FailureCode.INTERRUPTED
        assert outcome.artifact is None and session.ownership == Ownership.ABORTED
        assert (await discovery.resume()).result == outcome.result and not surface.actions

    asyncio.run(scenario())


def test_pause_waits_for_action_settlement_before_takeover():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def block(action, target, value):
            entered.set()
            await release.wait()

        _, session, surface, _, cap, profile, _ = handoff_rig(action_hook=block)
        current = await session.observe()
        action = asyncio.create_task(
            session.execute(
                ActionProposal(
                    observation_id=current.observation_id,
                    ownership_epoch=current.ownership_epoch,
                    action=Fill(target="nickname_field", value=InputRef(name="nickname")),
                ),
                cap,
                dict(INPUTS),
                {},
                frozenset({"prepare"}),
            )
        )
        await entered.wait()
        paused = asyncio.create_task(
            session.pause_discovery("session_expired", profile.discovery_checkpoints[1], None)
        )
        await asyncio.sleep(0)
        assert session.ownership == Ownership.PAUSING and not paused.done()
        with pytest.raises(RuntimeFault):
            await session.takeover()
        release.set()
        await action
        await paused
        await session.takeover()
        assert session.ownership == Ownership.HUMAN and len(surface.actions) == 1
        await session.abort()

    asyncio.run(scenario())


def test_cancelled_discovery_waits_for_unsettled_action_before_terminal_abort():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def block(action, target, value):
            entered.set()
            await release.wait()

        actor = Actor(
            lambda request: {
                "kind": "fill",
                "target_ref": "nickname_field",
                "input_name": "nickname",
            }
        )
        discovery, session, surface, _, _, _, _ = handoff_rig(actor=actor, action_hook=block)
        running = asyncio.create_task(discovery.run(dict(INPUTS)))
        await entered.wait()
        running.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not running.done()
        with pytest.raises(RuntimeFault):
            await session.takeover()
        release.set()
        outcome = await running
        assert outcome.result.code == FailureCode.INTERRUPTED
        assert session.ownership == Ownership.ABORTED
        assert len(surface.actions) == 1 and outcome.artifact is None

    asyncio.run(scenario())


def test_authored_initial_checkpoint_supports_planner_help_without_profile_extensions():
    async def scenario():
        discovery, session, surface, _, cap, _, _ = handoff_rig(reviewed=False)
        outcome = await discovery.run(dict(INPUTS))
        assert isinstance(outcome.result, Intervention)
        assert outcome.result.step == "initial_restored"
        await session.takeover()
        surface.observations[:] = [derived(observation(cap, INPUTS, filled=True))]
        with pytest.raises(RuntimeFault):
            await discovery.resume()
        assert session.ownership == Ownership.HUMAN
        surface.observations[:] = [derived(observation(cap, INPUTS))]
        outcome = await discovery.resume()
        assert isinstance(outcome.result, Success)
        assert outcome.artifact is None

    asyncio.run(scenario())


def test_active_budget_is_cumulative_across_operator_pause():
    class SlowActor(Actor):
        async def propose(self, request):
            await asyncio.sleep(0.6)
            return await super().propose(request)

    async def scenario():
        actor = SlowActor(lambda request: {"kind": "intervene", "reason": "unsupported_state"})
        discovery, session, surface, _, _, _, _ = handoff_rig(actor=actor, active_seconds=1)
        assert isinstance((await discovery.run(dict(INPUTS))).result, Intervention)
        await session.takeover()
        outcome = await discovery.resume()
        assert outcome.result.code == FailureCode.BUDGET_EXCEEDED
        assert outcome.artifact is None and session.ownership == Ownership.ABORTED
        assert not surface.actions

    asyncio.run(scenario())


def test_cancellation_during_resume_validation_revokes_human_session():
    async def scenario():
        discovery, session, surface, _, _, _, _ = handoff_rig()
        assert isinstance((await discovery.run(dict(INPUTS))).result, Intervention)
        await session.takeover()
        entered = asyncio.Event()
        original_observe = surface.observe

        async def blocking_observe(run_id, session_id, epoch):
            entered.set()
            await asyncio.Event().wait()
            return await original_observe(run_id, session_id, epoch)

        surface.observe = blocking_observe
        resuming = asyncio.create_task(discovery.resume())
        await entered.wait()
        resuming.cancel()
        with pytest.raises(asyncio.CancelledError):
            await resuming
        assert session.ownership == Ownership.ABORTED
        assert (await discovery.abort()).result.code == FailureCode.INTERRUPTED

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["missing_profile_dependency", "changed_pending_checkpoint"])
def test_assisted_candidate_requires_its_reviewed_replay_dependency(mutation):
    async def scenario():
        discovery, session, surface, _, cap, profile, _ = handoff_rig(expire=True)
        assert isinstance((await discovery.run(dict(INPUTS))).result, Intervention)
        await session.takeover()
        surface.observations[:] = [derived(observation(cap, INPUTS, filled=True, review=True))]
        outcome = await discovery.resume()
        assert isinstance(outcome.result, Success)
        candidate = outcome.artifact
        if mutation == "missing_profile_dependency":
            profile = profile.model_copy(update={"discovery_checkpoints": ()})
        else:
            checkpoint = profile.discovery_checkpoints[1].model_copy(
                update={"restored": (Visible(target="nickname_field"),)}
            )
            profile = profile.model_copy(
                update={"discovery_checkpoints": (profile.discovery_checkpoints[0], checkpoint)}
            )
        replay_surface = FakeSurface(BINDING, [observation(cap, INPUTS)])
        replay_session = Session(replay_surface, policy(cap), EvidenceSink(StringIO()))
        replay = Replay(candidate, profile, replay_session, frozenset({"prepare"}))
        result = await replay.run(dict(INPUTS))
        assert result.code == FailureCode.INVALID_ARTIFACT
        assert not replay_surface.actions

    asyncio.run(scenario())
