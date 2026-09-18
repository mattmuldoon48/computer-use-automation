"""Local provider boundary regressions; no live provider requests are made."""

import asyncio
import json
import traceback

import pytest
from pydantic import ValidationError

from ui_capability.provider import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    ModelDecision,
    OpenAIPlanner,
    ProviderError,
)

SECRET = "sk-never-persist-this-credential"


def decision(**changes):
    return {
        "observation_id": "observed_1",
        "ownership_epoch": 0,
        "kind": "finish",
        "target_ref": None,
        "input_name": None,
        "destination": None,
        "milliseconds": None,
        "reason": None,
    } | changes


def envelope(text=None, **changes):
    return {
        "id": "resp_local123",
        "model": "approved-model",
        "status": "completed",
        "error": None,
        "usage": {"input_tokens": 12, "output_tokens": 9, "total_tokens": 21},
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text or json.dumps(decision())}],
            }
        ],
    } | changes


class LocalPlanner(OpenAIPlanner):
    def __init__(self, response=None, *, max_calls=2, timeout_seconds=1):
        super().__init__(
            model="approved-model",
            max_calls=max_calls,
            max_output_tokens=512,
            timeout_seconds=timeout_seconds,
        )
        self.response = envelope() if response is None else response
        self.attempts = 0

    async def _transport(self, body):
        self.attempts += 1
        if isinstance(self.response, Exception):
            raise self.response
        return json.dumps(self.response).encode(), "req_local123"


@pytest.fixture(autouse=True)
def local_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "fill", "target_ref": "ref_1"},
        {"kind": "click", "target_ref": "ref_1", "input_name": "name"},
        {"kind": "finish", "destination": "/somewhere"},
        {"kind": "wait", "milliseconds": True},
        {"kind": "wait", "milliseconds": 5001},
        {"kind": "intervene", "reason": "arbitrary_reason"},
        {"ownership_epoch": "0"},
        {"value": "never-accepted-literal"},
    ],
)
def test_decision_rejects_incoherent_or_untyped_actions(changes):
    with pytest.raises(ValidationError):
        ModelDecision.model_validate(decision(**changes))


def test_missing_nullable_field_is_not_silently_defaulted():
    payload = decision()
    del payload["input_name"]
    with pytest.raises(ValidationError):
        ModelDecision.model_validate(payload)


@pytest.mark.parametrize(
    "options",
    [
        {"model": "bad\r\nmodel"},
        {"max_calls": True},
        {"max_output_tokens": 0},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": 10**1000},
    ],
)
def test_invalid_configuration_is_sanitized(options):
    config = {
        "model": "approved-model",
        "max_calls": 2,
        "max_output_tokens": 512,
        "timeout_seconds": 1,
    } | options
    with pytest.raises(ProviderError) as caught:
        OpenAIPlanner(**config)
    assert str(caught.value) == "provider_error"


def test_constructor_requires_key_without_contacting_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("constructor attempted network")

    monkeypatch.setattr(asyncio, "open_connection", forbidden)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ProviderError) as caught:
        LocalPlanner()
    assert str(caught.value) == "provider_error"
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    planner = LocalPlanner()
    assert planner.call_count == 0
    assert planner.live is False


def test_transport_failure_consumes_budget_and_hides_diagnostics():
    async def scenario():
        planner = LocalPlanner(OSError(SECRET), max_calls=1)
        with pytest.raises(ProviderError) as caught:
            await planner.propose({"goal_template": "private-goal"})
        rendered = "".join(traceback.format_exception(caught.value))
        assert SECRET not in rendered
        assert str(caught.value) == "provider_error"
        with pytest.raises(ProviderError) as exhausted:
            await planner.propose({})
        assert exhausted.value.code == "budget_exceeded"
        assert planner.attempts == planner.call_count == 1
        assert planner.records == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"goal_template": "x" * (MAX_REQUEST_BYTES + 1)},
        {"goal_template": "é" * (MAX_REQUEST_BYTES // 2)},
        {"invalid_number": float("nan")},
        {"oversized_list": [None] * 16_385},
    ],
)
def test_invalid_or_oversized_request_never_attempts_network(payload):
    async def scenario():
        planner = LocalPlanner()
        with pytest.raises(ProviderError):
            await planner.propose(payload)
        assert planner.call_count == planner.attempts == 0

    asyncio.run(scenario())


def test_malformed_decision_is_repairable_but_never_recorded_as_valid():
    async def scenario():
        planner = LocalPlanner(envelope(json.dumps(decision(kind="fill", target_ref="ref_1"))))
        with pytest.raises(ProviderError) as caught:
            await planner.propose({})
        assert caught.value.code == "malformed_output"
        assert planner.records[0]["decision_validated"] is False
        planner.response = envelope()
        result = await planner.propose({})
        assert result.kind == "finish"
        assert planner.call_count == 2
        assert planner.records[-1]["decision_validated"] is True
        assert planner.records[-1]["usage"] == {
            "input_tokens": 12,
            "output_tokens": 9,
            "total_tokens": 21,
        }
        assert "text" not in json.dumps(planner.records)

    asyncio.run(scenario())


def test_invalid_decision_diagnostics_exclude_values_messages_and_unknown_keys():
    async def scenario():
        payload = decision(kind="fill", target_ref=SECRET, input_name=SECRET)
        payload[SECRET] = SECRET
        planner = LocalPlanner(envelope(json.dumps(payload)))
        with pytest.raises(ProviderError) as caught:
            await planner.propose({})
        assert caught.value.code == "malformed_output"
        diagnostic = planner.records[0]["decision_failure"]
        assert diagnostic["category"] == "validation"
        assert diagnostic["kind"] == "fill"
        assert diagnostic["invalid_fields"] == ["decision", "input_name"]
        assert SECRET not in json.dumps(planner.records)
        assert SECRET not in "".join(traceback.format_exception(caught.value))

    asyncio.run(scenario())


@pytest.mark.parametrize("input_name", ["member_id", "undeclared"])
def test_fill_accepts_only_declared_input_references(input_name):
    async def scenario():
        planner = LocalPlanner(
            envelope(json.dumps(decision(kind="fill", target_ref="ref_1", input_name=input_name)))
        )
        request = {
            "input_definitions": {"member_id": {"type": "string"}},
            "allowed_action_kinds": ["fill"],
            "observation": {"targets": [{"ref": "ref_1", "allowed_actions": ["fill"]}]},
        }
        if input_name == "member_id":
            result = await planner.propose(request)
            assert result.input_name == "member_id"
        else:
            with pytest.raises(ProviderError) as caught:
                await planner.propose(request)
            assert caught.value.code == "malformed_output"
            assert planner.records[-1]["decision_validated"] is False
            assert planner.records[-1]["decision_failure"] == {
                "category": "reference",
                "invalid_fields": ["input_name"],
            }

    asyncio.run(scenario())


def test_provider_rejects_action_kind_not_authorized_for_observed_target():
    async def scenario():
        planner = LocalPlanner(envelope(json.dumps(decision(kind="click", target_ref="ref_1"))))
        with pytest.raises(ProviderError) as caught:
            await planner.propose(
                {
                    "allowed_action_kinds": ["click", "fill"],
                    "observation": {"targets": [{"ref": "ref_1", "allowed_actions": ["fill"]}]},
                }
            )
        assert caught.value.code == "malformed_output"
        assert planner.records[-1]["decision_failure"]["category"] == "authority"
        assert planner.records[-1]["decision_validated"] is False

    asyncio.run(scenario())


def test_duplicate_decision_keys_are_rejected():
    async def scenario():
        text = json.dumps(decision())[:-1] + ', "kind": "click"}'
        planner = LocalPlanner(envelope(text))
        with pytest.raises(ProviderError) as caught:
            await planner.propose({})
        assert caught.value.code == "malformed_output"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response",
    [
        envelope(status="incomplete"),
        envelope(error={"message": SECRET}),
        envelope(
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "refusal", "refusal": SECRET}],
                }
            ]
        ),
        envelope(model="untrusted\nmodel"),
        envelope(usage={"input_tokens": True, "output_tokens": 1, "total_tokens": 2}),
    ],
)
def test_refusal_incomplete_errors_and_unbounded_metadata_fail_closed(response):
    async def scenario():
        planner = LocalPlanner(response)
        with pytest.raises(ProviderError) as caught:
            await planner.propose({})
        assert caught.value.code == "provider_error"
        assert SECRET not in str(caught.value)
        assert SECRET not in json.dumps(planner.records)
        assert all(record["decision_validated"] is False for record in planner.records)

    asyncio.run(scenario())


class Writer:
    def __init__(self):
        self.transport = self
        self.closed = False
        self.aborted = False
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True


def production_planner(timeout=1):
    return OpenAIPlanner(
        model="approved-model", max_calls=1, max_output_tokens=512, timeout_seconds=timeout
    )


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_close_connection_without_retry(monkeypatch, cancel):
    async def scenario():
        writer = Writer()
        opened = asyncio.Event()
        attempts = 0

        async def connect(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            opened.set()
            return asyncio.StreamReader(), writer

        monkeypatch.setattr(asyncio, "open_connection", connect)
        planner = production_planner(timeout=0.02 if not cancel else 1)
        task = asyncio.create_task(planner.propose({}))
        await opened.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else ProviderError):
            await task
        assert writer.closed and writer.aborted
        assert planner.call_count == attempts == 1
        assert planner.records == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "wire",
    [
        b"HTTP/1.1 302 Found\r\nLocation: https://evil.test/\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 1048577\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n100001\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 1\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n\r\n",
        b"HTTP/1.1 200 OK\r\n\r\n" + b"x" * (MAX_RESPONSE_BYTES + 1),
    ],
)
def test_http_redirects_oversize_and_ambiguous_framing_fail_closed(monkeypatch, wire):
    async def scenario():
        writer = Writer()
        attempts = 0

        async def connect(host, port, **kwargs):
            nonlocal attempts
            attempts += 1
            assert (host, port) == ("api.openai.com", 443)
            reader = asyncio.StreamReader()
            reader.feed_data(wire)
            reader.feed_eof()
            return reader, writer

        monkeypatch.setattr(asyncio, "open_connection", connect)
        planner = production_planner()
        with pytest.raises(ProviderError):
            await planner.propose({})
        assert attempts == planner.call_count == 1
        assert writer.closed and writer.aborted
        assert planner.records == []

    asyncio.run(scenario())


@pytest.mark.parametrize("framing", ["length", "chunked", "close"])
def test_bounded_http_response_yields_validated_decision(monkeypatch, framing):
    async def scenario():
        payload = json.dumps(envelope()).encode()
        if framing == "length":
            frame = f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
        elif framing == "chunked":
            frame = (
                b"Transfer-Encoding: chunked\r\n\r\n"
                + f"{len(payload):x}\r\n".encode()
                + payload
                + b"\r\n0\r\n\r\n"
            )
        else:
            frame = b"\r\n" + payload
        writer = Writer()

        async def connect(*args, **kwargs):
            reader = asyncio.StreamReader()
            reader.feed_data(b"HTTP/1.1 200 OK\r\nx-request-id: req_local123\r\n" + frame)
            reader.feed_eof()
            return reader, writer

        monkeypatch.setattr(asyncio, "open_connection", connect)
        planner = production_planner()
        result = await planner.propose({"goal_template": "private-goal"})
        assert result.kind == "finish"
        assert planner.records[0]["response_id"] == "resp_local123"
        assert planner.records[0]["request_id"] == "req_local123"
        assert planner.records[0]["decision_validated"] is True
        assert "private-goal" not in json.dumps(planner.records)
        assert SECRET not in json.dumps(planner.records)
        assert writer.writes[0].startswith(b"POST /v1/responses HTTP/1.1\r\n")
        assert writer.closed and writer.aborted

    asyncio.run(scenario())
