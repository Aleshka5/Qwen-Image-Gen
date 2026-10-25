# 0001 — Progress events on the inference stream

Status: shipped in `localhost/qwen-inference:latest` (`inference/app.py`, `inference/logstream.py`,
`inference/pipeline/generator.py`). The contract it produced is [inference_api.md](../inference_api.md#progress-stages).

Audience: the inference project at `Common/models/services/inference`. That project only launches the image;
the code is built from this repository.

## What stays

`POST /v1/generate` stays `multipart/form-data` in and `text/event-stream` out. Keep `event: start`, `event: log`, `event: done`, and `event: error`. The inference process stays stateless: it does not connect to Mongo, RabbitMQ, or MinIO, and it does not learn a user id. One job still runs at a time on the GPU.

## What to add

Emit a progress event whenever the pipeline enters a stage or finishes a denoise step:

```
event: progress
data: {"stage": 4, "total": 10, "name": "denoise"}
```

`stage` and `total` are integers. `total` is always 10. `name` is a short token.

| stage | name | when |
|---|---|---|
| 1 | `start` | the request has been accepted |
| 2 | `prepare` | inputs are decoded and sized |
| 3 | `encode` | conditioning and the VAE encode |
| 4–8 | `denoise` | spread the sampling steps across these four stages |
| 9 | `decode` | VAE decode |
| 10 | `done` | the PNG bytes are ready, sent together with `event: done` |

`event: log` may continue for operators. The browser does not show those lines.

## How it was implemented

`inference/logstream.py` gained a `Progress` item and a `progress(stage, name)` function: it reads the same
`ContextVar` the log handler uses, so the pipeline needs no HTTP knowledge and no new global registry. The SSE
generator writes `event: progress` for every `Progress` it takes off the request queue — the same queue that
carries the log lines, which is why the events stay ordered against them.

| Stage | Where the call sits |
|---|---|
| 1 | `inference/app.py`, right after `event: start` |
| 2 | `generator.generate()`, first statement under the GPU lock |
| 3 | `generator.generate()`, just before `pipe(...)` |
| 4–8 | `generator._log_denoise_step()`, one per step, `4 + min(4, int(step / total * 4))` |
| 9 | the instrumented `vae.decode` wrapper (`_instrument_pipeline`) |
| 10 | `inference/app.py`, right before `event: done` |

Denoise sends an event per step and the orchestrator keeps only a stage that differs from the previous one, so a
40-step job produces ten `progress` writes on the task, not forty.

## The old fallback

The mapping from the `N/M` log line is still in `app/inference.py::_legacy_progress` and still runs against a
container that predates this change. The first real `progress` event switches it off for the rest of the stream, so
rolling the model image back needs no change on the web side.
