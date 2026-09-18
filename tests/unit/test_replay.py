import asyncio
import builtins

import pytest

from tests.support import BINDING, INPUTS, artifact, observation, rig
from ui_capability.contracts import (
    Budgets,
    CountEquals,
    FailureCode,
    Fill,
    Intervention,
    Ownership,
    RetryPolicy,
    Success,
    Visible,
)
from ui_capability.errors import RuntimeFault
from ui_capability.profiles import Guard, Profile
from ui_capability.replay import money_minor


def execute(r, inputs=None):
    return asyncio.run(r.replay.run(dict(INPUTS) if inputs is None else inputs))


def test_fresh_inputs_return_typed_review_without_model_dependency(monkeypatch):
    original = builtins.__import__
    calls = []

    def forbidden_provider(name, *args, **kwargs):
        if any(
            part in name.split(".")
            for part in ("openai", "anthropic", "discovery", "provider", "models")
        ):
            calls.append(name)
            raise AssertionError("Replay attempted a provider import")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbidden_provider)
    for values in (
        dict(INPUTS),
        {"member_id": "000099", "product_code": "SAVINGS_PLUS", "nickname": "Holiday"},
    ):
        r = rig(inputs=values)
        result = execute(r, values)
        assert isinstance(result, Success)
        assert result.outputs == {
            **values,
            "monthly_fee_minor": 123456,
            "currency": "USD",
            "submitted": False,
        }
        assert r.surface.actions[0][2] == values["nickname"]
        assert result.metadata.provider_call_count == 0
        assert not result.metadata.human_intervened
    assert calls == []


@pytest.mark.parametrize(
    "bad",
    [
        {**INPUTS, "member_id": 42},
        {**INPUTS, "nickname": ""},
        {**INPUTS, "product_code": "UNSUPPORTED"},
        {**INPUTS, "extra": "canary"},
        None,
    ],
)
def test_invalid_inputs_do_not_act(bad):
    r = rig()
    result = asyncio.run(r.replay.run(bad))
    assert result.code == FailureCode.INVALID_ARGUMENT
    assert not r.surface.actions


def test_business_outcome_is_not_adapter_crash():
    profile = Profile(
        profile_id="member_ops",
        binding=BINDING,
        targets=artifact().targets,
        terminal_checks=(Visible(target="review"),),
        guards=(
            Guard(
                id="absent",
                kind="business",
                when=Visible(target="not_found"),
                outcome_code="member_not_found",
            ),
        ),
    )
    r = rig(profile=profile)
    r.surface.observations[:] = [observation(r.cap, INPUTS, extra=("not_found",))]
    result = execute(r)
    assert result.kind == "business_outcome" and result.code == "member_not_found"
    assert not r.surface.actions
    crash = rig()
    crash.surface.observe_error = RuntimeError("private-crash-canary")
    failure = execute(crash)
    assert failure.kind == "failure" and failure.code == FailureCode.INTERRUPTED
    assert "private-crash-canary" not in crash.audit.getvalue()


def test_wrong_member_review_cannot_return_success():
    r = rig(wrong_member=True)
    result = execute(r)
    assert result.code == FailureCode.IDENTITY_MISMATCH
    assert result.kind == "failure"


def test_ambiguous_target_has_zero_actions():
    r = rig()
    before = observation(r.cap, INPUTS)
    duplicate = next(t for t in before.targets if t.ref == "nickname_field")
    r.surface.observations[:] = [
        before.model_copy(update={"targets": (*before.targets, duplicate)})
    ]
    result = execute(r)
    assert result.code == FailureCode.AMBIGUOUS_TARGET
    assert not r.surface.actions


def test_safe_retry_is_bounded_but_unknown_preview_is_not_repeated():
    cap = artifact()
    first = cap.steps[0].model_copy(update={"retry": RetryPolicy(max_retries=2, interval_ms=1)})
    r = rig(cap=cap.model_copy(update={"steps": (first, *cap.steps[1:])}), update=False)
    result = execute(r)
    assert result.code == FailureCode.POSTCONDITION_FAILED
    assert len(r.surface.actions) == 3
    unsafe = rig()
    original = unsafe.surface.on_action

    async def only_fill(action, target, value):
        if isinstance(action, Fill):
            await original(action, target, value)

    unsafe.surface.on_action = only_fill
    result = execute(unsafe)
    assert result.code == FailureCode.UNKNOWN_ACTION_OUTCOME
    assert [a.kind for a, _, _ in unsafe.surface.actions] == ["fill", "click"]


def test_delayed_postcondition_waits_without_duplicate_click():
    r = rig()
    original = r.surface.on_action

    async def delay_review(action, target, value):
        if isinstance(action, Fill):
            await original(action, target, value)
        else:
            r.surface.observations[:] = [
                observation(r.cap, INPUTS, filled=True),
                observation(r.cap, INPUTS, filled=True, review=True),
            ]
            r.surface.observation_count = 0

    r.surface.on_action = delay_review
    result = execute(r)
    assert isinstance(result, Success)
    assert [a.kind for a, _, _ in r.surface.actions] == ["fill", "click"]


def test_permission_guard_wins_over_business_or_success():
    profile = Profile(
        profile_id="member_ops",
        binding=BINDING,
        targets=artifact().targets,
        terminal_checks=(Visible(target="review"),),
        guards=(
            Guard(
                id="absent",
                kind="business",
                when=Visible(target="not_found"),
                outcome_code="member_not_found",
            ),
            Guard(
                id="denied",
                kind="failure",
                when=Visible(target="permission"),
                failure_code=FailureCode.PERMISSION_DENIED,
            ),
        ),
    )
    r = rig(profile=profile)
    r.surface.observations[:] = [
        observation(r.cap, INPUTS, review=True, extra=("not_found", "permission"))
    ]
    assert execute(r).code == FailureCode.PERMISSION_DENIED
    assert not r.surface.actions


def test_known_recovery_is_allowlisted_bounded_and_rechecks_identity():
    from ui_capability.contracts import Click

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
                max_count=1,
                checkpoint=(CountEquals(target="interstitial", count=0),),
            ),
        ),
    )
    stuck = rig(cap=cap, profile=profile, update=False)
    stuck.surface.observations[:] = [observation(cap, INPUTS, extra=("interstitial", "dismiss"))]
    assert execute(stuck).code == FailureCode.RECOVERY_EXHAUSTED
    assert len(stuck.surface.actions) == 1
    wrong = rig(cap=cap, profile=profile)
    wrong.surface.observations[:] = [observation(cap, INPUTS, extra=("interstitial", "dismiss"))]

    async def wrong_identity(action, target, value):
        wrong.surface.observations[:] = [observation(cap, INPUTS, wrong_member=True)]

    wrong.surface.on_action = wrong_identity
    assert execute(wrong).code == FailureCode.IDENTITY_MISMATCH
    assert len(wrong.surface.actions) == 1


def test_session_expiry_is_lifecycle_not_success_and_can_resume_same_session():
    async def scenario():
        profile = Profile(
            profile_id="member_ops",
            binding=BINDING,
            targets=artifact().targets,
            terminal_checks=(Visible(target="review"),),
            guards=(
                Guard(
                    id="expired",
                    kind="intervention",
                    when=Visible(target="expired"),
                    reason="session_expired",
                ),
            ),
        )
        r = rig(profile=profile)
        r.surface.observations[:] = [observation(r.cap, INPUTS, extra=("expired",))]
        paused = await r.replay.run(dict(INPUTS))
        assert isinstance(paused, Intervention)
        assert r.session.ownership == Ownership.AWAITING_OPERATOR
        assert not r.surface.actions
        await r.session.takeover()
        r.surface.observations[:] = [observation(r.cap, INPUTS, wrong_member=True)]
        with pytest.raises(RuntimeFault):
            await r.replay.resume()
        assert r.session.ownership == Ownership.HUMAN
        r.surface.observations[:] = [observation(r.cap, INPUTS)]
        result = await r.replay.resume()
        assert isinstance(result, Success)
        assert result.metadata.human_intervened

    asyncio.run(scenario())


def test_action_budget_counts_actual_attempts():
    cap = artifact()
    goal = cap.goal.model_copy(update={"budgets": Budgets(max_actions=1)})
    r = rig(cap=cap.model_copy(update={"goal": goal}))
    assert execute(r).code == FailureCode.BUDGET_EXCEEDED
    assert len(r.surface.actions) == 1


def test_rejected_inputs_and_adapter_errors_never_leak_to_jsonl():
    canary = "SECRET_CANARY_VALUE_93"
    r = rig()
    result = execute(r, {**INPUTS, "member_id": {"rejected": canary}})
    assert result.code == FailureCode.INVALID_ARGUMENT
    assert canary not in r.audit.getvalue()
    r = rig(inputs={**INPUTS, "nickname": canary})
    assert isinstance(execute(r, {**INPUTS, "nickname": canary}), Success)
    assert canary not in r.audit.getvalue()
    assert INPUTS["member_id"] not in r.audit.getvalue()


@pytest.mark.parametrize("text,expected", [("0.01", 1), ("1,234.56", 123456), ("10.00", 1000)])
def test_money_uses_exact_minor_units(text, expected):
    assert money_minor(text, "en_US") == expected


@pytest.mark.parametrize("text", ["1.234", "1,23.45", "$1.00", "NaN", "1e2", " 1.00", "1.000,00"])
def test_money_rejects_ambiguous_or_malformed_display(text):
    with pytest.raises(RuntimeFault):
        money_minor(text, "en_US")
