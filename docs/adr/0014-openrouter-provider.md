# 0014 — OpenRouter as a provider

Status: accepted (2026-10-24)

Extends [0013](0013-workspace-queue-and-common-network.md). Adds a fourth kind to `settings.providers`; it does not change the queue, the networks, or the trust boundary.

## Context

OpenRouter is a single key in front of several image models (`google/gemini-*`, `openai/gpt-5*-image`). Its catalog is queryable — `GET /api/v1/models`, filtered on `architecture.output_modalities` containing `image` — and the 2026-10-23 snapshot lists ten concrete models besides the two `openrouter/auto` routers.

It has no `/images/generations` endpoint. Image models are served by `POST /api/v1/chat/completions` with `modalities: ["image", "text"]`; the picture comes back as a base64 data URL in `choices[0].message.images[].image_url.url`. Inputs are chat content parts: `{"type": "text"}` and `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,…"}}`, prompt first. `image_config.aspect_ratio` is documented only for the Gemini image models. Errors are `{"error": {"code", "message"}}` with the same HTTP status, and a 200 can still carry no image (moderation, refusal) with the reason in the message content.

Documentation was read from the Wayback snapshots of `docs/features/multimodal/image-generation` (2025-10-31), `docs/features/multimodal/images` (2025-11-01), and `docs/api-reference/errors` (2025-11-11); `openrouter.ai` answers 403 to this host, and the model roster comes from the archived `api/v1/models` of 2026-10-23.

## Decision

* New provider kind `openrouter`, one field: `api_key`. It is sealed like the Wan and Higgsfield secrets and returned only as `key_hint`.
* The model list is static in `qimg/catalog.py`, like the other vendors: a generation is accepted only for a model the provider enabled from the catalog. The roster ages; refreshing it is a catalog edit, not a migration.
* Every listed model accepts images, so `generate`, `customize`, and `mask_fill` are offered. Mask fill follows the external-provider pattern: the mask image plus a sentence in the prompt.
* `image_config.aspect_ratio` is sent only for models flagged `aspect` (Gemini). Everything else is resized to the requested frame after the answer, the way Higgsfield results already are.
* No progress stages. A job appears as `queued` → `running` → `done` | `failed`, and the vendor message is stored in `error`.
* `X-Title` identifies this app in OpenRouter usage.

## Consequences

Prices are per image output token at the vendor and are not surfaced in this UI. An unknown or retired model id fails at the vendor and the task is `failed` with that text; it is not retried. Adding a model means adding it to `CATALOG` and rebuilding the UI, which lists models from `GET /api/catalog`.
