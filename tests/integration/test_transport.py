"""Hand-authored browser fixtures: receiver counts prove denied sends did not occur.

These tests deliberately own raw browser handles as an adversarial harness. The
production Surface never exports them, nor queries these receiving counters.
"""

import asyncio
import json
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from playwright.async_api import Browser, BrowserContext, Error, Page, async_playwright

from sandbox import create_app
from sandbox.server import running_app
from ui_capability.contracts import Binding
from ui_capability.evidence import EvidenceSink
from ui_capability.transport import (
    TransportGuard,
    TransportPolicy,
    TransportRule,
    member_ops_transport,
)


@dataclass
class Receiver:
    origin: str = ""
    receipts: Counter[tuple[str, str]] = field(default_factory=Counter)
    upgrades: int = 0


@contextmanager
def receiving_server() -> Iterator[Receiver]:
    """Independent actual HTTP receiver, including failed websocket upgrades."""
    receiver = Receiver()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            receiver.receipts[("GET", self.path)] += 1
            upgrade = self.headers.get("Upgrade", "").lower() == "websocket"
            if upgrade:
                receiver.upgrades += 1
            self.send_response(400 if upgrade else 200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Independent receiver</h1>")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    receiver.origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield receiver
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def document_app(body: str) -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def document() -> str:
        return "<!doctype html><html><body>" + body + "</body></html>"

    return app


async def guarded_context(
    browser: Browser, origin: str, *rules: TransportRule
) -> tuple[BrowserContext, TransportGuard, StringIO]:
    policy = TransportPolicy(origin=origin, rules=(TransportRule(method="GET", path="/"), *rules))
    audit = StringIO()
    guard = TransportGuard(policy, EvidenceSink(audit))
    context = await guard.new_context(browser)
    context.set_default_timeout(5000)
    return context, guard, audit


def assert_sanitized(audit: StringIO) -> None:
    events = [json.loads(line) for line in audit.getvalue().splitlines()]
    assert events
    assert all(event == {"event": "transport_denied", "code": "POLICY_DENIED"} for event in events)


@pytest.mark.parametrize("entry", ["direct", "frame", "popup"])
def test_foreign_origin_receives_no_direct_frame_or_popup_request(entry: str) -> None:
    with receiving_server() as receiver:
        app = document_app(
            f'<a href="{receiver.origin}/forbidden">Direct</a>'
            f'<a href="{receiver.origin}/forbidden" target="_blank">Popup</a>'
            '<iframe name="receiving"></iframe>'
            "<button onclick=\"document.querySelector('iframe').src="
            f"'{receiver.origin}/forbidden'\">"
            "Frame</button>"
        )
        with running_app(app) as origin:

            async def scenario() -> None:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    try:
                        # Positive control: the separate receiver really accepts
                        # browser traffic; denial cannot be a dead server.
                        probe = await browser.new_page()
                        await probe.goto(receiver.origin + "/positive")
                        assert receiver.receipts[("GET", "/positive")] == 1
                        await probe.close()
                        context, guard, audit = await guarded_context(browser, origin)
                        page = await context.new_page()
                        await page.goto(origin)
                        async with context.expect_event(
                            "requestfailed", predicate=lambda request: "/forbidden" in request.url
                        ):
                            if entry == "direct":
                                await page.get_by_role("link", name="Direct", exact=True).click(
                                    no_wait_after=True
                                )
                            elif entry == "frame":
                                await page.get_by_role("button", name="Frame", exact=True).click()
                            else:
                                await page.get_by_role("link", name="Popup", exact=True).click()
                        assert receiver.receipts[("GET", "/forbidden")] == 0
                        assert guard.denied_count >= 1
                        assert_sanitized(audit)
                        await context.close()
                    finally:
                        await browser.close()

            asyncio.run(scenario())


@pytest.mark.parametrize("chain", [False, True])
def test_redirects_are_rejected_before_any_followup_receiver(chain: bool) -> None:
    with receiving_server() as receiver:
        hits: Counter[str] = Counter()
        app = document_app('<a href="/redirect-start">Redirect</a>')

        @app.get("/redirect-start")
        async def first() -> RedirectResponse:
            hits["first"] += 1
            destination = (
                "/redirect-next" if chain else receiver.origin + "/forbidden?private-marker"
            )
            return RedirectResponse(destination, status_code=302)

        @app.get("/redirect-next")
        async def second() -> RedirectResponse:
            hits["second"] += 1
            return RedirectResponse(receiver.origin + "/forbidden?private-marker", status_code=307)

        with running_app(app) as origin:

            async def scenario() -> None:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    try:
                        context, guard, audit = await guarded_context(
                            browser,
                            origin,
                            TransportRule(method="GET", path="/redirect-start"),
                            TransportRule(method="GET", path="/redirect-next"),
                        )
                        page = await context.new_page()
                        await page.goto(origin)
                        async with page.expect_response(
                            lambda response: response.url == origin + "/redirect-start"
                        ) as denied:
                            await page.get_by_role("link", name="Redirect").click(
                                no_wait_after=True
                            )
                        assert (await denied.value).status == 403
                        assert hits == {"first": 1}
                        assert not receiver.receipts
                        assert guard.denied_count >= 1
                        assert_sanitized(audit)
                        assert "private-marker" not in audit.getvalue()
                        await context.close()
                    finally:
                        await browser.close()

            asyncio.run(scenario())


async def prepare_review(page: Page, origin: str) -> None:
    await page.goto(origin)
    await (
        page.frame_locator('iframe[name="navigation"]')
        .get_by_role("link", name="Member search", exact=True)
        .click()
    )
    workspace = page.frame_locator('iframe[name="workspace"]')
    await workspace.locator('input[name="member_id"]').fill("000042")
    await workspace.get_by_role("button", name="Search", exact=True).click()
    await workspace.get_by_role("link", name="000042", exact=True).click()
    await workspace.get_by_role("link", name="Prepare sub-account", exact=True).click()
    preparation = workspace.get_by_role("group", name="Account preparation", exact=True)
    await preparation.get_by_label("Product", exact=True).select_option("SAVINGS_BASIC")
    await preparation.get_by_label("Nickname", exact=True).fill("Synthetic transport check")
    await preparation.get_by_role("button", name="Review", exact=True).click()
    await workspace.get_by_role("heading", name="Review sub-account", exact=True).wait_for()


def test_final_submit_blocked_even_for_direct_human_browser_click() -> None:
    app = create_app()
    with running_app(app) as origin:

        async def scenario() -> None:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    guard = TransportGuard(
                        member_ops_transport(
                            Binding(origin=origin, entry_route="/", app="member_ops", version="v1")
                        ),
                        EvidenceSink(StringIO()),
                    )
                    context = await guard.new_context(browser)
                    page = await context.new_page()
                    await prepare_review(page, origin)
                    async with context.expect_event(
                        "requestfailed",
                        predicate=lambda request: request.url.endswith("/workspace/submit"),
                    ):
                        await (
                            page.frame_locator('iframe[name="workspace"]')
                            .get_by_role("button", name="Open account", exact=True)
                            .click(no_wait_after=True)
                        )
                    assert app.state.oracle.submit_requests == 0
                    assert app.state.oracle.ledger == []
                    assert guard.denied_count >= 1
                    await context.close()
                    # Positive control proves this same endpoint genuinely
                    # mutates state without the guard; the fixture is not a no-op.
                    unguarded = await browser.new_page()
                    await prepare_review(unguarded, origin)
                    await (
                        unguarded.frame_locator('iframe[name="workspace"]')
                        .get_by_role("button", name="Open account", exact=True)
                        .click()
                    )
                    await (
                        unguarded.frame_locator('iframe[name="workspace"]')
                        .get_by_text("A synthetic account was opened by submission.", exact=True)
                        .wait_for()
                    )
                    assert app.state.oracle.submit_requests == 1
                    assert len(app.state.oracle.ledger) == 1
                finally:
                    await browser.close()

        asyncio.run(scenario())


def test_websocket_denial_precedes_receiver_upgrade() -> None:
    with receiving_server() as receiver:
        ws_url = receiver.origin.replace("http:", "ws:") + "/socket"
        app = document_app(
            "<button onclick=\"const socket = new WebSocket('" + ws_url + "');"
            "socket.onclose=()=>document.querySelector('output').textContent='closed'\">"
            "Connect</button><output></output>"
        )
        with running_app(app) as origin:

            async def scenario() -> None:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    try:
                        # A real upgrade reaches this receiver without mediation.
                        probe = await browser.new_page()
                        await probe.goto(origin)
                        await probe.get_by_role("button", name="Connect").click()
                        await probe.get_by_text("closed", exact=True).wait_for()
                        assert receiver.upgrades == 1
                        await probe.close()
                        context, guard, audit = await guarded_context(browser, origin)
                        page = await context.new_page()
                        await page.goto(origin)
                        await page.get_by_role("button", name="Connect").click()
                        await page.get_by_text("closed", exact=True).wait_for()
                        assert receiver.upgrades == 1
                        assert guard.denied_count >= 1
                        assert_sanitized(audit)
                        await context.close()
                    finally:
                        await browser.close()

            asyncio.run(scenario())


def test_service_worker_registration_cannot_send_even_allowlisted_script() -> None:
    hits: Counter[str] = Counter()
    app = document_app(
        "<button onclick=\"navigator.serviceWorker.register('/worker.js').finally("
        "()=>document.querySelector('output').textContent='settled')\">Register</button>"
        "<output></output>"
    )

    @app.get("/worker.js")
    async def worker() -> Response:
        hits["worker"] += 1
        return Response(
            "self.addEventListener('install', () => self.skipWaiting());",
            media_type="application/javascript",
        )

    with running_app(app) as origin:

        async def scenario() -> None:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    context, _, _ = await guarded_context(
                        browser, origin, TransportRule(method="GET", path="/worker.js")
                    )
                    page = await context.new_page()
                    await page.goto(origin)
                    await page.get_by_role("button", name="Register").click()
                    await page.get_by_text("settled", exact=True).wait_for()
                    assert hits["worker"] == 0
                    assert context.service_workers == []
                    await context.close()
                    # The test fixture does register a genuine worker when not
                    # blocked, so zero earlier is meaningful.
                    positive = await browser.new_context(service_workers="allow")
                    probe = await positive.new_page()
                    await probe.goto(origin)
                    async with positive.expect_event("serviceworker"):
                        await probe.get_by_role("button", name="Register").click()
                    await probe.get_by_text("settled", exact=True).wait_for()
                    assert hits["worker"] == 1
                    assert len(positive.service_workers) == 1
                    await positive.close()
                finally:
                    await browser.close()

        asyncio.run(scenario())


def test_multipart_upload_never_reaches_allowlisted_post() -> None:
    hits: Counter[str] = Counter()
    app = document_app(
        '<form action="/workspace/review" method="post" enctype="multipart/form-data">'
        '<input name="file" type="file"><button>Upload</button></form>'
    )

    @app.post("/workspace/review", response_class=HTMLResponse)
    async def receiver(request: Request) -> str:
        hits["upload"] += 1
        return "<h1>Received</h1>"

    with running_app(app) as origin:

        async def scenario() -> None:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    context, guard, audit = await guarded_context(
                        browser, origin, TransportRule(method="POST", path="/workspace/review")
                    )
                    page = await context.new_page()
                    await page.goto(origin)
                    await page.locator('input[type="file"]').set_input_files(
                        {
                            "name": "synthetic.txt",
                            "mimeType": "text/plain",
                            "buffer": b"private-marker",
                        }
                    )
                    async with context.expect_event("requestfailed"):
                        await page.get_by_role("button", name="Upload").click(no_wait_after=True)
                    assert hits["upload"] == 0
                    assert guard.denied_count >= 1
                    assert_sanitized(audit)
                    await context.close()
                finally:
                    await browser.close()

        asyncio.run(scenario())


@pytest.mark.parametrize("suffix", ["/?private-marker", "/%2Fforbidden", "/workspace/submit"])
def test_unreviewed_path_and_query_never_reach_origin(suffix: str) -> None:
    with receiving_server() as receiver:

        async def scenario() -> None:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    context, guard, audit = await guarded_context(browser, receiver.origin)
                    page = await context.new_page()
                    with pytest.raises(Error):
                        await page.goto(receiver.origin + suffix)
                    assert not receiver.receipts
                    assert guard.denied_count >= 1
                    assert_sanitized(audit)
                    await context.close()
                finally:
                    await browser.close()

        asyncio.run(scenario())


def test_download_link_to_unreviewed_endpoint_has_zero_receiver_receipts() -> None:
    hits: Counter[str] = Counter()
    app = document_app('<a href="/download" download="synthetic.txt">Download</a>')

    @app.get("/download")
    async def download() -> Response:
        hits["download"] += 1
        return Response(
            "synthetic export",
            media_type="text/plain",
            headers={"Content-Disposition": 'attachment; filename="synthetic.txt"'},
        )

    with running_app(app) as origin:

        async def scenario() -> None:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    context, _, audit = await guarded_context(browser, origin)
                    page = await context.new_page()
                    await page.goto(origin)
                    async with page.expect_download() as pending:
                        await page.get_by_role("link", name="Download").click(no_wait_after=True)
                    download = await pending.value
                    assert await download.failure() is not None
                    assert hits["download"] == 0
                    assert_sanitized(audit)
                    await context.close()
                    # Positive control: the same UI really downloads when unguarded.
                    positive = await browser.new_context(accept_downloads=True)
                    positive_page = await positive.new_page()
                    await positive_page.goto(origin)
                    async with positive_page.expect_download() as allowed:
                        await positive_page.get_by_role("link", name="Download").click()
                    assert await (await allowed.value).failure() is None
                    assert hits["download"] == 1
                    await positive.close()
                finally:
                    await browser.close()

        asyncio.run(scenario())
