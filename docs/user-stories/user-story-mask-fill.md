# Change Request: Mask-fill form

> **Status:** Proposed (discovery). Not implemented.
> **Repos:** qwen-image-service (this document).
> **Caller of this service:** the browser, through the Auth Gateway at `https://image.filenkov.store`.
> **This service calls:** the already loaded `QwenImage21Pipeline`, then WebStorage `POST {WEBSTORAGE_URL}/api/generated` the same way `POST /api/generate` does.

**Actor:** A person who already passed the gateway’s `image` role check (`FAMILY` or `ADMIN`). They open this site to fill part of a photo.

**Goal:** A third form, beside generation (`GET /`) and customisation (`GET /customize`). The person adds one photo, paints a mask on it, optionally adds reference photos in an order, writes an English prompt, and clicks Generate. The picture comes back on this page. After a successful run the process saves it to WebStorage under the gateway user id.

**Value:** Local edits stay on the same GPU page as the other two forms. The person does not get a second model, a login, or a history list here.

---

## 1. Background

Two forms exist today.

| | Generation | Customisation |
|---|---|---|
| Page | `GET /` — `app/templates/index.html`, `app/static/app.js` | `GET /customize` — `app/templates/customize.html`, `app/static/customize.js` |
| API | `POST /api/generate` | `POST /api/customize` |
| Photo | Up to `MAX_IMAGES` (10) reference files, field `images`, order kept | Exactly one file, field `image` |
| Prompt | `prompt`, English, required | `system_prompt`, English, required |
| Size | Preset or custom. JSON `width` / `height` are the frame sent to the pipeline | No size control. `PhotoFrame` fits the file for the model and restores the PNG to the file’s width and height |
| Result on the page | Card under the form: `#gallery`, `#meta` (`width×height · seed · duration`), `#storage-error` only when `saved` is false | Same card, without `#storage-error` |
| WebStorage | After success, `save_generated` in `app/webstorage.py`. Best-effort. `400` / `413` / `503` do not call Storage | Does not save and does not read `X-Auth-*` |

Shared controls on both forms: optional `negative_prompt`, `steps`, `true_cfg_scale`, `seed` (`-1` means random). While the request runs, the page polls `GET /api/logs`. Chrome labels are Russian. The prompt is English. Both pages link to each other in `nav.pages`.

The loaded model is `Qwen/Qwen-Image-2.1` through `QwenImage21Pipeline` (`app/generator.py`). One call accepts a prompt and up to 10 condition images on `image`. This service does not load a second pipeline. Qwen-Image-2.1 describes a local edit as the original photo plus a separate mask: white is the area to change, black stays. That mask is another condition image, not a `mask_image` argument on this pipeline.

`tests/test_routes.py` pins the URL map. `tests/test_no_auth_in_app.py` allows `X-Auth-User-Id` and `X-Auth-Email` only inside `api_generate` in `app/routes.py`, and only so the save can be signed. There is no JWT check in this process.

## 2. Locked decisions

| # | Decision |
|---|---|
| D1 | New page `GET /mask-fill`. New API `POST /api/mask-fill`. Generation and customisation keep their paths, fields, and JSON. |
| D2 | One main photo, field `image`, same formats as customisation (JPEG, PNG, WEBP, BMP). Required. |
| D3 | The browser paints the mask. The request sends it as field `mask`: one PNG, same pixel width and height as the main photo. White (`#fff`) is the area to fill. Black (`#000`) is left alone. A mask that is not that size is `400`. An all-black mask is `400`. |
| D4 | Reference photos use the repeated field `images`, in the order the person arranged. Same formats as generation. Count is at most `MAX_IMAGES - 2` (8 when the cap is 10), because the main photo and the mask already use two of the ten condition slots. |
| D5 | Prompt field is `prompt` (not `system_prompt`). English, required, same 2000-character rule as generation. Optional `negative_prompt`, `steps`, `true_cfg_scale`, and `seed` use the same rules as `POST /api/generate`. |
| D6 | No size picker. The model frame is the main photo fitted the way `PhotoFrame` fits a customisation. The PNG and the JSON `width` / `height` are the uploaded photo’s size. The mask is scaled to that model frame with nearest-neighbour, then the result is restored to the photo’s size. |
| D7 | The pipeline stays `generator.generate`. `images` on that request are, in order: fitted main photo (RGB), fitted mask (RGB, white / black), then the reference photos. No `QwenImageInpaintPipeline`, no new weights, no edit to `app/generator.py`. |
| D8 | The user’s prompt is sent as written. The form tells them the photo is image 1, the mask is image 2, and references are image 3 onward. The server does not prepend a hidden prompt. |
| D9 | After a successful run, save with the existing `save_generated`. Do not change `app/webstorage.py` or Storage’s API. Archive `images`, in order: main photo, mask, reference photos. `width` and `height` in that call are the original photo size, the same numbers as the JSON. |
| D10 | Save rules match generation. A UUID `X-Auth-User-Id` is required to save. Forward that header and, when present, `X-Auth-Email`. Do not send `Cookie`, `Authorization`, or `X-Auth-Role`. No user id, or a Storage error, still returns the picture with `saved: false`. `400` / `413` / `503` do not call Storage. |
| D11 | The page shows the result the way generation does: gallery, meta line, and `storage_error` only when `saved` is false. A failed request shows the error on the status line and does not reveal a storage error. |
| D12 | This service still does not authenticate. The new GET does not read identity headers. The new POST reads only the two headers in D10. |

## 3. User story

### US-QWN-04 — Fill a painted region and keep the run

**As** a person editing on this site, **I paint over part of a photo, add reference photos in order, write a prompt, and get the filled image on the page, stored under my user id.**

**Acceptance**

- `nav.pages` on `/`, `/customize`, and `/mask-fill` links to all three forms. The current page is `aria-current="page"`.
- Choosing the main photo shows it on a canvas. The person can paint, erase, change the brush size, undo the last stroke, and clear. Paint is visible on top of the photo. The uploaded mask is a black-and-white PNG at the photo’s pixel size, not a screenshot of the coloured overlay.
- Generate stays unavailable until there is a main photo, some white paint, and a prompt. The server still returns `400` if those are missing or the mask does not match the photo.
- Reference thumbnails show `image 3`, `image 4`, … and can be reordered with the same arrows and drag-and-drop as generation. That order is the order of the `images` parts.
- `POST /api/mask-fill` returns the generation JSON shape: `seed`, `duration`, `width`, `height`, `images` (local PNG URLs), and either `saved` + `storage_id` or `saved: false` + `storage_error`.
- The result card sits under the form. The picture is shown when the status is 200. `storage_error` is shown only when `saved` is false, using `textContent`.
- With a UUID `X-Auth-User-Id`, a successful run `POST`s the bundle to WebStorage. Without a UUID, Storage is not called and `storage_error` is `not saved: no user id`.
- Customisation still does not save. Generation’s fields and save behaviour stay as they are.
- Default `uv run pytest` passes without a GPU. No new dependency. The container still does not publish port 8000.

## 4. Out of scope

- No history page, no delete, and no new WebStorage route or form field.
- No login, session, JWT, or role check.
- No second pipeline, no change to `MEMORY_MODE`, and no prompt-rewriting model.
- No size picker and no change to the customisation response.
- No published port and no `ports:` in `compose.yaml`.
- No GPU test in the default suite. The Podman smoke stays opt-in (`RUN_PODMAN_TESTS=1`).

## 5. What to change

| Area | Change |
|---|---|
| `app/maskfill.py` | New. Pair the main photo and the mask, fit both, restore the result. Public entry `prepare_mask_fill`. |
| `app/routes.py` | `GET /mask-fill` and `POST /api/mask-fill` only. Call `prepare_mask_fill`, then `generator.generate`, then the same save sequence as `api_generate`. Leave `api_generate` and `api_customize` in place. |
| `app/templates/mask_fill.html`, `app/static/mask_fill.js`, `app/static/mask_fill.css` | New form, canvas, ordered references, result card. |
| `app/templates/index.html`, `app/templates/customize.html` | Add the third nav link only. |
| `tests/test_maskfill.py` | Mask size, all-black rejection, reference cap, restore. No GPU. |
| `tests/test_mask_fill_routes.py` | URL, validation, image order into the stub generator, save called or skipped. |
| `tests/test_mask_fill_form.py` | Result markup and `storage_error` handling, same checks as `tests/test_generate_form.py`. |
| `tests/test_routes.py`, `tests/test_no_auth_in_app.py` | Allow the two new routes. Allow the two identity headers and `save_generated` on `api_mask_fill` as well as `api_generate`. |
| `docs/api-contract.md`, `README.md`, `docs/design.md` | Document the form and the save. Do not change the run command. |

Do not edit `app/generator.py`, `app/webstorage.py`, `app/config.py`, `pyproject.toml`, `uv.lock`, `Dockerfile`, or `compose.yaml` for this story.
