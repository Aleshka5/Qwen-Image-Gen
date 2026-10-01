# HTTP API contract

Behavior is taken from `app/routes.py`, `app/imaging.py`, `app/webstorage.py`, `app/logbuf.py`, `app/__init__.py`, and `tests/test_routes.py`. The service is Flask behind gunicorn. From outside, the browser uses `https://image.filenkov.store` (Auth Gateway). This process listens on `0.0.0.0:8000` inside the container and does not log anyone in.

Path, header, and JSON field names below are the wire identifiers. Some validation error strings returned by the code are still Russian; they are quoted as the body the client receives.

## Common rules

- The body of `POST /api/generate` and `POST /api/customize` is `multipart/form-data`.
- Successful JSON does not sort keys (`JSON_SORT_KEYS = false`).
- Total body size is limited by `MAX_UPLOAD_MB` (default 60). That value is Flask `MAX_CONTENT_LENGTH`.
- The application has no login, session, cookie, JWT, or role check. The gateway decides who is allowed. A request that reached the process is already allowed.
- Identity headers are read only by `POST /api/generate`, and only to sign the archive. `GET` routes and `POST /api/customize` do not use them.

## Route map

| Method | Path | Response |
|---|---|---|
| `GET` | `/` | HTML generate form |
| `GET` | `/customize` | HTML form for editing one photo |
| `GET` | `/healthz` | JSON status of the process and the model |
| `GET` | `/api/config` | JSON limits and presets |
| `GET` | `/api/logs` | JSON ring buffer of logs |
| `POST` | `/api/generate` | JSON generation result |
| `POST` | `/api/customize` | JSON photo-edit result |
| `GET` | `/outputs/<name>` | PNG, or JSON 404 |
| `GET` | `/static/<path:filename>` | Flask static files (`app/static/`) |

Flask also registers `HEAD` and `OPTIONS`. No other rules are on the map: `/login` and similar paths return 404.

## `GET /healthz`

Anonymous for the application. The gateway serves this path without the `image` role. The call does **not** load the model (`ensure_loaded` is not called).

```json
{
  "status": "ok",
  "model": "Qwen/Qwen-Image-2.1",
  "memory_mode": "int8",
  "loaded": false,
  "load_error": null
}
```

| Field | Meaning |
|---|---|
| `status` | always `"ok"` when the process answers |
| `model` | `MODEL_ID` |
| `memory_mode` | `MEMORY_MODE` |
| `loaded` | the pipeline is already in memory |
| `load_error` | text of the last load error, or `null` |

## `GET /api/config`

```json
{
  "model": "Qwen/Qwen-Image-2.1",
  "max_images": 10,
  "max_upload_mb": 60,
  "max_steps": 60,
  "accepts_images": true,
  "presets": [
    {"key": "2048x2048", "label": "1:1 — 2048×2048", "width": 2048, "height": 2048}
  ]
}
```

`presets` is built from `RESOLUTION_PRESETS`: for `high` and `medium`, and for each aspect ratio. `accepts_images` is whether the loaded pipeline accepts an `image` argument. Before the model is loaded, the test stub and the real generator can disagree; after `ensure_loaded` the flag comes from the `__call__` signature.

## `GET /api/logs`

In-process ring buffer (`app/logbuf.py`). The forms need it: generation holds a thread, and the page polls logs from another. Capacity is 400 records. Only records from loggers named `app*` at `INFO` and above are stored.

Query: `after` is an integer id; return lines whose `id` is greater. A missing parameter, an empty string, a non-number, and a negative value all mean `after = 0`.

```json
{
  "lines": [
    {"id": 1, "level": "INFO", "message": "pipeline «denoise» 1/4: …"}
  ]
}
```

The buffer lives in the worker's memory and disappears when the container restarts. It is not an audit log.

## `POST /api/generate`

Form fields:

| Field | Required | Rule |
|---|---|---|
| `prompt` | yes | Non-empty string, English only, at most 2000 characters. Edge whitespace is stripped |
| `negative_prompt` | no | Same length and language limits. An empty field stays an empty string for the archive |
| `images` | no | Repeated file field, up to `MAX_IMAGES` (10). JPEG, PNG, WEBP, BMP. The long side is shrunk to `INPUT_MAX_SIDE` (1536) |
| `size_mode` | no | `preset`, `custom`, or omitted — then the legacy `resolution` field is used |
| `quality` | when `preset` | `high` (~2K) or `medium` (same aspect, about 1024²). An empty value counts as `high` |
| `aspect` | when `preset` | `1:1`, `4:3`, `3:4`, `3:2`, `2:3`, `16:9`, `9:16`. An empty value is `1:1` |
| `width`, `height` | when `custom` | Integers 32…3000, then the side is rounded down to a multiple of 32 |
| `resolution` | legacy | A preset key (`2048x2048`, …) or `WxH` in the same 32…3000 range. An empty field is the first preset, `2048x2048` |
| `steps` | no | Integer 1…`MAX_STEPS` (60). Default `DEFAULT_STEPS` (40) |
| `true_cfg_scale` | no | Number 1.0…10.0. Default `DEFAULT_TRUE_CFG_SCALE` (1.0) |
| `seed` | no | Integer 0…2147483647 (`2**31 - 1`). Empty or `-1` picks `secrets.randbelow(2**31 - 1)`, so 0…2147483646 |

Cyrillic in `prompt` and `negative_prompt` is rejected (range `Ѐ–ӿ`).

Native `high` frames: `2048×2048`, `2400×1792`, `1792×2400`, `2528×1696`, `1696×2528`, `2752×1536`, `1536×2752`. `medium` is exactly half of those.

If `negative_prompt` is empty, the pipeline receives `DEFAULT_NEGATIVE_PROMPT` (an empty string by default). WebStorage receives the stripped user field, without that default substituted in.

Headers, and only on this method:

| Header | Rule |
|---|---|
| `X-Auth-User-Id` | UUID (8-4-4-4-12 hex, any case, edge whitespace stripped). Any other text, an empty value, or a missing header means "no user" |
| `X-Auth-Email` | Optional text. Edge whitespace is stripped; an empty value is not forwarded |

The UUID version is not checked: v4, v1, and the nil UUID are all accepted. `X-Auth-Role`, `Authorization`, `Cookie`, and a form field do not set the user. The application does not verify a JWT.

Success is `200`:

```json
{
  "seed": 1823486689,
  "duration": 96.4,
  "width": 2048,
  "height": 2048,
  "images": [{"name": "20260922-124332-3b47ec64.png", "url": "/outputs/20260922-124332-3b47ec64.png"}],
  "saved": true,
  "storage_id": "20260927T115012Z-3b47ec64"
}
```

| Field | Meaning |
|---|---|
| `seed` | The seed that was used (after replacing `-1`) |
| `duration` | Seconds, `round(..., 2)` |
| `width`, `height` | The frame passed to the pipeline |
| `images` | Local PNGs. `name` is the file in `OUTPUT_DIR`, `url` is `GET /outputs/<name>` |
| `saved` | Whether the run was written to WebStorage |
| `storage_id` | The `id` string from the 201 response. Present only when `saved` is true |
| `storage_error` | Why the save failed. Present only when `saved` is false |

Without a valid UUID:

```json
{
  "saved": false,
  "storage_error": "not saved: no user id"
}
```

That object has no `storage_id`. Local `images` are still returned. WebStorage is not called.

If the UUID was valid and the archive failed (HTTP error, disconnect, timeout, a status other than 201, or no string `id`), the generation status stays `200`: `saved: false`, and `storage_error` is the `error` field from the Storage JSON body, or the status code (`"503"`), `"timeout"`, `"connection error"`, or `"invalid storage response"`. The pictures stay on disk.

Only the first PNG goes to the archive (`images[0]`). The pipeline is called with `num_images_per_prompt = 1`.

`400`, `413`, and `503` do not call WebStorage.

## `POST /api/customize`

Edits one photo. The size in the response is the original file frame, not the intermediate model frame. This method does not write to WebStorage and does not read `X-Auth-*` headers.

| Field | Required | Rule |
|---|---|---|
| `system_prompt` | yes | Same as `prompt` on generate: English, up to 2000 characters |
| `negative_prompt` | no | Same as on generate |
| `image` | yes | Exactly one file, the same formats. The field name is `image`, not `images` |
| `steps` | no | 1…`MAX_STEPS`, default `DEFAULT_STEPS` |
| `true_cfg_scale` | no | 1.0…10.0 |
| `seed` | no | Same as on generate |

The model receives a frame fitted into `MIN_SIDE`…`MAX_SIDE` (512…2752) on a multiple of 32. The response JSON contains `seed`, `duration`, `width`, `height`, and `images`. `width` and `height` are the uploaded photo's size. The saved PNG is restored to that size (`PhotoFrame.restore`). There are no `saved`, `storage_id`, or `storage_error` fields.

## `GET /outputs/<name>`

Returns a PNG (`image/png`) from `OUTPUT_DIR`. The name must not leave the directory: `../` and an encoded escape return 400 (`ValidationError`) or 404, and a file outside the directory is not served. The directory keeps the latest `KEEP_OUTPUTS` files (default 200).

Missing file:

```json
{"error": "Файл не найден"}
```

status 404.

## `GET /static/<path:filename>`

Ordinary Flask static files: `style.css`, `app.js`, `customize.js`.

## Errors

| Status | When | Body |
|---|---|---|
| 400 | `ValidationError`: empty prompt, Cyrillic, length, steps, CFG, size, format or file count, path escape | `{"error": "…"}` |
| 413 | Body larger than `MAX_UPLOAD_MB` | `{"error": "Суммарный размер загрузки больше 60 МБ"}` |
| 503 | `PipelineError` (load, config, OOM inside the generator) | `{"error": "<PipelineError text>"}` |
| 404 | No file at `/outputs/<name>` | `{"error": "Файл не найден"}` |
| 404 | Unknown path | Werkzeug response, not the application's JSON envelope |
| 405 | Right path, wrong method (`GET /api/generate`, `POST /healthz`) | Werkzeug response, not a JSON envelope |
| 500 | Any other exception | `{"error": "Внутренняя ошибка: ValueError"}` — the class name, without the exception text |

The shared handler does not replace `HTTPException`, so an unknown-path 404 and a 405 stay Flask's own responses.

## Outbound WebStorage call

Only after a successful generation and a written PNG, and only when `X-Auth-User-Id` is a UUID.

```http
POST {WEBSTORAGE_URL}/api/generated
X-Auth-User-Id: <uuid>
X-Auth-Email: <if it was non-empty>
Content-Type: multipart/form-data
```

Default `WEBSTORAGE_URL=http://app:8000`, timeout `WEBSTORAGE_TIMEOUT=60` seconds. `Cookie`, `Authorization`, and `X-Auth-Role` are not sent.

Parts, in this order:

| Part | Contents |
|---|---|
| `prompt` | Stripped prompt |
| `negative_prompt` | Stripped user field, an empty string if it was omitted |
| `seed` | The seed actually used |
| `steps` | |
| `true_cfg_scale` | Number without trailing zeros |
| `width`, `height` | Generation frame |
| `duration` | Seconds with one decimal place (`1.26` → `"1.3"`) |
| `images` | References re-encoded as PNG, names `image-1.png`, `image-2.png`, … |
| `result` | The first saved PNG, file name as in `images[0].name` |

The expected status is **201** and a JSON object with a non-empty string `id`. `created_at` is not read. Any other outcome becomes `saved: false` and does not change the generation HTTP status.
