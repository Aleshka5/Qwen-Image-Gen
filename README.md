# Qwen-Image Service

The **orchestrator** for [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1)
on a **Tesla V100 32 GB**: up to 10 reference photos, a choice of resolution, an
English prompt — the same UI, the same JSON API, the same WebStorage archive as
before.

The models are not here. They run in three inference services in
[`../Common/models`](../Common/models) and this process only sequences them:

```
browser ──► Auth Gateway ──► qwen-image-split (this repo, no GPU, no torch)
                                  │  /v1/encode       → qwen-embed :8011  Qwen3-VL 8B
                                  │  /v1/vae/encode   → qwen-vae   :8012  VAE encode
                                  │  /v1/denoise SSE  → qwen-dit   :8013  DiT 7B int8
                                  └  /v1/vae/decode   → qwen-vae   :8012  VAE decode
```

The contract with those services — endpoints, tensors, shapes, limits — is
[`docs/models_inference_api.md`](docs/models_inference_api.md). The decision
record is [ADR 0009](docs/adr/0009-split-orchestrator-and-inference-stages.md).

What this process still owns: the HTTP/UI layer, the one resize that both the
encoder and the VAE must agree on (`app/geometry.py`), the seed and step
payloads, the `img_shapes` sent to the DiT, and the WebStorage POST after a run.
It imports no torch, allocates no VRAM, and stays at ~120 MB RSS.

---

## Network and access

Browsers open the service at **https://image.filenkov.store**. That is the Auth Gateway, not this process.
The container is reachable only on `models_network`: the gateway proxies to the upstream `http://qwen-image:8000`.

The gateway decides who is allowed: the `image` role (`FAMILY` or `ADMIN` in User-Service).
The gateway strips incoming client `X-Auth-*` headers. This service has no login, sessions, role check, or JWT check.
The exception is `POST /api/generate` and `POST /api/mask-fill`: `X-Auth-User-Id` and `X-Auth-Email` are read only to sign the save into WebStorage.
`GET` routes, including `GET /mask-fill`, do not use those headers. A request that arrived here is already allowed.
The trust boundary is the network itself. WebStorage is not on `models_network`: any other container there can reach this service, so keep that network to the gateway, the consumers and the three inference stages.

Two names to keep straight:

| Name | What it is |
|---|---|
| `qwen-image` | **network alias** on `models_network` — the DNS name the gateway resolves, unchanged |
| `qwen-image-split` | the container name in `podman ps` |

The container is not named `qwen-image` because the switch-service catalogue still
owns that name for the retired monolith, and its reconciler stops any container
wearing it while the `image` service is `off`. The alias keeps every existing
consumer working. See `../Common/models/docs/deploy.md`.

To call WebStorage after a successful generation or mask fill, this container also joins the Auth network.
`AUTH_DOCKER_NETWORK` is the Auth stack network (`deploy_auth_network` when Auth is started from `Auth-Service/deploy`).
This repository does not create it: the Auth stack must already be up.
On that network the container calls the DNS name `app`, which is what WebStorage is called there.
Do not rename this container to `app`.

After a successful generation or mask fill the process `POST`s to `WEBSTORAGE_URL` (default `http://app:8000`) at `/api/generated`.
It forwards `X-Auth-User-Id` and, if that header was on the incoming request, `X-Auth-Email`.
`Cookie`, `Authorization`, and `X-Auth-Role` are not sent. This service does not verify a JWT.

`GET /healthz` is anonymous in the application. The gateway serves that path without a role, for operator probes. Everything else requires the `image` role.

> **Port 8000 is not published.** The only published port is `127.0.0.1:18000`,
> for health probes: a rootless podman container is not reachable from the host by
> IP and container names do not resolve on the host, so the probe has to go
> through loopback. It is bound to 127.0.0.1, not 0.0.0.0 — never widen it, and
> never publish 8000 itself: that would put the UI on the host next to the gateway
> and bypass it.

---

## What each piece holds now

The old monolith loaded one `QwenImage21Pipeline` and time-shared 33 GB of fp16
weights on one card (ADR 0003, ADR 0008). Now each stage holds its own module:

| Stage | Weights | VRAM | Where the code is |
|---|---|---|---|
| `qwen-embed` | Qwen3-VL 8B encoder + processor | 10.9 GB (24 of 36 layers + vision on the card, tail in 4.3 GB of RAM) | `Common/models/services/embedding` |
| `qwen-dit` | 7B DiT, int8 weight-only, + scheduler | 6.6 GB | `Common/models/services/dit` |
| `qwen-vae` | 64-channel VAE | ~1.3 GB | `Common/models/services/vae` |
| `qwen-image-split` | none | 0 | here |

The V100 still dictates the dtypes: no bfloat16, so fp16 everywhere; no
FlashAttention-2 (needs SM 8.0), so `DIFFUSERS_ATTN_BACKEND=native` (PyTorch SDPA)
in every stage; int8 for the DiT because 33 GB of fp16 does not fit in 32 GB.

The encoder's RAM/VRAM split lives in `Common/models/shared/qwensplit/offload.py`
— it moved out of this repository together with the model. It keeps the vision
tower and the first 24 decoder layers on the card and the tail in RAM, and it
returns the **pre-RMSNorm** hidden state the DiT was trained on.

Capacity on this card: ~19 GB resident, plus ~2 GB of prefix KV per 1024²
reference. One reference is comfortable, two are tight, ten do not fit — the DiT
answers 503 rather than corrupting the frame. `docs/models_inference_api.md` has
the measurements.

---

## Requirements

* Podman with `podman compose`. GPU passthrough (`nvidia-container-toolkit` +
  CDI, device `nvidia.com/gpu=all`) is needed by the **inference stages**; this
  container needs no GPU and no CUDA.
* The three stages of `../Common/models` up on `models_network`, with the weights
  in the `qwen-hf-cache` volume. This image is their base, so it is built first.
* External networks: `models_network` (created once — `Switch-Service`: `make network`)
  and the Auth network (`deploy_auth_network`).
* Volume `qwen-outputs` for results. `qwen-hf-cache` is mounted by the stages,
  not by this container.

## Build and run

```bash
# 1. The image. It serves two roles: this orchestrator (`app` stage) and the ML
#    base that the three inference services are built FROM.
podman build -t localhost/qwen-image-service:latest -f Dockerfile .

# 2. The inference stages (built FROM the image above).
cd ../Common/models && ./scripts/build.sh && ./scripts/up.sh

# 3. This orchestrator.
cd ../qwen-image-service && cp .env.example .env   # once
podman compose up -d

# 4. Prove it
curl -s http://127.0.0.1:18000/healthz | jq        # loaded:true, three ready stages
../Common/models/scripts/smoke.sh                  # real 512² generation, saves a PNG
```

`GET /healthz` now reports the stages:

```json
{"status":"ok","loaded":true,"memory_mode":"split",
 "stages":[{"name":"embedding","url":"http://qwen-embed:8011","ready":true,"error":null,
            "detail":{"model_id":"/data/huggingface/Qwen-Image-2.1","device":"cuda","gpu_layers":24}},
           {"name":"dit","url":"http://qwen-dit:8013","ready":true,
            "detail":{"quantize":"int8","busy":false}},
           {"name":"vae","url":"http://qwen-vae:8012","ready":true,
            "detail":{"tile_size":1536}}]}
```

It answers 200 even while a stage is warming up (`ready:false`); the per-stage
`load_error` says why.

## Configuration

Everything is in `.env` (see `.env.example`); nothing else is needed at runtime.

| Key | Meaning |
|---|---|
| `EMBEDDING_URL`, `VAE_URL`, `DIT_URL` | stage base URLs inside `models_network` |
| `OUTPUT_RESOLUTION` | area condition images are resized to (pipeline default 1024) |
| `ENCODER_TIMEOUT`, `VAE_TIMEOUT`, `DIT_TIMEOUT` | per-hop read timeouts; the DiT one covers a whole denoise loop |
| `HEALTH_TIMEOUT` | `/healthz` probe timeout per stage |
| `PRELOAD_MODEL` | probe the stages at start-up (weights stay theirs) |
| `MODEL_ID` | display only: `/healthz` and the UI header |
| `DEFAULT_STEPS`, `MAX_STEPS`, `DEFAULT_TRUE_CFG_SCALE`, `DEFAULT_NEGATIVE_PROMPT` | generation defaults |
| `MIN_SIDE`, `MAX_SIDE`, `MAX_IMAGES`, `MAX_UPLOAD_MB`, `INPUT_MAX_SIDE` | input limits |
| `OUTPUT_DIR`, `KEEP_OUTPUTS` | where PNGs are written and how many are kept |
| `WEBSTORAGE_URL`, `WEBSTORAGE_TIMEOUT` | archive hook |

The stage-side knobs (`TEXT_ENCODER_GPU_LAYERS`, `DIT_QUANTIZE`, `VAE_TILE_SIZE`,
…) live in `../Common/models/.env`. They change what the stages load, not what
this service asks for.

## Tests

```bash
uv sync                 # Flask, gunicorn, Pillow, requests, pytest
uv run pytest -q
```

218 tests, no GPU and no network: `tests/conftest.py` replaces the `generator`
object with a stub (the app itself is imported for real now — it has no torch),
`tests/test_split_pipeline.py` drives the real `SplitPipeline` against a faked
transport and checks the call order, payloads and `img_shapes`, and
`tests/test_geometry_contract.py` pins the geometry that the stages pin too.
The model-bound checks live in `../Common/models/scripts/parity.sh`, which
compares the whole stack with diffusers' own pipeline.

The Podman network smoke test stays opt-in (`RUN_PODMAN_TESTS=1`).
`tests/test_deploy_docs.py` is the deployment contract: it fails if `compose.yaml`
grows a GPU device or a published 8000, if the container name drifts, or if the
README stops documenting the start order — see [docs/tests.md](docs/tests.md).

## API

Unchanged: see [`docs/api-contract.md`](docs/api-contract.md). Three pages
(`/`, `/customize`, `/mask-fill`), three JSON endpoints, `/api/logs`,
`/outputs/<name>`, `/healthz`.

## Layout

```
app/
  __init__.py    Flask factory; probes the stages in the background
  config.py      environment -> Settings (stage URLs, timeouts, generation limits)
  generator.py   SplitPipeline: the four hops, SSE consumption, error mapping
  stages.py      HTTP transport: multipart, SSE parsing, StageError
  geometry.py    resize / latent grid / img_shapes (twin of the stages' copy)
  routes.py      the HTTP layer (unchanged; /healthz gained `stages`)
  imaging.py     presets, uploads, output files (unchanged)
  maskfill.py    mask-fill frame preparation (unchanged)
  webstorage.py  POST /api/generated (unchanged)
  logbuf.py      the log ring the UI polls (unchanged)
docs/models_inference_api.md   the stage contract — read this first
docs/api-contract.md           this service's own HTTP API
docs/design.md                 module map and request flow
docs/tests.md                  what the suite covers and what it stubs
docs/adr/0009-...              why the pipeline was split
tests/                         pytest suite, no GPU
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `/healthz` `loaded:false`, `load_error: "embedding: ..."` | the stage is down or still loading: `../Common/models/scripts/status.sh` |
| 503 `dit (HTTP 503): denoise out of VRAM` | too many or too large references for the KV cache; fewer refs, or `use_kv_cache=false` |
| 503 `embedding service unreachable at http://qwen-embed:8011` | wrong network, or the stage was never started |
| generation works but `saved:false` | no `X-Auth-User-Id` header (direct call, not through the gateway) — expected |
| container exits right after start | a container named `qwen-image` exists (switchd owns it); this one is `qwen-image-split` |
