"""Bounded data language. Artifacts contain data, never executable selectors or code."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Scalar = str | int | bool


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class InputRef(Contract):
    kind: Literal["input_ref"] = "input_ref"
    name: Name


class SecretRef(Contract):
    kind: Literal["secret_ref"] = "secret_ref"
    name: Name


class LocalRef(Contract):
    kind: Literal["local_ref"] = "local_ref"
    name: Name


class PublicLiteral(Contract):
    kind: Literal["literal"] = "literal"
    value: Scalar


BoundValue = Annotated[InputRef | SecretRef | LocalRef | PublicLiteral, Field(discriminator="kind")]


class ValueDefinition(Contract):
    type: Literal["string", "enum", "integer", "boolean"]
    classification: Literal["public", "restricted"]
    min_length: int = Field(default=1, ge=0, le=4096)
    max_length: int = Field(default=128, ge=1, le=4096)
    values: tuple[str, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.min_length > self.max_length or (self.type == "enum" and not self.values):
            raise ValueError("invalid value definition")
        return self

    def accepts(self, value: object) -> bool:
        if self.type == "boolean":
            return type(value) is bool
        if self.type == "integer":
            return type(value) is int
        if not isinstance(value, str) or not self.min_length <= len(value) <= self.max_length:
            return False
        return self.type != "enum" or value in self.values


class Binding(Contract):
    origin: str
    entry_route: str
    app: Name
    version: Name
    locale: Literal["en_US"] = "en_US"

    @model_validator(mode="after")
    def exact_origin(self) -> Self:
        parsed = urlsplit(self.origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("origin must be an exact HTTP origin")
        if not self.entry_route.startswith("/") or any(c in self.entry_route for c in "?#\\"):
            raise ValueError("invalid entry route")
        return self


class Budgets(Contract):
    max_actions: int = Field(default=20, ge=1, le=1000)
    active_seconds: int = Field(default=180, ge=1, le=3600)
    operator_seconds: int = Field(default=300, ge=1, le=3600)


class GoalContract(Contract):
    goal_template: str = Field(min_length=1, max_length=2000)
    inputs: dict[Name, ValueDefinition]
    outputs: dict[Name, ValueDefinition]
    binding: Binding
    terminal_intent: Literal["unsubmitted_review"]
    budgets: Budgets = Budgets()
    policy_ref: Name


class FrameScope(Contract):
    kind: Literal["main", "named_frame"]
    name: Name | None = None

    @model_validator(mode="after")
    def named(self) -> Self:
        if (self.kind == "named_frame") != (self.name is not None):
            raise ValueError("frame name mismatch")
        return self


class RoleLocator(Contract):
    kind: Literal["role"] = "role"
    role: Literal["button", "link", "textbox", "combobox", "heading", "cell", "status"]
    name: BoundValue


class LabelLocator(Contract):
    kind: Literal["label"] = "label"
    label: BoundValue


class TableLocator(Contract):
    kind: Literal["table_label_control"] = "table_label_control"
    label: BoundValue
    control: Literal["input", "select", "button"]


class TableValueLocator(Contract):
    """One value cell adjacent to an exact, visible table row caption."""

    kind: Literal["table_label_value"] = "table_label_value"
    label: BoundValue


Locator = Annotated[
    RoleLocator | LabelLocator | TableLocator | TableValueLocator, Field(discriminator="kind")
]


class TargetSpec(Contract):
    frame: FrameScope
    section: BoundValue | None = None
    locator: Locator
    expected_matches: Literal[1] = 1


class Visible(Contract):
    kind: Literal["visible"] = "visible"
    target: Name


class Equals(Contract):
    kind: Literal["text_equals", "value_equals"]
    target: Name
    value: BoundValue


class RouteMatches(Contract):
    kind: Literal["route_matches"] = "route_matches"
    route: BoundValue


class CountEquals(Contract):
    kind: Literal["count_equals"] = "count_equals"
    target: Name
    count: int = Field(ge=0, le=100)


class Conjunction(Contract):
    kind: Literal["all"] = "all"
    conditions: tuple[Predicate, ...] = Field(min_length=1, max_length=20)


Predicate = Annotated[
    Visible | Equals | RouteMatches | CountEquals | Conjunction, Field(discriminator="kind")
]
Conjunction.model_rebuild()


class Click(Contract):
    kind: Literal["click"] = "click"
    target: Name


class Fill(Contract):
    kind: Literal["fill"] = "fill"
    target: Name
    value: BoundValue


class Select(Contract):
    kind: Literal["select"] = "select"
    target: Name
    value: BoundValue


class Navigate(Contract):
    kind: Literal["navigate"] = "navigate"
    destination: BoundValue


class Wait(Contract):
    kind: Literal["wait"] = "wait"
    milliseconds: int = Field(ge=1, le=5000)


class Extract(Contract):
    kind: Literal["extract"] = "extract"
    output: Name


class Finish(Contract):
    kind: Literal["finish"] = "finish"
    checks: tuple[Predicate, ...] = Field(min_length=1)


class Intervene(Contract):
    kind: Literal["intervene"] = "intervene"
    reason: Literal["session_expired", "unknown_dialog", "unsupported_state"]


Action = Annotated[
    Click | Fill | Select | Navigate | Wait | Extract | Finish | Intervene,
    Field(discriminator="kind"),
]
ExecutableAction = Annotated[Click | Fill | Select | Navigate | Wait, Field(discriminator="kind")]


class ActionProposal(Contract):
    observation_id: str
    ownership_epoch: int = Field(ge=0)
    action: Action


class ObservedTarget(Contract):
    ref: Name
    spec: TargetSpec
    role: str
    control: str
    visible: bool
    text: str = ""
    value: Scalar = ""
    grounding: Literal["visible_role", "visible_label", "visible_table_caption"]
    derived: bool = False


class Observation(Contract):
    observation_id: str
    run_id: str
    session_id: str
    ownership_epoch: int = Field(ge=0)
    origin: str
    route: str
    targets: tuple[ObservedTarget, ...]
    visible_text: tuple[str, ...] = ()
    destinations: tuple[str, ...] = ()
    dialog: Literal["none", "unknown"] = "none"
    screenshot: None = None  # Phase 1 deliberately cannot persist image bytes.


class RetryPolicy(Contract):
    max_retries: int = Field(default=0, ge=0, le=3)
    settle_observations: int = Field(default=2, ge=1, le=20)
    interval_ms: int = Field(default=10, ge=1, le=1000)


class Step(Contract):
    id: Name
    action: ExecutableAction
    preconditions: tuple[Predicate, ...] = Field(min_length=1)
    postconditions: tuple[Predicate, ...] = Field(min_length=1)
    retry: RetryPolicy = RetryPolicy()


class Extraction(Contract):
    target: Name
    source: Literal["text", "value"]
    parser: Literal["string", "money_minor", "boolean"]


class OutputMatch(Contract):
    output: Name
    expected: BoundValue


class BusinessOutcomeDefinition(Contract):
    code: Name
    details: dict[Name, ValueDefinition]


class DiscoveryAssistance(Contract):
    """A candidate dependency, never a recording of executable human actions."""

    checkpoints: tuple[Name, ...] = Field(min_length=1)
    dependency: Literal["same_session_operator_restoration"] = "same_session_operator_restoration"
    replay_status: Literal["candidate_unverified"] = "candidate_unverified"


class Provenance(Contract):
    kind: Literal["test_fixture", "live_discovery"]
    action_source: Literal["hand_authored_test", "observed_live"]
    contract_source: Literal["caller"] = "caller"
    detector_source: Literal["authored_profile"] = "authored_profile"
    discovery_run_id: str | None = None
    assistance: DiscoveryAssistance | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def honest(self) -> Self:
        live = self.kind == "live_discovery"
        if live != (self.action_source == "observed_live") or live != bool(self.discovery_run_id):
            raise ValueError("inconsistent provenance")
        return self


class CapabilityArtifact(Contract):
    schema_version: Literal[1] = 1
    capability_id: Name
    capability_version: int = Field(ge=1)
    goal: GoalContract
    surface_requirements: tuple[Literal["web", "frames", "visible_text", "form_controls"], ...]
    targets: dict[Name, TargetSpec]
    steps: tuple[Step, ...] = Field(min_length=1)
    preconditions: tuple[Predicate, ...] = Field(min_length=1)
    identity_checks: tuple[Predicate, ...] = Field(min_length=1)
    final_checks: tuple[Predicate, ...] = Field(min_length=1)
    extractions: dict[Name, Extraction]
    output_matches: tuple[OutputMatch, ...] = Field(min_length=1)
    business_outcomes: tuple[BusinessOutcomeDefinition, ...] = ()
    recovery_rules: tuple[Name, ...] = ()
    required_permissions: tuple[Name, ...]
    redaction: tuple[Name, ...]
    provenance: Provenance

    @model_validator(mode="after")
    def references(self) -> Self:
        if set(self.extractions) != set(self.goal.outputs):
            raise ValueError("incomplete output contract")
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("duplicate step")
        restricted = {k for k, v in self.goal.inputs.items() if v.classification == "restricted"}
        if not restricted.issubset(self.redaction):
            raise ValueError("missing restricted input redaction")
        for node in walk(self):
            if isinstance(node, InputRef) and node.name not in self.goal.inputs:
                raise ValueError("unresolved input reference")
            if isinstance(node, LocalRef):
                # Locals are populated only by terminal extraction in this core.
                if node.name not in self.extractions:
                    raise ValueError("unresolved local reference")
            target = getattr(node, "target", None)
            if target is not None and target not in self.targets:
                raise ValueError("unresolved target")
            if isinstance(node, OutputMatch) and node.output not in self.goal.outputs:
                raise ValueError("unresolved output")
        return self


class Ownership(StrEnum):
    AUTOMATION = "AUTOMATION"
    PAUSING = "PAUSING"
    AWAITING_OPERATOR = "AWAITING_OPERATOR"
    HUMAN = "HUMAN"
    VALIDATING_RESUME = "VALIDATING_RESUME"
    ABORTED = "ABORTED"


class FailureCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INVALID_ARTIFACT = "INVALID_ARTIFACT"
    POLICY_DENIED = "POLICY_DENIED"
    INCOMPATIBLE = "INCOMPATIBLE"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    OWNERSHIP_DENIED = "OWNERSHIP_DENIED"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    UNKNOWN_ACTION_OUTCOME = "UNKNOWN_ACTION_OUTCOME"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RECOVERY_EXHAUSTED = "RECOVERY_EXHAUSTED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INVALID_RESUME = "INVALID_RESUME"
    INTERRUPTED = "INTERRUPTED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    CONTRADICTORY_STATE = "CONTRADICTORY_STATE"


class Metadata(Contract):
    run_id: str
    artifact_hash: str
    profile_hash: str
    failing_step: str | None = None
    retry_count: int = 0
    provider_call_count: int = Field(default=0, ge=0)
    human_intervened: bool = False
    step_index: int | None = Field(default=None, ge=0)


class Success(Contract):
    kind: Literal["success"] = "success"
    outputs: dict[str, Scalar]
    metadata: Metadata


class BusinessOutcome(Contract):
    kind: Literal["business_outcome"] = "business_outcome"
    code: Name
    details: dict[str, Scalar]
    metadata: Metadata


DiagnosticStage = Literal[
    "preflight",
    "observation",
    "initial",
    "preconditions",
    "action",
    "postconditions",
    "guard",
    "identity",
    "terminal",
    "extraction",
    "output_match",
    "recovery",
    "resume",
    "discovery",
]
DiagnosticExpected = Literal[
    "valid_contract",
    "compatible_surface",
    "authorized_action",
    "fresh_observation",
    "unique_target",
    "supported_control",
    "visible",
    "count_equals",
    "text_equals",
    "value_equals",
    "route_matches",
    "all",
    "string",
    "boolean",
    "money_minor",
    "declared_output_type",
    "output_match",
    "known_state",
    "within_budget",
    "checkpoint",
    "valid_decision",
]
DiagnosticObserved = Literal[
    "not_evaluated",
    "matched",
    "missing",
    "ambiguous",
    "mismatch",
    "invalid_format",
    "invalid_type",
    "denied",
    "stale",
    "interrupted",
    "budget_exhausted",
    "unknown",
]
ActionPurpose = Literal["direct", "replay", "recovery", "discovery"]


class Diagnostic(Contract):
    """Value-free context; indexes address sorted artifact keys, never UI text.

    Predicate paths address tuple positions within the named checkpoint stage.
    Guard indexes address the profile's ordered guards. An index at len(steps)
    in result metadata denotes final verification, not another browser action.
    """

    stage: DiagnosticStage
    expected: DiagnosticExpected
    observed: DiagnosticObserved
    target_index: int | None = Field(default=None, ge=0)
    output_index: int | None = Field(default=None, ge=0)
    predicate_path: tuple[Annotated[int, Field(ge=0)], ...] = ()
    guard_index: int | None = Field(default=None, ge=0)
    match_count: int | None = Field(default=None, ge=0)
    expected_count: int | None = Field(default=None, ge=0)


class Failure(Contract):
    kind: Literal["failure"] = "failure"
    code: FailureCode
    metadata: Metadata
    diagnostic: Diagnostic | None = None


TerminalResult = Annotated[Success | BusinessOutcome | Failure, Field(discriminator="kind")]


class Intervention(Contract):
    state: Literal["awaiting_operator"] = "awaiting_operator"
    reason: Literal["session_expired", "unknown_dialog", "unsupported_state"]
    step: Name
    session_id: str
    ownership_epoch: int
    metadata: Metadata


def walk(value: object) -> list[Contract]:
    """Traverse typed data, including reference positions inside locators and predicates."""
    result: list[Contract] = []
    if isinstance(value, Contract):
        result.append(value)
        for name in type(value).model_fields:
            result.extend(walk(getattr(value, name)))
    elif isinstance(value, dict):
        for item in value.values():
            result.extend(walk(item))
    elif isinstance(value, (tuple, list)):
        for item in value:
            result.extend(walk(item))
    return result
