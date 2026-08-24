import json
from uuid import uuid4

import httpx
import pytest

from fire_inspect.ai.config import CapabilitySettings
from fire_inspect.ai.http_provider import GenericHTTPProvider
from fire_inspect.ai.providers import ProviderFailure
from fire_inspect.ai.schemas import SemanticRole, TranscriptOutput, TranscriptSegment


def settings(secret: str = "SECRET-CANARY") -> CapabilitySettings:
    return CapabilitySettings(
        capability="embedding",
        provider="http",
        endpoint="http://local-ai.invalid/capability",
        model="replaceable-model",
        api_key=secret,
        timeout_seconds=1,
    )


def test_generic_http_embedding_adapter_validates_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer SECRET-CANARY"
        body = json.loads(request.content)
        assert body["untrusted_data"]["texts"] == ["条款"]
        return httpx.Response(
            200,
            json={"embeddings": [{"values": [0.1, 0.2], "model_fingerprint": "gateway:model:2"}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    output = GenericHTTPProvider(settings(), client=client).embed(["条款"])
    assert output[0].values == (0.1, 0.2)


def test_generic_http_document_draft_adapter_validates_schema_and_preserves_references() -> None:
    fact_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["capability"] == "document_drafting"
        assert body["document_type"] == "record"
        assert body["untrusted_data"]["facts"] == [
            {"id": str(fact_id), "content": "已确认事实", "revision": 2}
        ]
        return httpx.Response(
            200,
            json={
                "document_type": "record",
                "fields": {"inspection_content": "已确认事实"},
                "referenced_fact_ids": [str(fact_id)],
                "photo_items": [],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    output = GenericHTTPProvider(settings(), client=client).draft_document(
        "record",
        {"facts": [{"id": str(fact_id), "content": "已确认事实", "revision": 2}]},
    )
    assert output.document_type == "record"
    assert output.fields == {"inspection_content": "已确认事实"}
    assert output.referenced_fact_ids == (fact_id,)


def test_generic_http_document_draft_adapter_rejects_malformed_references() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "document_type": "record",
                    "fields": {"inspection_content": "候选"},
                    "referenced_fact_ids": ["not-a-uuid"],
                },
            )
        )
    )
    with pytest.raises(ProviderFailure, match="格式无效"):
        GenericHTTPProvider(settings(), client=client).draft_document("record", {"facts": []})


def test_provider_error_does_not_leak_secret_or_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="SECRET RESPONSE WITH TRANSCRIPT")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderFailure) as error:
        GenericHTTPProvider(settings(), client=client).embed(["敏感文本"])
    message = str(error.value)
    assert "SECRET-CANARY" not in message
    assert "SECRET RESPONSE" not in message
    assert "敏感文本" not in message


def test_malformed_provider_response_is_not_accepted() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"wrong": []}))
    )
    with pytest.raises(ProviderFailure, match="格式无效"):
        GenericHTTPProvider(settings(), client=client).embed(["条款"])


@pytest.mark.parametrize(
    ("status_code", "message"),
    ((401, "鉴权失败"), (403, "鉴权失败"), (429, "限流"), (503, "请求失败")),
)
def test_http_status_failures_have_capability_specific_safe_messages(
    status_code: int, message: str
) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, text="SECRET RESPONSE", request=request)
        )
    )
    with pytest.raises(ProviderFailure, match=message) as error:
        GenericHTTPProvider(settings(), client=client).embed(["敏感条款"])
    assert "SECRET" not in str(error.value)
    assert "敏感条款" not in str(error.value)


def test_timeout_is_safe_and_retryable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("SECRET TIMEOUT DETAIL", request=request)

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    with pytest.raises(ProviderFailure, match="超时") as error:
        GenericHTTPProvider(settings(), client=client).embed(["敏感条款"])
    assert "SECRET" not in str(error.value)


def test_all_http_capabilities_reject_malformed_or_partial_payloads(tmp_path) -> None:
    material_id = uuid4()
    media = tmp_path / "media.bin"
    media.write_bytes(b"media")
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    transcript = TranscriptOutput(
        full_text="问：现场如何？",
        segments=(
            TranscriptSegment(
                material_id,
                0,
                1_000,
                "问：现场如何？",
                SemanticRole.QUESTION,
            ),
        ),
    )

    def provider(payload: dict[str, object]) -> GenericHTTPProvider:
        client = httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        )
        return GenericHTTPProvider(settings(), client=client)

    with pytest.raises(ProviderFailure, match="格式无效"):
        provider({"segments": [{"start_ms": 0, "end_ms": 1_000}]}).transcribe(
            material_id, media, 1_000
        )
    with pytest.raises(ProviderFailure, match="格式无效"):
        provider({"descriptions": [{"timestamp_ms": 0}]}).describe(material_id, ((0, frame),))
    with pytest.raises(ProviderFailure, match="格式无效"):
        provider(
            {
                "facts": [
                    {
                        "content": "自动处罚决定",
                        "evidence": [],
                        "is_final_enforcement_conclusion": True,
                    }
                ]
            }
        ).extract_facts(transcript, ())
    with pytest.raises(ProviderFailure, match="格式无效"):
        provider({"text": "", "navigation": [{"kind": "unknown"}]}).answer(
            "问题", {"inspection_id": str(uuid4())}
        )
