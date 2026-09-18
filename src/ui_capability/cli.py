"""UI capability replay, explicit live discovery, and same-session operator control."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from .contracts import (
    BusinessOutcome,
    Diagnostic,
    Failure,
    FailureCode,
    Intervention,
    Ownership,
    Scalar,
    Success,
    TerminalResult,
)
from .demo import load_demo
from .errors import RuntimeFault
from .evidence import EvidenceSink, diagnostic_projection
from .replay import Replay
from .session import Session
from .surfaces.playwright import PlaywrightSurface


async def _command() -> str:
    """Wait without blocking browser callbacks or leaving an orphaned input thread."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future[str] = loop.create_future()

    def ready() -> None:
        if not result.done():
            result.set_result(sys.stdin.readline().strip().lower())

    print("operator> ", end="", flush=True)
    loop.add_reader(sys.stdin.fileno(), ready)
    try:
        return await result
    finally:
        loop.remove_reader(sys.stdin.fileno())


def result_projection(result: TerminalResult | Intervention) -> dict[str, object]:
    projection: dict[str, object] = {
        "kind": "awaiting_operator" if isinstance(result, Intervention) else result.kind,
        "step_index": result.metadata.step_index,
        "human_intervened": result.metadata.human_intervened,
        "provider_call_count": result.metadata.provider_call_count,
    }
    if isinstance(result, Failure):
        projection["code"] = result.code.value
        if (diagnostic := diagnostic_projection(result.diagnostic)) is not None:
            projection["diagnostic"] = diagnostic
    elif isinstance(result, BusinessOutcome):
        projection["code"] = (
            result.code
            if result.code in {"member_not_found", "product_ineligible", "validation_rejected"}
            else "DECLARED_BUSINESS_OUTCOME"
        )
    elif isinstance(result, Intervention):
        projection["reason"] = result.reason
    return projection


def write_failure_snapshot(
    directory: Path,
    result: TerminalResult | Intervention,
    capture: dict[str, object],
) -> None:
    """Retain useful semantic state without text, values, or unmasked pixels."""
    if not isinstance(result, Failure):
        return
    snapshot = {
        "schema_version": 1,
        "kind": "semantic_failure_snapshot",
        "observation_scope": "last_observed_state_not_a_live_attestation",
        "result": result_projection(result),
        "structure": capture.get("structure", {"state": "unavailable"}),
    }
    (directory / "failure.snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")


def _source_snapshot() -> dict[str, object]:
    """Fingerprint the executed public runtime; never read environment secrets."""
    package = Path(__file__).resolve().parent
    root = package.parent.parent
    sandbox = root / "sandbox"
    if not sandbox.is_dir():
        sandbox = package.parent / "sandbox"
    files: dict[str, str] = {}
    for prefix, directory in (("src/ui_capability", package), ("sandbox", sandbox)):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".js", ".json", ".html"}:
                name = f"{prefix}/{path.relative_to(directory).as_posix()}"
                files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("pyproject.toml", "uv.lock", ".python-version"):
        path = root / name
        if path.is_file():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    commit: str | None = None
    dirty: bool | None = None
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        lines = revision.stdout.splitlines()
        if revision.returncode == 0 and len(lines) == 2 and Path(lines[0]).resolve() == root:
            status = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if status.returncode == 0:
                commit, dirty = lines[1], bool(status.stdout)
    except (OSError, subprocess.TimeoutExpired):
        pass  # Installed packages can lack Git; the runtime digest remains available.
    return {
        "commit": commit,
        "worktree_dirty": dirty,
        "source_digest": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "scope": "Runtime Python/JavaScript/JSON/templates and available project configuration",
        "files": files,
    }


async def run_browser(
    origin: str,
    inputs: object,
    evidence_dir: Path,
    *,
    headed: bool,
    artifact_path: Path | None = None,
    interactive: bool = True,
) -> int:
    bundle = load_demo(origin, artifact_path)
    # No browser starts for malformed invocation values; never serialize validation errors.
    if (
        not isinstance(inputs, dict)
        or set(inputs) != set(bundle.artifact.goal.inputs)
        or any(
            not definition.accepts(inputs[name])
            for name, definition in bundle.artifact.goal.inputs.items()
        )
    ):
        print('{"kind":"failure","code":"INVALID_ARGUMENT"}')
        return 2
    values: dict[str, Scalar] = dict(inputs)
    run_dir = evidence_dir / uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=False)
    source = _source_snapshot()
    (run_dir / "source.json").write_text(json.dumps(source, indent=2) + "\n")
    for name, contract in (
        ("artifact", bundle.artifact),
        ("profile", bundle.profile),
        ("policy", bundle.policy),
    ):
        (run_dir / f"fixture.{name}.json").write_text(contract.model_dump_json(indent=2) + "\n")
    started = time.monotonic()
    with (run_dir / "events.jsonl").open("x") as stream:
        sink = EvidenceSink(stream)
        surface = await PlaywrightSurface.launch(
            bundle.artifact.goal.binding,
            bundle.profile.targets,
            values,
            sink,
            headed=headed,
        )
        session = Session(surface, bundle.policy, sink)
        sink.start(
            UUID(session.run_id),
            UUID(session.session_id),
            bundle.artifact,
            bundle.profile,
            run_type="discovered_capability_replay"
            if bundle.artifact.provenance.kind == "live_discovery"
            else "hand_authored_browser_fixture",
        )
        replay = Replay(bundle.artifact, bundle.profile, session, frozenset({"prepare"}))
        capture: dict[str, object] = {}
        try:
            result = await replay.run(values)
            while isinstance(result, Intervention):
                print(json.dumps(result_projection(result)), flush=True)
                capture = await session.capture_safe(run_dir / "paused.png")
                (run_dir / "paused.capture.json").write_text(json.dumps(capture, indent=2) + "\n")
                if not headed or not interactive:
                    print(
                        "Headed interactive session required; no human handoff claimed.", flush=True
                    )
                    await session.abort()
                    break
                print(
                    "Automation is paused. Type takeover to take control of this same browser.\n"
                    "For session expiry: enter a synthetic alias in Demo operator, "
                    "then click Demo sign in.\n"
                    "Do not open an account. After correcting the UI, type resume.\n"
                    "Other commands: capture, abort. No real credentials are needed.",
                    flush=True,
                )
                deadline = time.monotonic() + bundle.artifact.goal.budgets.operator_seconds
                while isinstance(result, Intervention):
                    try:
                        async with asyncio.timeout(max(0.01, deadline - time.monotonic())):
                            command = await _command()
                    except TimeoutError:
                        await session.abort()
                        print("Operator deadline expired; session aborted.", flush=True)
                        break
                    if command in {"", "abort"}:
                        await session.abort()
                        break
                    try:
                        if command == "takeover":
                            await session.takeover()
                            print(
                                "HUMAN ownership granted. Use the existing browser, "
                                "then type resume.",
                                flush=True,
                            )
                        elif command == "resume":
                            result = await replay.resume()
                            print(json.dumps(result_projection(result)), flush=True)
                        elif command == "capture":
                            capture = await session.capture_safe(run_dir / "operator.png")
                            (run_dir / "operator.capture.json").write_text(
                                json.dumps(capture, indent=2) + "\n"
                            )
                            print(
                                "Saved redacted capture or explicit withholding reason.", flush=True
                            )
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
                if session.ownership == Ownership.ABORTED:
                    break
            if isinstance(result, Intervention) and session.ownership == Ownership.ABORTED:
                result = Failure(
                    code=FailureCode.INTERRUPTED,
                    metadata=result.metadata.model_copy(
                        update={"human_intervened": session.human_intervened}
                    ),
                    diagnostic=Diagnostic(
                        stage="resume", expected="checkpoint", observed="interrupted"
                    ),
                )
                sink.result(result)
            capture = await session.capture_safe(run_dir / "terminal.png")
            write_failure_snapshot(run_dir, result, capture)
            summary = {
                "schema_version": 1,
                "source": {key: value for key, value in source.items() if key != "files"},
                "run_type": "discovered_capability_replay"
                if bundle.artifact.provenance.kind == "live_discovery"
                else "hand_authored_browser_fixture",
                "live_discovery": False,
                "artifact_provenance": bundle.artifact.provenance.kind,
                "operator_evidence": "terminal_control_transfer"
                if session.human_intervened
                else "none",
                "session_id": session.session_id,
                "ownership": session.ownership.value,
                "artifact_hash": result.metadata.artifact_hash,
                "profile_hash": result.metadata.profile_hash,
                "playwright_version": version("playwright"),
                "browser_version": surface.browser_version,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "action_count": session.action_count,
                "result": result_projection(result),
                "capture": capture,
            }
            (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(result_projection(result)), flush=True)
            print(f"Redacted replay evidence: {run_dir}", flush=True)
            return 0 if isinstance(result, Success) else 2 if isinstance(result, Failure) else 3
        finally:
            await surface.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="uicap", description="Policy-gated UI discovery and model-free replay"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sandbox = sub.add_parser("sandbox", help="run the synthetic UI only")
    sandbox.add_argument("operation", choices=["serve"])
    sandbox.add_argument("--port", type=int, default=8000)
    sandbox.add_argument("--case", default="happy", choices=_cases())
    for name in ("demo", "replay"):
        command = sub.add_parser(name)
        command.add_argument("--headed", action="store_true")
        command.add_argument("--inputs-file", type=Path)
        command.add_argument("--evidence-dir", type=Path, default=Path("artifacts/local"))
        command.add_argument("--artifact", type=Path)
        if name == "demo":
            command.add_argument("--case", choices=_cases(), default="happy")
        else:
            command.add_argument("--origin", default="http://127.0.0.1:8000")
    discover = sub.add_parser(
        "discover", help="authorized model discovery, then fresh model-free replay"
    )
    discover.add_argument("--model", required=True)
    discover.add_argument(
        "--authorize-api",
        action="store_true",
        help="authorize sending filtered UI observations and paid model requests",
    )
    discover.add_argument("--max-calls", type=int, default=12)
    discover.add_argument("--max-output-tokens", type=int, default=1200)
    discover.add_argument("--call-timeout", type=float, default=30)
    discover.add_argument("--headed", action="store_true")
    discover.add_argument("--case", choices=("happy", "session_expired"), default="happy")
    discover.add_argument("--inputs-file", type=Path)
    discover.add_argument("--replay-inputs-file", type=Path)
    discover.add_argument("--evidence-dir", type=Path, default=Path("artifacts/local/discovery"))
    args = parser.parse_args()
    try:
        if args.command == "sandbox":
            import uvicorn

            from sandbox.app import create_app

            uvicorn.run(
                create_app(args.case),
                host="127.0.0.1",
                port=args.port,
                access_log=False,
                log_level="warning",
                ws="none",
            )
            return
        inputs: object = (
            json.loads(args.inputs_file.read_text())
            if args.inputs_file
            else {
                "member_id": "000042",
                "product_code": "SAVINGS_BASIC",
                "nickname": "Rainy day",
            }
        )
        if args.command == "discover":
            if not args.authorize_api:
                print('{"kind":"failure","code":"API_AUTHORIZATION_REQUIRED"}', file=sys.stderr)
                raise SystemExit(2)
            from .discovery_cli import run_authorized_discovery

            status = asyncio.run(run_authorized_discovery(args, inputs))
        elif args.command == "demo":
            if (
                args.case == "member_not_found"
                and args.inputs_file is None
                and isinstance(inputs, dict)
            ):
                inputs = {**inputs, "member_id": "999999"}
            from sandbox.app import create_app
            from sandbox.server import running_app

            # Scenario knowledge exists only in this test/demo wrapper, never in Replay/Surface.
            with running_app(create_app(args.case)) as origin:
                status = asyncio.run(
                    run_browser(
                        origin,
                        inputs,
                        args.evidence_dir,
                        headed=args.headed,
                        artifact_path=args.artifact,
                    )
                )
        else:
            status = asyncio.run(
                run_browser(
                    args.origin,
                    inputs,
                    args.evidence_dir,
                    headed=args.headed,
                    artifact_path=args.artifact,
                )
            )
    except (ValidationError, ValueError, OSError, RuntimeFault):
        print('{"kind":"failure","code":"CONFIGURATION_OR_RUNTIME_ERROR"}', file=sys.stderr)
        status = 2
    raise SystemExit(status)


def _cases() -> list[str]:
    return [
        "happy",
        "member_not_found",
        "product_ineligible",
        "validation_rejected",
        "slow_load",
        "known_interstitial",
        "permission_denied",
        "session_expired",
        "unknown_dialog",
        "duplicate_target",
        "wrong_member_review",
    ]


if __name__ == "__main__":
    main()
