# ADR 0005. A WebStorage failure does not fail generation

## Status

Accepted

## Context

The form on `GET /` remains the only generator. History and deletion live in WebStorage. The browser does not talk to Storage during generation: after `save_outputs`, the process does.

The user is known only from headers the gateway set: `X-Auth-User-Id` (UUID, the `sub` of the access JWT) and optional `X-Auth-Email`. Verifying the JWT here would mean copying the gateway secret. An operator `curl` from the generation network brings no UUID, but must still receive the picture.

A Storage quota error, 403, 503, disconnect, or timeout happens after the PNG is already written. The frame must not be lost because the archive failed. The archive client uploads files; it is not a second inference. Its timeout is 60 s, and gunicorn stays at `--timeout 0`.

## Decision

`app/webstorage.py` does `POST {WEBSTORAGE_URL}/api/generated` only from `api_generate` in `app/routes.py`, only after a successful generation, and only if `X-Auth-User-Id` matches a UUID. That header is forwarded, and `X-Auth-Email` is forwarded when the string is non-empty. `Cookie`, `Authorization`, and `X-Auth-Role` are not sent. An id from the form body is not read.

A 201 response with a non-empty string `id` yields `saved: true` and `storage_id`. Otherwise the generation JSON is still `200`: `saved: false`, `storage_error`, no `storage_id`, and the local `images` stay. With no UUID, `storage_error` is `not saved: no user id` and no socket is opened.

`ValidationError` (400), exceeding `MAX_CONTENT_LENGTH` (413), and `PipelineError` (503) do not call the archive. `POST /api/customize` does not call the archive.

The timeout is `WEBSTORAGE_TIMEOUT` (60). Errors are logged without the user id, email, or tokens.

## Consequences

- Two outcomes on screen: after 200 the picture is always there; the `storage_error` line appears only when `saved` is false. `tests/test_routes.py` and `tests/test_generate_form.py` check this.
- The archive receives the first PNG and the references re-encoded as PNG. The archived `negative_prompt` is what the user sent, without substituting `DEFAULT_NEGATIVE_PROMPT`.
- `duration` in the client JSON is rounded to hundredths. In the archive multipart it is rounded to tenths.
- Reading `request.headers` outside `api_generate` fails `tests/test_no_auth_in_app.py`.
