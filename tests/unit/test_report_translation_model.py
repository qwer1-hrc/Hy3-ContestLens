import json

import httpx
import pytest

from hy3_contestlens.errors import ContestLensError
from hy3_contestlens.model import Hy3Client
from hy3_contestlens.settings import Hy3Settings, ReportTranslationSettings


def client_for(items, captured):
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"items": items})}}]})
    profile = ReportTranslationSettings().model_settings(Hy3Settings(api_key="mock-only", reasoning_effort="high", max_tokens=127000))
    return Hy3Client(profile, transport=httpx.MockTransport(respond))


@pytest.mark.asyncio
async def test_translator_uses_isolated_role_and_validated_chinese_text():
    captured = []
    cache_key = "a" * 64 + ":0"
    client = client_for([{"id": "t1", "text": "步骤 S1 的不变量成立。"}], captured)
    result = await client.translate_report_texts({cache_key: "The invariant in S1 holds."})
    assert result == {cache_key: "步骤 S1 的不变量成立。"}
    assert "report_translator_zh" in captured[0]["messages"][0]["content"]
    assert "UNTRUSTED REPORT DATA" in captured[0]["messages"][1]["content"]
    assert "not a solver or critic" in captured[0]["messages"][1]["content"]
    assert cache_key not in captured[0]["messages"][1]["content"]
    assert '"t1"' in captured[0]["messages"][1]["content"]
    assert captured[0]["reasoning_effort"] == "low"
    assert captured[0]["max_tokens"] == 4096
    assert captured[0]["temperature"] == 0.1


@pytest.mark.asyncio
@pytest.mark.parametrize("items", [
    [], [{"id": "other", "text": "证据"}],
    [{"id": "t1", "text": "证据"}, {"id": "t1", "text": "重复"}],
    [{"id": "t1", "text": ""}], [{"id": "t1", "text": "Untranslated evidence."}],
])
async def test_invalid_fragments_are_left_unresolved_for_targeted_retry(items):
    client = client_for(items, [])
    assert await client.translate_report_texts({"a": "Original evidence."}) == {}


@pytest.mark.asyncio
async def test_bad_item_does_not_discard_another_valid_translation():
    client = client_for([
        {"id": "t1", "text": "已翻译的有效证据。"},
        {"id": "t2", "text": "Still English."},
        {"id": "unknown", "text": "忽略未知编号。"},
    ], [])
    assert await client.translate_report_texts({"hash1": "First evidence.", "hash2": "Second evidence."}) == {"hash1": "已翻译的有效证据。"}


@pytest.mark.asyncio
async def test_invalid_json_shape_remains_a_model_error():
    client = client_for([{"id": "t1"}], [])
    with pytest.raises(ContestLensError) as error:
        await client.translate_report_texts({"a": "Original evidence."})
    assert error.value.code == "HY3_INVALID_RESPONSE"
