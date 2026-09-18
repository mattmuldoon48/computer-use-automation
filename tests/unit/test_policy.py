"""Hand-authored fixtures exercising trusted authority, never live evidence."""

import pytest

from ui_capability.contracts import (
    Binding,
    CapabilityArtifact,
    Click,
    Equals,
    Extraction,
    FailureCode,
    Fill,
    FrameScope,
    GoalContract,
    InputRef,
    LocalRef,
    Navigate,
    OutputMatch,
    Provenance,
    PublicLiteral,
    RetryPolicy,
    RoleLocator,
    Step,
    TargetSpec,
    ValueDefinition,
    Visible,
)
from ui_capability.errors import RuntimeFault
from ui_capability.policy import ActionRule, Effect, Policy


def policy_fixture() -> tuple[Policy, CapabilityArtifact]:
    binding = Binding(
        origin="http://localhost:8000", entry_route="/review", app="member_ops", version="v1"
    )
    target = TargetSpec(
        frame=FrameScope(kind="main"),
        locator=RoleLocator(role="button", name=PublicLiteral(value="Review")),
    )
    restricted = ValueDefinition(type="string", classification="restricted", max_length=32)
    public = ValueDefinition(type="string", classification="public")
    inputs = {
        "member_id": restricted,
        "nickname": restricted,
        "product_code": ValueDefinition(
            type="enum", classification="public", values=("SAVINGS_BASIC", "SAVINGS_PLUS")
        ),
    }
    outputs = {
        **inputs,
        "monthly_fee_minor": ValueDefinition(type="integer", classification="public"),
        "currency": public,
        "submitted": ValueDefinition(type="boolean", classification="public"),
    }
    targets = {"review": target}
    for name in outputs:
        targets[name] = TargetSpec(
            frame=FrameScope(kind="main"),
            locator=RoleLocator(role="cell", name=PublicLiteral(value=name)),
        )
    identity = Equals(kind="text_equals", target="member_id", value=InputRef(name="member_id"))
    artifact = CapabilityArtifact(
        capability_id="prepare_review",
        capability_version=1,
        goal=GoalContract(
            goal_template="Prepare review without submitting",
            inputs=inputs,
            outputs=outputs,
            binding=binding,
            terminal_intent="unsubmitted_review",
            policy_ref="review_policy",
        ),
        surface_requirements=("web", "visible_text"),
        targets=targets,
        steps=(
            Step(
                id="review",
                action=Click(target="review"),
                preconditions=(Visible(target="review"),),
                postconditions=(identity,),
            ),
        ),
        preconditions=(Visible(target="review"),),
        identity_checks=(identity,),
        final_checks=(identity,),
        extractions={
            name: Extraction(
                target=name,
                source="text",
                parser=(
                    "money_minor"
                    if name == "monthly_fee_minor"
                    else "boolean"
                    if name == "submitted"
                    else "string"
                ),
            )
            for name in outputs
        },
        output_matches=tuple(
            OutputMatch(output=name, expected=InputRef(name=name)) for name in inputs
        )
        + (OutputMatch(output="submitted", expected=PublicLiteral(value=False)),),
        required_permissions=("prepare",),
        redaction=("member_id", "nickname"),
        provenance=Provenance(kind="test_fixture", action_source="hand_authored_test"),
    )
    policy = Policy(
        policy_id="review_policy",
        binding=binding,
        rules=(
            ActionRule(
                action="click",
                target=target,
                origin=binding.origin,
                route="/review",
                permission="prepare",
                effect=Effect.PREVIEW,
            ),
        ),
        approved_literals=("Review", *outputs, False),
        permissions=("prepare",),
    )
    return policy, artifact


def test_final_submission_is_not_authorized_by_click_permission() -> None:
    policy, artifact = policy_fixture()
    submit = artifact.targets["review"].model_copy(
        update={
            "locator": RoleLocator(role="button", name=PublicLiteral(value="Submit")),
        }
    )
    with pytest.raises(RuntimeFault) as caught:
        policy.authorize(
            Click(target="submit"),
            submit,
            policy.binding.origin,
            "/review",
            ("prepare",),
            frozenset({"prepare", "submit"}),
        )
    assert caught.value.code is FailureCode.POLICY_DENIED


@pytest.mark.parametrize("effect", [Effect.UNKNOWN, Effect.IRREVERSIBLE])
def test_unknown_and_irreversible_effects_are_denied(effect: Effect) -> None:
    policy, artifact = policy_fixture()
    policy = policy.model_copy(
        update={"rules": (policy.rules[0].model_copy(update={"effect": effect}),)}
    )
    with pytest.raises(RuntimeFault) as caught:
        policy.authorize(
            artifact.steps[0].action,
            artifact.targets["review"],
            policy.binding.origin,
            "/review",
            ("prepare",),
            frozenset({"prepare"}),
        )
    assert caught.value.code is FailureCode.POLICY_DENIED


def test_exact_origin_includes_port() -> None:
    policy, artifact = policy_fixture()
    with pytest.raises(RuntimeFault) as caught:
        policy.authorize(
            artifact.steps[0].action,
            artifact.targets["review"],
            "http://localhost:8001",
            "/review",
            ("prepare",),
            frozenset({"prepare"}),
        )
    assert caught.value.code is FailureCode.POLICY_DENIED


def test_caller_cannot_expand_policy_ceiling() -> None:
    policy, artifact = policy_fixture()
    with pytest.raises(RuntimeFault) as caught:
        policy.authorize(
            artifact.steps[0].action,
            artifact.targets["review"],
            policy.binding.origin,
            "/review",
            ("prepare", "submit"),
            frozenset({"prepare", "submit"}),
        )
    assert caught.value.code is FailureCode.PERMISSION_DENIED


def test_required_permission_must_be_held_by_caller() -> None:
    policy, artifact = policy_fixture()
    with pytest.raises(RuntimeFault) as caught:
        policy.authorize(
            artifact.steps[0].action,
            artifact.targets["review"],
            policy.binding.origin,
            "/review",
            ("prepare",),
            frozenset(),
        )
    assert caught.value.code is FailureCode.PERMISSION_DENIED


def test_ambiguous_rules_never_choose_first() -> None:
    policy, artifact = policy_fixture()
    policy = policy.model_copy(update={"rules": policy.rules * 2})
    with pytest.raises(RuntimeFault) as caught:
        policy.validate_artifact(artifact)
    assert caught.value.code is FailureCode.POLICY_DENIED


def test_restricted_literal_is_rejected_before_execution() -> None:
    policy, artifact = policy_fixture()
    step = artifact.steps[0].model_copy(
        update={
            "action": Fill(target="review", value=PublicLiteral(value="RESTRICTED_CANARY")),
        }
    )
    artifact = artifact.model_copy(update={"steps": (step,)})
    with pytest.raises(RuntimeFault) as caught:
        policy.validate_artifact(artifact)
    assert caught.value.code is FailureCode.POLICY_DENIED
    assert "RESTRICTED_CANARY" not in str(caught.value)


def test_literal_review_uses_exact_types_not_python_numeric_equality() -> None:
    policy, artifact = policy_fixture()
    policy = policy.model_copy(update={"approved_literals": (*policy.approved_literals[:-1], 0)})
    with pytest.raises(RuntimeFault) as caught:
        policy.validate_artifact(artifact)
    assert caught.value.code is FailureCode.POLICY_DENIED


def test_title_only_terminal_claim_cannot_replace_bound_output_checks() -> None:
    policy, artifact = policy_fixture()
    artifact = artifact.model_copy(
        update={
            "final_checks": (Visible(target="review"),),
            "output_matches": (
                OutputMatch(output="submitted", expected=PublicLiteral(value=False)),
            ),
        }
    )
    with pytest.raises(RuntimeFault) as caught:
        policy.validate_artifact(artifact)
    assert caught.value.code is FailureCode.INVALID_ARTIFACT


def test_local_cannot_be_used_before_terminal_extraction() -> None:
    policy, artifact = policy_fixture()
    artifact = artifact.model_copy(
        update={
            "identity_checks": (
                Equals(kind="text_equals", target="member_id", value=LocalRef(name="member_id")),
            )
        }
    )
    with pytest.raises(RuntimeFault) as caught:
        policy.validate_artifact(artifact)
    assert caught.value.code is FailureCode.INVALID_ARTIFACT


def test_bound_review_artifact_retains_preview_authority_without_retry() -> None:
    policy, artifact = policy_fixture()
    policy.validate_artifact(artifact)
    assert (
        policy.authorize(
            artifact.steps[0].action,
            artifact.targets["review"],
            policy.binding.origin,
            "/review",
            ("prepare",),
            frozenset({"prepare"}),
        )
        is Effect.PREVIEW
    )


def test_unknown_application_version_fails_before_execution() -> None:
    policy, artifact = policy_fixture()
    goal = artifact.goal.model_copy(
        update={
            "binding": artifact.goal.binding.model_copy(update={"version": "v2"}),
        }
    )
    with pytest.raises(RuntimeFault) as caught:
        policy.validate_artifact(artifact.model_copy(update={"goal": goal}))
    assert caught.value.code is FailureCode.INCOMPATIBLE


def test_preview_is_permitted_once_but_not_automatically_retried() -> None:
    policy, artifact = policy_fixture()
    step = artifact.steps[0].model_copy(update={"retry": RetryPolicy(max_retries=1)})
    with pytest.raises(RuntimeFault) as caught:
        policy.validate_artifact(artifact.model_copy(update={"steps": (step,)}))
    assert caught.value.code is FailureCode.POLICY_DENIED


def test_navigation_reference_cannot_escape_reviewed_destination() -> None:
    policy, artifact = policy_fixture()
    destination = "http://localhost:8000/review"
    inputs = {
        **artifact.goal.inputs,
        "destination": ValueDefinition(
            type="string",
            classification="public",
        ),
    }
    step = artifact.steps[0].model_copy(
        update={
            "action": Navigate(destination=InputRef(name="destination")),
        }
    )
    artifact = artifact.model_copy(
        update={
            "goal": artifact.goal.model_copy(update={"inputs": inputs}),
            "steps": (step,),
        }
    )
    policy = policy.model_copy(
        update={
            "rules": (
                ActionRule(
                    action="navigate",
                    origin=policy.binding.origin,
                    route="/review",
                    destination=destination,
                    permission="prepare",
                    effect=Effect.READ,
                ),
            ),
            "approved_literals": (*policy.approved_literals, destination),
        }
    )
    policy.validate_artifact(artifact)
    assert (
        policy.authorize(
            Navigate(destination=PublicLiteral(value=destination)),
            None,
            policy.binding.origin,
            "/review",
            ("prepare",),
            frozenset({"prepare"}),
        )
        is Effect.READ
    )
    with pytest.raises(RuntimeFault) as caught:
        policy.authorize(
            Navigate(destination=PublicLiteral(value="http://localhost:8001/submit")),
            None,
            policy.binding.origin,
            "/review",
            ("prepare",),
            frozenset({"prepare"}),
        )
    assert caught.value.code is FailureCode.POLICY_DENIED
