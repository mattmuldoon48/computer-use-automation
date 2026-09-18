"""Explicitly authorized discovery and isolated replay; never imported by Replay."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4

from .cli import _command, _source_snapshot, result_projection, run_browser, write_failure_snapshot
from .contracts import Intervention, Scalar, Success
from .demo import load_demo
from .discovery import Discovery, DiscoveryOutcome, live_planner
from .errors import RuntimeFault
from .evidence import EvidenceSink, contract_hash, diagnostic_projection
from .provider import OpenAIPlanner, Planner, ProviderError
from .session import Session
from .surfaces.playwright import PlaywrightSurface


def _values(supplied: object, origin: str) -> dict[str, Scalar]:
    definitions = load_demo(origin).artifact.goal.inputs
    if (
        not isinstance(supplied, dict)
        or set(supplied) != set(definitions)
        or any(not definition.accepts(supplied[name]) for name, definition in definitions.items())
    ):
        raise ValueError("invalid discovery inputs")
    return dict(supplied)


async def _operator_handoff(
    discovery: Discovery,
    outcome: DiscoveryOutcome,
    session: Session,
    run_dir: Path,
    *,
    headed: bool,
    interactive: bool,
    operator_seconds: int,
) -> DiscoveryOutcome:
    """Keep the discovery and live browser alive using the existing terminal protocol."""
    while isinstance(outcome.result, Intervention):
        print(json.dumps(result_projection(outcome.result)), flush=True)
        capture = await session.capture_safe(run_dir / "paused.png")
        (run_dir / "paused.capture.json").write_text(json.dumps(capture, indent=2) + "\n")
        if not headed or not interactive:
            print("Headed interactive session required; no human handoff claimed.", flush=True)
            return await discovery.abort()
        print(
            "Discovery is paused. Goal/target: authored.goal.json; state: paused.capture.json.\n"
            "Type takeover, then restore the reviewed state in this SAME browser.\n"
            "For session expiry, enter a synthetic Demo operator alias and click Demo sign in.\n"
            "Do not change the member or form fields, or open an account. Then type resume.\n"
            "Commands: takeover, resume, capture, abort.",
            flush=True,
        )
        deadline = time.monotonic() + operator_seconds
        while isinstance(outcome.result, Intervention):
            try:
                async with asyncio.timeout(max(0.01, deadline - time.monotonic())):
                    command = await _command()
            except TimeoutError:
                print("Operator deadline expired; discovery aborted.", flush=True)
                return await discovery.abort()
            if command in {"", "abort"}:
                return await discovery.abort()
            try:
                if command == "takeover":
                    await session.takeover()
                    print(
                        "HUMAN ownership granted. Restore the existing browser, then resume.",
                        flush=True,
                    )
                elif command == "resume":
                    outcome = await discovery.resume()
                    break  # A new intervention has its own reviewed checkpoint/deadline.
                elif command == "capture":
                    capture = await session.capture_safe(run_dir / "operator.png")
                    (run_dir / "operator.capture.json").write_text(
                        json.dumps(capture, indent=2) + "\n"
                    )
                    print("Saved redacted capture or explicit withholding reason.", flush=True)
                else:
                    print("Commands: takeover, resume, capture, abort", flush=True)
            except RuntimeFault as fault:
                print(
                    json.dumps(
                        {
                            "resume_rejected": fault.code.value,
                            "ownership": session.ownership.value,
                            "diagnostic": diagnostic_projection(fault.diagnostic),
                        }
                    ),
                    flush=True,
                )
    return outcome


async def discover_browser(
    origin: str,
    inputs: object,
    replay_inputs: object,
    evidence_dir: Path,
    planner: Planner,
    *,
    headed: bool = False,
    interactive: bool = True,
) -> int:
    values = _values(inputs, origin)
    replay_values = _values(replay_inputs, origin)
    if any(values[name] == replay_values[name] for name in values):
        raise ValueError("fresh replay must change member, product, and nickname")
    bundle = load_demo(origin)
    live = live_planner(planner)
    run_dir = evidence_dir / uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=False)
    source = _source_snapshot()
    (run_dir / "source.json").write_text(json.dumps(source, indent=2) + "\n")
    # Authored goal and authority are explicit, separate from any discovered actions.
    for name, contract in (
        ("goal", bundle.artifact.goal),
        ("profile", bundle.profile),
        ("policy", bundle.policy),
    ):
        (run_dir / f"authored.{name}.json").write_text(contract.model_dump_json(indent=2) + "\n")
    started = time.monotonic()
    with (run_dir / "events.jsonl").open("x") as stream:
        sink = EvidenceSink(stream)
        surface = await PlaywrightSurface.launch(
            bundle.artifact.goal.binding,
            bundle.profile.targets,
            values,
            sink,
            headed=headed,
            discovery=True,
        )
        session = Session(surface, bundle.policy, sink)
        sink.start(
            UUID(session.run_id),
            UUID(session.session_id),
            bundle.artifact.goal,
            bundle.profile,
            run_type="live_discovery" if live else "discovery_simulation",
        )
        try:
            discovery = Discovery(
                bundle.artifact, bundle.profile, session, planner, frozenset({"prepare"})
            )
            outcome = await discovery.run(values)
            outcome = await _operator_handoff(
                discovery,
                outcome,
                session,
                run_dir,
                headed=headed,
                interactive=interactive,
                operator_seconds=bundle.artifact.goal.budgets.operator_seconds,
            )
            capture = await session.capture_safe(run_dir / "terminal.png")
            write_failure_snapshot(run_dir, outcome.result, capture)
            summary: dict[str, object] = {
                "schema_version": 1,
                "source": {key: value for key, value in source.items() if key != "files"},
                "run_type": "live_discovery" if live else "discovery_simulation",
                "live_discovery": live,
                "session_id": session.session_id,
                "run_id": session.run_id,
                "profile_hash": contract_hash(bundle.profile),
                "goal_hash": contract_hash(bundle.artifact.goal),
                "discovery_mode": "human_assisted" if session.human_intervened else "unassisted",
                "operator_evidence": (
                    "terminal_control_transfer" if live else "automated_test_actor"
                )
                if session.human_intervened
                else "none",
                "playwright_version": version("playwright"),
                "browser_version": surface.browser_version,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "action_count": session.action_count,
                "provider_call_count": planner.call_count if live else 0,
                "planner_decision_count": planner.call_count,
                "provider_records": planner.records,
                "result": result_projection(outcome.result),
                "capture": capture,
                "fresh_replay": "not_run",
                "artifact_status": "not_compiled",
            }
            candidate = run_dir / "candidate.capability.json"
            if isinstance(outcome.result, Success) and outcome.artifact is not None:
                candidate.write_text(outcome.artifact.model_dump_json(indent=2) + "\n")
                summary["artifact_hash"] = contract_hash(outcome.artifact)
                summary["artifact_status"] = (
                    "candidate_with_operator_dependency"
                    if session.human_intervened
                    else "candidate"
                )
            (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(result_projection(outcome.result)), flush=True)
        except asyncio.CancelledError:
            await discovery.abort()
            raise
        finally:
            await surface.close()
    print(f"Redacted discovery evidence: {run_dir}", flush=True)
    if not isinstance(outcome.result, Success) or outcome.artifact is None:
        return 2
    # Discovery context is already closed. This uses the same provider-free Replay
    # entry point as the normal CLI, with a new browser and different inputs.
    summary["fresh_replay"] = "running"
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    try:
        replay_status = await run_browser(
            origin,
            replay_values,
            run_dir / "fresh-replay",
            headed=headed,
            artifact_path=candidate,
            interactive=interactive,
        )
    except asyncio.CancelledError:
        summary["fresh_replay"] = "interrupted"
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        raise
    except (RuntimeFault, OSError, ValueError):
        replay_status = 2
    replay_summaries = tuple((run_dir / "fresh-replay").glob("*/summary.json"))
    if len(replay_summaries) == 1:
        summary["fresh_replay_summary"] = str(replay_summaries[0].relative_to(run_dir))
    summary["fresh_replay"] = "success" if replay_status == 0 else "failed"
    summary["artifact_status"] = (
        ("validated_with_operator_dependency" if session.human_intervened else "validated")
        if replay_status == 0
        else "candidate_replay_failed"
    )
    summary["fresh_replay_exit_code"] = replay_status
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return replay_status


async def run_authorized_discovery(args: argparse.Namespace, inputs: object) -> int:
    if not args.authorize_api:
        raise ValueError("API authorization required")
    replay_inputs: object = (
        json.loads(args.replay_inputs_file.read_text())
        if args.replay_inputs_file
        else {"member_id": "000099", "product_code": "SAVINGS_PLUS", "nickname": "Holiday"}
    )
    try:
        planner = OpenAIPlanner(
            model=args.model,
            max_calls=args.max_calls,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.call_timeout,
        )
    except ProviderError as error:
        print(json.dumps({"kind": "failure", "code": error.code}), flush=True)
        return 2
    from sandbox.app import create_app
    from sandbox.server import running_app

    with running_app(create_app(args.case)) as origin:
        return await discover_browser(
            origin,
            inputs,
            replay_inputs,
            args.evidence_dir,
            planner,
            headed=args.headed,
        )
