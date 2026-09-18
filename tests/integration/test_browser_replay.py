"""Actual Chromium against loopback UI; operator actions here are AUTOMATED, not human evidence."""

import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest
from PIL import Image
from playwright.async_api import Page, async_playwright

from sandbox.app import create_app
from sandbox.server import running_app
from ui_capability.contracts import (
    ActionProposal,
    Click,
    Failure,
    FailureCode,
    Intervention,
    Ownership,
    Success,
)
from ui_capability.demo import load_demo
from ui_capability.errors import RuntimeFault
from ui_capability.evidence import EvidenceSink
from ui_capability.provider import OpenAIPlanner
from ui_capability.replay import Replay
from ui_capability.session import Session
from ui_capability.surfaces.playwright import PlaywrightSurface

pytestmark = pytest.mark.browser
VALUES = {"member_id": "000042", "product_code": "SAVINGS_BASIC", "nickname": "Rainy day"}
LIVE_ARTIFACT = (
    Path(__file__).resolve().parents[2] / "evidence/phase4-adversarial/revised.capability.json"
)


@pytest.fixture(
    params=[
        pytest.param(None, id="authored"),
        pytest.param(LIVE_ARTIFACT, id="live-checkpoint-revision"),
    ]
)
def artifact_path(request, monkeypatch):
    provider_attempts = []

    async def forbidden_provider(*args, **kwargs):
        provider_attempts.append(True)
        raise AssertionError("Replay must never invoke a provider")

    monkeypatch.setattr(OpenAIPlanner, "propose", forbidden_provider)
    monkeypatch.setattr(OpenAIPlanner, "_transport", forbidden_provider)
    yield request.param
    assert provider_attempts == []


def operator_page(surface: PlaywrightSurface) -> Page:
    # Deliberately test-only: simulate outside operator/hostile input. This handle
    # is never exposed through Surface, Session, Replay, or a model tool.
    return surface._PlaywrightSurface__page


async def launch(origin, values=None, *, artifact_path=None):
    values = dict(VALUES) if values is None else values
    bundle = load_demo(origin, artifact_path=artifact_path)
    audit = StringIO()
    sink = EvidenceSink(audit)
    surface = await PlaywrightSurface.launch(
        bundle.artifact.goal.binding, bundle.profile.targets, values, sink
    )
    session = Session(surface, bundle.policy, sink)
    replay = Replay(bundle.artifact, bundle.profile, session, frozenset({"prepare"}))
    return bundle, audit, surface, session, replay


def test_changed_inputs_in_fresh_browser_contexts_and_exact_outputs(artifact_path):
    app = create_app()

    async def scenario(origin):
        contexts = []
        for values, fee in (
            (dict(VALUES), 250),
            (
                {
                    "member_id": "000099",
                    "product_code": "SAVINGS_PLUS",
                    "nickname": "PRIVATE_CANARY_HOLIDAY",
                },
                700,
            ),
        ):
            _, audit, surface, _, replay = await launch(origin, values, artifact_path=artifact_path)
            try:
                result = await replay.run(values)
                assert isinstance(result, Success), result
                assert result.outputs == {
                    **values,
                    "monthly_fee_minor": fee,
                    "currency": "USD",
                    "submitted": False,
                }
                assert not result.metadata.human_intervened
                assert result.metadata.provider_call_count == 0
                assert values["member_id"] not in audit.getvalue()
                assert values["nickname"] not in audit.getvalue()
                contexts.append(surface.context_id)
            finally:
                await surface.close()
        assert contexts[0] != contexts[1]
        assert app.state.oracle.submit_requests == 0
        assert app.state.oracle.ledger == []
        assert app.state.oracle.request_counts[("POST", "/workspace/review")] == 2

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


@pytest.mark.parametrize(
    "case,inputs,kind,code",
    [
        ("happy", {**VALUES, "member_id": "999999"}, "business_outcome", "member_not_found"),
        ("product_ineligible", VALUES, "business_outcome", "product_ineligible"),
        ("validation_rejected", VALUES, "business_outcome", "validation_rejected"),
        ("permission_denied", VALUES, "failure", FailureCode.PERMISSION_DENIED),
        ("wrong_member_review", VALUES, "failure", FailureCode.IDENTITY_MISMATCH),
        ("duplicate_target", VALUES, "failure", FailureCode.AMBIGUOUS_TARGET),
    ],
)
def test_real_ui_exception_states_do_not_become_success(artifact_path, case, inputs, kind, code):
    app = create_app(case)

    async def scenario(origin):
        _, _, surface, _, replay = await launch(origin, dict(inputs), artifact_path=artifact_path)
        try:
            result = await replay.run(dict(inputs))
            assert result.kind == kind, result
            assert result.code == code
            assert result.metadata.provider_call_count == 0
            assert app.state.oracle.submit_requests == 0
            assert app.state.oracle.ledger == []
            if case == "duplicate_target":
                assert app.state.oracle.request_counts[("POST", "/workspace/review")] == 0
        finally:
            await surface.close()

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


@pytest.mark.parametrize("case", ["slow_load", "known_interstitial"])
def test_real_slow_load_and_reviewed_recovery_do_not_duplicate_effects(artifact_path, case):
    app = create_app(case)

    async def scenario(origin):
        _, _, surface, _, replay = await launch(origin, artifact_path=artifact_path)
        try:
            result = await replay.run(dict(VALUES))
            assert isinstance(result, Success), result
            assert result.metadata.provider_call_count == 0
            assert app.state.oracle.request_counts[("POST", "/workspace/search")] == 1
            assert app.state.oracle.request_counts[("POST", "/workspace/review")] == 1
            if case == "known_interstitial":
                assert app.state.oracle.request_counts[("POST", "/workspace/dismiss")] == 1
            assert app.state.oracle.submit_requests == 0
            assert app.state.oracle.ledger == []
        finally:
            await surface.close()

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


def test_submission_denied_at_action_and_transport_boundaries(artifact_path):
    app = create_app()

    async def scenario(origin):
        bundle, _, surface, session, replay = await launch(origin, artifact_path=artifact_path)
        try:
            result = await replay.run(dict(VALUES))
            assert isinstance(result, Success), result
            assert result.metadata.provider_call_count == 0
            observed = await session.observe()
            proposal = ActionProposal(
                observation_id=observed.observation_id,
                ownership_epoch=observed.ownership_epoch,
                action=Click(target="submit_button"),
            )
            with pytest.raises(RuntimeFault) as denied:
                await session.execute(
                    proposal, bundle.artifact, dict(VALUES), {}, frozenset({"prepare"})
                )
            assert denied.value.code == FailureCode.POLICY_DENIED
            assert app.state.oracle.submit_requests == 0
            # Simulate an outside actor bypassing the automation gate. Context-level
            # network mediation must still prevent receipt of the submission.
            page = operator_page(surface)
            async with page.expect_event("requestfailed"):
                await (
                    page.frame_locator('iframe[name="workspace"]')
                    .get_by_role("button", name="Open account", exact=True)
                    .click()
                )
            assert surface.transport_denied_count >= 1
            assert app.state.oracle.submit_requests == 0
            assert app.state.oracle.ledger == []
        finally:
            await surface.close()

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


def test_automated_same_session_handoff_with_cookie_rotation_and_redacted_events(artifact_path):
    app = create_app("session_expired")

    async def scenario(origin):
        bundle, audit, surface, session, replay = await launch(origin, artifact_path=artifact_path)
        try:
            result = await replay.run(dict(VALUES))
            assert isinstance(result, Intervention), result
            assert result.reason == "session_expired"
            assert result.metadata.provider_call_count == 0
            page = operator_page(surface)
            context = page.context
            before_cookies = await context.cookies()
            context_id = surface.context_id
            await session.takeover()
            observed = await session.observe()
            with pytest.raises(RuntimeFault) as denied:
                await session.execute(
                    ActionProposal(
                        observation_id=observed.observation_id,
                        ownership_epoch=observed.ownership_epoch,
                        action=bundle.artifact.steps[0].action,
                    ),
                    bundle.artifact,
                    dict(VALUES),
                    {},
                    frozenset({"prepare"}),
                )
            assert denied.value.code == FailureCode.OWNERSHIP_DENIED
            # Premature resume cannot pass an expired-session checkpoint.
            with pytest.raises(RuntimeFault):
                await replay.resume()
            assert session.ownership == Ownership.HUMAN
            workspace = page.frame_locator('iframe[name="workspace"]')
            await workspace.get_by_label("Demo operator", exact=True).fill(
                "PRIVATE_OPERATOR_CANARY"
            )
            await workspace.get_by_role("button", name="Demo sign in", exact=True).click()
            await workspace.get_by_role(
                "heading", name="Prepare sub-account", exact=True
            ).wait_for()
            assert await context.cookies() != before_cookies
            # A visible but wrong member must not resume, even after reauthentication.
            await workspace.get_by_role("cell", name=VALUES["member_id"], exact=True).evaluate(
                "(cell) => cell.textContent = '000099'"
            )
            with pytest.raises(RuntimeFault) as wrong_identity:
                await replay.resume()
            assert wrong_identity.value.code == FailureCode.INVALID_RESUME
            assert session.ownership == Ownership.HUMAN
            await workspace.get_by_role("cell", name="000099", exact=True).evaluate(
                "(cell, value) => cell.textContent = value", arg=VALUES["member_id"]
            )
            # Recorder binding is observational only: hostile payloads cannot grant control.
            await page.evaluate(
                "window.__uiCapabilityActivity({kind:'change',control:'PRIVATE_EVENT_CANARY',frame:'main',ownership:'AUTOMATION'})"
            )
            assert session.ownership == Ownership.HUMAN
            result = await replay.resume()
            assert isinstance(result, Success), result
            assert result.metadata.human_intervened
            assert result.metadata.provider_call_count == 0
            assert page.context is context and surface.context_id == context_id
            assert app.state.oracle.signin_count == 1
            assert app.state.oracle.submit_requests == 0 and app.state.oracle.ledger == []
            events = [json.loads(line) for line in audit.getvalue().splitlines()]
            activities = [event for event in events if event["event"] == "human_activity"]
            assert {event["kind"] for event in activities} >= {"click", "change", "navigation"}
            assert "PRIVATE_OPERATOR_CANARY" not in audit.getvalue()
            assert "PRIVATE_EVENT_CANARY" not in audit.getvalue()
        finally:
            await surface.close()

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


def test_unknown_native_dialog_is_held_never_autoaccepted(artifact_path, tmp_path):
    app = create_app("unknown_dialog")

    async def scenario(origin):
        _, _, surface, session, replay = await launch(origin, artifact_path=artifact_path)
        try:
            result = await replay.run(dict(VALUES))
            assert isinstance(result, Intervention), result
            assert result.reason == "unknown_dialog"
            assert result.metadata.provider_call_count == 0
            assert (await session.observe()).dialog == "unknown"
            capture = await session.capture_safe(tmp_path / "dialog.png")
            assert capture["withheld"] is True
            assert not (tmp_path / "dialog.png").exists()
            assert app.state.oracle.submit_requests == 0
            assert app.state.oracle.ledger == []
            await session.abort()
        finally:
            await surface.close()

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


@pytest.mark.parametrize("modal_kind", ["html-dialog", "aria-dialog"])
def test_unknown_html_modal_pauses_without_dismissal_or_sensitive_capture(
    artifact_path, modal_kind, tmp_path
):
    app = create_app()

    async def scenario(origin):
        _, audit, surface, session, replay = await launch(origin, artifact_path=artifact_path)
        try:
            workspace = operator_page(surface).frame(name="workspace")
            assert workspace is not None
            # Automated hostile UI injection, distinct from a native JavaScript dialog.
            await workspace.evaluate(
                """kind => {
                    const modal = document.createElement(
                        kind === 'html-dialog' ? 'dialog' : 'div'
                    );
                    modal.textContent = 'PRIVATE_MODAL_CANARY';
                    if (kind === 'aria-dialog') {
                        modal.setAttribute('role', 'dialog');
                        modal.setAttribute('aria-modal', 'true');
                        modal.style.cssText =
                            'position:fixed;top:0;left:0;padding:20px;background:white';
                    }
                    document.body.appendChild(modal);
                    if (kind === 'html-dialog') modal.showModal();
                }""",
                modal_kind,
            )
            result = await replay.run(dict(VALUES))
            assert isinstance(result, Intervention), result
            assert result.reason == "unknown_dialog"
            assert result.metadata.provider_call_count == 0
            assert (await session.observe()).dialog == "unknown"
            assert await workspace.locator("dialog, [role=dialog]").is_visible()
            path = tmp_path / "html-modal.png"
            assert (await session.capture_safe(path))["withheld"] is True
            assert not path.exists()
            assert "PRIVATE_MODAL_CANARY" not in audit.getvalue()
            assert app.state.oracle.request_counts[("POST", "/workspace/search")] == 0
            assert app.state.oracle.request_counts[("POST", "/workspace/review")] == 0
            assert app.state.oracle.submit_requests == 0
            assert app.state.oracle.ledger == []
            await session.abort()
        finally:
            await surface.close()

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


@pytest.mark.parametrize("crash_scope", ["renderer", "browser_process"])
def test_real_browser_crash_fails_closed_without_submission_or_resume(artifact_path, crash_scope):
    app = create_app("slow_load")

    async def scenario(origin):
        _, _, surface, session, replay = await launch(origin, artifact_path=artifact_path)
        replay_task = crash_task = None
        try:
            page = operator_page(surface)
            browser = page.context.browser
            disconnected = asyncio.Event()
            browser.on("disconnected", lambda *_: disconnected.set())
            cdp = (
                await browser.new_browser_cdp_session()
                if crash_scope == "browser_process"
                else await page.context.new_cdp_session(page)
            )
            async with page.expect_request(
                lambda request: (
                    request.method == "POST" and request.url == origin + "/workspace/search"
                )
            ):
                replay_task = asyncio.create_task(replay.run(dict(VALUES)))
            # Exercise real crashes, not healthy page closure or fake adapter errors.
            if crash_scope == "browser_process":
                crash_task = asyncio.create_task(cdp.send("Browser.crash"))
                await asyncio.wait_for(disconnected.wait(), timeout=15)
            else:
                async with page.expect_event("crash"):
                    crash_task = asyncio.create_task(cdp.send("Page.crash"))
            result = await asyncio.wait_for(replay_task, timeout=15)
            assert isinstance(result, Failure), result
            assert result.code in {
                FailureCode.INTERRUPTED,
                FailureCode.UNKNOWN_ACTION_OUTCOME,
            }
            if crash_scope == "browser_process":
                assert result.code == FailureCode.INTERRUPTED
            assert result.metadata.provider_call_count == 0
            assert session.ownership == Ownership.ABORTED
            with pytest.raises(RuntimeFault) as denied:
                await replay.resume()
            assert denied.value.code == FailureCode.OWNERSHIP_DENIED
            assert app.state.oracle.request_counts[("POST", "/workspace/review")] == 0
            assert app.state.oracle.submit_requests == 0
            assert app.state.oracle.ledger == []
        finally:
            if replay_task is not None and not replay_task.done():
                replay_task.cancel()
            await surface.close()
            tasks = [task for task in (replay_task, crash_task) if task is not None]
            await asyncio.gather(*tasks, return_exceptions=True)

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


def test_capture_masks_shell_changes_and_withholds_incomplete_mask(artifact_path, tmp_path):
    app = create_app()

    async def scenario(origin):
        _, _, surface, session, replay = await launch(origin, artifact_path=artifact_path)
        try:
            result = await replay.run(dict(VALUES))
            assert isinstance(result, Success), result
            assert result.metadata.provider_call_count == 0
            path = tmp_path / "masked.png"
            capture = await session.capture_safe(path)
            assert capture["withheld"] is False, capture
            with Image.open(path) as image:
                assert image.convert("RGB").getextrema() == ((32, 32), (32, 32), (32, 32))
            page = operator_page(surface)
            await page.evaluate("document.querySelector('h1').textContent='PRIVATE_SHELL_CANARY'")
            changed_path = tmp_path / "changed.png"
            changed = await session.capture_safe(changed_path)
            assert changed["withheld"] is False, changed
            with Image.open(changed_path) as image:
                assert image.convert("RGB").getextrema() == ((32, 32), (32, 32), (32, 32))
            # Moving the masked root exposes pixels outside its bounding rectangle.
            await page.evaluate("document.documentElement.style.transform='translateX(20px)'")
            refused = await session.capture_safe(tmp_path / "unsafe.png")
            assert refused["withheld"] is True
            assert not (tmp_path / "unsafe.png").exists()
            assert app.state.oracle.submit_requests == 0
            assert app.state.oracle.ledger == []
        finally:
            await surface.close()

    with running_app(app) as origin:
        asyncio.run(scenario(origin))


def test_unguarded_sandbox_submission_really_mutates_ledger_through_ui():
    # Positive control: denial tests are not testing a fake/nonfunctional endpoint.
    app = create_app()

    async def scenario(origin):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            try:
                await page.goto(origin)
                navigation = page.frame_locator('iframe[name="navigation"]')
                workspace = page.frame_locator('iframe[name="workspace"]')
                await navigation.get_by_role("link", name="Member search", exact=True).click()
                await workspace.locator('input[name="member_id"]').fill("000042")
                await workspace.get_by_role("button", name="Search", exact=True).click()
                await workspace.get_by_role("link", name="000042", exact=True).click()
                await workspace.get_by_role("link", name="Prepare sub-account", exact=True).click()
                await workspace.get_by_label("Product", exact=True).select_option("SAVINGS_BASIC")
                await workspace.get_by_label("Nickname", exact=True).fill(
                    "Synthetic positive control"
                )
                await (
                    workspace.get_by_role("group", name="Account preparation", exact=True)
                    .get_by_role("button", name="Review", exact=True)
                    .click()
                )
                await workspace.get_by_role("button", name="Open account", exact=True).click()
                await workspace.get_by_text(
                    "A synthetic account was opened by submission.", exact=True
                ).wait_for()
                assert app.state.oracle.submit_requests == 1
                assert app.state.oracle.ledger == [
                    {
                        "member_id": "000042",
                        "product_code": "SAVINGS_BASIC",
                        "nickname": "Synthetic positive control",
                        "monthly_fee_minor": 250,
                        "currency": "USD",
                        "submitted": True,
                    }
                ]
            finally:
                await browser.close()

    with running_app(app) as origin:
        asyncio.run(scenario(origin))
