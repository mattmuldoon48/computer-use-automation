"""Single-owner action gate and same-context, checkpoint-bound handoff."""

import asyncio
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .contracts import (
    ActionProposal,
    ActionPurpose,
    CapabilityArtifact,
    Click,
    Diagnostic,
    DiagnosticExpected,
    DiagnosticObserved,
    DiagnosticStage,
    ExecutableAction,
    FailureCode,
    Fill,
    Navigate,
    Observation,
    ObservedTarget,
    Ownership,
    PublicLiteral,
    RoleLocator,
    Scalar,
    Select,
    Step,
    TableLocator,
    TargetSpec,
    Wait,
)
from .errors import RuntimeFault
from .evidence import EvidenceSink, diagnostic_projection
from .policy import Effect, Policy
from .surfaces.base import CaptureSurface, Surface
from .values import evaluate_all, matches, resolve

_SAFE_EFFECTS = frozenset({Effect.READ, Effect.SAFE_OVERWRITE})
_REASONS = frozenset({"session_expired", "unknown_dialog", "unsupported_state"})


def _fault_diagnostic(code: FailureCode, stage: DiagnosticStage) -> Diagnostic:
    expected: DiagnosticExpected = "known_state"
    observed: DiagnosticObserved = "unknown"
    if code in {FailureCode.INVALID_ARGUMENT, FailureCode.INVALID_ARTIFACT}:
        expected, observed = "valid_contract", "invalid_format"
    elif code is FailureCode.INCOMPATIBLE:
        expected, observed = "compatible_surface", "mismatch"
    elif code is FailureCode.STALE_OBSERVATION:
        expected, observed = "fresh_observation", "stale"
    elif code in {
        FailureCode.POLICY_DENIED,
        FailureCode.PERMISSION_DENIED,
        FailureCode.OWNERSHIP_DENIED,
    }:
        expected, observed = "authorized_action", "denied"
    elif code is FailureCode.BUDGET_EXCEEDED:
        expected, observed = "within_budget", "budget_exhausted"
    elif code is FailureCode.INTERRUPTED:
        observed = "interrupted"
    return Diagnostic(stage=stage, expected=expected, observed=observed)


def _same_state(previous: Observation, current: Observation) -> bool:
    """Ignore observation-local IDs, but not data used by any replay guard."""
    if (
        previous.origin != current.origin
        or previous.route != current.route
        or previous.visible_text != current.visible_text
        or previous.destinations != current.destinations
        or previous.dialog != current.dialog
        or len(previous.targets) != len(current.targets)
    ):
        return False
    for before, after in zip(previous.targets, current.targets, strict=True):
        if (
            before.spec != after.spec
            or before.role != after.role
            or before.control != after.control
            or before.visible != after.visible
            or before.text != after.text
            or type(before.value) is not type(after.value)
            or before.value != after.value
            or before.grounding != after.grounding
            or before.derived != after.derived
        ):
            return False
    return True


class Session:
    def __init__(
        self,
        surface: Surface,
        policy: Policy,
        evidence: EvidenceSink,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.__surface = surface
        self.__policy = policy.model_copy(deep=True)
        self.__evidence = evidence
        self._run_id = uuid4().hex if run_id is None else run_id
        self._session_id = uuid4().hex if session_id is None else session_id
        self._context_id = surface.context_id
        self._ownership = Ownership.AUTOMATION
        self._epoch = 0
        self._human_intervened = False
        self._action_count = 0
        self._lock = asyncio.Lock()
        self._observation: Observation | None = None
        self._last_observation: Observation | None = None
        self._seen_observations: set[str] = set()
        self._paused_step: Step | None = None
        self._timeout: float = 180
        self._settling: asyncio.Task[None] | None = None

    @property
    def policy(self) -> Policy:
        return self.__policy

    @property
    def evidence(self) -> EvidenceSink:
        return self.__evidence

    @property
    def ownership(self) -> Ownership:
        return self._ownership

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def human_intervened(self) -> bool:
        return self._human_intervened

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def action_count(self) -> int:
        return self._action_count

    def _transition(self, ownership: Ownership) -> None:
        self._ownership = ownership
        self._epoch += 1
        self._observation = None
        self.__evidence.emit("ownership", ownership=ownership, epoch=self._epoch)
        self.__surface.set_ownership(ownership)

    def _automation(self) -> None:
        if self._ownership != Ownership.AUTOMATION:
            raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)

    def _compatible(self, artifact: CapabilityArtifact, inputs: dict[str, Scalar]) -> None:
        if (
            self.__surface.context_id != self._context_id
            or self.__surface.binding != artifact.goal.binding
            or not set(artifact.surface_requirements).issubset(self.__surface.features)
            or (
                "frames" not in self.__surface.features
                and any(spec.frame.kind == "named_frame" for spec in artifact.targets.values())
            )
        ):
            raise RuntimeFault(FailureCode.INCOMPATIBLE)
        self.__policy.validate_artifact(artifact)
        if set(inputs) != set(artifact.goal.inputs) or any(
            not definition.accepts(inputs[name])
            for name, definition in artifact.goal.inputs.items()
        ):
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)

    async def _read(self) -> Observation:
        epoch = self._epoch
        if self.__surface.context_id != self._context_id:
            raise RuntimeFault(FailureCode.INCOMPATIBLE)
        try:
            observed = await self.__surface.observe(self._run_id, self._session_id, epoch)
        except RuntimeFault:
            raise
        except Exception:
            raise RuntimeFault(FailureCode.INTERRUPTED) from None
        if (
            self.__surface.context_id != self._context_id
            or observed.run_id != self._run_id
            or observed.session_id != self._session_id
            or observed.ownership_epoch != epoch
            or self._epoch != epoch
            or observed.observation_id in self._seen_observations
        ):
            raise RuntimeFault(FailureCode.STALE_OBSERVATION)
        self._last_observation = observed
        self._seen_observations.add(observed.observation_id)
        return observed

    async def observe(self) -> Observation:
        try:
            async with asyncio.timeout(self._timeout), self._lock:
                if self._ownership == Ownership.ABORTED:
                    raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
                self._observation = None
                observed = await self._read()
                self._observation = observed
                return observed
        except TimeoutError:
            raise RuntimeFault(FailureCode.BUDGET_EXCEEDED) from None

    def _target(
        self,
        action: ExecutableAction,
        artifact: CapabilityArtifact,
        observation: Observation,
        inputs: dict[str, Scalar],
        locals_: dict[str, Scalar],
    ) -> tuple[TargetSpec | None, ObservedTarget | None]:
        if not isinstance(action, (Click, Fill, Select)):
            return None, None
        spec = artifact.targets.get(action.target)
        if spec is None:
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        found = matches(spec, observation, inputs, locals_)
        if len(found) > 1:
            raise RuntimeFault(
                FailureCode.AMBIGUOUS_TARGET,
                Diagnostic(
                    stage="action",
                    expected="unique_target",
                    observed="ambiguous",
                    target_index=sorted(artifact.targets).index(action.target),
                    match_count=len(found),
                    expected_count=1,
                ),
            )
        if not found:
            raise RuntimeFault(
                FailureCode.TARGET_NOT_FOUND,
                Diagnostic(
                    stage="action",
                    expected="unique_target",
                    observed="missing",
                    target_index=sorted(artifact.targets).index(action.target),
                    match_count=0,
                    expected_count=1,
                ),
            )
        target = found[0]
        locator = spec.locator
        if (
            (isinstance(locator, RoleLocator) and target.role != locator.role)
            or (isinstance(locator, TableLocator) and target.control != locator.control)
            or (isinstance(action, Fill) and target.control not in {"input", "textarea"})
            or (isinstance(action, Select) and target.control != "select")
            or (isinstance(action, Click) and target.control not in {"button", "link"})
        ):
            raise RuntimeFault(
                FailureCode.TARGET_NOT_FOUND,
                Diagnostic(
                    stage="action",
                    expected="supported_control",
                    observed="mismatch",
                    target_index=sorted(artifact.targets).index(action.target),
                    match_count=1,
                    expected_count=1,
                ),
            )
        return spec, target

    def _authorization(
        self,
        action: ExecutableAction,
        artifact: CapabilityArtifact,
        observed: Observation,
        inputs: dict[str, Scalar],
        locals_: dict[str, Scalar],
        caller_permissions: frozenset[str],
    ) -> tuple[Effect, ObservedTarget | None, Scalar | None]:
        if observed.origin != artifact.goal.binding.origin or observed.dialog != "none":
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        spec, target = self._target(action, artifact, observed, inputs, locals_)
        value: Scalar | None = None
        authorized_action = action
        if isinstance(action, (Fill, Select)):
            value = resolve(action.value, inputs, locals_)
        elif isinstance(action, Navigate):
            value = resolve(action.destination, inputs, locals_)
            if not isinstance(value, str) or value not in observed.destinations:
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            authorized_action = Navigate(destination=PublicLiteral(value=value))
        effect = self.__policy.authorize(
            authorized_action,
            spec,
            observed.origin,
            observed.route,
            artifact.required_permissions,
            caller_permissions,
        )
        return effect, target, value

    def _settled(self, task: asyncio.Task[None]) -> None:
        # A timed-out/cancelled caller does not undo an application action. Retain
        # the gate until that action really settles; consume diagnostics privately.
        if not task.cancelled():
            task.exception()
        self._settling = None
        self._lock.release()

    async def execute(
        self,
        proposal: ActionProposal,
        artifact: CapabilityArtifact,
        inputs: dict[str, Scalar],
        locals_: dict[str, Scalar],
        caller_permissions: frozenset[str],
        *,
        purpose: ActionPurpose = "direct",
        step_index: int | None = None,
        guard_index: int | None = None,
    ) -> None:
        effect: Effect | None = None
        performed = False
        release_lock = False
        trace: dict[str, object] = {
            "purpose": purpose,
            "step_index": step_index,
            "guard_index": guard_index,
        }
        action = proposal.action
        if isinstance(action, (Click, Fill, Select, Navigate, Wait)):
            trace["action_kind"] = action.kind
        if isinstance(action, (Click, Fill, Select)) and action.target in artifact.targets:
            trace["target_index"] = sorted(artifact.targets).index(action.target)
        try:
            self._automation()
            self._compatible(artifact, inputs)
            self._timeout = artifact.goal.budgets.active_seconds
            async with asyncio.timeout(self._timeout):
                await self._lock.acquire()
                release_lock = True
                self._automation()
                cached = self._observation
                if (
                    cached is None
                    or proposal.ownership_epoch != self._epoch
                    or proposal.observation_id != cached.observation_id
                    or cached.run_id != self._run_id
                    or cached.session_id != self._session_id
                    or cached.ownership_epoch != self._epoch
                ):
                    raise RuntimeFault(FailureCode.STALE_OBSERVATION)
                action = proposal.action
                if not isinstance(action, (Click, Fill, Select, Navigate, Wait)):
                    raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
                if self._action_count >= artifact.goal.budgets.max_actions:
                    raise RuntimeFault(FailureCode.BUDGET_EXCEEDED)
                self._observation = None
                fresh = await self._read()
                self.__evidence.observation(fresh, artifact, inputs, locals_, step_index=step_index)
                self._automation()
                if not _same_state(cached, fresh):
                    raise RuntimeFault(FailureCode.STALE_OBSERVATION)
                effect, target, value = self._authorization(
                    action, artifact, fresh, inputs, locals_, caller_permissions
                )
                trace["action_index"] = self._action_count
                trace["effect"] = effect
                self.__evidence.emit("action_intent", epoch=self._epoch, **trace)
                self._action_count += 1
                performed = True
                task = asyncio.create_task(self.__surface.perform(action, target, value))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Detached settlement is still serialized by this same lock.
                    self._settling = task
                    task.add_done_callback(self._settled)
                    release_lock = False
                    raise
                except RuntimeFault:
                    raise
                except Exception:
                    code = (
                        FailureCode.POSTCONDITION_FAILED
                        if effect in _SAFE_EFFECTS
                        else FailureCode.UNKNOWN_ACTION_OUTCOME
                    )
                    raise RuntimeFault(code) from None
        except TimeoutError:
            code = (
                FailureCode.UNKNOWN_ACTION_OUTCOME
                if performed and effect not in _SAFE_EFFECTS
                else FailureCode.BUDGET_EXCEEDED
            )
            diagnostic = _fault_diagnostic(code, "action")
            self.__evidence.emit(
                "action_rejected", code=code, epoch=self._epoch, diagnostic=diagnostic, **trace
            )
            raise RuntimeFault(code, diagnostic) from None
        except asyncio.CancelledError:
            code = (
                FailureCode.UNKNOWN_ACTION_OUTCOME
                if performed and effect not in _SAFE_EFFECTS
                else FailureCode.INTERRUPTED
            )
            diagnostic = _fault_diagnostic(code, "action")
            self.__evidence.emit(
                "action_rejected", code=code, epoch=self._epoch, diagnostic=diagnostic, **trace
            )
            raise RuntimeFault(code, diagnostic) from None
        except RuntimeFault as fault:
            diagnostic = (
                fault.diagnostic
                if isinstance(fault.diagnostic, Diagnostic)
                and diagnostic_projection(fault.diagnostic) is not None
                else _fault_diagnostic(fault.code, "action")
            )
            indexes = {}
            for name in ("target_index", "guard_index"):
                index = trace.get(name)
                if type(index) is int and index >= 0:
                    indexes[name] = index
            diagnostic = diagnostic.model_copy(update=indexes)
            self.__evidence.emit(
                "action_rejected",
                code=fault.code,
                epoch=self._epoch,
                diagnostic=diagnostic,
                **trace,
            )
            raise RuntimeFault(fault.code, diagnostic) from None
        finally:
            if release_lock:
                self._lock.release()

    async def pause(self, reason: str, step: Step) -> None:
        self._automation()
        if reason not in _REASONS or not isinstance(step, Step):
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        self._paused_step = step.model_copy(deep=True)
        self._transition(Ownership.PAUSING)
        try:
            async with asyncio.timeout(self._timeout), self._lock:
                if self._ownership != Ownership.PAUSING:
                    raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
                self._transition(Ownership.AWAITING_OPERATOR)
        except TimeoutError:
            raise RuntimeFault(FailureCode.BUDGET_EXCEEDED) from None

    async def takeover(self) -> None:
        # Pause already settled the gate; reject a premature takeover immediately.
        if self._ownership != Ownership.AWAITING_OPERATOR:
            raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
        async with self._lock:
            if self._ownership != Ownership.AWAITING_OPERATOR:
                raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
            self._human_intervened = True
            self._transition(Ownership.HUMAN)

    async def resume(
        self,
        artifact: CapabilityArtifact,
        inputs: dict[str, Scalar],
        caller_permissions: frozenset[str],
        step: Step,
    ) -> Literal["retry", "advance"]:
        if self.ownership != Ownership.HUMAN:
            raise RuntimeFault(
                FailureCode.OWNERSHIP_DENIED,
                _fault_diagnostic(FailureCode.OWNERSHIP_DENIED, "resume"),
            )
        self._transition(Ownership.VALIDATING_RESUME)
        try:
            async with asyncio.timeout(artifact.goal.budgets.active_seconds), self._lock:
                if self._ownership != Ownership.VALIDATING_RESUME:
                    raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
                if self._paused_step != step or step not in artifact.steps:
                    raise RuntimeFault(
                        FailureCode.INVALID_RESUME,
                        Diagnostic(stage="resume", expected="checkpoint", observed="mismatch"),
                    )
                self._compatible(artifact, inputs)
                if not set(artifact.required_permissions).issubset(caller_permissions):
                    raise RuntimeFault(FailureCode.PERMISSION_DENIED)
                observed = await self._read()
                self.__evidence.observation(
                    observed,
                    artifact,
                    inputs,
                    {},
                    step_index=artifact.steps.index(step),
                    stage="resume",
                )
                if (
                    observed.origin != artifact.goal.binding.origin
                    or observed.dialog != "none"
                    or not any(
                        rule.origin == observed.origin
                        and rule.route == observed.route
                        and rule.permission in artifact.required_permissions
                        and rule.effect not in {Effect.UNKNOWN, Effect.IRREVERSIBLE}
                        for rule in self.__policy.rules
                    )
                ):
                    raise RuntimeFault(
                        FailureCode.INVALID_RESUME,
                        Diagnostic(stage="resume", expected="known_state", observed="mismatch"),
                    )
                if not evaluate_all(artifact.identity_checks, observed, artifact, inputs, {}):
                    raise RuntimeFault(
                        FailureCode.INVALID_RESUME,
                        Diagnostic(stage="identity", expected="checkpoint", observed="mismatch"),
                    )
                result: Literal["retry", "advance"]
                if evaluate_all(step.postconditions, observed, artifact, inputs, {}):
                    result = "advance"
                elif evaluate_all(step.preconditions, observed, artifact, inputs, {}):
                    effect, _, _ = self._authorization(
                        step.action, artifact, observed, inputs, {}, caller_permissions
                    )
                    if effect not in _SAFE_EFFECTS:
                        raise RuntimeFault(
                            FailureCode.INVALID_RESUME,
                            Diagnostic(
                                stage="resume", expected="authorized_action", observed="denied"
                            ),
                        )
                    result = "retry"
                else:
                    raise RuntimeFault(
                        FailureCode.INVALID_RESUME,
                        Diagnostic(stage="resume", expected="checkpoint", observed="mismatch"),
                    )
                if self._ownership != Ownership.VALIDATING_RESUME:
                    raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
                self._paused_step = None
                self._transition(Ownership.AUTOMATION)
                return result
        except TimeoutError:
            raise RuntimeFault(
                FailureCode.BUDGET_EXCEEDED,
                _fault_diagnostic(FailureCode.BUDGET_EXCEEDED, "resume"),
            ) from None
        except RuntimeFault as fault:
            diagnostic = (
                fault.diagnostic
                if isinstance(fault.diagnostic, Diagnostic)
                and diagnostic_projection(fault.diagnostic) is not None
                else _fault_diagnostic(fault.code, "resume")
            )
            if diagnostic.stage == "action":
                diagnostic = diagnostic.model_copy(update={"stage": "resume"})
            raise RuntimeFault(fault.code, diagnostic) from None
        finally:
            if self._ownership == Ownership.VALIDATING_RESUME:
                self._transition(Ownership.HUMAN)

    async def abort(self) -> None:
        if self._ownership != Ownership.ABORTED:
            self._transition(Ownership.ABORTED)
        try:
            async with asyncio.timeout(self._timeout), self._lock:
                pass
        except TimeoutError:
            raise RuntimeFault(FailureCode.BUDGET_EXCEEDED) from None

    async def capture_safe(self, path: Path) -> dict[str, object]:
        structure = self.__evidence.structure(self._last_observation)
        if not isinstance(self.__surface, CaptureSurface) or self._settling is not None:
            return {"withheld": True, "reason": "unsupported_or_unsettled", "structure": structure}
        try:
            async with asyncio.timeout(5), self._lock:
                captured = await self.__surface.capture_safe(path)
                return {**captured, "structure": structure}
        except Exception:
            return {"withheld": True, "reason": "capture_unavailable", "structure": structure}
