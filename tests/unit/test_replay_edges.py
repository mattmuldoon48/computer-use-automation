import asyncio

from tests.support import BINDING, INPUTS, artifact, observation, rig
from ui_capability.contracts import (
    Budgets,
    Click,
    FailureCode,
    Intervention,
    Ownership,
    RetryPolicy,
    Success,
    Visible,
)
from ui_capability.profiles import Guard, Profile
from ui_capability.replay import money_minor


def test_declared_unsafe_retry_is_rejected_before_any_action():
    cap = artifact()
    unsafe = cap.steps[1].model_copy(update={"retry": RetryPolicy(max_retries=1)})
    r = rig(cap=cap.model_copy(update={"steps": (cap.steps[0], unsafe)}))
    result = asyncio.run(r.replay.run(dict(INPUTS)))
    assert result.code == FailureCode.POLICY_DENIED
    assert not r.surface.actions


def test_known_recovery_returns_to_verified_checkpoint_and_completes():
    cap = artifact().model_copy(update={"recovery_rules": ("dismiss_notice",)})
    profile = Profile(
        profile_id="member_ops",
        binding=BINDING,
        targets=artifact().targets,
        terminal_checks=(Visible(target="review"),),
        guards=(
            Guard(
                id="dismiss_notice",
                kind="recovery",
                when=Visible(target="interstitial"),
                action=Click(target="dismiss"),
                checkpoint=(Visible(target="nickname_field"),),
            ),
        ),
    )
    r = rig(cap=cap, profile=profile)
    r.surface.observations[:] = [observation(cap, INPUTS, extra=("interstitial", "dismiss"))]
    result = asyncio.run(r.replay.run(dict(INPUTS)))
    assert isinstance(result, Success)
    assert [(action.kind, action.target) for action, _, _ in r.surface.actions] == [
        ("click", "dismiss"),
        ("fill", "nickname_field"),
        ("click", "review_button"),
    ]


def test_unknown_dialog_pauses_without_accepting_or_clicking():
    r = rig()
    r.surface.observations[:] = [
        observation(r.cap, INPUTS).model_copy(update={"dialog": "unknown"})
    ]
    result = asyncio.run(r.replay.run(dict(INPUTS)))
    assert isinstance(result, Intervention)
    assert result.reason == "unknown_dialog"
    assert not r.surface.actions


def test_observation_timeout_is_bounded():
    cap = artifact()
    cap = cap.model_copy(
        update={"goal": cap.goal.model_copy(update={"budgets": Budgets(active_seconds=1)})}
    )
    r = rig(cap=cap)
    r.surface.observe_delay = 5
    result = asyncio.run(r.replay.run(dict(INPUTS)))
    assert result.code == FailureCode.BUDGET_EXCEEDED
    assert not r.surface.actions


def test_operator_deadline_expires_without_resuming(monkeypatch):
    async def scenario():
        r = rig()
        r.surface.observations[:] = [
            observation(r.cap, INPUTS).model_copy(update={"dialog": "unknown"})
        ]
        result = await r.replay.run(dict(INPUTS))
        assert isinstance(result, Intervention)
        await r.session.takeover()
        # Advance the deadline clock only; do not sleep through an operator timeout.
        import ui_capability.replay as module

        now = module.time.monotonic()
        monkeypatch.setattr(module.time, "monotonic", lambda: now + 301)
        result = await r.replay.resume()
        assert result.code == FailureCode.BUDGET_EXCEEDED
        assert r.session.ownership == Ownership.ABORTED
        assert not r.surface.actions

    asyncio.run(scenario())


def test_decimal_context_does_not_round_large_displayed_values():
    assert money_minor("1234567890123456789012345678.90", "en_US") == 123456789012345678901234567890


def test_artifact_cannot_weaken_trusted_terminal_business_condition():
    cap = artifact()
    weak = (Visible(target="nickname_field"),)
    last = cap.steps[-1].model_copy(update={"postconditions": weak})
    cap = cap.model_copy(update={"steps": (cap.steps[0], last), "final_checks": weak})
    r = rig(cap=cap)
    original = r.surface.on_action

    async def never_reach_review(action, target, value):
        if action.kind == "fill":
            await original(action, target, value)

    r.surface.on_action = never_reach_review
    result = asyncio.run(r.replay.run(dict(INPUTS)))
    assert result.code == FailureCode.POSTCONDITION_FAILED
    assert result.kind == "failure"


def test_artifact_cannot_rebind_trusted_review_target():
    cap = artifact()
    cap = cap.model_copy(update={"targets": {**cap.targets, "review": cap.targets["member_id"]}})
    r = rig(cap=cap)
    result = asyncio.run(r.replay.run(dict(INPUTS)))
    assert result.code == FailureCode.INCOMPATIBLE
    assert not r.surface.actions
