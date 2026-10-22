"""Custom pipeline: the orchestrator's half of Qwen-Image 2.1.

This process runs no model and imports no torch. One generation is four HTTP
calls to the inference services (they live in ``../Common/models``; the contract
is ``docs/models_inference_api.md``)::

    encode(prompt, images) --embedding--> prompt_embeds, image_pad_mask
    encode(images)         --vae-------> condition_latents        (only with references)
    denoise(...)           --dit-------> latents                  (SSE: one event per step)
    decode(latents)        --vae-------> PNG

What stays here, and only here:

* the resize. One geometry feeds both the encoder and the VAE — the encoder's
  token grid and the VAE's latent grid have the same geometry, so two different
  resizes would give the DiT an ``img_shapes`` that does not match the
  conditioning;
* the negation rule: ``true_cfg_scale <= 1`` skips the negative-prompt encode
  entirely, which is what the pipeline does when it warns that guidance is off;
* the seed, the sigma schedule and the loop belong to the DiT, which owns them;
* every tensor stays opaque. A blob coming back from one stage is forwarded to
  the next untouched.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from . import geometry, stages
from .config import settings

log = logging.getLogger(__name__)

# How long a /healthz snapshot of the stages is trusted before probing again.
_PROBE_TTL_S = 10.0

_STAGE_LABELS = {
    "embedding": "encode_prompt",
    "vae_encode": "vae.encode_image",
    "dit": "denoise",
    "vae_decode": "vae.decode",
}


@dataclass(slots=True)
class GenerationRequest:
    prompt: str
    negative_prompt: str
    width: int
    height: int
    steps: int
    true_cfg_scale: float
    seed: int
    images: list[Image.Image]


@dataclass(slots=True)
class GenerationResult:
    images: list[Image.Image]
    seed: int
    duration: float


class PipelineError(RuntimeError):
    """A stage failed, or the request cannot be expressed on the latent grid."""


@dataclass(slots=True)
class StageStatus:
    name: str
    url: str
    ready: bool = False
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"name": self.name, "url": self.url, "ready": self.ready, "error": self.error}
        if self.detail:
            body["detail"] = self.detail
        return body


class SplitPipeline:
    """The generation pipeline, spread over three services."""

    def __init__(self) -> None:
        self._stages: list[StageStatus] = []
        self._probed_at = 0.0

    # ------------------------------------------------ what routes.py asks for
    @property
    def accepts_images(self) -> bool:
        return True

    @property
    def is_loaded(self) -> bool:
        """True when the last probe found all three stages ready.

        ``/healthz`` reads this, so a stale snapshot is refreshed: the stages
        finish loading at their own pace and the answer has to follow them.
        """
        if time.monotonic() - self._probed_at > _PROBE_TTL_S:
            self.probe()
        return bool(self._stages) and all(stage.ready for stage in self._stages)

    @property
    def load_error(self) -> str | None:
        for stage in self._stages:
            if stage.error:
                return f"{stage.name}: {stage.error}"
        return None

    def ensure_loaded(self) -> bool:
        """Probe the stages once. Never raises: a stage may still be warming up."""
        self.probe()
        if not self.is_loaded:
            log.warning("stages not ready yet: %s", self.load_error or "warming up")
        return self.is_loaded

    def probe(self) -> list[StageStatus]:
        """``GET /healthz`` on every stage. Cheap enough for the health endpoint."""
        found: list[StageStatus] = []
        for name, url in (
            ("embedding", settings.embedding_url),
            ("dit", settings.dit_url),
            ("vae", settings.vae_url),
        ):
            status = StageStatus(name=name, url=url)
            try:
                payload = stages.health(url, settings.health_timeout)
                status.ready = bool(payload.get("ready"))
                status.error = payload.get("load_error")
                status.detail = {
                    key: payload[key]
                    for key in ("model_id", "device", "quantize", "gpu_layers", "busy", "tile_size")
                    if key in payload
                }
            except Exception as exc:
                status.error = f"{type(exc).__name__}: {exc}"
            found.append(status)
        self._stages = found
        self._probed_at = time.monotonic()
        return found

    # ---------------------------------------------------------------- generate
    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.monotonic()
        width, height = geometry.align_sides(request.width, request.height)
        if width < 64 or height < 64:
            raise PipelineError(f"requested size {request.width}x{request.height} collapses to {width}x{height}")

        conditions = self._prepare_conditions(request.images)
        sizes = [image.size for image, _ in conditions]
        pngs = [payload for _, payload in conditions]

        negative = request.negative_prompt.strip()
        do_true_cfg = request.true_cfg_scale > 1 and bool(negative)
        if request.true_cfg_scale > 1 and not negative:
            log.warning(
                "CFG %.2f без negative prompt: guidance выключена, один проход на шаг",
                request.true_cfg_scale,
            )

        log.info(
            "pipeline call: %dx%d steps=%d cfg=%.2f seed=%d refs=%d negative=%r prompt=%r",
            width, height, request.steps, request.true_cfg_scale, request.seed, len(pngs), negative, request.prompt,
        )

        prompt_blob = self._encode_prompt(request.prompt, pngs, sizes, role="cond")
        negative_blob = self._encode_prompt(negative, pngs, sizes, role="negative") if do_true_cfg else None
        condition_blob = self._encode_images(pngs, sizes) if pngs else None

        latents = self._denoise(
            width=width,
            height=height,
            steps=request.steps,
            seed=request.seed,
            true_cfg_scale=request.true_cfg_scale,
            sizes=sizes,
            prompt_blob=prompt_blob,
            negative_blob=negative_blob,
            condition_blob=condition_blob,
        )

        images = [self._decode(latents, width, height)]
        duration = time.monotonic() - started
        log.info("pipeline done: %d output(s) [%dx%d], %.1f s, refs=%d",
                 len(images), width, height, duration, len(pngs))
        return GenerationResult(images=images, seed=request.seed, duration=duration)

    # ------------------------------------------------------------------ stages
    def _prepare_conditions(self, images: list[Image.Image]) -> list[tuple[Image.Image, bytes]]:
        """One resize per reference, shared by the encoder and the VAE."""
        prepared: list[tuple[Image.Image, bytes]] = []
        for index, image in enumerate(images, start=1):
            rgba = image if image.mode == "RGBA" else image.convert("RGBA")
            width, height = geometry.condition_size(rgba.width, rgba.height, settings.output_resolution)
            resized = geometry.resize(rgba, width, height)
            buffer = io.BytesIO()
            resized.save(buffer, format="PNG")
            prepared.append((resized, buffer.getvalue()))
            log.info("condition image%d: %dx%d -> %dx%d, %d KB",
                     index, image.width, image.height, width, height, len(buffer.getvalue()) // 1024)
        return prepared

    def _encode_prompt(self, prompt: str, pngs: list[bytes], sizes: list[tuple[int, int]], *, role: str) -> bytes:
        meta = {
            "prompt": prompt,
            "role": role,
            "num_images_per_prompt": 1,
            "sizes": [list(size) for size in sizes],
            "output_resolution": settings.output_resolution,
        }
        started = time.monotonic()
        response = self._post(
            "embedding", settings.embedding_url, "/v1/encode", meta, None, pngs, settings.encoder_timeout
        )
        self._log_stage("embedding", started, f"role={role} refs={len(pngs)} {len(response.content) // 1024} KB")
        return response.content

    def _encode_images(self, pngs: list[bytes], sizes: list[tuple[int, int]]) -> bytes:
        meta = {"sizes": [list(size) for size in sizes]}
        started = time.monotonic()
        response = self._post("vae", settings.vae_url, "/v1/vae/encode", meta, None, pngs, settings.vae_timeout)
        self._log_stage("vae_encode", started, f"refs={len(pngs)} {len(response.content) // 1024} KB")
        return response.content

    def _denoise(
        self,
        *,
        width: int,
        height: int,
        steps: int,
        seed: int,
        true_cfg_scale: float,
        sizes: list[tuple[int, int]],
        prompt_blob: bytes,
        negative_blob: bytes | None,
        condition_blob: bytes | None,
    ) -> bytes:
        meta = {
            "width": width,
            "height": height,
            "steps": steps,
            "seed": seed,
            "true_cfg_scale": true_cfg_scale,
            "img_shapes": [[list(shape) for shape in sample] for sample in geometry.img_shapes(sizes, width, height)],
            "use_kv_cache": True,
        }
        blobs = [prompt_blob]
        if negative_blob is not None:
            blobs.append(negative_blob)
        if condition_blob is not None:
            blobs.append(condition_blob)

        started = time.monotonic()
        try:
            response = stages.post(
                f"{settings.dit_url}/v1/denoise",
                meta=meta,
                blobs=blobs,
                timeout=settings.dit_timeout,
                stream=True,
            )
        except Exception as exc:
            raise PipelineError(f"dit service unreachable at {settings.dit_url}: {exc}") from exc
        _check("dit", response)

        result: dict[str, Any] | None = None
        try:
            for event, payload in stages.events(response):
                if event == "start":
                    log.info(
                        "pipeline «denoise»: start, %s steps, %sx%s, tokens=%s, condition=%s, kv_cache=%s",
                        payload.get("steps"), payload.get("width"), payload.get("height"),
                        payload.get("latent_tokens"), payload.get("condition_tokens"), payload.get("kv_cache"),
                    )
                elif event == "step":
                    log.info(
                        "pipeline «denoise» %s/%s: timestep=%.4g, шаг %.2f с, с начала %.1f с, осталось ~%.0f с%s",
                        payload.get("step"), payload.get("total"), float(payload.get("timestep") or 0.0),
                        float(payload.get("step_s") or 0.0), float(payload.get("elapsed_s") or 0.0),
                        float(payload.get("eta_s") or 0.0),
                        f", VRAM peak {payload['vram_peak_gb']} GB" if payload.get("vram_peak_gb") else "",
                    )
                elif event == "done":
                    result = payload
                elif event == "error":
                    raise PipelineError(f"dit: {payload.get('error') or payload}")
        finally:
            response.close()

        if result is None:
            raise PipelineError("dit stream ended without a result (worker restart or timeout)")
        try:
            latents = stages.base64_blob(result["latents_b64"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineError(f"dit returned no usable latents: {exc}") from exc
        self._log_stage(
            "dit", started,
            f"{steps} steps, tokens={result.get('tokens')}, peak {result.get('peak_vram_gb')} GB, "
            f"{int(result.get('latents_bytes') or len(latents)) // 1024} KB latents",
        )
        return latents

    def _decode(self, latents: bytes, width: int, height: int) -> Image.Image:
        started = time.monotonic()
        response = self._post(
            "vae", settings.vae_url, "/v1/vae/decode", {"width": width, "height": height}, [latents], None,
            settings.vae_timeout,
        )
        try:
            image = Image.open(io.BytesIO(response.content))
            image.load()
        except Exception as exc:
            raise PipelineError(f"vae returned an undecodable image: {exc}") from exc
        self._log_stage("vae_decode", started, f"{image.width}x{image.height} {image.mode}")
        return image

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _post(
        stage: str,
        base_url: str,
        path: str,
        meta: dict[str, Any] | None,
        blobs: list[bytes] | None,
        images: list[bytes] | None,
        timeout: float,
    ):
        try:
            response = stages.post(f"{base_url}{path}", meta=meta, blobs=blobs, images=images, timeout=timeout)
        except Exception as exc:
            raise PipelineError(f"{stage} service unreachable at {base_url}: {exc}") from exc
        _check(stage, response)
        return response

    @staticmethod
    def _log_stage(stage: str, started: float, detail: str) -> None:
        log.info("pipeline «%s»: done in %.2f s, %s", _STAGE_LABELS.get(stage, stage), time.monotonic() - started, detail)


def _check(stage: str, response) -> None:
    """A stage refusal becomes a PipelineError, which the routes answer with 503."""
    try:
        stages.check(stage, response)
    except stages.StageError as exc:
        raise PipelineError(str(exc)) from exc


# The old name, kept so imports written against the monolith keep working.
QwenImageGenerator = SplitPipeline

generator = SplitPipeline()
