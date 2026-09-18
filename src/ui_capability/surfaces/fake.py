"""Deterministic test double. Its observations are not live UI evidence."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from uuid import uuid4

from ..contracts import Binding, ExecutableAction, Observation, ObservedTarget, Ownership, Scalar

ActionRecord = tuple[ExecutableAction, ObservedTarget | None, Scalar | None]
ActionCallback = Callable[
    [ExecutableAction, ObservedTarget | None, Scalar | None], Awaitable[None] | None
]


class FakeSurface:
    """Advance scripted observations per observe, repeating the last indefinitely.

    Templates' run/session/epoch/observation IDs are replaced on each read.
    All other fields remain as supplied. Public state and hooks are test-only.
    A callback can update observations or block on an asyncio.Event to exercise
    ownership races. ``actions`` includes attempted actions that later fail.
    """

    def __init__(
        self,
        binding: Binding,
        observations: list[Observation],
        on_action: ActionCallback | None = None,
        *,
        features: frozenset[str] = frozenset({"web", "frames", "visible_text", "form_controls"}),
        context_id: str | None = None,
        observe_delay: float = 0,
        action_delay: float = 0,
        observe_error: Exception | None = None,
        action_error: Exception | None = None,
    ) -> None:
        if not observations:
            raise ValueError("fake surface requires observations")
        self.binding = binding
        self.features = features
        self.context_id = uuid4().hex if context_id is None else context_id
        self.observations = list(observations)
        self.on_action = on_action
        self.observe_delay = observe_delay
        self.action_delay = action_delay
        self.observe_error = observe_error
        self.action_error = action_error
        self.actions: list[ActionRecord] = []
        self.observation_count = 0
        self.ownership = Ownership.AUTOMATION

    def set_ownership(self, ownership: Ownership) -> None:
        self.ownership = ownership

    async def observe(self, run_id: str, session_id: str, epoch: int) -> Observation:
        if self.observe_delay:
            await asyncio.sleep(self.observe_delay)
        if self.observe_error is not None:
            raise self.observe_error
        template = self.observations[min(self.observation_count, len(self.observations) - 1)]
        self.observation_count += 1
        return template.model_copy(
            update={
                "observation_id": uuid4().hex,
                "run_id": run_id,
                "session_id": session_id,
                "ownership_epoch": epoch,
            }
        )

    async def perform(
        self, action: ExecutableAction, target: ObservedTarget | None, value: Scalar | None
    ) -> None:
        self.actions.append((action, target, value))
        if self.action_delay:
            await asyncio.sleep(self.action_delay)
        if self.on_action is not None:
            result = self.on_action(action, target, value)
            if inspect.isawaitable(result):
                await result
        if self.action_error is not None:
            raise self.action_error
