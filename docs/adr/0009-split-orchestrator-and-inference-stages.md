# ADR 0009. Split the pipeline into an orchestrator and three inference services

## Status

Accepted, implemented 2026-10-22.

## Context

The service loaded one `QwenImage21Pipeline` and called `pipe(...)` (ADR 0003).
One generation is three phases — text encoder, 40-step DiT loop, VAE decode —
and fp16 weights for all three are ~33 GB on a 32 GB V100, so the process
time-shared them: the encoder tail in RAM, the DiT in int8, the DiT parked in RAM
while the VAE decoded (`app/offload.py`, `docs/qwen-image-2.1-split-scaling.md`).

That works, and two things pushed against it:

* **The phases fight for one address space.** The VAE decode needs the card free,
  so the DiT leaves and comes back; the encoder's VRAM budget is picked against
  the DiT's, not on its own merit. Every tuning knob is a compromise between
  three unrelated modules.
* **There is no seam to scale on.** The encoder is idle for the whole denoise
  loop, the VAE is idle between hops, and the DiT is the only part that actually
  needs to run concurrently — but all three are one process, one worker, one
  `_gpu_lock` (ADR 0008).

`docs/qwen-image-2.1-split-scaling.md` records why the *official* API cannot be
split: `__call__` will not accept precomputed condition latents or encoder
outputs for the edit path, and it owns the loop. What it also records is that a
custom worker could split them — that is this ADR.

## Decision

Run the three phases as three HTTP services and put the pipeline logic in this
process:

| Service | Repository | Owns |
|---|---|---|
| `qwen-embed` | `../Common/models/services/embedding` | `Qwen3VLForConditionalGeneration` + processor: prompt and condition images → `prompt_embeds`, `prompt_embeds_mask`, `image_pad_mask` (pre-final-RMSNorm) |
| `qwen-dit` | `../Common/models/services/dit` | the loop: seed, sigma schedule, prefix KV cache, `scheduler.step`, SSE progress, final latents |
| `qwen-vae` | `../Common/models/services/vae` | `AutoencoderKLQwenImage21`: `/v1/vae/encode` (argmax + normalization + packing) and `/v1/vae/decode` |
| orchestrator | here | resize (one, shared), `img_shapes`, request validation, the four hops, UI/API, WebStorage — no model |

The DiT loop is a literal port of `pipeline_qwenimage21.py` at the pinned
diffusers commit (`bdc2bea`), not a call into the pipeline: the loop has to be
fed tensors the public `__call__` will not accept.

Transport: `multipart/form-data` with a `meta` JSON field, repeated `tensors`
fields (safetensors blobs) and repeated `images` (PNG). The orchestrator forwards
blobs without opening them, so it has no torch dependency. SSE carries the DiT's
per-step progress; the last `done` event carries the latents.

Deployment: all four containers on `models_network`, ports 8011/8012/8013 and
the orchestrator's 8000, health probes on 127.0.0.1. The orchestrator keeps the
DNS alias `qwen-image` for the gateway; its container name is `qwen-image-split`
because switchd owns the old name.

## Consequences

* **Output is unchanged.** `scripts/parity.sh` in Common/models compares the split
  with diffusers' own `pipe(...)` (same components, int8 DiT, same encoder
  placement): latents `max_abs 0.0`, PNG `mean_abs 0.0`, for text-only and for a
  condition image, through the stage API and through `/api/generate`. The encoder,
  compared against a fully-GPU encoder, differs by 0.2 % relative — fp16
  placement, not logic; identical placement is bit-exact.
* **Each stage has its own budget.** Encoder 10.9 GB VRAM + 4.3 GB RAM, DiT 6.6 GB,
  VAE 1.3 GB: ~19 GB resident instead of one 20–28 GB monolith, and the VAE no
  longer needs the DiT evicted before a decode.
* **Scaling seams are real.** Encoder and VAE are stateless and can be replicated
  or moved; the DiT scales per concurrent job (its prefix KV cache pins a job to
  one replica for all steps). On this single card they still queue, but each
  `DIT_URL` can point at another host.
* **The orchestrator lost 6 GB of runtime weight.** It no longer imports torch; the
  image is still the ML base because the stages are built FROM it, but the
  process holds no CUDA context and no weights.
* **More moving parts.** Four containers, four logs, cross-service timeouts, and
  a 503 from a stage has to be explained to the user (`PipelineError` → 503 with
  the stage's message). `compose.yaml` no longer starts the whole system: this
  repo starts the app, Common/models starts the models.
* **Geometry is duplicated on purpose.** The resize is the one piece of maths both
  repositories need; it is copied (`app/geometry.py` here,
  `shared/qwensplit/geometry.py` there) with the same contract test in both. The
  alternative — a shared package installed in two images — couples two projects
  that deploy separately, and the parity harness catches drift end to end.
* **A request can now fail halfway.** Encoder done, DiT out of VRAM: the browser
  gets 503 and no partial file. That is better than a corrupted frame, and it is
  what the old process did too (it raised `PipelineError`).
* **KV cache is still the reference limit.** ~2 GB per 1024² reference on top of
  ~19 GB resident: one comfortable, two tight, ten impossible. Splitting did not
  create VRAM.
