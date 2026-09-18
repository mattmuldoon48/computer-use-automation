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
    Scalar,
    Select,
    Step,
    TargetSpec,
    Visible,
    walk,
)
from .errors import RuntimeFault
from .policy import Policy
from .values import bind_target, evaluate_all, matches


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
        self, template: CapabilityArtifact, policy: Policy, inputs: dict[str, Scalar]
    ) -> None:
        self.template = goal_template(template)
        self.policy = policy
        self.inputs = dict(inputs)
        self.steps: list[Step] = []
        if any(
            isinstance(node, PublicLiteral)
            and isinstance(node.value, str)
            and self._sensitive(node.value)
            for node in walk(self.template)
        ):
            raise RuntimeFault(FailureCode.POLICY_DENIED)

    def public(self, value: Scalar) -> bool:
        return any(
            type(value) is type(approved) and value == approved
            for approved in self.policy.approved_literals
        )

    def _sensitive(self, value: str) -> bool:
        return any(
            definition.classification == "restricted"
            and isinstance(supplied := self.inputs[name], str)
            and bool(supplied)
            and supplied in value
            for name, definition in self.template.goal.inputs.items()
        )

    def _safe_spec(self, spec: TargetSpec) -> bool:
        for node in walk(spec):
            if isinstance(node, PublicLiteral):
                if not self.public(node.value):
                    return False
                if isinstance(node.value, str) and (
                    not node.value.strip() or len(node.value) > 256 or self._sensitive(node.value)
                ):
                    return False
            elif isinstance(node, InputRef) and node.name not in self.inputs:
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

    def _route(self, observation: Observation) -> None:
        if not self.public(observation.route) or self._sensitive(observation.route):
            raise RuntimeFault(FailureCode.POLICY_DENIED)

    def _bound_value(self, value: Scalar) -> InputRef | PublicLiteral | None:
        names = [
            name
            for name, supplied in self.inputs.items()
            if type(value) is type(supplied) and value == supplied
        ]
        if len(names) == 1:
            return InputRef(name=names[0])
        if names or not self.public(value):
            return None
        if isinstance(value, str) and (not value.strip() or self._sensitive(value)):
            return None
        return PublicLiteral(value=value)

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
                value = self._bound_value(getattr(target, field))
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
