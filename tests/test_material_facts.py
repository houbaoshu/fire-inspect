import io
import wave

from PIL import Image
from sqlalchemy import select

from backend.app.ai import FactCandidate, MockAI, OpenAICompatibleAI
from backend.app.database import SessionLocal
from backend.app.models import (
    Evidence,
    EvidenceType,
    Fact,
    FactEvidenceLink,
    Inspection,
    Material,
    MaterialKind,
    User,
)
from backend.app.processing import _facts, _vision
from backend.app.storage import get_storage


def test_qwen3_asr_uses_data_uri_chat_payload(monkeypatch):
    audio_buffer = io.BytesIO()
    with wave.open(audio_buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 16000)

    captured: dict = {}

    def fake_post(path: str, payload: dict) -> dict:
        captured.update({"path": path, "payload": payload})
        return {"choices": [{"message": {"content": "测试转写正文"}}]}

    provider = object.__new__(OpenAICompatibleAI)
    provider.transcription_model = "qwen3-asr-flash"
    monkeypatch.setattr(provider, "_post", fake_post)

    results = provider.transcribe(audio_buffer.getvalue(), "audio/wav")

    assert captured["path"] == "/chat/completions"
    request = captured["payload"]
    assert request["model"] == "qwen3-asr-flash"
    assert request["stream"] is False
    assert request["asr_options"] == {"enable_itn": False}
    audio_input = request["messages"][0]["content"][0]
    assert audio_input["type"] == "input_audio"
    assert audio_input["input_audio"]["data"].startswith("data:audio/wav;base64,")
    assert results[0].text == "测试转写正文"
    assert results[0].start_ms == 0
    assert results[0].end_ms == 1000
    assert results[0].speaker_label == "说话人 1"


def test_text_material_creates_candidate_with_exact_evidence_and_requires_confirmation(
    client, inspector_headers
):
    inspection = client.post(
        "/api/v1/inspections", json={"title": "测试检查"}, headers=inspector_headers
    ).json()
    data = "消防控制室无人值班。现场未发现值班人员。".encode()
    upload = client.post(
        f"/api/v1/inspections/{inspection['id']}/upload-sessions",
        json={"filename": "现场说明.txt", "content_type": "text/plain", "size_bytes": len(data)},
        headers=inspector_headers,
    ).json()
    part = client.put(
        f"/api/v1/upload-sessions/{upload['id']}/parts/1",
        content=data,
        headers=inspector_headers,
    )
    assert part.status_code == 200
    completed = client.post(
        f"/api/v1/upload-sessions/{upload['id']}/complete",
        json={"parts": [part.json()]},
        headers=inspector_headers,
    )
    assert completed.status_code == 200, completed.text
    assert all(step["status"] == "succeeded" for step in completed.json()["steps"])

    facts = client.get(f"/api/v1/inspections/{inspection['id']}/facts").json()
    assert facts
    assert facts[0]["status"] == "candidate"
    assert facts[0]["needs_confirmation"] is True
    assert facts[0]["evidence"][0]["material_id"] == completed.json()["id"]
    assert facts[0]["evidence"][0]["start_ms"] == 0

    transcript = client.get(f"/api/v1/materials/{completed.json()['id']}/transcript").json()
    edited_transcript = client.patch(
        f"/api/v1/transcript/{transcript[0]['id']}",
        json={
            "speaker_identity": "现场负责人",
            "identity_confirmed": True,
            "text": "人工核对后的原话",
            "row_version": transcript[0]["row_version"],
        },
        headers=inspector_headers,
    )
    assert edited_transcript.status_code == 200
    retried = client.post(
        f"/api/v1/materials/{completed.json()['id']}/steps/transcription/retry",
        headers=inspector_headers,
    )
    assert retried.status_code == 202
    preserved = client.get(f"/api/v1/materials/{completed.json()['id']}/transcript").json()[0]
    assert preserved["text"] == "人工核对后的原话"
    assert preserved["speaker_identity"] == "现场负责人"

    confirmed = client.post(f"/api/v1/facts/{facts[0]['id']}/confirm", headers=inspector_headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    assistant = client.post(
        "/api/v1/assistant/messages",
        json={
            "message": "这条事实在哪里能证明？",
            "context": {
                "page_type": "facts",
                "inspection_id": inspection["id"],
                "fact_id": facts[0]["id"],
            },
        },
        headers=inspector_headers,
    )
    assert assistant.status_code == 200
    seek = next(action for action in assistant.json()["actions"] if action["type"] == "seek_media")
    assert seek["params"]["material_id"] == completed.json()["id"]
    assert seek["params"]["start_ms"] == 0


def test_video_keyframe_visual_result_creates_candidate_fact_with_image_evidence(monkeypatch):
    class VisualAI(MockAI):
        def analyze(self, images: list[bytes]) -> list[dict]:
            return [
                {"description": "疏散通道内明确可见堆放的纸箱", "confidence": 0.92} for _ in images
            ]

        def extract_facts(self, transcript: list[str]) -> list[FactCandidate]:
            assert "画面客观描述：疏散通道内明确可见堆放的纸箱" in transcript
            return [
                FactCandidate(
                    title="疏散通道内堆放纸箱",
                    detail=None,
                    confidence=0.92,
                    evidence_indexes=[0],
                )
            ]

    visual_ai = VisualAI()
    monkeypatch.setattr("backend.app.processing.get_ai", lambda: visual_ai)
    image = Image.new("RGB", (48, 32), "white")
    image_buffer = io.BytesIO()
    image.save(image_buffer, format="JPEG")
    frame_data = image_buffer.getvalue()

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "inspector"))
        inspection = Inspection(title="关键帧事实测试", owner_id=user.id)
        db.add(inspection)
        db.flush()
        material = Material(
            inspection_id=inspection.id,
            filename="现场视频.mp4",
            content_type="video/mp4",
            kind=MaterialKind.VIDEO,
            size_bytes=1,
            sha256="0" * 64,
            object_key=f"inspections/{inspection.id}/original/video.mp4",
            created_by=user.id,
        )
        db.add(material)
        db.flush()
        frame_key = f"inspections/{inspection.id}/derived/{material.id}/frames/0000000000.jpg"
        get_storage().put_bytes(frame_key, frame_data, "image/jpeg")
        frame = Evidence(
            inspection_id=inspection.id,
            type=EvidenceType.IMAGE,
            material_id=material.id,
            start_ms=0,
            end_ms=0,
            object_key=frame_key,
            label="现场视频.mp4 0.0s 关键画面",
            locator={"timestamp_ms": 0},
        )
        db.add(frame)
        db.commit()

        vision_result = _vision(db, material, b"")
        fact_result = _facts(db, material)
        db.commit()

        assert vision_result["analyzed_count"] == 1
        assert fact_result["candidate_count"] == 1
        db.refresh(frame)
        assert frame.locator["description"] == "疏散通道内明确可见堆放的纸箱"
        fact = db.scalar(select(Fact).where(Fact.inspection_id == inspection.id))
        link = db.scalar(select(FactEvidenceLink).where(FactEvidenceLink.fact_id == fact.id))
        assert link.evidence_id == frame.id
