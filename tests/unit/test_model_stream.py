import asyncio
import json

import httpx
import pytest

from hy3_contestlens.domain import CriticReview, SolverOutput
from hy3_contestlens.errors import ContestLensError
from hy3_contestlens.model import Hy3Client
from hy3_contestlens.model_diagnostics import model_run_context
from hy3_contestlens.settings import Hy3Settings
from hy3_contestlens.model_stream import CompletionStream, RepetitionGuard, StreamResponseError


SOLUTION = {
    "problem_summary": "中文题面", "steps": [{"step_id": "S1", "goal": "solve", "statement": "scan", "justification": "proof"}],
    "complexity": {"time": "O(n)", "space": "O(1)"}, "cpp_source": "int main() { return 0; }",
}


def frame(content=None, *, finish=None, reasoning=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return 'data: ' + json.dumps({"id": "stream-1", "model": "hy3", "choices": [
        {"index": 0, "delta": delta, "finish_reason": finish},
    ]}, ensure_ascii=False) + '\n\n'


def stream_text(content=None, *, finish="stop", done=True):
    answer = json.dumps(SOLUTION if content is None else content, ensure_ascii=False)
    return (": keepalive\n\n" + frame(reasoning="PRIVATE_REASONING_SENTINEL")
            + "".join(frame(answer[i:i + 17]) for i in range(0, len(answer), 17))
            + frame(finish=finish)
            + 'data: {"choices":[],"usage":{"completion_tokens":100}}\n\n'
            + ("data: [DONE]\n\n" if done else ""))


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, content, *, fail=False, on_chunk=None, hang=False):
        self.content = content.encode("utf-8")
        self.fail = fail
        self.on_chunk = on_chunk
        self.hang = hang
        self.closed = False

    async def __aiter__(self):
        for offset in range(0, len(self.content), 11):
            if self.on_chunk:
                self.on_chunk()
            yield self.content[offset:offset + 11]
        if self.hang:
            await asyncio.sleep(10)
        if self.fail:
            raise httpx.RemoteProtocolError("upstream closed the stream with api_key=SECRET_KEY")

    async def aclose(self):
        self.closed = True


def make_client(tmp_path, streams, **settings):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        stream = streams[len(requests) - 1]
        if isinstance(stream, httpx.Response):
            return stream
        return httpx.Response(200, headers={"Content-Type": "text/event-stream", "x-request-id": "stream-request"}, stream=stream)

    client = Hy3Client(Hy3Settings(api_key="SECRET_KEY", retry_backoff_seconds=0, **settings), diagnostics_dir=tmp_path,
                       transport=httpx.MockTransport(respond))
    return client, requests


def logs(tmp_path):
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(tmp_path.glob("call_*.json"))]


@pytest.mark.asyncio
async def test_stream_reassembles_utf8_json_preserves_budget_and_omits_reasoning(tmp_path):
    stream = ByteStream(stream_text())
    client, requests = make_client(tmp_path, [stream], max_tokens=127000, reasoning_effort="high")
    result = await client.solve({}, "road")
    assert isinstance(result, SolverOutput) and result.model_dump()["problem_summary"] == "中文题面"
    assert stream.closed
    request = requests[0]
    assert request["stream"] is True and request["stream_options"] == {"include_usage": True}
    assert request["max_tokens"] == 127000 and request["reasoning_effort"] == "high"
    assert request["response_format"]["type"] == "json_schema"
    record = logs(tmp_path)[0]
    assert record["response"]["finish_reason"] == "stop"
    assert record["response"]["usage"]["completion_tokens"] == 100
    assert record["response"]["stream"]["done"]
    assert record["response"]["stream"]["reasoning_chars"] > 0
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps(record) and "SECRET_KEY" not in json.dumps(record)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["disconnect", "eof", "no_finish", "error", "delta_error", "invalid_json", "length", "bad_schema"])
async def test_stream_failure_retries_from_scratch_and_never_accepts_partial_output(tmp_path, kind):
    first = {
        "disconnect": ByteStream(frame('{"partial":'), fail=True),
        "eof": ByteStream(stream_text(done=False)),
        "no_finish": ByteStream(frame(json.dumps(SOLUTION)) + "data: [DONE]\n\n"),
        "error": ByteStream('data: {"error":{"message":"SECRET_KEY"}}\n\n' + "data: [DONE]\n\n"),
        "delta_error": ByteStream('data: {"choices":[{"delta":{"error":"SECRET_KEY"}}]}\n\n'),
        "invalid_json": ByteStream("data: not-json\n\n"),
        "length": ByteStream(stream_text(finish="length")),
        "bad_schema": ByteStream(stream_text({"problem_summary": "partial"})),
    }[kind]
    client, requests = make_client(tmp_path, [first, ByteStream(stream_text())])
    result = await client.solve({}, "road")
    assert result.cpp_source == SOLUTION["cpp_source"]
    records = logs(tmp_path)
    assert len(requests) == 2
    assert [record["outcome"] for record in records] == ["error", "success"]
    if kind == "disconnect":
        assert records[0]["failure"]["exception_type"] == "RemoteProtocolError"
    if kind in {"eof", "no_finish"}:
        assert records[0]["failure"]["kind"] == "stream_incomplete"
    assert "SECRET_KEY" not in json.dumps(records)
    assert first.closed


@pytest.mark.asyncio
async def test_regular_json_and_explicit_nonstream_endpoints_are_still_supported(tmp_path):
    response = lambda: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(SOLUTION)}, "finish_reason": "stop"}]})
    for enabled in (True, False):
        client, requests = make_client(tmp_path, [response()], stream=enabled)
        assert (await client.solve({}, "road")).cpp_source == SOLUTION["cpp_source"]
        assert requests[0]["stream"] is enabled
        assert ("stream_options" in requests[0]) is enabled


@pytest.mark.asyncio
async def test_cancellation_while_receiving_chunks_closes_stream_and_does_not_retry(tmp_path):
    chunks = []
    stream = ByteStream(stream_text(), on_chunk=lambda: chunks.append(True))
    client, requests = make_client(tmp_path, [stream])
    with pytest.raises(asyncio.CancelledError), model_run_context(tmp_path, "run_cancel", cancelled=lambda: len(chunks) > 3):
        await client.solve({}, "road")
    assert stream.closed and len(requests) == 1


@pytest.mark.asyncio
async def test_stream_total_timeout_is_bounded_and_logged(tmp_path):
    stream = ByteStream(frame(reasoning="thinking"), hang=True)
    client, requests = make_client(tmp_path, [stream], max_attempts=1)
    client.timeout_seconds = 0.05
    with pytest.raises(ContestLensError) as caught:
        await client.solve({}, "road")
    assert caught.value.details["exception_type"] == "TimeoutError"
    assert stream.closed and len(requests) == 1


@pytest.mark.asyncio
async def test_body_disconnect_after_json_headers_retains_transport_error(tmp_path):
    response = httpx.Response(200, headers={"content-type": "application/json"}, stream=ByteStream("", fail=True))
    client, _ = make_client(tmp_path, [response], max_attempts=1)
    with pytest.raises(ContestLensError) as caught:
        await client.solve({}, "road")
    assert caught.value.details["exception_type"] == "RemoteProtocolError"


def test_guard_requires_sustained_repeated_prose_and_never_checks_reasoning():
    phrase = "UNRESOLVED because no defect is identified and the summary does not claim one. "
    guard = RepetitionGuard()
    guard.feed(phrase * 30)  # Occasional repetition does not terminate a review.
    with pytest.raises(StreamResponseError, match="repetitive loop"):
        for _ in range(100):
            guard.feed(phrase * 4)
    # Long, distinct evidence and repetitive code are not rejected.
    RepetitionGuard().feed(" ".join(f"Step {i} establishes the distinct bound n < {i + 1234}." for i in range(500)))
    stream = CompletionStream(guard_repetition=True)
    for line in frame(reasoning=phrase * 500).splitlines():
        stream.feed(line)
    assert stream.reasoning_chars > 10000
    unguarded = CompletionStream()
    for line in frame(phrase * 500).splitlines():
        unguarded.feed(line)
    assert unguarded.content_chars > 10000


@pytest.mark.asyncio
async def test_repetitive_review_closes_early_retries_and_emits_safe_progress(tmp_path):
    phrase = "The summary does not claim a defect, consistent with UNRESOLVED. "
    repeated = '{"summary":"' + phrase * 500
    first = ByteStream("".join(frame(repeated[i:i + 128]) for i in range(0, len(repeated), 128)))
    valid = {"reviewer": "algorithm_critic", "assessments": [], "summary": "No defect found."}
    client, requests = make_client(tmp_path, [first, ByteStream(stream_text(valid))], reasoning_effort="high", max_tokens=127000)
    events = []
    with model_run_context(tmp_path, "run_progress", progress=events.append):
        result = await client.json_completion(role="algorithm_critic", prompt="Review.", schema=CriticReview)
    assert result.summary == valid["summary"] and first.closed
    assert len(requests) == 2
    assert all(r["reasoning_effort"] == "high" and r["max_tokens"] == 127000 for r in requests)
    assert requests[1]["messages"][-1]["content"].find("repetitive_output") >= 0
    assert phrase not in requests[1]["messages"][-1]["content"]
    assert events[0]["phase"] == "waiting" and events[-1]["phase"] == "success"
    retry = next(e for e in events if e["phase"] == "retrying")
    assert retry["failure_kind"] == "repetitive_output" and retry["answer_chars"] < 12000
    assert len({e["call_id"] for e in events}) == 1
    assert {e["attempt"] for e in events} == {1, 2}
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps(events)
    assert "SECRET_KEY" not in json.dumps(events)


@pytest.mark.asyncio
async def test_progress_failure_does_not_fail_model_call(tmp_path):
    client, _ = make_client(tmp_path, [ByteStream(stream_text())])
    def broken(data):
        raise OSError("unavailable")
    with model_run_context(tmp_path, "run_progress", progress=broken):
        assert (await client.solve({}, "road")).cpp_source == SOLUTION["cpp_source"]
