# Change Request: Save each generation to WebStorage

> **Status:** Implemented
> **Repos:** qwen-image-service (this document). The archive and the history UI live in WebStorage: [`docs/epics/generated-images/init.md`](../../WebStorage/docs/epics/generated-images/init.md).
> **Caller of this service:** the browser, through the Auth Gateway at `https://image.filenkov.store`.
> **This service calls:** WebStorage `http://app:8000` on `auth_network`.

**Actor:** A person who already passed the gateway’s `image` role check (`FAMILY` or `ADMIN`). They generate on this site. WebStorage is where the run is kept.

**Goal:** This site stays the generator. After a successful `POST /api/generate`, this process saves the prompt, the reference images, and the result to WebStorage, attributed to the user id the gateway put on the request. The form still shows the picture here. History and delete live on Storage.

**Value:** One GPU UI. The durable copy is per-user in WebStorage, so it survives the output prune. The browser does not call Storage during generate.

---

## 1. Background

The gateway proxies `image.filenkov.store` to `http://qwen-image:8000`. Before that proxy it strips client-supplied `X-Auth-*`, `X-User-Id`, and `X-Storage-Role`, then injects its own headers. On this host those are the four `X-Auth-*` headers only. There is no `X-User-Id` and no `X-Storage-Role`.

| Header | Value |
|---|---|
| `X-Auth-User-Id` | Access JWT `sub` (User-Service UUID) |
| `X-Auth-Email` | JWT email |
| `X-Auth-Name` | JWT name |
| `X-Auth-Role` | Hub `global_role` |

This app ignores those headers today. `tests/test_no_auth_in_app.py` locks that in. This request is the exception: read `X-Auth-User-Id` (and forward `X-Auth-Email` when it is present). Do not trust a user id from the form body. Do not verify the JWT. The gateway already did, and it overwrote any client header.

WebStorage’s `app` is on `auth_network` as the DNS name `app`. This container is on `qwen_image_gen_network` only. To call Storage on the machine, `qwen-image` also joins `auth_network` (external; the Auth stack creates it). It does not publish port 8000. WebStorage does not join `qwen_image_gen_network`.

`qwen_image_gen_network` stays gateway + `qwen-image`. Putting WebStorage on that network would let it call generate with no `image` role check. The save goes the other way: this process calls Storage.

## 2. Locked decisions

| # | Decision |
|---|---|
| D1 | The form at `GET /` remains the only generator. No history list on this site. |
| D2 | On a successful generate, if `X-Auth-User-Id` is a UUID, POST the bundle to `{WEBSTORAGE_URL}/api/generated` (default `http://app:8000`). |
| D3 | Forward `X-Auth-User-Id` and, when present, `X-Auth-Email`. Do not send `Cookie`, `Authorization`, or `X-Auth-Role`. |
| D4 | No user id (operator `curl` on the docker network) still generates. The response says the run was not saved. The picture is still returned. |
| D5 | A Storage error (quota, 403, 503, timeout) does not fail the generate. The picture is returned, with `saved: false` and Storage’s error text. |
| D6 | A failed generate (400 / 413 / 503) does not call Storage. |
| D7 | Client timeout toward Storage is 60s. The GPU wait is unchanged (`--timeout 0`). Saving is a file upload, not another generate. |

## 3. Save call

After `save_outputs`, and before the JSON response:

```http
POST http://app:8000/api/generated
X-Auth-User-Id: <uuid from the inbound request>
X-Auth-Email: <when the inbound request had it>
Content-Type: multipart/form-data
```

| Part | |
|---|---|
| `prompt` | the prompt that was used |
| `negative_prompt` | empty string when unset |
| `seed` | the seed that was used (after `-1` was replaced) |
| `steps` | |
| `true_cfg_scale` | |
| `width`, `height` | |
| `duration` | seconds, one decimal is enough |
| `images` | the reference files, same order, field name `images` repeated |
| `result` | the PNG just written, one file |

WebStorage answers `201` with `{ "id", "created_at" }`. This service does not store that id except by returning it to the browser.

Generate response gains two fields. Existing fields stay.

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

When nothing was saved:

```json
{
  "saved": false,
  "storage_error": "quota exceeded"
}
```

`storage_id` is omitted when `saved` is false. `storage_error` is omitted when `saved` is true.

The form shows the image either way. When `saved` is false it shows `storage_error` under the result. When `saved` is true it does not send the user to Storage; the history page is the Storage sidebar.

## 4. Run

Attach both networks. `AUTH_DOCKER_NETWORK` is the Auth stack’s network (`deploy_auth_network` when Auth is started from `Auth-Service/deploy`: compose project `deploy`, network key `auth_network`). Do not create it here.

```bash
podman run -d --name qwen-image \
  --network qwen_image_gen_network \
  --network "${AUTH_DOCKER_NETWORK:-deploy_auth_network}" \
  --device nvidia.com/gpu=all \
  --security-opt=label=disable \
  --env-file .env \
  -v qwen-hf-cache:/data/huggingface \
  -v qwen-outputs:/data/outputs \
  --shm-size=8g --memory=64g \
  qwen-image-service:latest
```

`.env` gains `WEBSTORAGE_URL=http://app:8000`. No host port.

On `auth_network` this container’s name is `qwen-image`. WebStorage’s name there is `app`. Those names must stay distinct. Do not rename this container to `app`.

## 5. User stories

### US-QWN-01 — Read the gateway user id

**As** this service, **I read the user id from `X-Auth-User-Id`** on `POST /api/generate`.

**Acceptance**

- The value is used only when it is a UUID. Any other value is treated as “no user”.
- The id is not taken from the multipart body.
- `X-Auth-Role` is not used for allow/deny. The gateway already allowed the request.
- `GET /`, `GET /healthz`, `GET /outputs/<name>`, and `GET /api/logs` do not read identity headers and do not call Storage.
- A request with no `X-Auth-User-Id` still generates.

### US-QWN-02 — Save the run to WebStorage

**As** a person generating on this site, **I get the image here, and the same run is stored under my user id.**

**Acceptance**

- After a successful generate with a UUID `X-Auth-User-Id`, this process `POST`s the bundle to `WEBSTORAGE_URL` with that header and the reference files in order plus the result PNG.
- `201` → the generate JSON has `saved: true` and `storage_id` from Storage.
- Storage `413` / `403` / `503` / connection error / timeout → generate JSON is still `200`, `saved: false`, `storage_error` set, and `images` still point at the local PNG.
- Generate `400`, `413`, or `503` does not call Storage.
- The form shows the image in both save outcomes, and shows `storage_error` only when `saved` is false.

### US-QWN-03 — Reach Storage on the docker network

**As** this container, **I call `http://app:8000` on `auth_network`.**

**Acceptance**

- The run command attaches `qwen_image_gen_network` and the Auth network, and still does not publish `8000`.
- README states the save target, the header it forwards, and that `qwen_image_gen_network` is still only the gateway and `qwen-image`.
- From the host, nothing listens on public `:8000`.

## 6. Out of scope

- No history page, no delete, no MinIO client in this repo.
- No JWT check and no User-Service call. The gateway remains the allow/deny for this host.
- No CORS. The browser still talks only to `image.filenkov.store`.
- No change to the model, `MEMORY_MODE`, or the gateway YAML.
- WebStorage does not join `qwen_image_gen_network`.

## 7. What to change

| Area | Change |
|---|---|
| `app/routes.py` | Read `X-Auth-User-Id` / `X-Auth-Email`. After a successful generate, POST the bundle. Add `saved` / `storage_id` / `storage_error` to the JSON. |
| `app/static/app.js` | Show `storage_error` when `saved` is false. |
| `.env.example` | `WEBSTORAGE_URL=http://app:8000` |
| `README.md` | Second network, save call, header, trust paragraph unchanged for `qwen_image_gen_network`. |
| `tests/test_no_auth_in_app.py` | Allow reading `X-Auth-User-Id` and `X-Auth-Email` on the generate path only. Spoofed headers on other routes still change nothing. |
| `tests/test_routes.py` | Save skipped without a user id; save POST body and headers with a stubbed Storage; Storage failure still returns the image. |
| `tests/test_deploy_docs.py` | Run command may include a second `--network`. Still no published port. |

## 8. Operator order

1. Auth stack is up, so `auth_network` exists and WebStorage’s `app` answers `http://app:8000` (WebStorage’s save route must be deployed first).
2. Recreate `qwen-image` on both networks with `WEBSTORAGE_URL`.
3. A person with the `image` role generates on `https://image.filenkov.store`. The run shows up in Storage under that user.
