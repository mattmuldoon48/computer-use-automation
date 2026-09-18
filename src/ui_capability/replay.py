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
    ActionPurpose,
    BusinessOutcome,
    CapabilityArtifact,
    Conjunction,
    CountEquals,
    Diagnostic,
    DiagnosticStage,
    Equals,
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
    RouteMatches,
    Scalar,
    SecretRef,
    Step,
    Success,
    TerminalResult,
    Visible,
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
        self._target_indexes = {
            name: index for index, name in enumerate(sorted(self.artifact.targets))
        }
        self._output_indexes = {
            name: index for index, name in enumerate(sorted(self.artifact.goal.outputs))
        }
        self._diagnostic = Diagnostic(
            stage="preflight", expected="valid_contract", observed="not_evaluated"
        )

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
            step_index=self._index,
        )

    def _failure(self, code: FailureCode, diagnostic: Diagnostic | None = None) -> Failure:
        if diagnostic is None:
            diagnostic = self._diagnostic
            if code == FailureCode.BUDGET_EXCEEDED:
                diagnostic = diagnostic.model_copy(
                    update={"expected": "within_budget", "observed": "budget_exhausted"}
                )
            elif code == FailureCode.INTERRUPTED:
                diagnostic = diagnostic.model_copy(update={"observed": "interrupted"})
            elif code in {
                FailureCode.POLICY_DENIED,
                FailureCode.PERMISSION_DENIED,
                FailureCode.OWNERSHIP_DENIED,
            }:
                diagnostic = diagnostic.model_copy(update={"observed": "denied"})
            elif (
                code == FailureCode.UNKNOWN_ACTION_OUTCOME
                and diagnostic.observed == "not_evaluated"
            ):
                diagnostic = diagnostic.model_copy(update={"observed": "unknown"})
        return Failure(code=code, metadata=self._metadata(), diagnostic=diagnostic)

    def _record(self, result: TerminalResult | Intervention) -> TerminalResult | Intervention:
        self.session.evidence.result(result)
        if not isinstance(result, Intervention):
            self._terminal = result
        return result

    def _preflight(self, supplied: object) -> None:
        # Revalidate even model_construct/model_copy(update=...) caller objects.
        self._diagnostic = Diagnostic(
            stage="preflight", expected="valid_contract", observed="invalid_format"
        )
        try:
            self.artifact = CapabilityArtifact.model_validate_json(self.artifact.model_dump_json())
            self.profile = Profile.model_validate_json(self.profile.model_dump_json())
        except (ValidationError, ValueError, TypeError):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT) from None
        self._validate_invocation(supplied)

    def _validate_invocation(self, supplied: object) -> None:
        """Validate shared invocation authority independently of an ordered recipe."""
        self._diagnostic = Diagnostic(
            stage="preflight", expected="valid_contract", observed="invalid_type"
        )
        if not isinstance(supplied, dict) or set(supplied) != set(self.artifact.goal.inputs):
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        for name, definition in self.artifact.goal.inputs.items():
            if not definition.accepts(supplied[name]):
                raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        self._inputs = dict(supplied)
        self._diagnostic = Diagnostic(
            stage="preflight", expected="authorized_action", observed="denied"
        )
        self.session.policy.validate_artifact(self.artifact)
        if not set(self.artifact.required_permissions).issubset(self.permissions):
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        self._diagnostic = Diagnostic(
            stage="preflight", expected="compatible_surface", observed="mismatch"
        )
        if self.profile.binding != self.artifact.goal.binding:
            raise RuntimeFault(FailureCode.INCOMPATIBLE)
        for name, profile_target in self.profile.targets.items():
            if self.artifact.targets.get(name) != profile_target:
                self._diagnostic = self._diagnostic.model_copy(
                    update={"target_index": self._target_indexes.get(name)}
                )
                raise RuntimeFault(FailureCode.INCOMPATIBLE)
        self._diagnostic = Diagnostic(
            stage="preflight", expected="valid_contract", observed="mismatch"
        )
        assistance = self.artifact.provenance.assistance
        if assistance is not None:
            checkpoints = {item.id: item for item in self.profile.discovery_checkpoints}
            for name in assistance.checkpoints:
                checkpoint = checkpoints.get(name)
                if checkpoint is None:
                    raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
                if checkpoint.action is not None and not any(
                    step.action == checkpoint.action
                    and step.preconditions == checkpoint.before
                    and step.postconditions == checkpoint.restored
                    for step in self.artifact.steps
                ):
                    raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
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
        for guard_index, guard in enumerate(self.profile.guards):
            self._diagnostic = Diagnostic(
                stage="preflight",
                expected="valid_contract",
                observed="mismatch",
                guard_index=guard_index,
            )
            if guard.kind == "business":
                definitions = {item.code: item for item in self.artifact.business_outcomes}
                if guard.outcome_code not in definitions:
                    raise RuntimeFault(FailureCode.INVALID_ARTIFACT)
            if guard.action is not None and guard.id in self.artifact.recovery_rules:
                self._diagnostic = Diagnostic(
                    stage="preflight",
                    expected="authorized_action",
                    observed="denied",
                    guard_index=guard_index,
                    target_index=self._target_indexes.get(getattr(guard.action, "target", "")),
                )
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
                raise RuntimeFault(
                    FailureCode.OWNERSHIP_DENIED,
                    Diagnostic(stage="preflight", expected="authorized_action", observed="denied"),
                )
            self._started = True
            try:
                self._preflight(inputs)
            except RuntimeFault as fault:
                return self._record(self._failure(fault.code, fault.diagnostic))
            return await self._drive()

    async def resume(self) -> TerminalResult | Intervention:
        async with self._run_lock:
            self._diagnostic = Diagnostic(
                stage="resume", expected="checkpoint", observed="mismatch"
            )
            if self._terminal is not None:
                raise RuntimeFault(
                    FailureCode.OWNERSHIP_DENIED,
                    Diagnostic(stage="resume", expected="authorized_action", observed="denied"),
                )
            if self._paused_at is None:
                raise RuntimeFault(FailureCode.INVALID_RESUME, self._diagnostic)
            if time.monotonic() - self._paused_at > self.artifact.goal.budgets.operator_seconds:
                await self.session.abort()
                return self._record(self._failure(FailureCode.BUDGET_EXCEEDED))
            # Invalid resume deliberately leaves the invocation resumable and HUMAN-owned.
            try:
                decision = await self.session.resume(
                    self.artifact, self._inputs, self.permissions, self.artifact.steps[self._index]
                )
            except RuntimeFault as fault:
                raise RuntimeFault(
                    fault.code, self._failure(fault.code, fault.diagnostic).diagnostic
                ) from None
            if decision == "advance":
                self._index += 1
            self._paused_at = None
            return await self._drive(resuming=True)

    async def _drive(self, *, resuming: bool = False) -> TerminalResult | Intervention:
        started = time.monotonic()
        remaining = self.artifact.goal.budgets.active_seconds - self._active_seconds
        try:
            if remaining <= 0:
                raise RuntimeFault(FailureCode.BUDGET_EXCEEDED)
            async with asyncio.timeout(remaining):
                if self._index == 0:
                    initial = await self._observe()
                    if not self._verify(self.artifact.preconditions, initial, "initial"):
                        raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
                while self._index < len(self.artifact.steps):
                    if resuming:
                        observed = await self._observe()
                        step = self.artifact.steps[self._index]
                        if self._verify(step.postconditions, observed, "postconditions"):
                            if not self._verify(
                                self.artifact.identity_checks, observed, "identity"
                            ):
                                raise RuntimeFault(FailureCode.IDENTITY_MISMATCH)
                            # The operator may have completed several checkpoints.
                            # Do not repeat a verified effect merely to replay its recipe.
                            self._index += 1
                            continue
                        resuming = False
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
            result = self._failure(fault.code, fault.diagnostic)
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
        self,
        predicates: tuple[Predicate, ...],
        observation: Observation,
        stage: DiagnosticStage,
        *,
        guard_index: int | None = None,
    ) -> bool:
        self._diagnostic = Diagnostic(
            stage=stage,
            expected="checkpoint",
            observed="not_evaluated",
            guard_index=guard_index,
        )
        details: list[Diagnostic] = []
        results: list[bool] = []
        try:
            for index, predicate in enumerate(predicates):
                results.append(
                    self._predicate(predicate, observation, stage, (index,), guard_index, details)
                )
        finally:
            self.session.evidence.checkpoint(
                self._index, stage, len(predicates), sum(results), details=tuple(details)
            )
        # Children precede their conjunction, so the first failed leaf is precise.
        self._diagnostic = next(
            (detail for detail in details if detail.observed != "matched"),
            details[0]
            if details
            else Diagnostic(
                stage=stage, expected="checkpoint", observed="matched", guard_index=guard_index
            ),
        )
        return all(results)

    def _predicate(
        self,
        predicate: Predicate,
        observation: Observation,
        stage: DiagnosticStage,
        path: tuple[int, ...],
        guard_index: int | None,
        details: list[Diagnostic],
    ) -> bool:
        diagnostic = Diagnostic(
            stage=stage,
            expected=predicate.kind,
            observed="not_evaluated",
            predicate_path=path,
            guard_index=guard_index,
            target_index=self._target_indexes.get(getattr(predicate, "target", "")),
        )
        self._diagnostic = diagnostic
        if isinstance(predicate, Conjunction):
            result = True
            for index, child in enumerate(predicate.conditions):
                # Never short circuit: later ambiguity must override an earlier false result.
                result = (
                    self._predicate(child, observation, stage, (*path, index), guard_index, details)
                    and result
                )
        elif isinstance(predicate, RouteMatches):
            result = evaluate(predicate, observation, self.artifact, self._inputs, self._locals)
        else:
            spec = self.artifact.targets.get(predicate.target)
            if spec is None:
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT, diagnostic)
            found = matches(spec, observation, self._inputs, self._locals)
            diagnostic = diagnostic.model_copy(
                update={
                    "match_count": len(found),
                    "expected_count": predicate.count if isinstance(predicate, CountEquals) else 1,
                }
            )
            self._diagnostic = diagnostic
            if isinstance(predicate, CountEquals):
                result = len(found) == predicate.count
            elif len(found) > 1:
                diagnostic = diagnostic.model_copy(update={"observed": "ambiguous"})
                details.append(diagnostic)
                self._diagnostic = diagnostic
                raise RuntimeFault(FailureCode.AMBIGUOUS_TARGET, diagnostic)
            elif not found:
                diagnostic = diagnostic.model_copy(update={"observed": "missing"})
                result = False
            elif isinstance(predicate, Visible):
                result = True
            elif isinstance(predicate, Equals):
                expected = resolve(predicate.value, self._inputs, self._locals)
                actual = found[0].text if predicate.kind == "text_equals" else found[0].value
                result = type(actual) is type(expected) and actual == expected
            else:
                raise RuntimeFault(FailureCode.INVALID_ARTIFACT, diagnostic)
        if diagnostic.observed == "not_evaluated":
            diagnostic = diagnostic.model_copy(
                update={"observed": "matched" if result else "mismatch"}
            )
        details.append(diagnostic)
        self._diagnostic = diagnostic
        return result

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
            self._diagnostic = Diagnostic(
                stage="observation", expected="fresh_observation", observed="not_evaluated"
            )
            if self.session.ownership != Ownership.AUTOMATION:
                raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
            observed = await self.session.observe()
            self.session.evidence.observation(
                observed, self.artifact, self._inputs, self._locals, step_index=self._index
            )
            if observed.origin != self.artifact.goal.binding.origin:
                raise RuntimeFault(
                    FailureCode.POLICY_DENIED,
                    Diagnostic(
                        stage="observation", expected="compatible_surface", observed="mismatch"
                    ),
                )
            active: list[tuple[int, Guard, Diagnostic]] = []
            for guard_index, guard in enumerate(self.profile.guards):
                if self._verify((guard.when,), observed, "guard", guard_index=guard_index):
                    active.append((guard_index, guard, self._diagnostic))
            # Safety has priority even if a business/success-looking state also appears.
            failures = [entry for entry in active if entry[1].kind == "failure"]
            if failures:
                _, guard, diagnostic = failures[0]
                raise RuntimeFault(guard.failure_code or FailureCode.PERMISSION_DENIED, diagnostic)
            self._diagnostic = Diagnostic(
                stage="observation", expected="known_state", observed="matched"
            )
            if observed.dialog == "unknown":
                self._diagnostic = self._diagnostic.model_copy(update={"observed": "unknown"})
                await self._pause("unknown_dialog")
            if len(active) > 1:
                raise RuntimeFault(
                    FailureCode.CONTRADICTORY_STATE,
                    Diagnostic(
                        stage="guard",
                        expected="known_state",
                        observed="ambiguous",
                        match_count=len(active),
                        expected_count=1,
                    ),
                )
            if not active:
                return observed
            guard_index, guard, self._diagnostic = active[0]
            if guard.kind == "intervention":
                assert guard.reason is not None
                await self._pause(guard.reason)
            if guard.kind == "business":
                raise _Stop(self._business(guard))
            self._diagnostic = Diagnostic(
                stage="recovery",
                expected="authorized_action",
                observed="denied",
                guard_index=guard_index,
            )
            if not allow_recovery or guard.id not in self.artifact.recovery_rules:
                raise RuntimeFault(FailureCode.RECOVERY_EXHAUSTED)
            count = self._recoveries.get(guard.id, 0)
            if count >= guard.max_count:
                self._diagnostic = self._diagnostic.model_copy(
                    update={"expected": "within_budget", "observed": "budget_exhausted"}
                )
                raise RuntimeFault(FailureCode.RECOVERY_EXHAUSTED)
            self._recoveries[guard.id] = count + 1
            assert guard.action is not None
            action_index = self.session.action_count
            await self._execute(guard.action, observed, purpose="recovery", guard_index=guard_index)
            self.session.evidence.emit(
                "recovery",
                count=count + 1,
                action_index=action_index,
                step_index=self._index,
                guard_index=guard_index,
            )
            recovered = await self._observe(allow_recovery=False)
            if not self._verify(
                self.artifact.identity_checks, recovered, "identity", guard_index=guard_index
            ):
                raise RuntimeFault(FailureCode.IDENTITY_MISMATCH)
            if not self._verify(guard.checkpoint, recovered, "recovery", guard_index=guard_index):
                raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
            self.session.evidence.emit(
                "action_verified",
                action_index=action_index,
                step_index=self._index,
                guard_index=guard_index,
                purpose="recovery",
            )
            return recovered

    def _business(self, guard: Guard) -> BusinessOutcome:
        self._diagnostic = Diagnostic(
            stage="guard",
            expected="valid_contract",
            observed="mismatch",
            guard_index=self._diagnostic.guard_index,
        )
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

    async def _execute(
        self,
        action: ExecutableAction,
        observation: Observation,
        *,
        purpose: ActionPurpose = "replay",
        guard_index: int | None = None,
    ) -> None:
        self._diagnostic = Diagnostic(
            stage="action",
            expected="authorized_action",
            observed="not_evaluated",
            target_index=self._target_indexes.get(getattr(action, "target", "")),
            guard_index=guard_index,
        )
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
            purpose=purpose,
            step_index=self._index,
            guard_index=guard_index,
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
        self._diagnostic = Diagnostic(
            stage="action",
            expected="authorized_action",
            observed="not_evaluated",
            target_index=self._target_indexes.get(getattr(step.action, "target", "")),
        )
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
            if not self._verify(step.preconditions, observed, "preconditions"):
                raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
            action_index: int | None = self.session.action_count
            try:
                await self._execute(step.action, observed)
            except RuntimeFault as fault:
                if (
                    fault.code
                    not in {FailureCode.UNKNOWN_ACTION_OUTCOME, FailureCode.POSTCONDITION_FAILED}
                    or not retryable
                ):
                    raise
            if self.session.action_count == action_index:
                action_index = None
            # Observe/wait before considering another action. Never infer success from click return.
            for poll in range(step.retry.settle_observations):
                if poll:
                    await asyncio.sleep(step.retry.interval_ms / 1000)
                observed = await self._observe()
                if self._verify(step.postconditions, observed, "postconditions"):
                    self.session.evidence.emit(
                        "action_verified",
                        action_index=action_index,
                        step_index=self._index,
                        purpose="replay",
                    )
                    return
            if not retryable:
                raise RuntimeFault(FailureCode.UNKNOWN_ACTION_OUTCOME)
            if attempt == step.retry.max_retries:
                raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
            if not self._verify(step.preconditions, observed, "preconditions"):
                raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
            self._retries += 1
            self.session.evidence.emit(
                "retry", count=self._retries, action_index=action_index, step_index=self._index
            )
            await asyncio.sleep(step.retry.interval_ms / 1000)
            observed = await self._observe()
            if self._verify(step.postconditions, observed, "postconditions"):
                self.session.evidence.emit(
                    "action_verified",
                    action_index=action_index,
                    step_index=self._index,
                    purpose="replay",
                )
                return

    def _extract(self, observation: Observation) -> Success:
        outputs: dict[str, Scalar] = {}
        for name, extraction in self.artifact.extractions.items():
            self._diagnostic = Diagnostic(
                stage="extraction",
                expected="unique_target",
                observed="not_evaluated",
                output_index=self._output_indexes[name],
                target_index=self._target_indexes[extraction.target],
                expected_count=1,
            )
            targets = matches(
                self.artifact.targets[extraction.target], observation, self._inputs, self._locals
            )
            self._diagnostic = self._diagnostic.model_copy(
                update={
                    "match_count": len(targets),
                    "observed": "matched"
                    if len(targets) == 1
                    else ("missing" if not targets else "ambiguous"),
                }
            )
            if len(targets) != 1:
                raise RuntimeFault(FailureCode.EXTRACTION_FAILED, self._diagnostic)
            value = getattr(targets[0], extraction.source)
            self._diagnostic = self._diagnostic.model_copy(
                update={"expected": extraction.parser, "observed": "invalid_type"}
            )
            if extraction.parser == "money_minor":
                if not isinstance(value, str):
                    raise RuntimeFault(FailureCode.EXTRACTION_FAILED, self._diagnostic)
                self._diagnostic = self._diagnostic.model_copy(
                    update={"observed": "invalid_format"}
                )
                try:
                    parsed: Scalar = money_minor(value, self.artifact.goal.binding.locale)
                except RuntimeFault as fault:
                    raise RuntimeFault(fault.code, fault.diagnostic or self._diagnostic) from None
            elif extraction.parser == "boolean":
                if type(value) is bool:
                    parsed = value
                elif value in ("true", "false"):
                    parsed = value == "true"
                else:
                    if isinstance(value, str):
                        self._diagnostic = self._diagnostic.model_copy(
                            update={"observed": "invalid_format"}
                        )
                    raise RuntimeFault(FailureCode.EXTRACTION_FAILED, self._diagnostic)
            elif isinstance(value, str):
                parsed = value
            else:
                raise RuntimeFault(FailureCode.EXTRACTION_FAILED, self._diagnostic)
            if not self.artifact.goal.outputs[name].accepts(parsed):
                self._diagnostic = self._diagnostic.model_copy(
                    update={"expected": "declared_output_type", "observed": "mismatch"}
                )
                raise RuntimeFault(FailureCode.EXTRACTION_FAILED, self._diagnostic)
            outputs[name] = parsed
        self._locals = outputs
        for index, match in enumerate(self.artifact.output_matches):
            self._diagnostic = Diagnostic(
                stage="output_match",
                expected="output_match",
                observed="not_evaluated",
                output_index=self._output_indexes[match.output],
                target_index=self._target_indexes[self.artifact.extractions[match.output].target],
                predicate_path=(index,),
            )
            expected = resolve(match.expected, self._inputs, self._locals)
            actual = outputs[match.output]
            if type(actual) is not type(expected) or actual != expected:
                self._diagnostic = self._diagnostic.model_copy(update={"observed": "mismatch"})
                raise RuntimeFault(FailureCode.IDENTITY_MISMATCH, self._diagnostic)
        return Success(outputs=outputs, metadata=self._metadata())
