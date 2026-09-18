"""Fake-surface safety regressions; these do not demonstrate live human handoff."""

import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest

from tests.support import INPUTS, artifact, observation, policy
from ui_capability.contracts import (
    ActionProposal,
    Budgets,
    CapabilityArtifact,
    Conjunction,
    CountEquals,
    Equals,
    ExecutableAction,
    FailureCode,
    Finish,
    FrameScope,
    InputRef,
    LabelLocator,
    LocalRef,
    Navigate,
    Observation,
    ObservedTarget,
    Ownership,
    PublicLiteral,
    RetryPolicy,
    RouteMatches,
    Scalar,
    SecretRef,
    Step,
    TableLocator,
    TargetSpec,
    Visible,
)
from ui_capability.errors import RuntimeFault
from ui_capability.evidence import EvidenceSink
from ui_capability.policy import ActionRule, Effect
from ui_capability.session import Session
from ui_capability.surfaces.fake import FakeSurface
from ui_capability.values import bind_target, evaluate, matches, resolve

PERMISSIONS = frozenset({"prepare"})


def setup(
    cap: CapabilityArtifact | None = None,
    observations: list[Observation] | None = None,
    effect: Effect = Effect.READ,
) -> tuple[Session, FakeSurface, CapabilityArtifact, StringIO]:
    cap = artifact() if cap is None else cap
    audit = StringIO()
    surface = FakeSurface(cap.goal.binding, observations or [observation(cap, INPUTS)])
    session = Session(surface, policy(cap, effect), EvidenceSink(audit))
    return session, surface, cap, audit


async def proposal(session: Session, step: Step) -> ActionProposal:
    observed = await session.observe()
    return ActionProposal(
        observation_id=observed.observation_id,
        ownership_epoch=session.epoch,
        action=step.action,
    )


@pytest.mark.parametrize("stale_field", ["observation_id", "ownership_epoch"])
def test_stale_proposal_never_reaches_surface(stale_field: str) -> None:
    async def scenario() -> None:
        session, surface, cap, _ = setup()
        proposed = await proposal(session, cap.steps[0])
        proposed = proposed.model_copy(
            update={stale_field: "old_observation" if stale_field == "observation_id" else 99}
        )
        with pytest.raises(RuntimeFault) as caught:
            await session.execute(proposed, cap, INPUTS, {}, PERMISSIONS)
        assert caught.value.code is FailureCode.STALE_OBSERVATION
        assert surface.actions == []

    asyncio.run(scenario())


def test_two_same_observation_proposals_execute_at_most_once() -> None:
    async def scenario() -> None:
        session, surface, cap, _ = setup()
        proposed = await proposal(session, cap.steps[0])
        results = await asyncio.gather(
            session.execute(proposed, cap, INPUTS, {}, PERMISSIONS),
            session.execute(proposed, cap, INPUTS, {}, PERMISSIONS),
            return_exceptions=True,
        )
        assert results.count(None) == 1
        failures = [item for item in results if isinstance(item, RuntimeFault)]
        assert [failure.code for failure in failures] == [FailureCode.STALE_OBSERVATION]
        assert len(surface.actions) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["run_id", "session_id", "ownership_epoch"])
def test_adapter_cannot_supply_cross_session_or_epoch_observation(field: str) -> None:
    class WrongScope(FakeSurface):
        async def observe(self, run_id: str, session_id: str, epoch: int) -> Observation:
            observed = await super().observe(run_id, session_id, epoch)
            return observed.model_copy(
                update={field: epoch + 1 if field == "ownership_epoch" else "other"}
            )

    async def scenario() -> None:
        cap = artifact()
        surface = WrongScope(cap.goal.binding, [observation(cap, INPUTS)])
        session = Session(surface, policy(cap, Effect.READ), EvidenceSink(StringIO()))
        with pytest.raises(RuntimeFault) as caught:
            await session.observe()
        assert caught.value.code is FailureCode.STALE_OBSERVATION
        assert surface.actions == []

    asyncio.run(scenario())


def test_guard_state_drift_between_proposal_and_execution_is_rejected() -> None:
    async def scenario() -> None:
        cap = artifact()
        session, surface, _, _ = setup(
            cap, [observation(cap, INPUTS), observation(cap, INPUTS, wrong_member=True)]
        )
        proposed = await proposal(session, cap.steps[0])
        with pytest.raises(RuntimeFault) as caught:
            await session.execute(proposed, cap, INPUTS, {}, PERMISSIONS)
        assert caught.value.code is FailureCode.STALE_OBSERVATION
        assert surface.actions == []

    asyncio.run(scenario())


def test_ambiguous_visible_target_and_control_mismatch_never_act() -> None:
    async def scenario() -> None:
        cap = artifact()
        original = observation(cap, INPUTS)
        field = next(target for target in original.targets if target.ref == "nickname_field")
        duplicate = field.model_copy(update={"ref": "other_field"})
        ambiguous = original.model_copy(update={"targets": (*original.targets, duplicate)})
        mismatch = original.model_copy(
            update={
                "targets": tuple(
                    target.model_copy(update={"control": "button"}) if target == field else target
                    for target in original.targets
                )
            }
        )
        for observed, code in (
            (ambiguous, FailureCode.AMBIGUOUS_TARGET),
            (mismatch, FailureCode.TARGET_NOT_FOUND),
        ):
            session, surface, _, _ = setup(cap, [observed])
            proposed = await proposal(session, cap.steps[0])
            with pytest.raises(RuntimeFault) as caught:
                await session.execute(proposed, cap, INPUTS, {}, PERMISSIONS)
            assert caught.value.code is code
            assert surface.actions == []

    asyncio.run(scenario())


def test_finish_proposal_cannot_bypass_deterministic_completion() -> None:
    async def scenario() -> None:
        session, surface, cap, _ = setup()
        proposed = (await proposal(session, cap.steps[0])).model_copy(
            update={"action": Finish(checks=cap.final_checks)}
        )
        with pytest.raises(RuntimeFault) as caught:
            await session.execute(proposed, cap, INPUTS, {}, PERMISSIONS)
        assert caught.value.code is FailureCode.INVALID_ARGUMENT
        assert surface.actions == []

    asyncio.run(scenario())


def test_human_observation_does_not_grant_automation_authority() -> None:
    async def scenario() -> None:
        session, surface, cap, _ = setup()
        await session.pause("session_expired", cap.steps[0])
        await session.takeover()
        proposed = await proposal(session, cap.steps[0])
        with pytest.raises(RuntimeFault) as caught:
            await session.execute(proposed, cap, INPUTS, {}, PERMISSIONS)
        assert caught.value.code is FailureCode.OWNERSHIP_DENIED
        assert surface.actions == []
        assert session.human_intervened is True

    asyncio.run(scenario())


def test_pause_closes_gate_before_settling_inflight_action() -> None:
    async def scenario() -> None:
        session, surface, cap, _ = setup()
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked(
            action: ExecutableAction, target: ObservedTarget | None, value: Scalar | None
        ) -> None:
            started.set()
            await release.wait()

        surface.on_action = blocked
        proposed = await proposal(session, cap.steps[0])
        executing = asyncio.create_task(session.execute(proposed, cap, INPUTS, {}, PERMISSIONS))
        await started.wait()
        pausing = asyncio.create_task(session.pause("session_expired", cap.steps[0]))
        await asyncio.sleep(0)
        assert session.ownership is Ownership.PAUSING
        assert not pausing.done()
        with pytest.raises(RuntimeFault) as takeover:
            await session.takeover()
        assert takeover.value.code is FailureCode.OWNERSHIP_DENIED
        with pytest.raises(RuntimeFault) as action:
            await session.execute(proposed, cap, INPUTS, {}, PERMISSIONS)
        assert action.value.code is FailureCode.OWNERSHIP_DENIED
        release.set()
        await executing
        await pausing
        await session.takeover()
        assert session.ownership is Ownership.HUMAN
        assert len(surface.actions) == 1

    asyncio.run(scenario())


def test_abort_waits_for_inflight_action_without_cancelling_it() -> None:
    async def scenario() -> None:
        session, surface, cap, _ = setup()
        started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def blocked(
            action: ExecutableAction, target: ObservedTarget | None, value: Scalar | None
        ) -> None:
            started.set()
            await release.wait()
            completed.set()

        surface.on_action = blocked
        executing = asyncio.create_task(
            session.execute(await proposal(session, cap.steps[0]), cap, INPUTS, {}, PERMISSIONS)
        )
        await started.wait()
        aborting = asyncio.create_task(session.abort())
        await asyncio.sleep(0)
        assert session.ownership is Ownership.ABORTED
        assert not aborting.done()
        release.set()
        await asyncio.gather(executing, aborting)
        assert completed.is_set()
        with pytest.raises(RuntimeFault) as caught:
            await session.takeover()
        assert caught.value.code is FailureCode.OWNERSHIP_DENIED

    asyncio.run(scenario())


def test_abort_supersedes_pause_waiting_for_inflight_action() -> None:
    async def scenario() -> None:
        session, surface, cap, _ = setup()
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked(
            action: ExecutableAction, target: ObservedTarget | None, value: Scalar | None
        ) -> None:
            started.set()
            await release.wait()

        surface.on_action = blocked
        executing = asyncio.create_task(
            session.execute(await proposal(session, cap.steps[0]), cap, INPUTS, {}, PERMISSIONS)
        )
        await started.wait()
        pausing = asyncio.create_task(session.pause("session_expired", cap.steps[0]))
        await asyncio.sleep(0)
        assert session.ownership is Ownership.PAUSING
        aborting = asyncio.create_task(session.abort())
        await asyncio.sleep(0)
        assert session.ownership is Ownership.ABORTED
        assert not pausing.done()
        assert not aborting.done()
        release.set()
        results = await asyncio.gather(executing, pausing, aborting, return_exceptions=True)
        assert results[0] is None
        assert isinstance(results[1], RuntimeFault)
        assert results[1].code is FailureCode.OWNERSHIP_DENIED
        assert results[2] is None
        assert session.ownership is Ownership.ABORTED
        with pytest.raises(RuntimeFault) as takeover:
            await session.takeover()
        assert takeover.value.code is FailureCode.OWNERSHIP_DENIED
        assert len(surface.actions) == 1

    asyncio.run(scenario())


def test_cancellation_preserves_gate_until_uncertain_action_settles() -> None:
    async def scenario() -> None:
        cap = artifact()
        step = cap.steps[1].model_copy(update={"retry": RetryPolicy()})
        cap = cap.model_copy(update={"steps": (cap.steps[0], step)})
        session, surface, _, _ = setup(cap, effect=Effect.PREVIEW)
        started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def blocked(
            action: ExecutableAction, target: ObservedTarget | None, value: Scalar | None
        ) -> None:
            started.set()
            await release.wait()
            completed.set()

        surface.on_action = blocked
        executing = asyncio.create_task(
            session.execute(await proposal(session, step), cap, INPUTS, {}, PERMISSIONS)
        )
        await started.wait()
        executing.cancel()
        with pytest.raises(RuntimeFault) as caught:
            await executing
        assert caught.value.code is FailureCode.UNKNOWN_ACTION_OUTCOME
        pausing = asyncio.create_task(session.pause("unsupported_state", step))
        await asyncio.sleep(0)
        assert session.ownership is Ownership.PAUSING
        assert not pausing.done()
        release.set()
        await pausing
        assert completed.is_set()
        await session.takeover()
        assert session.ownership is Ownership.HUMAN

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("effect", "code"),
    [
        (Effect.READ, FailureCode.POSTCONDITION_FAILED),
        (Effect.PREVIEW, FailureCode.UNKNOWN_ACTION_OUTCOME),
    ],
)
def test_failed_acknowledgment_is_classified_by_policy_and_never_retried(
    effect: Effect, code: FailureCode
) -> None:
    async def scenario() -> None:
        cap = artifact()
        step = cap.steps[1].model_copy(update={"retry": RetryPolicy()})
        cap = cap.model_copy(update={"steps": (cap.steps[0], step)})
        session, surface, _, audit = setup(cap, effect=effect)
        surface.action_error = ValueError("PRIVATE-ACK-CANARY")
        with pytest.raises(RuntimeFault) as caught:
            await session.execute(await proposal(session, step), cap, INPUTS, {}, PERMISSIONS)
        assert caught.value.code is code
        assert "PRIVATE-ACK-CANARY" not in str(caught.value) + audit.getvalue()
        assert len(surface.actions) == 1

    asyncio.run(scenario())


def test_action_timeout_keeps_the_gate_until_actual_settlement() -> None:
    async def scenario() -> None:
        cap = artifact()
        cap = cap.model_copy(
            update={"goal": cap.goal.model_copy(update={"budgets": Budgets(active_seconds=1)})}
        )
        session, surface, _, _ = setup(cap)
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked(
            action: ExecutableAction, target: ObservedTarget | None, value: Scalar | None
        ) -> None:
            started.set()
            await release.wait()

        surface.on_action = blocked
        executing = asyncio.create_task(
            session.execute(await proposal(session, cap.steps[0]), cap, INPUTS, {}, PERMISSIONS)
        )
        await started.wait()
        with pytest.raises(RuntimeFault) as caught:
            await executing
        assert caught.value.code is FailureCode.BUDGET_EXCEEDED
        pausing = asyncio.create_task(session.pause("unsupported_state", cap.steps[0]))
        await asyncio.sleep(0)
        assert not pausing.done()
        release.set()
        await pausing
        await session.takeover()
        assert len(surface.actions) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(("filled", "decision"), [(False, "retry"), (True, "advance")])
def test_resume_requires_safe_precondition_or_verified_postcondition(
    filled: bool, decision: str
) -> None:
    async def scenario() -> None:
        cap = artifact()
        session, surface, _, _ = setup(cap, [observation(cap, INPUTS, filled=filled)])
        old = await proposal(session, cap.steps[0])
        await session.pause("session_expired", cap.steps[0])
        await session.takeover()
        assert await session.resume(cap, INPUTS, PERMISSIONS, cap.steps[0]) == decision
        assert session.ownership is Ownership.AUTOMATION
        with pytest.raises(RuntimeFault) as caught:
            await session.execute(old, cap, INPUTS, {}, PERMISSIONS)
        assert caught.value.code is FailureCode.STALE_OBSERVATION
        assert surface.actions == []

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["identity", "context", "origin", "route", "checkpoint"])
def test_invalid_resume_preserves_human_ownership(invalid: str, tmp_path: Path) -> None:
    async def scenario() -> None:
        cap = artifact()
        observed = observation(cap, INPUTS, wrong_member=invalid == "identity")
        if invalid == "origin":
            observed = observed.model_copy(update={"origin": "https://outside.invalid"})
        elif invalid == "route":
            observed = observation(cap, INPUTS, filled=True).model_copy(
                update={"route": "/forbidden"}
            )
        elif invalid == "checkpoint":
            observed = observed.model_copy(
                update={
                    "targets": tuple(
                        target for target in observed.targets if target.ref != "nickname_field"
                    )
                }
            )
        session, surface, _, _ = setup(cap, [observed])
        await session.pause("session_expired", cap.steps[0])
        await session.takeover()
        if invalid == "context":
            surface.context_id = "replacement_context"
        with pytest.raises(RuntimeFault) as caught:
            await session.resume(cap, INPUTS, PERMISSIONS, cap.steps[0])
        assert caught.value.code is (
            FailureCode.INCOMPATIBLE if invalid == "context" else FailureCode.INVALID_RESUME
        )
        assert session.ownership is Ownership.HUMAN
        assert caught.value.diagnostic is not None
        assert caught.value.diagnostic.observed in {"mismatch", "denied", "unknown"}
        assert surface.actions == []
        capture = await session.capture_safe(tmp_path / "resume.png")
        structure = capture["structure"]
        if invalid == "context":
            assert structure["state"] == "unavailable"
        else:
            assert structure["target_details_state"] == "current"
            if invalid == "checkpoint":
                target = structure["targets"][sorted(cap.targets).index("nickname_field")]
                assert target["state"] == "missing" and target["match_count"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("tamper", ["other_step", "same_id_new_checkpoint"])
def test_resume_cannot_skip_to_callers_chosen_checkpoint(tamper: str) -> None:
    async def scenario() -> None:
        cap = artifact()
        session, surface, _, _ = setup(cap, [observation(cap, INPUTS, review=True)])
        await session.pause("session_expired", cap.steps[0])
        await session.takeover()
        requested = (
            cap.steps[1]
            if tamper == "other_step"
            else cap.steps[0].model_copy(update={"postconditions": cap.identity_checks})
        )
        with pytest.raises(RuntimeFault) as caught:
            await session.resume(cap, INPUTS, PERMISSIONS, requested)
        assert caught.value.code is FailureCode.INVALID_RESUME
        assert session.ownership is Ownership.HUMAN
        assert surface.actions == []

    asyncio.run(scenario())


@pytest.mark.parametrize("review", [False, True])
def test_resume_may_verify_unsafe_step_but_never_retry_it(review: bool) -> None:
    async def scenario() -> None:
        cap = artifact()
        step = cap.steps[1].model_copy(update={"retry": RetryPolicy()})
        cap = cap.model_copy(update={"steps": (cap.steps[0], step)})
        session, surface, _, _ = setup(
            cap, [observation(cap, INPUTS, review=review)], Effect.PREVIEW
        )
        await session.pause("session_expired", step)
        await session.takeover()
        if review:
            assert await session.resume(cap, INPUTS, PERMISSIONS, step) == "advance"
            assert session.ownership is Ownership.AUTOMATION
        else:
            with pytest.raises(RuntimeFault) as caught:
                await session.resume(cap, INPUTS, PERMISSIONS, step)
            assert caught.value.code is FailureCode.INVALID_RESUME
            assert session.ownership is Ownership.HUMAN
        assert surface.actions == []

    asyncio.run(scenario())


def test_duplicate_resume_requests_do_not_both_gain_ownership() -> None:
    async def scenario() -> None:
        session, _, cap, _ = setup()
        await session.pause("session_expired", cap.steps[0])
        await session.takeover()
        results = await asyncio.gather(
            session.resume(cap, INPUTS, PERMISSIONS, cap.steps[0]),
            session.resume(cap, INPUTS, PERMISSIONS, cap.steps[0]),
            return_exceptions=True,
        )
        assert results.count("retry") == 1
        faults = [item for item in results if isinstance(item, RuntimeFault)]
        assert [fault.code for fault in faults] == [FailureCode.OWNERSHIP_DENIED]
        assert session.ownership is Ownership.AUTOMATION

    asyncio.run(scenario())


@pytest.mark.parametrize("observed_destination", [False, True])
def test_navigation_reference_requires_observed_and_approved_destination(
    observed_destination: bool,
) -> None:
    async def scenario() -> None:
        cap = artifact()
        step = Step(
            id="navigate",
            action=Navigate(destination=InputRef(name="nickname")),
            preconditions=cap.steps[0].preconditions,
            postconditions=(RouteMatches(route=PublicLiteral(value="/review")),),
        )
        cap = cap.model_copy(update={"steps": (step,)})
        trusted = policy(cap, Effect.READ)
        trusted = trusted.model_copy(
            update={
                "approved_literals": (*trusted.approved_literals, "/review"),
                "rules": (
                    *trusted.rules,
                    ActionRule(
                        action="navigate",
                        origin=cap.goal.binding.origin,
                        route="/prepare",
                        destination="/review",
                        permission="prepare",
                        effect=Effect.READ,
                    ),
                ),
            }
        )
        inputs: dict[str, Scalar] = {**INPUTS, "nickname": "/review"}
        observed = observation(cap, inputs).model_copy(
            update={"destinations": ("/review",) if observed_destination else ()}
        )
        surface = FakeSurface(cap.goal.binding, [observed])
        session = Session(surface, trusted, EvidenceSink(StringIO()))
        proposed = await proposal(session, step)
        if observed_destination:
            await session.execute(proposed, cap, inputs, {}, PERMISSIONS)
            assert surface.actions[0][1:] == (None, "/review")
        else:
            with pytest.raises(RuntimeFault) as caught:
                await session.execute(proposed, cap, inputs, {}, PERMISSIONS)
            assert caught.value.code is FailureCode.POLICY_DENIED
            assert surface.actions == []

    asyncio.run(scenario())


def test_selector_bindings_preserve_identifiers_and_require_exact_scope() -> None:
    spec = TargetSpec(
        frame=FrameScope(kind="named_frame", name="workspace"),
        section=InputRef(name="member_id"),
        locator=TableLocator(label=LocalRef(name="caption"), control="input"),
    )
    inputs: dict[str, Scalar] = {"member_id": "000042"}
    locals_: dict[str, Scalar] = {"caption": "Nickname"}
    bound = bind_target(spec, inputs, locals_)
    assert bound.section == PublicLiteral(value="000042")
    assert bound.locator == TableLocator(label=PublicLiteral(value="Nickname"), control="input")
    target = ObservedTarget(
        ref="nickname",
        spec=bound,
        role="textbox",
        control="input",
        visible=True,
        grounding="visible_table_caption",
    )
    wrong = target.model_copy(
        update={
            "ref": "wrong_member",
            "spec": bound.model_copy(update={"section": PublicLiteral(value="42")}),
        }
    )
    hidden = target.model_copy(update={"ref": "hidden", "visible": False})
    cap = artifact()
    observed = observation(cap, INPUTS).model_copy(update={"targets": (wrong, hidden, target)})
    assert matches(spec, observed, inputs, locals_) == (target,)
    with pytest.raises(RuntimeFault) as missing:
        bind_target(spec, inputs, {})
    assert missing.value.code is FailureCode.INVALID_ARGUMENT
    with pytest.raises(RuntimeFault) as secret:
        resolve(SecretRef(name="secret"), {}, {"secret": "PRIVATE-SECRET-CANARY"})
    assert secret.value.code is FailureCode.PERMISSION_DENIED
    assert "PRIVATE-SECRET-CANARY" not in str(secret.value)


def test_predicate_uniqueness_cannot_be_hidden_by_false_conjunction() -> None:
    cap = artifact()
    observed = observation(cap, INPUTS)
    target = next(item for item in observed.targets if item.ref == "member_id")
    observed = observed.model_copy(
        update={"targets": (*observed.targets, target.model_copy(update={"ref": "duplicate"}))}
    )
    assert evaluate(CountEquals(target="member_id", count=2), observed, cap, INPUTS, {}) is True
    conjunction = Conjunction(
        conditions=(
            RouteMatches(route=PublicLiteral(value="/not_here")),
            cap.identity_checks[0],
        )
    )
    for predicate in (Visible(target="member_id"), cap.identity_checks[0], conjunction):
        with pytest.raises(RuntimeFault) as caught:
            evaluate(predicate, observed, cap, INPUTS, {})
        assert caught.value.code is FailureCode.AMBIGUOUS_TARGET


def test_scalar_predicates_do_not_coerce_boolean_integer_or_identifier() -> None:
    cap = artifact()
    observed = observation(cap, INPUTS)
    assert (
        evaluate(
            Equals(kind="value_equals", target="submitted", value=PublicLiteral(value=0)),
            observed,
            cap,
            INPUTS,
            {},
        )
        is False
    )
    assert (
        evaluate(
            Equals(kind="text_equals", target="member_id", value=PublicLiteral(value=42)),
            observed,
            cap,
            INPUTS,
            {},
        )
        is False
    )
    with pytest.raises(RuntimeFault) as caught:
        bind_target(
            TargetSpec(
                frame=FrameScope(kind="main"),
                locator=LabelLocator(label=InputRef(name="member_id")),
            ),
            {"member_id": 42},
            {},
        )
    assert caught.value.code is FailureCode.INVALID_ARGUMENT


def test_authorized_action_evidence_explains_purpose_without_execution_values() -> None:
    async def scenario() -> None:
        session, surface, cap, audit = setup()
        await session.execute(
            await proposal(session, cap.steps[0]),
            cap,
            INPUTS,
            {},
            PERMISSIONS,
            purpose="recovery",
            step_index=0,
            guard_index=2,
        )
        events = [json.loads(line) for line in audit.getvalue().splitlines()]
        intent = next(event for event in events if event["event"] == "action_intent")
        assert intent["purpose"] == "recovery"
        assert intent["action_kind"] == "fill" and intent["effect"] == "SAFE_OVERWRITE"
        assert intent["action_index"] == 0 and intent["step_index"] == 0
        assert intent["guard_index"] == 2
        assert intent["target_index"] == sorted(cap.targets).index("nickname_field")
        assert surface.actions[0][2] == INPUTS["nickname"]
        assert INPUTS["nickname"] not in audit.getvalue()
        assert "nickname_field" not in audit.getvalue()

    asyncio.run(scenario())


@pytest.mark.parametrize("rejection", ["missing", "ambiguous", "control", "role", "policy"])
def test_rejected_action_exposes_safe_observed_reason_not_authorization(
    rejection: str,
) -> None:
    async def scenario() -> None:
        cap = artifact()
        original = observation(cap, INPUTS)
        field = next(target for target in original.targets if target.ref == "nickname_field")
        canary = "PRIVATE-ROLE-CONTROL-VALUE"
        if rejection == "missing":
            targets = tuple(target for target in original.targets if target != field)
        elif rejection == "ambiguous":
            targets = (*original.targets, field.model_copy(update={"ref": canary}))
        else:
            replacement = field.model_copy(
                update={
                    **({rejection: canary} if rejection in {"role", "control"} else {}),
                    "text": canary,
                    "value": canary,
                }
            )
            targets = tuple(
                replacement if target == field else target for target in original.targets
            )
        observed = original.model_copy(update={"targets": targets})
        session, surface, _, audit = setup(cap, [observed])
        with pytest.raises(RuntimeFault) as caught:
            await session.execute(
                await proposal(session, cap.steps[0]),
                cap,
                INPUTS,
                {},
                frozenset() if rejection == "policy" else PERMISSIONS,
                purpose="replay",
                step_index=0,
            )
        diagnostic = caught.value.diagnostic
        assert diagnostic is not None
        assert diagnostic.target_index == sorted(cap.targets).index("nickname_field")
        if rejection in {"missing", "ambiguous"}:
            assert diagnostic.expected == "unique_target"
            assert diagnostic.observed == rejection
            assert diagnostic.match_count == (0 if rejection == "missing" else 2)
        elif rejection in {"control", "role"}:
            assert diagnostic.expected == "supported_control"
            assert diagnostic.observed == "mismatch"
        else:
            assert diagnostic.expected == "authorized_action"
            assert diagnostic.observed == "denied"
        events = [json.loads(line) for line in audit.getvalue().splitlines()]
        assert all(event["event"] != "action_intent" for event in events)
        rejected = next(event for event in events if event["event"] == "action_rejected")
        assert "effect" not in rejected and "action_index" not in rejected
        assert rejected["purpose"] == "replay"
        assert rejected["diagnostic"]["observed"] == diagnostic.observed
        assert canary not in audit.getvalue() + str(caught.value)
        assert surface.actions == []

    asyncio.run(scenario())


def test_stale_revalidation_capture_describes_actual_last_observation(tmp_path: Path) -> None:
    async def scenario() -> None:
        cap = artifact()
        original = observation(cap, INPUTS)
        changed = original.model_copy(
            update={
                "targets": tuple(
                    target for target in original.targets if target.ref != "nickname_field"
                ),
            }
        )
        session, surface, _, audit = setup(cap, [original, changed])
        with pytest.raises(RuntimeFault) as caught:
            await session.execute(
                await proposal(session, cap.steps[0]), cap, INPUTS, {}, PERMISSIONS
            )
        assert caught.value.code is FailureCode.STALE_OBSERVATION
        assert caught.value.diagnostic.expected == "fresh_observation"
        assert caught.value.diagnostic.observed == "stale"
        capture = await session.capture_safe(tmp_path / "capture.png")
        structure = capture["structure"]
        field = structure["targets"][sorted(cap.targets).index("nickname_field")]
        assert structure["target_details_state"] == "current"
        assert field["state"] == "missing" and field["match_count"] == 0
        assert surface.actions == []
        events = [json.loads(line) for line in audit.getvalue().splitlines()]
        assert all("step_index" not in event for event in events)

    asyncio.run(scenario())
