"""Bounded UI discovery. The planner proposes; deterministic gates decide and verify."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from .compiler import DeterministicCompiler, goal_template
from .contracts import (
    ActionProposal,
    CapabilityArtifact,
    Click,
    Diagnostic,
    DiscoveryAssistance,
    ExecutableAction,
    Failure,
    FailureCode,
    Fill,
    InputRef,
    Intervention,
    Metadata,
    Navigate,
    Observation,
    Ownership,
    PublicLiteral,
    Select,
    Success,
    TerminalResult,
    Wait,
)
from .errors import RuntimeFault
from .evidence import contract_hash
from .profiles import DiscoveryCheckpoint, Profile
from .provider import ModelDecision, OpenAIPlanner, Planner, ProviderError
from .replay import Replay, _Stop
from .session import Session


@dataclass(frozen=True)
class DiscoveryOutcome:
    result: TerminalResult | Intervention
    artifact: CapabilityArtifact | None


@dataclass(frozen=True)
class _PendingAction:
    action: ExecutableAction
    before: Observation
    action_index: int


def live_planner(planner: Planner) -> bool:
    """Only the real HTTP adapter can issue a live-discovery capability."""
    return type(planner) is OpenAIPlanner and planner.live


class _DiscoveryDriver(Replay):
    """Discovery trace with a separate, reviewed restoration protocol.

    Shared replay routines supply guard precedence, authority and output validation.
    Discovery never resumes against the empty template's nonexistent replay steps.
    """

    def __init__(
        self,
        template: CapabilityArtifact,
        profile: Profile,
        session: Session,
        planner: Planner,
        caller_permissions: frozenset[str],
    ) -> None:
        super().__init__(goal_template(template), profile, session, caller_permissions)
        self.planner = planner
        self._compiler: DeterministicCompiler | None = None
        self._repair_used = False
        self._pending: _PendingAction | None = None
        self._checkpoint: DiscoveryCheckpoint | None = None
        self._assisted_checkpoints: list[str] = []
        self._operator_task: asyncio.Task[None] | None = None
        self._candidate: CapabilityArtifact | None = None
        self._initial_verified = False
        bound = self.artifact.goal.budgets.max_actions + 2
        configured = getattr(planner, "max_calls", bound)
        self._max_calls = (
            min(bound, configured) if type(configured) is int and configured > 0 else bound
        )

    def _metadata(self) -> Metadata:
        base = super()._metadata()
        return base.model_copy(
            update={
                "provider_call_count": self.planner.call_count if live_planner(self.planner) else 0,
                "step_index": self._index,
            }
        )

    def _preflight(self, supplied: object) -> None:
        try:
            self.artifact = goal_template(self.artifact)
            self.profile = Profile.model_validate_json(self.profile.model_dump_json())
        except (ValidationError, ValueError, TypeError):
            raise RuntimeFault(FailureCode.INVALID_ARTIFACT) from None
        if type(self.planner.call_count) is not int or self.planner.call_count != 0:
            raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
        self._validate_invocation(supplied)
        self._compiler = DeterministicCompiler(
            self.artifact, self.session.policy, self._inputs, reviewed_targets=self.profile.targets
        )

    async def _pause(
        self, reason: Literal["session_expired", "unknown_dialog", "unsupported_state"]
    ) -> None:
        observed = self.session.current_observation
        if observed is None:
            raise RuntimeFault(FailureCode.STALE_OBSERVATION)
        pending = self._pending
        candidates = [
            checkpoint
            for checkpoint in self.profile.discovery_checkpoints
            if (
                pending is not None
                and checkpoint.reason == reason
                and checkpoint.action == pending.action
                and self._checks(checkpoint.before, pending.before)
            )
            or (
                pending is None
                and checkpoint.action is None
                and self._checks(checkpoint.restored, observed)
            )
        ]
        if (
            not candidates
            and pending is None
            and reason == "unsupported_state"
            and observed.dialog == "none"
            and self._checks(self.artifact.preconditions + self.artifact.identity_checks, observed)
        ):
            # An authored initial checkpoint plus exact snapshot restoration does not
            # generalize operator work, even when a profile has no discovery additions.
            candidates = [
                DiscoveryCheckpoint(
                    id="initial_restored",
                    restored=self.artifact.preconditions + self.artifact.identity_checks,
                )
            ]
        if len(candidates) != 1:
            raise RuntimeFault(FailureCode.INTERRUPTED)
        self._checkpoint = candidates[0]
        await self.session.pause_discovery(
            reason, self._checkpoint, observed if pending is None else None
        )
        self._paused_at = time.monotonic()
        self._operator_task = asyncio.create_task(self._expire_operator())
        raise _Stop(
            Intervention(
                reason=reason,
                step=self._checkpoint.id,
                session_id=self.session.session_id,
                ownership_epoch=self.session.epoch,
                metadata=self._metadata(),
            )
        )

    async def _expire_operator(self) -> None:
        try:
            await asyncio.sleep(self.artifact.goal.budgets.operator_seconds)
            async with self._run_lock:
                if self._paused_at is not None and self._terminal is None:
                    await self._finish_failure(FailureCode.BUDGET_EXCEEDED)
        except asyncio.CancelledError:
            return

    def _cancel_operator_timer(self) -> None:
        task, self._operator_task = self._operator_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _finish_failure(
        self, code: FailureCode, diagnostic: Diagnostic | None = None
    ) -> DiscoveryOutcome:
        self._cancel_operator_timer()
        self._paused_at = None
        await self._abort_safely()
        result = self._failure(code, diagnostic)
        self._record(result)
        return DiscoveryOutcome(result=result, artifact=None)

    async def abort(self) -> DiscoveryOutcome:
        # Revoke immediately, even if a provider call/action currently holds the run lock.
        await self._abort_safely()
        async with self._run_lock:
            if self._terminal is not None:
                return DiscoveryOutcome(result=self._terminal, artifact=self._candidate)
            return await self._finish_failure(FailureCode.INTERRUPTED)

    async def resume_discovery(self) -> DiscoveryOutcome:
        async with self._run_lock:
            if self._terminal is not None:
                if self.session.ownership == Ownership.ABORTED:
                    return DiscoveryOutcome(result=self._terminal, artifact=None)
                raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
            if self._paused_at is None or self._checkpoint is None:
                raise RuntimeFault(FailureCode.INVALID_RESUME)
            remaining_operator = self.artifact.goal.budgets.operator_seconds - (
                time.monotonic() - self._paused_at
            )
            remaining_active = self.artifact.goal.budgets.active_seconds - self._active_seconds
            if min(remaining_operator, remaining_active) <= 0:
                return await self._finish_failure(FailureCode.BUDGET_EXCEEDED)
            started = time.monotonic()
            try:
                async with asyncio.timeout(min(remaining_operator, remaining_active)):
                    await self.session.resume_discovery(
                        self.artifact,
                        self._inputs,
                        self.permissions,
                        self._checkpoint,
                        blocked_states=tuple(guard.when for guard in self.profile.guards),
                        step_index=self._index,
                    )
            except TimeoutError:
                return await self._finish_failure(FailureCode.BUDGET_EXCEEDED)
            except asyncio.CancelledError:
                await self._finish_failure(FailureCode.INTERRUPTED)
                raise
            except RuntimeFault as fault:
                if fault.code == FailureCode.BUDGET_EXCEEDED:
                    return await self._finish_failure(fault.code, fault.diagnostic)
                # Invalid restoration leaves both the checkpoint and HUMAN ownership intact.
                raise
            finally:
                self._active_seconds += time.monotonic() - started
            self._cancel_operator_timer()
            self._paused_at = None
            if self._checkpoint.id not in self._assisted_checkpoints:
                self._assisted_checkpoints.append(self._checkpoint.id)
            return await self._drive_discovery(resuming=True)

    async def _observe(self, *, allow_recovery: bool = False) -> Observation:
        return await super()._observe(allow_recovery=False)

    def _permitted(self, action: ExecutableAction, observation: Observation) -> bool:
        try:
            self.session.policy.authorize(
                action,
                self.artifact.targets.get(getattr(action, "target", "")),
                observation.origin,
                observation.route,
                self.artifact.required_permissions,
                self.permissions,
            )
            return True
        except RuntimeFault:
            return False

    def _request(self, observation: Observation) -> dict[str, object]:
        assert self._compiler is not None
        compiler = self._compiler
        targets: list[dict[str, object]] = []
        allowed_kinds = {"intervene"}
        input_name = next(iter(self._inputs), None)
        for target in observation.targets:
            if not target.derived or not target.visible:
                continue
            try:
                name = compiler.target_name(target, observation)
            except RuntimeFault as fault:
                if fault.code == FailureCode.AMBIGUOUS_TARGET:
                    raise
                continue
            candidates: list[ExecutableAction] = []
            if target.control in {"button", "link"}:
                candidates.append(Click(target=name))
            elif target.control in {"input", "textarea"} and input_name is not None:
                candidates.append(Fill(target=name, value=InputRef(name=input_name)))
            elif target.control == "select" and input_name is not None:
                candidates.append(Select(target=name, value=InputRef(name=input_name)))
            allowed_actions = [
                action.kind for action in candidates if self._permitted(action, observation)
            ]
            allowed_kinds.update(allowed_actions)
            descriptor: dict[str, object] = {
                "ref": target.ref,
                "descriptor": self.artifact.targets[name].model_dump(mode="json"),
                "allowed_actions": allowed_actions,
            }
            for field in ("text", "value"):
                value = compiler.project_value(getattr(target, field), target=name, field=field)
                if value is not None:
                    descriptor[field] = value.model_dump(mode="json")
            targets.append(descriptor)
        if len(targets) > 128:
            raise RuntimeFault(FailureCode.BUDGET_EXCEEDED)
        visible_text = []
        for text in observation.visible_text[:128]:
            value = compiler.project_value(text)
            if value is not None:
                visible_text.append(value.model_dump(mode="json"))
        # Reviewed schema/configuration, never values from this invocation.
        definitions = {
            name: {
                "type": definition.type,
                "classification": definition.classification,
                "min_length": definition.min_length,
                "max_length": definition.max_length,
                "values": list(definition.values) if definition.classification == "public" else [],
            }
            for name, definition in self.artifact.goal.inputs.items()
        }
        compiler._route(observation)
        previous_diagnostic = self._diagnostic
        try:
            completion_checks = {
                "identity": self._verify(self.artifact.identity_checks, observation, "identity"),
                "terminal": self._verify(
                    self.profile.terminal_checks + self.artifact.final_checks,
                    observation,
                    "terminal",
                ),
            }
        finally:
            # Unmet completion probes are normal during discovery, not a later
            # provider failure's cause. Raised faults carry their own diagnostic.
            self._diagnostic = previous_diagnostic
        if all(completion_checks.values()):
            allowed_kinds.add("finish")
        destinations = [
            destination
            for destination in observation.destinations[:128]
            if compiler.public_route(destination)
            and self._permitted(Navigate(destination=PublicLiteral(value=destination)), observation)
        ]
        if destinations:
            allowed_kinds.add("navigate")
        if self._permitted(Wait(milliseconds=1), observation):
            allowed_kinds.add("wait")
        request: dict[str, object] = {
            "goal_template": self.artifact.goal.goal_template,
            "input_definitions": definitions,
            "completion_checks": completion_checks,
            "allowed_action_kinds": sorted(allowed_kinds),
            "observation": {
                "observation_id": observation.observation_id,
                "ownership_epoch": observation.ownership_epoch,
                "route": observation.route,
                "targets": targets,
                "visible_text": visible_text,
                "destinations": destinations,
            },
            "bounds": {
                "remaining_actions": self.artifact.goal.budgets.max_actions
                - self.session.action_count,
                "remaining_provider_calls": self._max_calls - self.planner.call_count,
            },
            "prior_action_kinds": [step.action.kind for step in compiler.steps],
            "completed_input_bindings": sorted(
                {
                    step.action.value.name
                    for step in compiler.steps
                    if isinstance(step.action, (Fill, Select))
                    and isinstance(step.action.value, InputRef)
                }
            ),
        }
        # Only reviewed configuration and structurally projected observations enter
        # the request. A byte scan would taint public labels and even JSON keys when
        # a restricted input is short or coincidentally equal to public structure.
        if len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > 64_000:
            raise RuntimeFault(
                FailureCode.BUDGET_EXCEEDED,
                Diagnostic(
                    stage="discovery", expected="within_budget", observed="budget_exhausted"
                ),
            )
        return request

    async def _propose(self, observation: Observation) -> ModelDecision:
        request = self._request(observation)
        while True:
            count = self.planner.call_count
            if count >= self._max_calls:
                raise RuntimeFault(
                    FailureCode.BUDGET_EXCEEDED,
                    Diagnostic(
                        stage="discovery", expected="within_budget", observed="budget_exhausted"
                    ),
                )
            try:
                decision = await self.planner.propose(request)
                decision = ModelDecision.model_validate_json(decision.model_dump_json())
            except (ProviderError, ValidationError) as error:
                if isinstance(error, ProviderError) and error.code == "budget_exceeded":
                    raise RuntimeFault(
                        FailureCode.BUDGET_EXCEEDED,
                        Diagnostic(
                            stage="discovery", expected="within_budget", observed="budget_exhausted"
                        ),
                    ) from None
                malformed = isinstance(error, ValidationError) or error.code == "malformed_output"
                if not malformed or self._repair_used:
                    raise RuntimeFault(
                        FailureCode.INTERRUPTED,
                        Diagnostic(
                            stage="discovery",
                            expected="valid_decision",
                            observed="invalid_format" if malformed else "unknown",
                        ),
                    ) from None
                self._repair_used = True
                request = self._request(observation)
                request["repair"] = (
                    "Return one valid structured decision; use null for irrelevant fields."
                )
                continue
            if self.planner.call_count != count + 1:
                raise RuntimeFault(
                    FailureCode.INTERRUPTED,
                    Diagnostic(stage="discovery", expected="valid_decision", observed="mismatch"),
                )
            if (
                decision.observation_id != observation.observation_id
                or decision.ownership_epoch != observation.ownership_epoch
                or decision.ownership_epoch != self.session.epoch
            ):
                raise RuntimeFault(
                    FailureCode.STALE_OBSERVATION,
                    Diagnostic(stage="discovery", expected="fresh_observation", observed="stale"),
                )
            return decision

    def _action(self, decision: ModelDecision, observation: Observation) -> ExecutableAction:
        assert self._compiler is not None
        if decision.kind in {"click", "fill", "select"}:
            found = [target for target in observation.targets if target.ref == decision.target_ref]
            if len(found) != 1:
                raise RuntimeFault(
                    FailureCode.AMBIGUOUS_TARGET if found else FailureCode.TARGET_NOT_FOUND
                )
            name = self._compiler.target_name(found[0], observation)
            if decision.kind == "click":
                return Click(target=name)
            if decision.input_name not in self._inputs:
                raise RuntimeFault(FailureCode.INVALID_ARGUMENT)
            value = InputRef(name=decision.input_name)
            return (
                Fill(target=name, value=value)
                if decision.kind == "fill"
                else Select(target=name, value=value)
            )
        if decision.kind == "navigate":
            if decision.destination not in observation.destinations:
                raise RuntimeFault(FailureCode.POLICY_DENIED)
            assert decision.destination is not None
            return Navigate(destination=PublicLiteral(value=decision.destination))
        if decision.kind == "wait":
            assert decision.milliseconds is not None
            return Wait(milliseconds=decision.milliseconds)
        raise RuntimeFault(FailureCode.INTERRUPTED)

    def _completed_live(self) -> bool:
        if not live_planner(self.planner) or self.planner.call_count <= 0:
            return False
        if not self.planner.records:
            return False
        last = self.planner.records[-1]
        return (
            last.get("status") == "completed"
            and last.get("decision_validated") is True
            and isinstance(last.get("response_id"), str)
            and bool(last["response_id"])
        )

    async def discover(self, inputs: object) -> DiscoveryOutcome:
        async with self._run_lock:
            if self._started:
                raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
            self._started = True
            try:
                self._preflight(inputs)
            except RuntimeFault as fault:
                return await self._finish_failure(fault.code, fault.diagnostic)
            return await self._drive_discovery()

    def _record_pending(self, after: Observation, *, restored: bool = False) -> None:
        assert self._compiler is not None and self._pending is not None
        pending = self._pending
        step = self._compiler.record(pending.action, pending.before, after)
        if restored:
            assert self._checkpoint is not None
            # A human-restored page is not evidence for arbitrary inferred postconditions.
            self._compiler.steps[-1] = step.model_copy(
                update={
                    "preconditions": self._checkpoint.before,
                    "postconditions": self._checkpoint.restored,
                }
            )
        self.session.evidence.emit(
            "action_verified", action_index=pending.action_index, step_index=self._index
        )
        self._index += 1
        self._pending = None

    async def _drive_discovery(self, *, resuming: bool = False) -> DiscoveryOutcome:
        started = time.monotonic()
        remaining = self.artifact.goal.budgets.active_seconds - self._active_seconds
        candidate: CapabilityArtifact | None = None
        result: TerminalResult | Intervention
        try:
            if remaining <= 0:
                raise RuntimeFault(FailureCode.BUDGET_EXCEEDED)
            async with asyncio.timeout(remaining):
                assert self._compiler is not None
                # Never reuse the proposal or observation from before a handoff.
                observed = await self._observe()
                if resuming:
                    assert self._checkpoint is not None
                    if not self._verify(
                        self.artifact.identity_checks + self._checkpoint.restored,
                        observed,
                        "resume",
                    ):
                        raise RuntimeFault(FailureCode.INVALID_RESUME)
                    if self._pending is not None:
                        self._record_pending(observed, restored=True)
                    self._checkpoint = None
                if not self._initial_verified:
                    if not self._verify(self.artifact.preconditions, observed, "initial"):
                        raise RuntimeFault(FailureCode.PRECONDITION_FAILED)
                    self._initial_verified = True
                while True:
                    if self.session.ownership != Ownership.AUTOMATION:
                        raise RuntimeFault(FailureCode.OWNERSHIP_DENIED)
                    decision = await self._propose(observed)
                    if decision.kind == "intervene":
                        await self._pause(decision.reason or "unsupported_state")
                    if decision.kind == "finish":
                        # A finish proposal is only a request for independent verification.
                        observed = await self._observe()
                        if not self._verify(self.artifact.identity_checks, observed, "identity"):
                            raise RuntimeFault(FailureCode.IDENTITY_MISMATCH)
                        if not self._verify(
                            self.profile.terminal_checks + self.artifact.final_checks,
                            observed,
                            "terminal",
                        ):
                            raise RuntimeFault(FailureCode.POSTCONDITION_FAILED)
                        extracted = self._extract(observed)
                        candidate = self._compiler.finish(
                            live=self._completed_live(), run_id=self.session.run_id
                        )
                        if self._assisted_checkpoints:
                            provenance = candidate.provenance.model_copy(
                                update={
                                    "assistance": DiscoveryAssistance(
                                        checkpoints=tuple(self._assisted_checkpoints)
                                    )
                                }
                            )
                            candidate = candidate.model_copy(update={"provenance": provenance})
                        reviewed = {item.id for item in self.profile.discovery_checkpoints}
                        if not set(self._assisted_checkpoints).issubset(reviewed):
                            # Exact initial-state fallback can finish this invocation, but
                            # cannot export a dependency absent from the trusted profile.
                            candidate = None
                        else:
                            self.artifact = candidate
                            self._artifact_hash = contract_hash(candidate)
                        result = Success(outputs=extracted.outputs, metadata=self._metadata())
                        break
                    if self.session.action_count >= self.artifact.goal.budgets.max_actions:
                        raise RuntimeFault(FailureCode.BUDGET_EXCEEDED)
                    action = self._action(decision, observed)
                    envelope = self._compiler.proposal_artifact(action, observed)
                    pending = _PendingAction(
                        action=action,
                        before=observed,
                        action_index=self.session.action_count,
                    )
                    await self.session.execute(
                        ActionProposal(
                            observation_id=observed.observation_id,
                            ownership_epoch=observed.ownership_epoch,
                            action=action,
                        ),
                        envelope,
                        self._inputs,
                        self._locals,
                        self.permissions,
                        purpose="discovery",
                        step_index=self._index,
                    )
                    self._pending = pending
                    observed = await self._observe()
                    self._record_pending(observed)
        except _Stop as stop:
            result = stop.result
        except RuntimeFault as fault:
            result = self._failure(fault.code, fault.diagnostic)
        except TimeoutError:
            result = self._failure(FailureCode.BUDGET_EXCEEDED)
        except asyncio.CancelledError:
            await self._finish_failure(FailureCode.INTERRUPTED)
            raise
        except Exception:
            result = self._failure(FailureCode.INTERRUPTED)
        finally:
            self._active_seconds += time.monotonic() - started
        if isinstance(result, Failure):
            return await self._finish_failure(result.code, result.diagnostic)
        if not isinstance(result, Intervention):
            self._candidate = candidate
        self._record(result)
        return DiscoveryOutcome(result=result, artifact=candidate)


class Discovery:
    """Keep this invocation and its Session alive across typed interventions."""

    def __init__(
        self,
        template: CapabilityArtifact,
        profile: Profile,
        session: Session,
        planner: Planner,
        caller_permissions: frozenset[str],
    ) -> None:
        self._driver = _DiscoveryDriver(template, profile, session, planner, caller_permissions)

    async def run(self, inputs: object) -> DiscoveryOutcome:
        return await self._driver.discover(inputs)

    async def resume(self) -> DiscoveryOutcome:
        return await self._driver.resume_discovery()

    async def abort(self) -> DiscoveryOutcome:
        return await self._driver.abort()
