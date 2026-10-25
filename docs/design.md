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
                                              └► Wan, OpenRouter, or Higgsfield HTTPS
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

The feed is the latest 50 tasks, newest first. A tile shows the seed, the status, and the result photo. A task that is not done yet shows the processing stage and the seed, with no photo. Clicking a tile opens the form that created it, in that project, with the prompt and the other stored settings filled in. The fields stay editable. Up returns to the previous view, and the sidebar stays on the Feed tab. The replay rules are [user-story-feed-replay.md](user-stories/user-story-feed-replay.md).

The editor draws rectangles (and "use entire frame") on the photo. Zoom and pan keep shapes in image coordinates. Parts may overlap while they are placed or moved. Confirm-and-save runs the same polygon test as `frontend/src/geometry.js` and refuses the level while any parts overlap. After confirm-and-save the level accepts delete and move, not a new shape and not a resize. Delete or Backspace removes the selected rectangle or circle. Circles exist only on level 2. Double-click or Edit goes down. A single click on a saved shape opens a context menu. Switching photo/text keeps one variant history. Text is not editable yet.

Generation always sends the original crop. The selected variant is shown at the current level and in the final render only.

## Providers

Local Qwen is kind `qwen`, id `local`. The worker calls `InferenceClient.generate(..., on_progress)`. Legacy `N/M` log lines become stages 4–8 named `denoise`. A future `event: progress` is stored as stage/total/name. External calls do not report stages.

Wan, OpenRouter, and Higgsfield are called from `qimg/providers.py`. Authorization headers are built there, not in `app/`. Wan receives JPEG base64. Higgsfield receives a file uploaded to its own storage, then a `public_url`, because MinIO is private. OpenRouter has no images endpoint: the request is `POST /api/v1/chat/completions` with `modalities: ["image", "text"]`, the crop and the mask travel as base64 `image_url` parts, and the picture comes back as a base64 data URL in `choices[0].message.images`. Gemini models also accept `image_config.aspect_ratio`, so the requested frame picks the closest documented ratio; other models are resized after the fact like every external answer. Mask fill on those models is the mask image plus a prompt hint. The hard circle mask is applied to the returned image, for every provider.

If the project or the part is gone when the result comes back, the worker deletes the blob it just wrote and deletes the task. Deleting a project or a part does the same for every task that belonged to it, including the artifact files once nothing else references them. The feed does not keep an `invalidated` row.

## What was removed

The three HTML forms and their `/api/generate`, `/api/customize`, and `/api/mask-fill` routes. WebStorage is not called. The log buffer is not polled by the page.
