# ADR 0004. Diffusers is pinned to a main commit

## Status

Accepted

## Context

The default `MODEL_ID` is `Qwen/Qwen-Image-2.1`. The class `QwenImage21Pipeline` exists on diffusers main and is absent from release 0.40.0. `PIPELINE_CLASS=auto` takes the class from `model_index.json`. If the installed diffusers does not know it, loading fails (`module diffusers has no attribute …` / `KeyError`).

Installing diffusers from a floating main is not acceptable: the next commit can change the pipeline API without a change in this service. The image does not need git to install the dependency when the dependency is a commit archive.

The official minimum for the Qwen3-VL encoder on 2.1 is transformers 5.17.

## Decision

In the `ml` group, diffusers is pinned to the main commit archive from 2026-09-25:

`https://github.com/huggingface/diffusers/archive/bdc2bea37a36038c44452811610489ea30ede229.tar.gz`

Alongside it: `transformers>=5.17.0`, `accelerate>=1.10.0`, `optimum-quanto>=0.2.7`. After a diffusers release that contains `QwenImage21Pipeline` (0.41.0 was expected), the pin is replaced by a lower version bound. Until that replacement, the commit is not moved just to stay current.

The uv migration removed `requirements-ml.txt`. The pin lives in `pyproject.toml` / `uv.lock` with the same commit.

## Consequences

- The image is reproducible: the same SHA until this ADR is revised.
- A diffusers bump is a separate decision: check loading `Qwen/Qwen-Image-2.1`, `encode_prompt` with references, and VAE decode.
- A local `uv sync` does not install this dependency (the `ml` group is image-only, ADR 0007). "Tests passed, the image has a different diffusers" shows up only on a GPU run.
