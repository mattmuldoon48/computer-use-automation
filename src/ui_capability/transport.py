"""Exact request mediation for the local sandbox, not an OS egress sandbox.

Create the fresh context through this guard before creating pages. A loopback
HTTP proxy covers browser-process downloads that bypass Playwright routing.
CONNECT is denied; this Phase 2 adapter supports only the local HTTP fixture.
Redirects never follow. Only fixed codes/counters leave this layer.
"""

from __future__ import annotations

import asyncio
from typing import Literal, Self
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Error, Route, WebSocketRoute
from pydantic import model_validator

from .contracts import Binding, Contract, FailureCode
from .errors import RuntimeFault
from .evidence import EvidenceSink
from .http_proxy import LoopbackProxy


def _plain_path(path: str) -> bool:
    return (
        path.startswith("/")
        and path.isascii()
        and not any(character in path for character in "?#%\\")
        and not any(ord(character) <= 32 or ord(character) == 127 for character in path)
        and "//" not in path
        and all(part not in {".", ".."} for part in path.split("/"))
    )


class TransportRule(Contract):
    method: Literal["GET", "POST"]
    path: str

    @model_validator(mode="after")
    def exact_path(self) -> Self:
        if not _plain_path(self.path):
            raise ValueError("transport paths must be exact plain paths")
        return self


class TransportPolicy(Contract):
    origin: str
    rules: tuple[TransportRule, ...]

    @model_validator(mode="after")
    def exact_origin(self) -> Self:
        Binding(origin=self.origin, entry_route="/", app="transport", version="v1")
        parsed = urlsplit(self.origin)
        if (
            not self.origin.isascii()
            or any(ord(character) <= 32 for character in self.origin)
            or "%" in self.origin
            or "\\" in self.origin
            or parsed.netloc != parsed.netloc.lower()
            or len({(rule.method, rule.path) for rule in self.rules}) != len(self.rules)
        ):
            raise ValueError("invalid exact transport origin or duplicate rules")
        # Accessing port rejects malformed/out-of-range ports at configuration time.
        _ = parsed.port
        return self

    def path_for(self, url: str) -> str | None:
        """Return a trusted path, never a query or credential-bearing URL."""
        if (
            not url.isascii()
            or any(ord(character) <= 32 or ord(character) == 127 for character in url)
            or any(character in url for character in "?#%\\")
        ):
            return None
        try:
            parsed = urlsplit(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if (
                origin != self.origin
                or parsed.scheme not in {"http", "https"}
                or parsed.username is not None
                or parsed.password is not None
                or not _plain_path(parsed.path)
            ):
                return None
        except ValueError:
            return None
        return parsed.path

    def permits(self, method: str, url: str) -> bool:
        path = self.path_for(url)
        return path is not None and any(
            rule.method == method and rule.path == path for rule in self.rules
        )


def member_ops_transport(binding: Binding) -> TransportPolicy:
    """Reviewed sandbox traffic; account submission is deliberately absent."""
    get_paths = (
        "/",
        "/navigation",
        "/workspace/home",
        "/workspace/search",
        "/workspace/member",
        "/workspace/prepare",
    )
    post_paths = (
        "/workspace/search",
        "/workspace/review",
        "/workspace/signin",
        "/workspace/dismiss",
    )
    return TransportPolicy(
        origin=binding.origin,
        rules=tuple(TransportRule(method="GET", path=path) for path in get_paths)
        + tuple(TransportRule(method="POST", path=path) for path in post_paths),
    )


class TransportGuard:
    """Context-wide allowlist used by the adapter and independent receiving tests."""

    def __init__(self, policy: TransportPolicy, evidence: EvidenceSink) -> None:
        self.__policy = policy
        self.__evidence = evidence
        self.__denied = 0
        self.__proxy: LoopbackProxy | None = None
        self.__closing: asyncio.Task[None] | None = None

    @property
    def denied_count(self) -> int:
        return self.__denied

    def _deny(self) -> None:
        self.__denied += 1
        self.__evidence.emit("transport_denied", code=FailureCode.POLICY_DENIED)

    async def new_context(self, browser: Browser) -> BrowserContext:
        if self.__proxy is not None:
            raise RuntimeFault(FailureCode.POLICY_DENIED)
        loop = asyncio.get_running_loop()

        def denied_on_loop() -> None:
            loop.call_soon_threadsafe(self._deny)

        try:
            self.__proxy = LoopbackProxy(
                self.__policy.origin,
                self.__policy.permits,
                denied_on_loop,
            )
        except ValueError:
            raise RuntimeFault(FailureCode.POLICY_DENIED) from None
        try:
            context = await browser.new_context(
                service_workers="block",
                accept_downloads=False,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
                proxy={"server": self.__proxy.origin, "bypass": "<-loopback>"},
            )
            context.on("close", lambda _: self._schedule_close())
            await context.route_web_socket(lambda _: True, self._websocket)
            await context.route("**/*", self._request)
            return context
        except BaseException:
            await self.close()
            raise

    def _schedule_close(self) -> None:
        if self.__closing is None and self.__proxy is not None:
            self.__closing = asyncio.create_task(asyncio.to_thread(self.__proxy.close))

    async def close(self) -> None:
        self._schedule_close()
        if self.__closing is not None:
            await self.__closing

    async def _websocket(self, socket: WebSocketRoute) -> None:
        self._deny()
        await socket.close(code=1008, reason="Policy denied")

    async def _request(self, route: Route) -> None:
        request = route.request
        if request.resource_type != "document" or not self.__policy.permits(
            request.method, request.url
        ):
            self._deny()
            await route.abort("blockedbyclient")
            return
        # The demo accepts only ordinary URL-encoded forms, never uploaded files
        # or arbitrary binary request payloads. Header values stay local.
        if request.method == "POST":
            content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type != "application/x-www-form-urlencoded":
                self._deny()
                await route.abort("blockedbyclient")
                return
        try:
            # Chromium uses the mandatory proxy. Do not route.fetch(): its API
            # client uses CONNECT even for HTTP, which this proxy rightly denies.
            await route.continue_()
        except Error:
            self._deny()
            try:
                await route.abort("blockedbyclient")
            except Error:
                pass  # Context closure can settle the intercepted request first.
