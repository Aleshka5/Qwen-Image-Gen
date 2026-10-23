# Design

The workspace is one page and three processes. The model is still the other project.

```
browser ──► Auth Gateway ──► qwen-image (gunicorn, this image)
                                 │  writes Mongo, MinIO, then Rabbit intake
                                 ▼
                           qimg-processor ──► Rabbit gen
                                 │
                                 ▼
                           qimg-orchestrator ──► qwen-inference :8010  (SSE)
                                              └► Wan or Higgsfield HTTPS
                                 │
                                 ▼
                           Mongo status + MinIO result
                                 ▲
browser polls GET /api/tasks/:id ┘
```

## Processes

| Process | Command | Networks |
|---|---|---|
| `qwen-image` | gunicorn `wsgi:app` :8000 | `deploy_auth_network`, `models_network`, `common_network` |
| `qimg-processor` | `python -m qimg.processor` | `common_network` |
| `qimg-orchestrator` | `python -m qimg.orchestrator` | `common_network`, `models_network` |

No host port, no GPU, no `depends_on`. The image healthcheck curls `:8000/healthz`; the workers disable it because they do not listen.

`qwen-inference` is not in this compose file. It does not touch Mongo, RabbitMQ, or MinIO.

## Records

Mongo database `qimg`:

| Collection | Role |
|---|---|
| `projects` | photo or scratch, name, source key, `geometry_locked` |
| `shapes` | normalized box, kind, type, color, saved flag |
| `variants` | original crop or a generated image |
| `tasks` | one generation, its artifact keys, status, progress |
| `settings` | per-user providers; secrets sealed |

MinIO bucket `qimg-images`. Every key starts with the user id, which is how `GET /api/media` checks ownership.

RabbitMQ exchange `qimg`. The intake message and the gen message carry `task_id` and the ordered artifacts `{role, key, position}`. The processor re-reads the task before it routes. The orchestrator claims with a conditional update so a duplicate delivery does not run twice.

An empty `MONGO_URL` + `RABBIT_URL` + `MINIO_ENDPOINT` selects the in-memory stand-ins. Tests and `uv run python wsgi.py` use that. Production sets all of them.

## UI

Vite and React, built into `app/static/ui` and served by the same Flask process. There is no client-side router. "+" opens a menu: upload one photo, or generate from scratch. The sheet animation is 350ms. Back is the Up button. "New project" in the left sidebar returns home. The project name defaults to the Europe/Moscow date and time and can be renamed.

Both sidebars are overlays, including at 390px. The right sidebar is the feed, an empty object column, or the variant list.

The editor draws rectangles (and "use entire frame") on the photo. Zoom and pan keep shapes in image coordinates. The overlap test is the same polygon test as `frontend/src/geometry.js`. After confirm-and-save the level accepts delete and move, not a new shape and not a resize. Circles exist only on level 2. Double-click or Edit goes down. A single click on a saved shape opens a context menu. Switching photo/text keeps one variant history. Text is not editable yet.

Generation always sends the original crop. The selected variant is shown at the current level and in the final render only.

## Providers

Local Qwen is kind `qwen`, id `local`. The worker calls `InferenceClient.generate(..., on_progress)`. Legacy `N/M` log lines become stages 4–8 named `denoise`. A future `event: progress` is stored as stage/total/name. External calls do not report stages.

Wan and Higgsfield are called from `qimg/providers.py`. Authorization headers are built there, not in `app/`. Wan receives JPEG base64. Higgsfield receives a file uploaded to its own storage, then a `public_url`, because MinIO is private. Mask fill on those models is the mask image plus a prompt hint. The hard circle mask is applied to the returned image, for every provider.

If the project is gone when the result comes back, the worker deletes the blob it just wrote and marks the task `invalidated`.

## What was removed

The three HTML forms and their `/api/generate`, `/api/customize`, and `/api/mask-fill` routes. WebStorage is not called. The log buffer is not polled by the page.
