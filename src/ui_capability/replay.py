"""Model-free interpreter: fixed guard ordering, bounded verification and safe retries."""

from __future__ import annotations

import asyncio
import re
import time
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal

from pydantic import ValidationError

from .contracts import (
    ActionProposal,
    BusinessOutcome,
    CapabilityArtifact,
    ExecutableAction,
    Failure,
    FailureCode,
    InputRef,
    Intervention,
    LocalRef,
    Metadata,
    Navigate,
    Observation,
    Ownership,
    Predicate,
    PublicLiteral,
    Scalar,
    SecretRef,
    Step,
    Success,
    TerminalResult,
    walk,
)
from .errors import RuntimeFault
from .evidence import contract_hash
from .policy import Effect
from .profiles import Guard, Profile
from .session import Session
from .values import evaluate, evaluate_all, matches, resolve


def money_minor(text: str, locale: Literal["en_US"]) -> int:
    """Parse explicit en_US decimal money; no floats, rounding, or inferred currencies."""
    if (
        locale != "en_US"
        or len(text) > 128
        or not re.fullmatch(r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)\.[0-9]{2}", text)
    ):
        raise RuntimeFault(FailureCode.EXTRACTION_FAILED)
    try:
        with localcontext() as context:
            context.prec = max(28, len(text) + 2)
            return int(Decimal(text.replace(",", "")) * 100)
    except (InvalidOperation, ValueError):
        raise RuntimeFault(FailureCode.EXTRACTION_FAILED) from None


class _Stop(Exception):
    def __init__(self, result: TerminalResult | Intervention) -> None:
        self.result = result


class Replay:
    """One invocation. The caller holds this object while the same session is paused.

    Inputs/results stay in memory. Evidence receives a structural projection only.
    No provider object, callback, SDK, planner, or dynamic action code is accepted.
    """

    def __init__(
        self,
        artifact: CapabilityArtifact,
        profile: Profile,
        session: Session,
        caller_permissions: frozenset[str],
    ) -> None:
        # Deep copies keep caller mutation from changing the invocation contract mid-run.
        self.artifact = artifact.model_copy(deep=True)
        self.profile = profile.model_copy(deep=True)
        self.session = session
        self.permissions = caller_permissions
        self._inputs: dict[str, Scalar] = {}
        self._locals: dict[str, Scalar] = {}
        self._index = 0
        self._retries = 0
        self._recoveries: dict[str, int] = {}
        self._active_seconds = 0.0
        self._paused_at: float | None = None
        self._started = False
        self._terminal: TerminalResult | None = None
        self._run_lock = asyncio.Lock()
        self._artifact_hash = contract_hash(self.artifact)
        self._profile_hash = contract_hash(self.profile)

    def _metadata(self) -> Metadata:
        return Metadata(
            run_id=self.session.run_id,
            artifact_hash=self._artifact_hash,
            profile_hash=self._profile_hash,
            failing_step=(
                self.artifact.steps[self._index].id
                if self._index < len(self.artifact.steps)
                else None
            ),
            retry_count=self._retries,
            human_intervened=self.session.human_intervened,
            step_index=self._index if self._index < len(self.artifact.steps) else None,
        )

    def _failure(self, code: FailureCode) -> Failure:
        return Failure(code=code, metadata=self._metadata())

    def _record(self, result: TerminalResult | Intervention) -> TerminalResult | Intervention:
        self.session.evidence.result(result)
        if not isinstance(result, Intervention):
            self._terminal = result
        return result

    def _preflight(self, supplied: object) -> None:
        # Revalidate even model_construct/model_copy(update=...) caller objects.
        try:
            self.artifact = CapabilityArtifact.model_validate_json(self.artifact.model_dump_json())
            self.profile = Profile.model_validate_json(self.profile.model_dump_json())
        except (ValidationError, ValueError, TypeError):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT) from None
        self._validate_invocation(supplied)

    def _validate_invocation(self, supplied: object) -> None:
        """Validate shared invocation authority independently of an ordered recipe."""
        if not isinstance(supplied, dict) or set(supplied) != set(self.artifact.goal.inputs):
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        for name, definition in self.artifact.goal.inputs.items():
            if not definition.accepts(supplied[name]):
                raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        self._inputs = dict(supplied)
        self.session.policy.validate_artifact(self.artifact)
        if not set(self.artifact.required_permissions).issubset(self.permissions):
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        if self.profile.binding != self.artifact.goal.binding:
            raise RuntimeFault(FailureCode.INCOMPATIBLE)
        if any(
            self.artifact.targets.get(name) != target
            for name, target in self.profile.targets.items()
        ):
            raise RuntimeFault(FailureCode.INCOMPATIBLE)
        rules = {guard.id for guard in self.profile.guards if guard.kind == "recovery"}
        if not set(self.artifact.recovery_rules).issubset(rules):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        # Profile predicates and reference positions are bounded by the same artifact namespace.
        for node in walk(self.profile):
            target = getattr(node, "target", None)
            if target is not None and target not in self.artifact.targets:
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
            if isinstance(node, InputRef) and node.name not in self._inputs:
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
            if isinstance(node, (LocalRef, SecretRef)):
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
            if isinstance(node, PublicLiteral) and not any(
                type(node.value) is type(value) and node.value == value
                for value in self.session.policy.approved_literals
            ):
                raise RuntimeFault(FailureCode.POLICY_DENIED)
        for guard in self.profile.guards:
            if guard.kind == "business":
                definitions = {item.code: item for item in self.artifact.business_outcomes}
                if guard.outcome_code not in definitions:
                    raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
            if guard.action is not None and guard.id in self.artifact.recovery_rules:
                action = self._policy_action(guard.action)
                target = self.artifact.targets.get(getattr(action, "target", ""))
                candidates = [
                    rule
                    for rule in self.session.policy.rules
                    if rule.action == action.kind and rule.target == target
                ]
                if not candidates:
                    raise RuntimeFault(FailureCode.POLICY_DENIED)
                for rule in candidates:
                    effect = self.session.policy.authorize(
                        action,
                        target,
                        rule.origin,
                        rule.route,
                        self.artifact.required_permissions,
                        self.permissions,
                    )
                    if not effect.retryable:
                        raise RuntimeFault(FailureCode.POLICY_DENIED)

    async def run(self, inputs: object) -> TerminalResult | Intervention:
        async with self._run_lock:
            if self._started:
                raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
            self._started = True
            try:
                self._preflight(inputs)
            except RuntimeFault as fault:
                return self._record(self._failure(fault.code))
            return await self._drive()

    async def resume(self) -> TerminalResult | Intervention:
        async with self._run_lock:
            if self._terminal is not None:
                raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
            if self._paused_at is None:
                raise RuntimeFault(FailureCode.INVALID_RESUME)
            if time.monotonic() - self._paused_at > self.artifact.goal.budgets.operator_seconds:
                await self.session.abort()
                return self._record(self._failure(FailureCode.BUDGET_EXCEEDED))
            # Invalid resume deliberately leaves the invocation resumable and HUMAN-owned.
            decision = await self.session.resume(
                self.artifact, self._inputs, self.permissions, self.artifact.steps[self._index]
            )
            if decision == "advance":
                self._index += 1
            self._paused_at = None
            return await self._drive()

    async def _drive(self) -> TerminalResult | Intervention:
        started = time.monotonic()
        remaining = self.artifact.goal.budgets.active_seconds - self._active_seconds
        try:
            if remaining <= 0:
                raise RuntimeFault(FailureCode.BUDGET_EXCEEDED)
            async with asyncio.timeout(remaining):
                if self._index == 0:
                    initial = await self._observe()
                    if not self._checks(self.artifact.preconditions, initial):
                        raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
                while self._index < len(self.artifact.steps):
                    await self._step(self.artifact.steps[self._index])
                    self._index += 1
                observed = await self._observe()
                if not self._verify(self.artifact.identity_checks, observed, "identity"):
                    raise RuntimeFault(FailureCode.IDENTITY_MISMATCH)
                if not self._verify(
                    self.profile.terminal_checks + self.artifact.final_checks, observed, "terminal"
                ):
                    raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
                result: TerminalResult | Intervention = self._extract(observed)
        except _Stop as stop:
            result = stop.result
        except RuntimeFault as fault:
            result = self._failure(fault.code)
        except TimeoutError:
            result = self._failure(FailureCode.BUDGET_EXCEEDED)
        except asyncio.CancelledError:
            await self._abort_safely()
            self._record(self._failure(FailureCode.INTERRUPTED))
            raise
        except Exception:
            # Adapter exceptions and rejected validation values never enter evidence.
            result = self._failure(FailureCode.INTERRUPTED)
        finally:
            self._active_seconds += time.monotonic() - started
        if isinstance(result, Failure):
            await self._abort_safely()
        return self._record(result)

    async def _abort_safely(self) -> None:
        try:
            await self.session.abort()
        except RuntimeFault:
            # Abort revokes ownership immediately. A still-settling action retains
            # the gate; a settlement timeout must not overwrite its original result.
            if self.session.ownership != Ownership.ABORTED:
                raise

    def _checks(self, predicates: tuple[Predicate, ...], observation: Observation) -> bool:
        return evaluate_all(predicates, observation, self.artifact, self._inputs, self._locals)

    def _verify(
        self, predicates: tuple[Predicate, ...], observation: Observation, stage: str
    ) -> bool:
        results = tuple(
            evaluate(predicate, observation, self.artifact, self._inputs, self._locals)
            for predicate in predicates
        )
        self.session.evidence.checkpoint(self._index, stage, len(results), sum(results))
        return all(results)

    async def _pause(
        self, reason: Literal["session_expired", "unknown_dialog", "unsupported_state"]
    ) -> None:
        if self._index >= len(self.artifact.steps):
            raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
        step = self.artifact.steps[self._index]
        await self.session.pause(reason, step)
        self._paused_at = time.monotonic()
        raise _Stop(
            Intervention(
                reason=reason,
                step=step.id,
                session_id=self.session.session_id,
                ownership_epoch=self.session.epoch,
                metadata=self._metadata(),
            )
        )

    async def _observe(self, *, allow_recovery: bool = True) -> Observation:
        while True:
            if self.session.ownership != Ownership.AUTOMATION:
                raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
            observed = await self.session.observe()
            if observed.origin != self.artifact.goal.binding.origin:
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            active = [
                guard for guard in self.profile.guards if self._checks((guard.when,), observed)
            ]
            # Safety has priority even if a business/success-looking state also appears.
            failures = [guard for guard in active if guard.kind == "failure"]
            if failures:
                raise RuntimeFault(failures[0].failure_code or FailureCode.PERMISSION_DENIED)
            if observed.dialog == "unknown":
                await self._pause("unknown_dialog")
            if len(active) > 1:
                raise RuntimeFault(FailureCode.CONTRADICTORY_STATE)
            if not active:
                return observed
            guard = active[0]
            if guard.kind == "intervention":
                assert guard.reason is not None
                await self._pause(guard.reason)
            if guard.kind == "business":
                raise _Stop(self._business(guard))
            if not allow_recovery or guard.id not in self.artifact.recovery_rules:
                raise RuntimeFault(FailureCode.RECOVERY_EXHAUSTED)
            count = self._recoveries.get(guard.id, 0)
            if count >= guard.max_count:
                raise RuntimeFault(FailureCode.RECOVERY_EXHAUSTED)
            self._recoveries[guard.id] = count + 1
            assert guard.action is not None
            await self._execute(guard.action, observed)
            self.session.evidence.emit("recovery", count=count + 1)
            recovered = await self._observe(allow_recovery=False)
            if not self._checks(self.artifact.identity_checks, recovered):
                raise RuntimeFault(FailureCode.IDENTITY_MISMATCH)
            if not self._checks(guard.checkpoint, recovered):
                raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
            return recovered

    def _business(self, guard: Guard) -> BusinessOutcome:
        definitions = [
            item for item in self.artifact.business_outcomes if item.code == guard.outcome_code
        ]
        if len(definitions) != 1:
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        details = {
            name: resolve(value, self._inputs, self._locals)
            for name, value in guard.details.items()
        }
        definition = definitions[0]
        if set(details) != set(definition.details) or any(
            not spec.accepts(details[name]) for name, spec in definition.details.items()
        ):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
        return BusinessOutcome(code=definition.code, details=details, metadata=self._metadata())

    async def _execute(self, action: ExecutableAction, observation: Observation) -> None:
        if self.session.action_count >= self.artifact.goal.budgets.max_actions:
            raise RuntimeFault(FailureCode.BUDGET_EXCEEDED)
        await self.session.execute(
            ActionProposal(
                observation_id=observation.observation_id,
                ownership_epoch=observation.ownership_epoch,
                action=action,
            ),
            self.artifact,
            self._inputs,
            self._locals,
            self.permissions,
        )

    def _policy_action(self, action: ExecutableAction) -> ExecutableAction:
        if isinstance(action, Navigate):
            return Navigate(
                destination=PublicLiteral(
                    value=resolve(action.destination, self._inputs, self._locals)
                )
            )
        return action

    async def _step(self, step: Step) -> None:
        observed = await self._observe()
        if not self._verify(step.preconditions, observed, "preconditions"):
            raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
        target = self.artifact.targets.get(getattr(step.action, "target", ""))
        effect = self.session.policy.authorize(
            self._policy_action(step.action),
            target,
            observed.origin,
            observed.route,
            self.artifact.required_permissions,
            self.permissions,
        )
        retryable = effect in {Effect.READ, Effect.SAFE_OVERWRITE}
        for attempt in range(step.retry.max_retries + 1):
            if not self._checks(step.preconditions, observed):
                raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
            try:
                await self._execute(step.action, observed)
            except RuntimeFault as fault:
                if (
                    fault.code
                    not in {FailureCode.UNKNOWN_ACTION_OUTCOME, FailureCode.POSTCONDITION_FAILED}
                    or not retryable
                ):
                    raise
            # Observe/wait before considering another action. Never infer success from click return.
            for poll in range(step.retry.settle_observations):
                if poll:
                    await asyncio.sleep(step.retry.interval_ms / 1000)
                observed = await self._observe()
                if self._verify(step.postconditions, observed, "postconditions"):
                    self.session.evidence.emit("action_verified")
                    return
            if not retryable:
                raise RuntimeFault(FailureCode.UNKNOWN_ACTION_OUTCOME)
            if attempt == step.retry.max_retries:
                raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
            if not self._checks(step.preconditions, observed):
                raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
            self._retries += 1
            self.session.evidence.emit("retry", count=self._retries)
            await asyncio.sleep(step.retry.interval_ms / 1000)
            observed = await self._observe()
            if self._checks(step.postconditions, observed):
                return

    def _extract(self, observation: Observation) -> Success:
        outputs: dict[str, Scalar] = {}
        for name, extraction in self.artifact.extractions.items():
            targets = matches(
                self.artifact.targets[extraction.target], observation, self._inputs, self._locals
            )
            if len(targets) != 1:
                raise RuntimeFault(FailureCode.EXTRACTION_FAILED)
            value = getattr(targets[0], extraction.source)
            if extraction.parser == "money_minor":
                if not isinstance(value, str):
                    raise RuntimeFault(FailureCode.EXTRACTION_FAILED)
                parsed: Scalar = money_minor(value, self.artifact.goal.binding.locale)
            elif extraction.parser == "boolean":
                if type(value) is bool:
                    parsed = value
                elif value in ("true", "false"):
                    parsed = value == "true"
                else:
                    raise RuntimeFault(FailureCode.EXTRACTION_FAILED)
            elif isinstance(value, str):
                parsed = value
            else:
                raise RuntimeFault(FailureCode.EXTRACTION_FAILED)
            if not self.artifact.goal.outputs[name].accepts(parsed):
                raise RuntimeFault(FailureCode.EXTRACTION_FAILED)
            outputs[name] = parsed
        self._locals = outputs
        for match in self.artifact.output_matches:
            expected = resolve(match.expected, self._inputs, self._locals)
            actual = outputs[match.output]
            if type(actual) is not type(expected) or actual != expected:
                raise RuntimeFault(FailureCode.IDENTITY_MISMATCH)
        return Success(outputs=outputs, metadata=self._metadata())
