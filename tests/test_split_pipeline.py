"""Sequencing of the three inference services, with the HTTP layer faked.

No network, no GPU: this checks the geometry, the order and the payloads the
orchestrator is responsible for. The contract those payloads follow is
``docs/models_inference_api.md``.
"""

from __future__ import annotations

import base64
import io
from unittest import mock

import pytest
from PIL import Image

from app import stages
from app.generator import GenerationRequest, PipelineError, QwenImageGenerator, SplitPipeline


def png(width: int = 64, height: int = 64, color=(120, 30, 90)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


LATENTS_B64 = base64.b64encode(b"LATENTS").decode()


class FakeResponse:
    def __init__(self, content: bytes = b"", status: int = 200, sse: list[str] | None = None) -> None:
        self.content = content
        self.status_code = status
        self._sse = sse or []
        self.text = "" if status < 400 else '{"error": "stage said no"}'

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def iter_lines(self, decode_unicode: bool = False):
        yield from self._sse

    def close(self) -> None:
        pass


class FakeStages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url, *, meta=None, blobs=None, images=None, timeout, stream=False):
        self.calls.append({"url": url, "meta": meta, "blobs": list(blobs or []),
                           "images": list(images or []), "stream": stream})
        if url.endswith("/v1/encode"):
            return FakeResponse(b"EMBEDDINGS")
        if url.endswith("/v1/vae/encode"):
            return FakeResponse(b"CONDITION")
        if url.endswith("/v1/denoise"):
            return FakeResponse(sse=[
                "event: start",
                'data: {"steps": 4, "width": 512, "height": 512, "latent_tokens": 256}',
                "",
                "event: step",
                'data: {"step": 1, "total": 4, "timestep": 1000, "step_s": 0.5, "elapsed_s": 0.5, "eta_s": 1.5}',
                "",
                "event: done",
                f'data: {{"latents_b64": "{LATENTS_B64}", "tokens": 256, "peak_vram_gb": 9.0}}',
                "",
            ])
        if url.endswith("/v1/vae/decode"):
            return FakeResponse(png(512, 512, (10, 200, 10)))
        raise AssertionError(f"unexpected url {url}")

    def endpoints(self) -> list[str]:
        return [call["url"].split("/v1/")[-1] for call in self.calls]


def make_request(**overrides) -> GenerationRequest:
    base = dict(prompt="a lighthouse", negative_prompt="", width=512, height=512,
                steps=4, true_cfg_scale=1.0, seed=1234, images=[])
    base.update(overrides)
    return GenerationRequest(**base)


@pytest.fixture
def faked():
    fake = FakeStages()
    with mock.patch.object(stages, "post", side_effect=fake.post):
        yield fake


def test_text_only_uses_three_hops(faked):
    result = SplitPipeline().generate(make_request())
    assert len(result.images) == 1
    assert result.seed == 1234
    assert faked.endpoints() == ["encode", "denoise", "vae/decode"]


def test_denoise_payload(faked):
    SplitPipeline().generate(make_request(steps=7, seed=99))
    denoise = next(call for call in faked.calls if call["url"].endswith("/v1/denoise"))
    meta = denoise["meta"]
    assert (meta["width"], meta["height"]) == (512, 512)
    assert meta["steps"] == 7
    assert meta["seed"] == 99
    assert meta["img_shapes"] == [[[1, 32, 32]]]
    assert denoise["blobs"] == [b"EMBEDDINGS"]
    assert denoise["stream"] is True


def test_latents_reach_the_decoder_untouched(faked):
    SplitPipeline().generate(make_request())
    decode = next(call for call in faked.calls if call["url"].endswith("/v1/vae/decode"))
    assert decode["blobs"] == [b"LATENTS"]
    assert decode["meta"] == {"width": 512, "height": 512}


def test_condition_image_is_resized_once_and_shared(faked):
    photo = Image.new("RGB", (480, 640), (200, 100, 50))
    SplitPipeline().generate(make_request(images=[photo]))
    encode_call = faked.calls[0]
    vae_call = next(call for call in faked.calls if call["url"].endswith("/v1/vae/encode"))
    # 480x640 -> the 3:4 box of a 1024² area, by the pipeline's own geometry
    assert Image.open(io.BytesIO(encode_call["images"][0])).size == (896, 1184)
    assert encode_call["images"][0] is vae_call["images"][0]
    assert vae_call["meta"]["sizes"] == [[896, 1184]]


def test_condition_hop_adds_a_call_and_carries_shapes(faked):
    SplitPipeline().generate(make_request(images=[Image.new("RGB", (480, 640))]))
    assert faked.endpoints() == ["encode", "vae/encode", "denoise", "vae/decode"]
    denoise = next(call for call in faked.calls if call["url"].endswith("/v1/denoise"))
    assert denoise["meta"]["img_shapes"] == [[[1, 74, 56], [1, 32, 32]]]
    assert denoise["blobs"] == [b"EMBEDDINGS", b"CONDITION"]


def test_cfg_one_skips_the_negative_encode(faked):
    SplitPipeline().generate(make_request(true_cfg_scale=1.5, negative_prompt=""))
    roles = [call["meta"].get("role") for call in faked.calls if call["url"].endswith("/v1/encode")]
    assert roles == ["cond"]


def test_cfg_above_one_encodes_the_negative_prompt(faked):
    SplitPipeline().generate(make_request(true_cfg_scale=2.0, negative_prompt="blurry"))
    encodes = [call for call in faked.calls if call["url"].endswith("/v1/encode")]
    assert [call["meta"]["role"] for call in encodes] == ["cond", "negative"]
    assert encodes[1]["meta"]["prompt"] == "blurry"
    denoise = next(call for call in faked.calls if call["url"].endswith("/v1/denoise"))
    assert denoise["meta"]["true_cfg_scale"] == 2.0
    # both encodes, no condition blob: the request carries no reference image
    assert denoise["blobs"] == [b"EMBEDDINGS", b"EMBEDDINGS"]


@pytest.mark.parametrize("endpoint", ["/v1/encode", "/v1/vae/encode", "/v1/denoise", "/v1/vae/decode"])
def test_stage_refusal_becomes_a_pipeline_error(endpoint):
    def answer(url, **kwargs):
        if url.endswith(endpoint):
            return FakeResponse(b"", status=503)
        return FakeStages().post(url, **kwargs)

    with mock.patch.object(stages, "post", side_effect=answer):
        with pytest.raises(PipelineError) as caught:
            SplitPipeline().generate(make_request(images=[Image.new("RGB", (64, 64))]))
    assert "503" in str(caught.value) or "stage said no" in str(caught.value)


def test_stream_without_result_is_an_error():
    truncated = FakeResponse(sse=["event: start", 'data: {"steps": 4}', ""])
    with mock.patch.object(stages, "post", side_effect=[FakeResponse(b"EMBEDDINGS"), truncated]):
        with pytest.raises(PipelineError):
            SplitPipeline().generate(make_request())


def test_unreachable_stage_names_the_service():
    with mock.patch.object(stages, "post", side_effect=ConnectionError("no route to host")):
        with pytest.raises(PipelineError) as caught:
            SplitPipeline().generate(make_request())
    assert "embedding service unreachable" in str(caught.value)


def test_too_small_is_rejected_before_any_call(faked):
    with pytest.raises(PipelineError):
        SplitPipeline().generate(make_request(width=32, height=32))
    assert faked.calls == []


def test_sides_are_floored_to_32(faked):
    SplitPipeline().generate(make_request(width=1000, height=700))
    denoise = next(call for call in faked.calls if call["url"].endswith("/v1/denoise"))
    assert (denoise["meta"]["width"], denoise["meta"]["height"]) == (992, 672)


def test_probe_reports_every_stage(monkeypatch):
    answers = {"http://qwen-embed:8011": {"ready": True, "load_error": None, "gpu_layers": 24},
               "http://qwen-dit:8013": {"ready": False, "load_error": "warming up"},
               "http://qwen-vae:8012": {"ready": True, "load_error": None}}

    monkeypatch.setattr(stages, "health", lambda url, timeout: answers[url])
    pipeline = SplitPipeline()
    statuses = pipeline.probe()
    assert [stage.name for stage in statuses] == ["embedding", "dit", "vae"]
    assert pipeline.is_loaded is False
    assert "dit" in (pipeline.load_error or "")
    assert statuses[0].detail == {"gpu_layers": 24}


def test_qwen_image_generator_alias_still_points_at_the_split():
    assert QwenImageGenerator is SplitPipeline
