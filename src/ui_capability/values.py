"""Resolve typed references without coercion or access to secrets."""

from .contracts import (
    BoundValue,
    CapabilityArtifact,
    Conjunction,
    CountEquals,
    Equals,
    FailureCode,
    InputRef,
    LabelLocator,
    LocalRef,
    Locator,
    Observation,
    ObservedTarget,
    Predicate,
    PublicLiteral,
    RoleLocator,
    RouteMatches,
    Scalar,
    SecretRef,
    TableLocator,
    TableValueLocator,
    TargetSpec,
    Visible,
)
from .errors import RuntimeFault


def resolve(value: BoundValue, inputs: dict[str, Scalar], locals_: dict[str, Scalar]) -> Scalar:
    if isinstance(value, SecretRef):
        # Phase 1 has no secret manager. Never treat a secret name as its value.
        raise RuntimeFault(FailureCode.PERMISSION_DENIED)
    if isinstance(value, PublicLiteral):
        result = value.value
    elif isinstance(value, InputRef):
        if value.name not in inputs:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        result = inputs[value.name]
    elif isinstance(value, LocalRef):
        if value.name not in locals_:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        result = locals_[value.name]
    else:
        raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
    if type(result) not in (str, int, bool):
        raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
    return result


def _label(
    value: BoundValue, inputs: dict[str, Scalar], locals_: dict[str, Scalar]
) -> PublicLiteral:
    result = resolve(value, inputs, locals_)
    if not isinstance(result, str):
        raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
    return PublicLiteral(value=result)


def bind_target(
    spec: TargetSpec, inputs: dict[str, Scalar], locals_: dict[str, Scalar]
) -> TargetSpec:
    locator = spec.locator
    if isinstance(locator, RoleLocator):
        bound: Locator = RoleLocator(role=locator.role, name=_label(locator.name, inputs, locals_))
    elif isinstance(locator, LabelLocator):
        bound = LabelLocator(label=_label(locator.label, inputs, locals_))
    elif isinstance(locator, TableLocator):
        bound = TableLocator(label=_label(locator.label, inputs, locals_), control=locator.control)
    elif isinstance(locator, TableValueLocator):
        bound = TableValueLocator(label=_label(locator.label, inputs, locals_))
    else:
        raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
    return TargetSpec(
        frame=spec.frame,
        section=None if spec.section is None else _label(spec.section, inputs, locals_),
        locator=bound,
    )


def matches(
    spec: TargetSpec,
    observation: Observation,
    inputs: dict[str, Scalar],
    locals_: dict[str, Scalar],
) -> tuple[ObservedTarget, ...]:
    bound = bind_target(spec, inputs, locals_)
    return tuple(
        target for target in observation.targets if target.visible and target.spec == bound
    )


def evaluate(
    predicate: Predicate,
    observation: Observation,
    artifact: CapabilityArtifact,
    inputs: dict[str, Scalar],
    locals_: dict[str, Scalar],
) -> bool:
    if isinstance(predicate, Conjunction):
        return evaluate_all(predicate.conditions, observation, artifact, inputs, locals_)
    if isinstance(predicate, RouteMatches):
        expected = resolve(predicate.route, inputs, locals_)
        return isinstance(expected, str) and observation.route == expected
    if not isinstance(predicate, (CountEquals, Visible, Equals)):
        raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
    spec = artifact.targets.get(predicate.target)
    if spec is None:
        raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
    found = matches(spec, observation, inputs, locals_)
    if isinstance(predicate, CountEquals):
        return len(found) == predicate.count
    if len(found) > 1:
        raise RuntimeFault(FailureCode.AMBIGUOUS_TARGET)
    if not found:
        return False
    if isinstance(predicate, Visible):
        return True
    expected = resolve(predicate.value, inputs, locals_)
    actual = found[0].text if predicate.kind == "text_equals" else found[0].value
    return type(actual) is type(expected) and actual == expected


def evaluate_all(
    predicates: tuple[Predicate, ...],
    observation: Observation,
    artifact: CapabilityArtifact,
    inputs: dict[str, Scalar],
    locals_: dict[str, Scalar],
) -> bool:
    # Evaluate every predicate: a false checkpoint cannot conceal an ambiguous
    # identity target later in the same conjunction.
    result = True
    for predicate in predicates:
        result = evaluate(predicate, observation, artifact, inputs, locals_) and result
    return result
