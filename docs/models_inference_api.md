# How this service uses its models

The models are not loaded here. Qwen-Image 2.1 is split into three inference
services that live in **`../Common/models`** and run as containers in
`models_network`. This service (the orchestrator) calls them over HTTP, in order,
once per generation. It imports no torch and allocates no VRAM.

```
prompt + photos ──► qwen-embed   /v1/encode        ──► prompt_embeds, image_pad_mask
photos          ──► qwen-vae     /v1/vae/encode    ──► condition_latents
(tensors above) ──► qwen-dit     /v1/denoise (SSE) ──► latents  (+ one event per step)
latents         ──► qwen-vae     /v1/vae/decode    ──► PNG
```

## Which model lives where

| Service | Container | Port | Weights (subfolder of `MODEL_ID`) | Loaded as |
|---|---|---|---|---|
| embedding | `qwen-embed` | 8011 | `text_encoder/` + `processor/` | `Qwen3VLForConditionalGeneration` (8B, fp16) + `Qwen3VLProcessor` |
| DiT | `qwen-dit` | 8013 | `transformer/` + `scheduler/` | `QwenImage21Transformer2DModel` (7B, int8) + `FlowMatchEulerDiscreteScheduler` |
| VAE | `qwen-vae` | 8012 | `vae/` | `AutoencoderKLQwenImage21` (fp16, 64-channel latent, 16× compression) |

`MODEL_ID` is a **local directory**, `/data/huggingface/Qwen-Image-2.1`, so no
service touches the network at start-up. Each stage reads only its own subfolder
(`from_pretrained(MODEL_ID, subfolder=...)`), and each stage also *mounts* only
its own part: `qwen-embed`, `qwen-vae` and `qwen-dit` get one volume each
(`qwen-weights-embed`, `qwen-weights-vae`, `qwen-weights-dit`), read-only,
hardlinked out of the master `qwen-hf-cache` volume. So the encoder container
cannot see the DiT's 14 GB or the VAE, and a part can be replaced or upgraded on
its own. All parts are safetensors; GGUF does not apply to these modules (see
`../Common/models/README.md`).

## Transport

`multipart/form-data`, three field names:

| field | content |
|---|---|
| `meta` | JSON, at most a few KB |
| `tensors` | repeated; each a `safetensors` blob |
| `images` | repeated; each a PNG |

Success is `application/octet-stream` (a safetensors blob) or `image/png`.
Failure is `{"error": "…"}` with:

| status | meaning | what this service does |
|---|---|---|
| 400 | the request is wrong (shape, missing tensor, bad PNG) | raises `PipelineError`, answers the browser 503 |
| 503 | the stage is not ready, or ran out of VRAM | same; the message is passed through verbatim |
| 500 | unexpected failure inside the stage | same |

The orchestrator forwards blobs **untouched**: it never opens one, which is why
it needs no torch and stays at ~120 MB RSS.

## Endpoints

### `POST /v1/encode` — embedding

`meta`: `{prompt, role: "cond"|"negative", num_images_per_prompt: 1, sizes: [[w,h]], output_resolution: 1024}`
`images`: the condition images, **already resized** (see the geometry rule below).

Returns, as tensors in one blob:

| name | shape | dtype |
|---|---|---|
| `prompt_embeds` | `(1, S, 4096)` | float16 |
| `prompt_embeds_mask` | `(1, S)` | int64 — absent when nothing is padded |
| `image_pad_mask` | `(1, S)` | bool |

With `role="negative"` the three names are prefixed `negative_`. `S` covers the
text template plus one vision token per 32×32 px of the reference (a 1024²
reference is ~1024 image tokens).

The hidden states are the last decoder layer's output **before** the final
RMSNorm — the state the DiT was trained on. `transformers` 5 returns the
normalized state by default, so the stage installs a hook that neutralises the
norm; do not "simplify" that away.

### `POST /v1/vae/encode` — VAE, condition images

`meta`: `{sizes: [[w,h], ...]}` — checked against the actual image.
`images`: the same resized PNGs.

Returns `condition_latents` `(1, sum(tokens), 64)` float16: per image
`(latent - latents_mean) / latents_std`, `argmax` sampling, packed as a spatial
flatten and concatenated on the token axis. One 1024² reference is 4096 tokens
(≈0.5 MB).

### `POST /v1/denoise` — DiT, Server-Sent Events

`meta`:

| key | meaning |
|---|---|
| `width`, `height` | output size, multiples of 32, ≥ 64 |
| `steps` | 1…200 |
| `seed` | seeds the initial noise (CPU generator, then moved to the card) |
| `true_cfg_scale` | `> 1` **and** negative tensors present ⇒ two forwards per step |
| `img_shapes` | `[[[1, rows, cols], …]]` per sample: condition images in order, target last |
| `use_kv_cache` | default `true` |

`tensors`: every blob from the two encode hops, merged by tensor name
(`prompt_embeds`, `image_pad_mask`, `condition_latents`, and the `negative_*`
set when guidance is on).

The response is a stream, which is what the browser's progress comes from:

```
event: start   {"steps":40,"width":1024,"height":1024,"latent_tokens":4096,
                "condition_tokens":0,"seq_len":19,"mu":0.83,"kv_cache":true,"seed":1234}
event: step    {"step":1,"total":40,"timestep":1000.0,"step_s":0.92,"elapsed_s":0.92,
                "eta_s":35.9,"vram_peak_gb":9.38}          ← one per denoise step
event: done    {"latents_b64":"<base64 safetensors>","latents_bytes":2097152,
                "elapsed_s":37.4,"peak_vram_gb":9.4,"tokens":4096}
event: error   {"error":"denoise out of VRAM: …"}
```

`latents` is packed `(1, tokens, 64)` — exactly what the decode hop takes. Every
`step` event is re-logged by this service, which is why the log panel in the UI
shows per-step progress without polling the DiT.

### `POST /v1/vae/decode` — VAE, latents to pixels

`meta`: `{width, height}`; `tensors`: the blob from `done`.
Returns `image/png` (RGBA). Full-frame decode first; tiling is only the fallback
when the card cannot hold the frame.

### Health

`GET /healthz` on each stage returns `{service, ready, load_error, …}`. This
service's own `/healthz` probes all three and lists them under `stages`; it
answers 200 even while a stage is warming up (`ready: false`).

An optional `POST /v1/admin/...` does not exist: stages have no mutable state.

## What the orchestrator must guarantee

These are the invariants the stages rely on — break one and the result is a
wrong image, not an error:

1. **One resize per reference.** The encoder's token grid and the VAE's latent
   grid must describe the same pixels. This service resizes each condition image
   once (`app/geometry.py`, the pipeline's own `calculate_dimensions`: area
   `OUTPUT_RESOLUTION²`, sides rounded to 32, LANCZOS) and sends the same PNG
   bytes to both stages.
2. **`img_shapes` matches that resize** — `(1, height/16, width/16)` per
   condition image, in order, then the target. The geometry copy here is pinned
   by `tests/test_geometry_contract.py`; the copy in Common/models is pinned by
   its own test with the same numbers.
3. **The seed and the schedule belong to the DiT.** Do not pre-draw noise here;
   the same seed must give the same image, and the stage owns that.
4. **Skip the negative encode when guidance is off.** `true_cfg_scale <= 1` (the
   2.1 default) plus no negative prompt means one forward per step; encoding a
   negative prompt anyway only costs time.
5. **Forward, do not rewrite.** Blobs are opaque; the tensor names inside them
   are the contract.

## Capacity, measured on the V100 32 GB

| Stage | VRAM | RAM |
|---|---|---|
| `qwen-embed` (24 of 36 layers + vision on the card) | 10.9 GB | 4.3 GB |
| `qwen-dit` (int8) | 6.6 GB | ~2 GB |
| `qwen-vae` | ~1.3 GB | ~1 GB |

The DiT's prefix KV cache is what limits references: ~2 GB per 1024² reference,
on top of ~19 GB resident. One reference is comfortable, two are tight, ten (the
UI allows it) do not fit — a request that exceeds it gets a clean 503 from the
DiT instead of a corrupted image. `use_kv_cache=false` removes the cache at
roughly twice the time per step.

Timings (warm): 512², 4 steps, text only — encoder 1.6 s, 4 × 0.3 s, decode
0.25 s. With one 1024² reference the encode hop dominates (~42 s), because 12 of
the encoder's 36 layers run in CPU RAM; `TEXT_ENCODER_GPU_LAYERS` in the
embedding stage trades VRAM for that time.

## Why not llama.cpp

llama.cpp has no Qwen-Image: `llama-server` runs GGUF LLMs and vision encoders,
and this pipeline needs a block-causal DiT with a prefix KV cache, a 64-channel
VAE with `argmax` sampling, and Qwen3-VL's pre-RMSNorm hidden states — none of
which llama.cpp implements. The three stages reuse the existing
`qwen-image-service` image (torch+cu126/sm_70, diffusers pinned) as their base.

## Where to change what

| Change | File |
|---|---|
| a new field on a hop | `app/generator.py` + the stage's `app.py` in Common/models |
| endpoint URL, timeout | `.env` (`EMBEDDING_URL`, `VAE_URL`, `DIT_URL`, `*_TIMEOUT`) |
| geometry / `img_shapes` | `app/geometry.py` here, `shared/qwensplit/geometry.py` there — both tests |
| progress events | `app/stages.py` (`events`) + `services/dit/app.py` |
| the stage's own maths | `services/*/` in Common/models; `docs/api.md` there |
