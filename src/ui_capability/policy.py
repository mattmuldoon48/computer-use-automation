"""Trusted authority ceiling and fail-closed validation for the narrow demo contract."""

from enum import StrEnum
from typing import Literal

from .contracts import (
    Binding,
    CapabilityArtifact,
    Contract,
    ExecutableAction,
    FailureCode,
    InputRef,
    LocalRef,
    Name,
    Navigate,
    PublicLiteral,
    Scalar,
    SecretRef,
    TargetSpec,
    walk,
)
from .errors import RuntimeFault


class Effect(StrEnum):
    READ = "READ"
    SAFE_OVERWRITE = "SAFE_OVERWRITE"
    PREVIEW = "PREVIEW"
    UNKNOWN = "UNKNOWN"
    IRREVERSIBLE = "IRREVERSIBLE"

    @property
    def retryable(self) -> bool:
        return self in {Effect.READ, Effect.SAFE_OVERWRITE}


class ActionRule(Contract):
    action: Literal["click", "fill", "select", "navigate", "wait"]
    target: TargetSpec | None = None
    origin: str
    route: str
    destination: str | None = None
    permission: Name
    effect: Effect


class Policy(Contract):
    policy_id: Name
    binding: Binding
    rules: tuple[ActionRule, ...]
    approved_literals: tuple[Scalar, ...]
    permissions: tuple[Name, ...]

    def _approved(self, value: Scalar) -> bool:
        # bool is an int subclass: equality alone would approve False using 0.
        return any(type(value) is type(item) and value == item for item in self.approved_literals)

    def _rules_for(
        self,
        action: ExecutableAction,
        target: TargetSpec | None,
        *,
        allow_reference: bool = False,
    ) -> list[ActionRule]:
        destination = None
        unresolved = isinstance(action, Navigate) and isinstance(action.destination, InputRef)
        if isinstance(action, Navigate):
            if isinstance(action.destination, PublicLiteral):
                if type(action.destination.value) is not str:
                    raise RuntimeFault(FailureCode.POLICY_DENIED)
                destination = action.destination.value
            elif not (allow_reference and unresolved):
                raise RuntimeFault(FailureCode.POLICY_DENIED)
        return [
            rule
            for rule in self.rules
            if rule.action == action.kind
            and rule.target == target
            and (
                (unresolved and allow_reference and rule.destination is not None)
                or rule.destination == destination
            )
            and rule.origin == self.binding.origin
        ]

    def authorize(
        self,
        action: ExecutableAction,
        target: TargetSpec | None,
        origin: str,
        route: str,
        required_permissions: tuple[str, ...],
        caller_permissions: frozenset[str],
    ) -> Effect:
        if origin != self.binding.origin:
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        requested = frozenset(required_permissions)
        if not requested.issubset(self.permissions) or not requested.issubset(caller_permissions):
            raise RuntimeFault(FailureCode.PERMISSION_DENIED)
        rules = [rule for rule in self._rules_for(action, target) if rule.route == route]
        if len(rules) != 1:
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        rule = rules[0]
        if rule.permission not in requested:
            raise RuntimeFault(FailureCode.PERMISSION_DENIED)
        if rule.effect in {Effect.UNKNOWN, Effect.IRREVERSIBLE}:
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        for node in walk(action):
            if isinstance(node, PublicLiteral) and not self._approved(node.value):
                raise RuntimeFault(FailureCode.POLICY_DENIED)
        return rule.effect

    def validate_artifact(self, artifact: CapabilityArtifact) -> None:
        if artifact.goal.binding != self.binding:
            raise RuntimeFault(FailureCode.INCOMPATIBLE)
        if artifact.goal.policy_ref != self.policy_id:
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        if not set(artifact.required_permissions).issubset(self.permissions):
            raise RuntimeFault(FailureCode.PERMISSION_DENIED)
        for node in walk(artifact):
            if isinstance(node, PublicLiteral) and not self._approved(node.value):
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            if isinstance(node, SecretRef):
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
            if isinstance(node, InputRef) and node.name not in artifact.goal.inputs:
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
            if isinstance(node, LocalRef) and node.name not in artifact.extractions:
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        # No extraction occurs until terminal verification. Target locators and
        # ordinary checkpoints must therefore be independent of locals.
        before_extraction = (
            artifact.targets,
            artifact.steps,
            artifact.preconditions,
            artifact.identity_checks,
            artifact.final_checks,
        )
        if any(isinstance(node, LocalRef) for node in walk(before_extraction)):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        for step in artifact.steps:
            target_name = getattr(step.action, "target", None)
            target = artifact.targets.get(target_name) if target_name is not None else None
            if target_name is not None and target is None:
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
            candidates = self._rules_for(step.action, target, allow_reference=True)
            if not candidates:
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            # Multiple reviewed routes are allowed, but classification at each
            # exact route must be unique; contradictory rules never pick first.
            if len({(rule.route, rule.destination) for rule in candidates}) != len(candidates):
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            for rule in candidates:
                action = step.action
                if isinstance(action, Navigate) and isinstance(action.destination, InputRef):
                    if rule.destination is None:
                        raise RuntimeFault(FailureCode.POLICY_DENIED)
                    action = Navigate(destination=PublicLiteral(value=rule.destination))
                effect = self.authorize(
                    action,
                    target,
                    rule.origin,
                    rule.route,
                    artifact.required_permissions,
                    frozenset(self.permissions),
                )
                if step.retry.max_retries and not effect.retryable:
                    raise RuntimeFault(FailureCode.POLICY_DENIED)
        self._validate_terminal(artifact)

    @staticmethod
    def _validate_terminal(artifact: CapabilityArtifact) -> None:
        inputs = artifact.goal.inputs
        outputs = artifact.goal.outputs
        for name in ("member_id", "nickname"):
            definition = inputs.get(name)
            output = outputs.get(name)
            if (
                definition is None
                or definition.type != "string"
                or definition.classification != "restricted"
                or definition.min_length < 1
                or output is None
                or output.type != "string"
                or output.classification != "restricted"
            ):
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        if inputs["nickname"].max_length > 32:
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        product = inputs.get("product_code")
        if (
            product is None
            or product.type != "enum"
            or not set(product.values).issubset({"SAVINGS_BASIC", "SAVINGS_PLUS"})
        ):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        expected_types = {
            "product_code": {"enum", "string"},
            "monthly_fee_minor": {"integer"},
            "currency": {"enum", "string"},
            "submitted": {"boolean"},
        }
        for name, types in expected_types.items():
            if name not in outputs or outputs[name].type not in types:
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        for name in ("member_id", "product_code", "nickname"):
            checks = [match for match in artifact.output_matches if match.output == name]
            if (
                len(checks) != 1
                or not isinstance(checks[0].expected, InputRef)
                or checks[0].expected.name != name
            ):
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        checks = [match for match in artifact.output_matches if match.output == "submitted"]
        if (
            len(checks) != 1
            or not isinstance(checks[0].expected, PublicLiteral)
            or checks[0].expected.value is not False
        ):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        if (
            artifact.extractions["monthly_fee_minor"].parser != "money_minor"
            or artifact.extractions["submitted"].parser != "boolean"
        ):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
