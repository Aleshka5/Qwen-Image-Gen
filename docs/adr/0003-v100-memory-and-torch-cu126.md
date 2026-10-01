# ADR 0003. V100 memory and torch cu126

## Status

Accepted

## Context

The host is a Tesla V100 32 GB, Volta, SM 7.0.

- There is no hardware bfloat16. Weights are computed in float16.
- FlashAttention-2 requires SM 8.0. The backend is `ATTENTION_BACKEND=native` (PyTorch SDPA). The value `sdpa` in `app/generator.py` is an alias of `native`.
- The Qwen-Image-2.1 pipeline in fp16 is about 33 GB: DiT 7B ~14 GB, Qwen3-VL 8B encoder ~17.5 GB, VAE ~1.4 GB. It does not fit on the card as a whole.
- CUDA 13 (cu130) no longer builds for Volta. cu121 wheels stop at torch 2.5.1, and the pinned diffusers and optimum-quanto require torch >= 2.6. The matching set is torch 2.7.1 and torchvision 0.22.1 from `https://download.pytorch.org/whl/cu126`: that build includes `sm_70`.
- Ten references hold a KV cache on the order of 2 GB per photo in fp16. `true_cfg_scale` above 1 keeps a second cache and does not fit in 32 GB.
- The encoder tail lives in RAM. The container needs about 40 GB of RAM while loading; a 64 GB limit leaves room if `TEXT_ENCODER_DTYPE` is returned to float32. `shm` of 8g is required by the PyTorch loaders.

Diffusers assumes every module is on one device, and inside `__call__` it moves encoder inputs to `pipe._execution_device`. A bare `text_encoder.to("cpu")` breaks the pass. The encoder is therefore wrapped in `CpuHostedTextEncoder` (`app/offload.py`): from the outside `device` is CUDA, and inside the tail runs on CPU. The vision tower and the first `TEXT_ENCODER_GPU_LAYERS` (default 24 of 36, so DeepStack 8/16/24 is covered) stay in VRAM.

## Decision

The image installs torch `2.7.1` and torchvision `0.22.1` only from `https://download.pytorch.org/whl/cu126`, inside the `ml` group (ADR 0007). Do not move the index above cu126/cu128.

The normal mode is `MEMORY_MODE=int8`: weight-only int8 DiT through optimum-quanto (~7 GB instead of ~14 GB). Compute stays fp16. `fp16` is for few references. `offload` is `enable_sequential_cpu_offload()`, a slow fallback.

`TEXT_ENCODER_DTYPE=float16`, `TEXT_ENCODER_GPU_VISION=true`, `TEXT_ENCODER_GPU_LAYERS=24`. Before decode the DiT moves to RAM. A full frame up to 2752×1536 is decoded whole. Tiles (`VAE_TILING`, `VAE_TILE_SIZE=1536`) are used when the full frame does not fit.

The container receives device `nvidia.com/gpu=all`, 64g of memory, and 8g of shm.

## Consequences

- A build without `sm_70` in `torch._C._cuda_getArchFlags()` does not run on the V100. The architecture check does not need a GPU.
- Torch must not be pulled silently from PyPI: a mismatched torch and torchvision pair breaks `torchvision::nms`. The pin lives in the `ml` group.
- OOM on ten photos is handled by fewer references, a lower resolution, and `true_cfg_scale=1`, then `MEMORY_MODE=offload`, not by a second worker (ADR 0008).
- `app/offload.py` has no unit tests (see `docs/tests.md`).
