# API contract

The browser opens `https://image.filenkov.store`. The Auth Gateway proxies to `http://qwen-image:8000` and injects `X-Auth-User-Id`, `X-Auth-Email`, and `X-Auth-Name`. This process does not check a session. A project route with no UUID user id answers **400** `{"error": "A user id is required"}`. It never answers 401 or 403.

`GET /` is one HTML shell. The workspace is a view stack inside that page. `/customize`, `/mask-fill`, `/api/generate`, `/api/customize`, `/api/mask-fill`, and `/api/logs` are gone.

Unless a row says otherwise, success JSON is the resource itself or a list wrapped as `{projects|shapes|variants|tasks|providers}`. Errors are `{"error": "..."}`.

## Identity and pages

| Method | Path | User id | Body |
|---|---|---|---|
| `GET` | `/` | no | HTML shell, account name from `X-Auth-Name` |
| `GET` | `/healthz` | no | `{status, model, memory_mode, loaded, load_error, inference}` |
| `GET` | `/api/config` | no | limits, aspect presets, level colors |
| `GET` | `/api/catalog` | no | provider kinds and their models |
| `GET` | `/static/ui/...` | no | built workspace assets |

`/healthz` is public on the gateway. `loaded` reports the inference container, not this process.

## Settings

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/settings` | `{providers: [...]}` |
| `POST` | `/api/settings/providers` | kind `wan`, `openrouter`, or `higgsfield`, plus the fields below |
| `PATCH` | `/api/settings/providers/:id` | same fields; blank secrets are left unchanged |
| `DELETE` | `/api/settings/providers/:id` | Local Qwen cannot be deleted |
| `POST` | `/api/settings/providers/:id/default` | no body |

Local Qwen is always present with id `local` and kind `qwen`. Wan needs `api_key`, `region` (`singapore`, `virginia`, `beijing`, `frankfurt`), and `workspace_id`. OpenRouter needs only `api_key`. Higgsfield needs `key_id` and `key_secret`. A stored key comes back only as `key_hint` (the last four characters). `secret_enc` is never returned. An empty `SETTINGS_SECRET` rejects the write. `models` is filtered against `GET /api/catalog`; an id the catalog does not know is dropped, and an empty list means every model of that kind.

## Projects and shapes

| Method | Path | Body |
|---|---|---|
| `GET` | `/api/projects` | `{projects}` newest first |
| `POST` | `/api/projects` | multipart `kind=photo\|scratch`, optional `file`, optional `name` |
| `GET` | `/api/projects/:id` | `{project, shapes}` |
| `PATCH` | `/api/projects/:id` | `{name}` |
| `DELETE` | `/api/projects/:id` | `{deleted: true}`; every task of that project is deleted, with its result, its variant, and its artifact files once no remaining task points at them |
| `GET` | `/api/projects/:id/preview` | PNG, longest side at most 2048 |
| `POST` | `/api/projects/:id/shapes` | `{parent_id, kind, x, y, w, h}` |
| `POST` | `/api/projects/:id/commit` | `{parent_id}` saves every sibling and writes an original crop |
| `PATCH` | `/api/shapes/:id` | move, and resize only before save |
| `DELETE` | `/api/shapes/:id` | the shape, its children, and every task of those shapes, with the same file cleanup as a project delete |
| `POST` | `/api/shapes/:id/type` | `{type: photo\|text}` |
| `GET` | `/api/shapes/:id/source` | full crop; `?preview=1` thumbnails it |
| `GET` | `/api/shapes/:id/variants` | `{variants}` |
| `POST` | `/api/shapes/:id/select` | `{variant_id}` |
| `GET` | `/api/projects/:id/variants` | scratch variants (no shape) |
| `POST` | `/api/projects/:id/select` | `{variant_id}` |

Coordinates are 0..1 relative to the parent. Level-1 kinds are `rect` and `full_frame`. Level-2 kinds are `rect` and `circle`. Overlaps are rejected. The cap is `MAX_SHAPES` (20). After commit, size is frozen; delete and move remain. Commit sets `geometry_locked` on the project (level 1) or on the parent shape (level 2), not on the shapes just saved, so a saved part can still receive children.

Level-1 colors come from `level1_colors`. A level-2 photo is `#143d8f`. A level-2 text object is `#8c1a2b`. Text shows the original crop and cannot be generated yet.

A shape record:

```json
{
  "id": "...",
  "project_id": "...",
  "parent_id": null,
  "kind": "rect",
  "type": null,
  "color": "#c47b2b",
  "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4,
  "saved": false,
  "geometry_locked": false,
  "selected_variant_id": null
}
```

## Generations

`POST /api/generations` is multipart.

| Field | Notes |
|---|---|
| `project_id` | required |
| `shape_id` | required for `customize` and `mask_fill`; absent for scratch `generate` |
| `mode` | `generate`, `customize`, or `mask_fill` |
| `provider_id` | `local` or a saved provider |
| `model` | an id from that provider's enabled list |
| `prompt` | required, English, at most 2000 characters |
| `negative_prompt` | English, optional |
| `steps`, `true_cfg_scale` | same bounds as the old form |
| `copies` | 1..`MAX_COPIES` (8). Above 1, every seed is random |
| `seed` | used only when `copies` is 1; blank or `-1` is random |
| `size_mode`, `quality`, `aspect`, `width`, `height` | scratch `generate` only. Edit modes use the crop size |
| `mask` | one file for `mask_fill`, same pixel size as the crop, not fully black |
| `images` | reference files, in order |

The response is `{reused, tasks}`. A task id is a UUID. The same user-controlled inputs, including the artifact bytes and an explicit single seed, return the existing `queued`, `running`, `pending`, or `done` batch instead of a new one. Random seeds are not part of that key. A batch whose tasks all failed may be submitted again.

Artifact order stored with the task: `source` (the original crop, never a selected variant), then `mask` when the mode is mask fill, then each `reference`. The pipeline still fits that crop to the 512–2752, multiple-of-32 frame. The editor does not downscale it first.

`GET /api/tasks/:id` and `GET /api/feed` return:

```json
{
  "id": "...",
  "status": "queued",
  "progress": {"stage": 0, "total": 10, "name": "queued"},
  "mode": "customize",
  "provider_id": "local",
  "provider_kind": "qwen",
  "model": "qwen-image-2.1",
  "seed": 7,
  "prompt": "...",
  "negative_prompt": "",
  "steps": 4,
  "true_cfg_scale": 1.0,
  "width": 1024,
  "height": 1024,
  "copies": 1,
  "error": null,
  "result_url": null,
  "mask_url": null,
  "references": [{"url": "/api/media/user/batches/batch/2.png"}],
  "shape_id": "...",
  "project_id": "...",
  "project_name": "...",
  "created_at": "2026-10-23T15:04:00Z"
}
```

`references` lists artifact role `reference` in stored order. `mask_url` is set for `mask_fill` and is null otherwise. The source crop is not included; the form still reads the live part. `provider_kind` is `qwen`, `wan`, `openrouter`, or `higgsfield`. Local jobs include `progress`. External jobs send `progress: null` and the UI shows the status word. Statuses the UI understands are `queued`, `running`, `done`, and `failed`. A task whose project or part is gone is deleted, along with its files, and is absent from both of these responses. `GET /api/tasks/:id` then answers 404.

`POST /api/renders` with `{scope: "project"|"shape", project_id, shape_id}` returns `{url}`. The composite pastes selected **generated** variants onto the original photo. A circle is a hard alpha mask after the model returns. A scratch render returns the selected generated variant, or the latest one.

`GET /api/media/<key>` serves a PNG. The key must start with the caller's user id.

## Internal inference

Unchanged, and not exposed to the gateway: `POST http://qwen-inference:8010/v1/generate` → `text/event-stream`. See [inference_api.md](inference_api.md) and [change request 0001](change-requests/0001-inference-progress-events.md).
