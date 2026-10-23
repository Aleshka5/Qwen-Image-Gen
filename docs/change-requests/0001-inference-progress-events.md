# 0001 — Progress events on the inference stream

Status: requested. The workspace already accepts the event. qwen-inference has not implemented it.

Audience: the inference project at `Common/models/services/inference`. This repository does not change that process.

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

## Until this ships

The orchestrator maps an existing denoise log line that contains `N/M` onto stages 4–8 with `name: denoise`. `event: start` becomes stage 1. `event: done` becomes stage 10. External providers (Wan, Higgsfield) have no stage numbers; the UI shows the words `queued`, `running`, `done`, and `failed`.
