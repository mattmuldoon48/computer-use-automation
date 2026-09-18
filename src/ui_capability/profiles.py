"""Authored application knowledge, separate from discovered capabilities."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .contracts import (
    Binding,
    BoundValue,
    Contract,
    ExecutableAction,
    FailureCode,
    Name,
    Predicate,
    TargetSpec,
    walk,
)


class Guard(Contract):
    id: Name
    kind: Literal["failure", "intervention", "business", "recovery"]
    when: Predicate
    failure_code: FailureCode | None = None
    reason: Literal["session_expired", "unknown_dialog", "unsupported_state"] | None = None
    outcome_code: Name | None = None
    details: dict[Name, BoundValue] = Field(default_factory=dict)
    action: ExecutableAction | None = None
    max_count: int = Field(default=1, ge=1, le=3)
    checkpoint: tuple[Predicate, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            (self.kind == "failure") != (self.failure_code is not None)
            or (self.kind == "intervention") != (self.reason is not None)
            or (self.kind == "business") != (self.outcome_code is not None)
            or (self.kind == "recovery") != (self.action is not None)
        ):
            raise ValueError("guard payload mismatch")
        if self.kind != "business" and self.details:
            raise ValueError("only business guards return details")
        if self.kind == "recovery" and not self.checkpoint:
            raise ValueError("recovery needs a checkpoint")
        return self


class Profile(Contract):
    profile_id: Name
    binding: Binding
    targets: dict[Name, TargetSpec]
    terminal_checks: tuple[Predicate, ...] = Field(min_length=1)
    guards: tuple[Guard, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({guard.id for guard in self.guards}) != len(self.guards):
            raise ValueError("duplicate guard")
        for node in walk((self.guards, self.terminal_checks)):
            target = getattr(node, "target", None)
            if target is not None and target not in self.targets:
                raise ValueError("unresolved profile target")
        return self
