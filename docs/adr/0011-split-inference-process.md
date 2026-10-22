# 0011 — The pipeline runs in its own container and streams its logs over SSE

Status: accepted (2026-10-23)

## Context

The service was one container: Flask, gunicorn, torch 2.7.1+cu126, the whole Qwen-Image 2.1 pipeline
and the GPU, all in one process. That worked, and it had three problems.

1. **The UI is bound to the model.** Restarting the web layer means reloading 33 GB of weights, and the
   web layer cannot be scaled, tested, or deployed without a GPU.
2. **A crash in a page or a route is a crash of the model.** Any exception that takes the worker takes
   33 GB of warm weights with it; the next request pays the full load.
3. **The logs of the pipeline and the logs of the request are the same stream.** `/api/logs` reads
   a ring buffer inside the Flask process, so the only way to watch a step is to hold the GPU job in the
   web worker that also serves the page.

The public protocol must not change: the Auth Gateway proxies `http://qwen-image:8000`, the pages poll
`/api/logs?after=N`, and WebStorage is saved from the same request that generated the image.

## Decision

Split the process in two, and move the pipeline **as is**.

* `qwen-inference` — image `localhost/qwen-inference:latest`, built from `Dockerfile`.
  `inference/pipeline/generator.py` is the old `app/generator.py`: same load path, same memory modes,
  same `_instrument` monkey-patching, same log strings. The only edits are imports (types moved to
  `inference/pipeline/types.py`, settings to `inference/config.py`).
* `qwen-image` — image `localhost/qwen-image-service:latest`, built from `Dockerfile.web`.
  Flask, the pages, the JSON API, WebStorage, and `app/inference.py`: an HTTP client that posts the job
  and reads the answer as `text/event-stream`.

The wire contract is one endpoint, [`POST /v1/generate`](../inference_api.md): multipart in,
`start` → `log`* → `done` | `error` out, PNGs inside the terminal event.

Log records are captured by a `logging.Handler` bound to a per-request `ContextVar`
(`inference/logstream.py`). The generation runs in a worker thread started with
`contextvars.copy_context()`, so the handler sees the right queue without a global registry.
The orchestrator pushes every `log` event into the same ring buffer `/api/logs` already reads
(`LiveLogBuffer.push`), which is why the page needed no change at all.

## Consequences

**Good**

* The web layer has no torch, no CUDA and no weights: ~200 MB image, no `nvidia.com/gpu` device,
  restarts in a second, and it can be rebuilt while a job runs (the job dies with the container that
  owns it, which is now a separate decision).
* `GET /healthz` on the orchestrator reports the pipeline's state instead of guessing it: `loaded`,
  `load_error`, `accepts_images` come from the container that knows.
* The pipeline's logs are a stream, not a buffer: the browser sees `pipeline «denoise» 3/40` while it is
  happening, and so does anyone tailing the container.
* A new public route cannot accidentally allocate VRAM, and a new pipeline knob cannot accidentally
  appear in the web image.
* The pipeline can be run, and its stream watched, without the web layer at all (`curl -N` in the README).

**Costs**

* Two images to build and two containers to run; `compose.yaml` grew a service, and `models_network`
  grew a container.
* One more failure mode: the inference container can be down while the web layer is up. `/healthz`
  reports it (`inference`, `load_error`), and every generation returns `503` with the reason instead of
  a timeout.
* The result comes back base64 inside the SSE event (~5.5 MB at 2K). A second endpoint for the file
  would save the encoding but introduce a result store with its own lifecycle; not worth it at one
  image per request.
* A crash of the inference container still loses the weights — that risk did not move, it just
  stopped being the web layer's risk.
* `preload_model` exists on both sides and means different things: loading weights (inference) and
  probing `/healthz` at start (orchestrator).

## Alternatives considered

**Three stage containers (embed / VAE / DiT) over HTTP.** Implemented on the branch
`split-orchestrator-and-stages`: it removes the 33 GB fp16 residency, but it also replaces one
`pipe(...)` call with a hand-written denoise loop and a tensor wire format, and it splits the log into
three processes. That is a different project — a change of math and of failure modes, not a move.

**Keep the monolith and attach a log sidecar.** Solves only the logs: the UI would still hold the
weights, and the pipeline's logging would still live in the web worker.

**Queue jobs (Redis/Celery) and poll for progress.** More moving parts than a single synchronous
stream between two containers, and it changes the public API from “one request, one answer” to a job
handle, which the gateway and the pages would have to learn.
