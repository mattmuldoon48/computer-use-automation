"""Compile executed, visibly verified actions; never consume a fixture action recipe."""

from __future__ import annotations

import json

from pydantic import Field

from .contracts import (
    CapabilityArtifact,
    Click,
    Equals,
    ExecutableAction,
    FailureCode,
    Fill,
    InputRef,
    Observation,
    ObservedTarget,
    Predicate,
    Provenance,
    PublicLiteral,
    RetryPolicy,
    RoleLocator,
    Scalar,
    Select,
    Step,
    TargetSpec,
    Visible,
    walk,
)
from .errors import RuntimeFault
from .policy import Policy
from .values import bind_target, evaluate_all, matches, resolve


class _GoalTemplate(CapabilityArtifact):
    """Internal contract-only view. Empty steps cannot be saved as a capability."""

    steps: tuple[Step, ...] = Field(default=(), max_length=0)


def goal_template(template: CapabilityArtifact) -> CapabilityArtifact:
    """Discard ordered actions before validating or using any supplied template data."""
    data = template.model_dump(mode="json", exclude={"steps", "provenance"})
    data["steps"] = []
    data["provenance"] = {"kind": "test_fixture", "action_source": "hand_authored_test"}
    return _GoalTemplate.model_validate_json(json.dumps(data))


class DeterministicCompiler:
    """A bounded trace of actions, with predicates justified by before/after observations."""

    def __init__(
        self,
        template: CapabilityArtifact,
        policy: Policy,
        inputs: dict[str, Scalar],
        *,
        reviewed_targets: dict[str, TargetSpec],
    ) -> None:
        self.template = goal_template(template)
        self.policy = policy
        self.inputs = dict(inputs)
        self.steps: list[Step] = []
        for name, spec in self.template.targets.items():
            if reviewed_targets.get(name) != spec:
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            if not self._safe_spec(spec):
                raise RuntimeFault(FailureCode.POLICY_DENIED)
        if any(
            isinstance(node, PublicLiteral) and not self.public(node.value)
            for node in walk(self.template)
        ):
            raise RuntimeFault(FailureCode.POLICY_DENIED)

    def public(self, value: Scalar) -> bool:
        return any(
            type(value) is type(approved) and value == approved
            for approved in self.policy.approved_literals
        )

    def _safe_spec(self, spec: TargetSpec) -> bool:
        for node in walk(spec):
            if isinstance(node, PublicLiteral):
                if not self.public(node.value):
                    return False
                if isinstance(node.value, str) and (
                    not node.value.strip() or len(node.value) > 256
                ):
                    return False
            elif isinstance(node, InputRef):
                if node.name not in self.inputs:
                    return False
            elif getattr(node, "kind", None) in {"local_ref", "secret_ref"}:
                return False
        return True

    def target_name(self, selected: ObservedTarget, observation: Observation) -> str:
        """Reverse binding is structural; equal input values never rewrite unrelated labels."""
        if not selected.derived or not selected.visible:
            raise RuntimeFault(FailureCode.TARGET_NOT_FOUND)
        same_ref = [target for target in observation.targets if target.ref == selected.ref]
        same_spec = [
            target
            for target in observation.targets
            if target.visible and target.spec == selected.spec
        ]
        if len(same_ref) != 1 or len(same_spec) != 1:
            raise RuntimeFault(FailureCode.AMBIGUOUS_TARGET)
        if same_ref[0] != selected:
            raise RuntimeFault(FailureCode.STALE_OBSERVATION)
        names = [
            name
            for name, spec in self.template.targets.items()
            if bind_target(spec, self.inputs, {}) == selected.spec
        ]
        if len(names) > 1:
            raise RuntimeFault(FailureCode.AMBIGUOUS_TARGET)
        if not names or not self._safe_spec(self.template.targets[names[0]]):
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        return names[0]

    def public_route(self, route: str) -> bool:
        """Routes come from the independent policy, never observed private strings."""
        return self.public(route) and (
            route == self.policy.binding.entry_route
            or any(route == rule.route or route == rule.destination for rule in self.policy.rules)
        )

    def _route(self, observation: Observation) -> None:
        if not self.public_route(observation.route):
            raise RuntimeFault(FailureCode.POLICY_DENIED)

    def project_value(
        self, value: Scalar, *, target: str | None = None, field: str | None = None
    ) -> InputRef | PublicLiteral | None:
        """Project data by its reviewed position, not by replacing matching strings.

        Raw, unpositioned UI text can establish only an unambiguous input reference.
        Public scalar approval is deliberately insufficient to publish runtime text.
        """
        bindings: list[InputRef | PublicLiteral] = []
        if target is not None:
            if field == "value":
                for step in reversed(self.steps):
                    if isinstance(step.action, (Fill, Select)) and step.action.target == target:
                        if isinstance(step.action.value, InputRef):
                            bindings.append(step.action.value)
                        break
            for match in self.template.output_matches:
                extraction = self.template.extractions[match.output]
                if (
                    extraction.target == target
                    and extraction.source == field
                    and extraction.parser == "string"
                    and isinstance(match.expected, InputRef)
                ):
                    bindings.append(match.expected)
            for node in walk(
                (
                    self.template.preconditions,
                    self.template.identity_checks,
                    self.template.final_checks,
                )
            ):
                if (
                    isinstance(node, Equals)
                    and node.target == target
                    and node.kind == ("text_equals" if field == "text" else "value_equals")
                    and isinstance(node.value, InputRef)
                ):
                    bindings.append(node.value)
            # Caller-added literal predicates are not authority to declassify
            # observed data. Only the reviewed accessible label below is public.
            # Accessible labels describe controls, not the contents of form/data cells.
            locator = self.template.targets[target].locator
            if (
                not bindings
                and field == "text"
                and isinstance(locator, RoleLocator)
                and locator.role in {"button", "link", "heading"}
                and isinstance(locator.name, (InputRef, PublicLiteral))
            ):
                bindings.append(locator.name)
        if target is not None and not bindings:
            return None
        if bindings:
            matching = [
                binding
                for binding in bindings
                if type(expected := resolve(binding, self.inputs, {})) is type(value)
                and expected == value
                and (not isinstance(binding, PublicLiteral) or self.public(binding.value))
            ]
            if matching and all(binding == matching[0] for binding in matching):
                return matching[0]
            return None
        names = [
            name
            for name, supplied in self.inputs.items()
            if type(value) is type(supplied) and value == supplied
        ]
        return InputRef(name=names[0]) if len(names) == 1 else None

    def _markers(self, observation: Observation) -> list[Predicate]:
        candidates: list[tuple[int, str, ObservedTarget]] = []
        for target in observation.targets:
            if not target.derived or not target.visible:
                continue
            try:
                name = self.target_name(target, observation)
            except RuntimeFault as fault:
                if fault.code == FailureCode.AMBIGUOUS_TARGET:
                    raise
                continue
            priority = 0 if target.role in {"heading", "status", "cell"} else 1
            candidates.append((priority, name, target))
        predicates: list[Predicate] = []
        for _, name, target in sorted(candidates, key=lambda item: item[:2]):
            predicates.append(Visible(target=name))
            for field in ("text", "value"):
                value = self.project_value(getattr(target, field), target=name, field=field)
                if isinstance(value, PublicLiteral):
                    predicates.append(
                        Equals(
                            kind="text_equals" if field == "text" else "value_equals",
                            target=name,
                            value=value,
                        )
                    )
        return predicates

    def preconditions(self, action: ExecutableAction, before: Observation) -> tuple[Predicate, ...]:
        self._route(before)
        if isinstance(action, (Click, Fill, Select)):
            found = matches(self.template.targets[action.target], before, self.inputs, {})
            if len(found) != 1:
                raise RuntimeFault(
                    FailureCode.AMBIGUOUS_TARGET if found else FailureCode.TARGET_NOT_FOUND
                )
            if self.target_name(found[0], before) != action.target:
                raise RuntimeFault(FailureCode.TARGET_NOT_FOUND)
            return (Visible(target=action.target),)
        markers = self._markers(before)
        if not markers:
            raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
        return (markers[0],)

    def proposal_artifact(
        self, action: ExecutableAction, before: Observation
    ) -> CapabilityArtifact:
        """An authorization envelope, not a completed or publishable discovery result."""
        preconditions = self.preconditions(action, before)
        pending = Step(
            id=f"discovered_{len(self.steps) + 1}",
            action=action,
            preconditions=preconditions,
            postconditions=preconditions,
            retry=RetryPolicy(max_retries=0),
        )
        return self._artifact(tuple(self.steps) + (pending,), self.template.provenance)

    def record(self, action: ExecutableAction, before: Observation, after: Observation) -> Step:
        preconditions = self.preconditions(action, before)
        self._route(after)
        if isinstance(action, (Fill, Select)):
            if not isinstance(action.value, InputRef):
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            postconditions: tuple[Predicate, ...] = (
                Equals(kind="value_equals", target=action.target, value=action.value),
            )
        else:
            changed = [
                marker
                for marker in self._markers(after)
                if not evaluate_all((marker,), before, self.template, self.inputs, {})
            ]
            if not changed:
                # A returned click/navigation/wait is not evidence of an application effect.
                raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
            postconditions = (changed[0],)
        if not evaluate_all(postconditions, after, self.template, self.inputs, {}):
            raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
        step = Step(
            id=f"discovered_{len(self.steps) + 1}",
            action=action,
            preconditions=preconditions,
            postconditions=postconditions,
            retry=RetryPolicy(max_retries=0),
        )
        self.steps.append(step)
        return step

    def _artifact(self, steps: tuple[Step, ...], provenance: Provenance) -> CapabilityArtifact:
        data = self.template.model_dump(mode="json", exclude={"steps", "provenance"})
        data["steps"] = [step.model_dump(mode="json") for step in steps]
        data["provenance"] = provenance.model_dump(mode="json")
        artifact = CapabilityArtifact.model_validate_json(json.dumps(data))
        self.policy.validate_artifact(artifact)
        return artifact

    def finish(self, *, live: bool, run_id: str) -> CapabilityArtifact:
        if not self.steps:
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        provenance = (
            Provenance(
                kind="live_discovery", action_source="observed_live", discovery_run_id=run_id
            )
            if live
            else Provenance(kind="test_fixture", action_source="hand_authored_test")
        )
        return self._artifact(tuple(self.steps), provenance)
