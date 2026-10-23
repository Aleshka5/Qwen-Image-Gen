# Internal contract: `qwen-image` → `qwen-inference`

One endpoint does the work, one reports state. Both live inside `models_network`
and are never exposed to the Auth Gateway. This is not the public API: the browser still talks
to `/api/generate`, `/api/customize`, `/api/mask-fill` and `/api/logs` exactly as before.

Why the split, and what it costs: [ADR 0011](adr/0011-split-inference-process.md).

---

## `GET /healthz`

`200` — the process is up. `503` — the pipeline object could not even be constructed.

```json
{
  "status": "ok",
  "model": "Qwen/Qwen-Image-2.1",
  "memory_mode": "offload",
  "loaded": true,
  "load_error": null,
  "accepts_images": true
}
```

| Field | Meaning |
|---|---|
| `loaded` | the weights are in memory; `false` while preloading, which is the normal state for the first seconds-minutes |
| `load_error` | `"<ExceptionType>: message"` if the last load attempt failed; `null` otherwise |
| `accepts_images` | the loaded pipeline class takes reference photos; known only after loading |
| `memory_mode` | `int8`, `fp16`, or `offload` — the mode this process actually runs |

The endpoint answers while a generation is streaming: the container runs gunicorn with
`--threads 4`, so a probe is served by another thread. It never triggers a load on its own
except at process start, when `PRELOAD_MODEL=true` warms the weights in a background thread.

`qwen-image` polls this endpoint (cached for 2 s) and republishes the same fields in its own
`/healthz`, plus `inference: "<INFERENCE_URL>"`.

---

## `POST /v1/generate`

`multipart/form-data` in, `text/event-stream` out. The job starts as soon as the request is parsed.

### Request fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `prompt` | string | yes | non-empty after strip |
| `negative_prompt` | string | no | `""` means “no negative prompt”, not a space: with `true_cfg_scale=1` a space makes the pipeline warn |
| `width`, `height` | int | yes | 32…8192. The orchestrator already snapped them to a multiple of 32 and to `GEN_MAX_SIDE` |
| `steps` | int | yes | 1…1000. The orchestrator caps them at `MAX_STEPS` |
| `true_cfg_scale` | float | no | default `1.0` |
| `seed` | int | no | default `0`; the orchestrator always sends one (random `-1` is resolved on its side) |
| `images` | file[] | no | already resized and normalized by the orchestrator, PNG, at most `MAX_IMAGES`. Order is preserved; it is the prompt-reference order |

Anything the field validation rejects is a plain `400` with `{"error": "..."}` and **no job runs**.
The limit on the body is `MAX_UPLOAD_MB`, as on the public side.

### Response events

```
event: start
data: {"model": "Qwen/Qwen-Image-2.1", "memory_mode": "offload"}

event: log
data: {"level": "INFO", "message": "pipeline «denoise» 1/4: timestep=1000, шаг 0.92 с, с начала 0.9 с"}

: keepalive

event: log
data: {"level": "WARNING", "message": "полный кадр не влез в VRAM, повтор тайлами 1536 px"}

event: done
data: {"seed": 42, "duration": 4.21, "count": 1, "images": ["iVBORw0KGgo..."]}
```

| Event | Payload | When |
|---|---|---|
| `start` | `model`, `memory_mode` | first event, before the pipeline is touched |
| `log` | `level`, `message` | one per logging record from `inference.*` — the same strings the monolith wrote, including `pipeline «denoise» N/M` per step |
| `done` | `seed`, `duration`, `count`, `images[]` | the job finished; `images` are base64 PNGs in pipeline order |
| `error` | `kind`, `error` | the job failed; `kind` is `pipeline` (out of memory, weights, model refusal) or `internal` |

Ordering is strict: `start`, any number of `log`, then exactly one `done` **or** `error`.
Nothing is sent after the terminal event, and the stream is closed.

### Details that matter

**`images` travel inside the event.** A second request for the result would mean a second job on the
same card, or a temporary file the orchestrator has to garbage-collect. The event is the whole answer;
at 2752×1536 a PNG is ~4 MB, so the base64 payload is ~5.5 MB — the GPU step costs more.

**Keepalive comments every 15 s.** `: keepalive\n\n` is an SSE comment, ignored by any parser. It keeps
idle connections from being dropped while a step is still computing, and it lets the client set one read
timeout for the whole job (`INFERENCE_TIMEOUT`, 1800 s) instead of per-chunk arithmetic.

**No retries, no resume.** A generation is not idempotent and holds the GPU; the orchestrator fails the
public request with `503` and the log line at fault. Nothing is cached, nothing is queued: a second
`POST /v1/generate` waits on the GPU lock until the first job ends.

**Errors never break the stream.** A failed job still ends with an `error` event and `200`, because the
response is already streaming by then — the transport succeeded, the pipeline did not. Only a request
that fails validation is a `4xx` before any event.

**`X-Accel-Buffering: no` on the response.** Without it an intermediate proxy may buffer the stream into
one chunk, and the page would see the pipeline log only after the image.

---

## Timeouts on the orchestrator side

| Setting | Default | Covers |
|---|---|---|
| `INFERENCE_URL` | `http://qwen-inference:8010` | DNS name inside `models_network` |
| `INFERENCE_TIMEOUT` | `1800` | the whole stream of one job, read timeout |
| `INFERENCE_HEALTH_TIMEOUT` | `3` | one `/healthz` probe |

If the stream breaks mid-job, the orchestrator raises `PipelineError("поток инференса оборвался…")`
and the public request returns `503`; the GPU job on the other side keeps running to its own end.
