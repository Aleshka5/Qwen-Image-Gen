# ADR 0010. One weight volume per inference stage; safetensors, not GGUF

## Status

Accepted, implemented 2026-10-22.

## Context

ADR 0009 split the pipeline into three services, but all three still mounted the
same `qwen-hf-cache` volume at `/data/huggingface` and simply read different
subfolders of `Qwen-Image-2.1`:

| Stage | Reads | Saw |
|---|---|---|
| `qwen-embed` | `text_encoder/`, `processor/` | all 31 GB, including the DiT's 14 GB and the VAE |
| `qwen-dit` | `transformer/`, `scheduler/` | all 31 GB |
| `qwen-vae` | `vae/` | all 31 GB |

Each stage was coupled to the whole model directory through its mount. That
blocked the obvious questions: can a stage be given a new version of its own
part without touching the others, can a part live somewhere else, and can a part
be stored in a different format.

The second question was about formats — GGUF or safetensors, per model.

## Decision

**Storage.** `qwen-hf-cache` stays the master copy. `scripts/split-weights.sh`
hardlinks each stage's parts into three volumes, and each stage mounts only its
own, read-only:

| Volume | Holds | Mount |
|---|---|---|
| `qwen-weights-embed` | `text_encoder/`, `processor/` | `/data/huggingface:ro` |
| `qwen-weights-dit` | `transformer/`, `scheduler/` | `/data/huggingface:ro` |
| `qwen-weights-vae` | `vae/` | `/data/huggingface:ro` |

The layout inside each volume is the HF layout, so `MODEL_ID` and `subfolder=`
are unchanged and no stage code changed. Hardlinks mean the split costs no disk
space (same inode, `df` does not move, 0.8 s for all three); read-only mounts are
what make shared inodes safe. Replacing a part is `rm` + copy, which breaks the
link and leaves the master copy intact.

**Format.** safetensors for all three parts. GGUF is not viable here, and the
blocker is the runtime, not the container: llama.cpp implements none of the three
modules — the block-causal `QwenImage21Transformer2DModel` with its prefix KV
cache, `AutoencoderKLQwenImage21` with 64-channel latents and `argmax` sampling,
or Qwen3-VL's pre-final-RMSNorm hidden states. A GGUF file could hold the bytes;
nothing in this stack could execute them. The one format change that does work
is recorded below and is opt-in.

## Consequences

* **A stage can only see its own part.** `qwen-embed` cannot read the DiT's
  weights, so a wrong `subfolder=`, a bad path or a stray write fails inside one
  container instead of corrupting a shared directory. A part can be replaced or
  moved on its own — the same seam ADR 0009 created for compute, now for data.
* **The split is free and reversible.** Hardlinks: no extra bytes, no copy step,
  and `--force` rebuilds from the master copy in under a second.
* **`HF_HOME` moved out of the mounted tree.** The part volumes are read-only, so
  the stages point `HF_HOME` at `/tmp/huggingface`; nothing writes weights at
  runtime, and `TMPDIR` was already `/dev/shm`.
* **The stale 11 GB HF-hub copy was removed.** `qwen-hf-cache/hub` (a
  `models--Qwen--Qwen-Image-2.1` download cache from the monolith, superseded by
  the flat `Qwen-Image-2.1/` directory) was referenced by nothing; deleting it
  reclaimed 11 GB.
* **Pre-quantized int8 DiT is available but not the default.** Saving the
  `optimum-quanto` int8 weights once (`scripts/quantize-dit.sh`) turns the DiT
  part from 14.2 GB of fp16 into a 6.7 GB safetensors artifact, and boot drops
  from ~25 s to ~5 s because the 17 s quantize pass goes away. The artifact is
  verified tensor-for-tensor against a fresh quantize: 232 quantized tensors,
  int8 data and scales, zero mismatches.
  It is **not** the default because the end-to-end result is not bit-identical:
  quanto rebuilds the module graph through `from_config`, and with the same seed
  the image differs by up to 9/255 per pixel (mean 0.09). That is inside
  `parity.sh`'s tolerance, but this stack otherwise reproduces diffusers byte for
  byte (`ref_t2i.png` and the split's PNG have the same sha256), and that
  property is worth more than 18 s of boot time. Casting the rebuilt model's
  buffers back to fp16 was measured and made the difference larger (mean 0.19),
  so the drift is in the rebuilt graph, not in a dtype that can be corrected
  afterwards.
* **A second copy of the DiT would be needed to keep both.** The int8 artifact
  replaces the fp16 part in the volume; `split-weights.sh --force` restores it.
  Keeping both available at once would cost another 7 GB and was not worth it on
  a 32 GB card that runs int8 regardless.

## Not done here

Preserving bit-exactness across the artifact reload would need either a quanto fix
or a loader that constructs the same module graph the quantize path builds. Until
then the fp16 part is the default and the artifact is a documented option.
