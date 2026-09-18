"""Minimal typed adapter boundary; never exposes browser or application handles."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts import Binding, ExecutableAction, Observation, ObservedTarget, Ownership, Scalar


class Surface(Protocol):
    @property
    def features(self) -> frozenset[str]: ...

    @property
    def binding(self) -> Binding: ...

    @property
    def context_id(self) -> str: ...

    def set_ownership(self, ownership: Ownership) -> None: ...

    async def observe(self, run_id: str, session_id: str, epoch: int) -> Observation: ...

    async def perform(
        self, action: ExecutableAction, target: ObservedTarget | None, value: Scalar | None
    ) -> None: ...


@runtime_checkable
class CaptureSurface(Protocol):
    async def capture_safe(self, path: Path) -> dict[str, object]: ...
