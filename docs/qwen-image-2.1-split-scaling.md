# Can Qwen-Image-2.1 run as three independently scaled services?

**Partial.** The text encoder, VAE, and denoise DiT are separate weight folders and separate modules, and a single generation really is three phases with a small tensor handoff. They cannot be brought up and autoscaled as three independent network services with the code this repository runs, or with the official Qwen-Image-2.1 serving path.

What people call "Qwen-2.1" here is the image model **`Qwen/Qwen-Image-2.1`**, pipeline class **`QwenImage21Pipeline`**. It is not an LLM.

## Direct answer

| Question | Answer |
|---|---|
| Are the three parts separate weights? | Yes. Hugging Face stores `text_encoder/`, `transformer/`, and `vae/` as separate subfolders. Diffusers and ComfyUI load them as distinct modules. |
| Does one generation use all three? | Yes, in order, inside one pipeline call. Edits use the VAE twice: encode condition images before the loop, decode the result after it. |
| Can each part be loaded without the others? | Yes. That is "weights can be loaded separately." |
| Can each part be its own service and scale on its own? | Not with an official API. A custom worker could split them. The DiT still cannot scale per step: its prefix KV cache pins a request to one replica for all 40 steps. |
| Would three processes help this V100? | No, not on the one 32 GB card this service is built for. The weights are already time-shared inside one process because they do not fit together. |

## What this service does today

One Flask/gunicorn process loads one pipeline and runs one GPU job at a time.

- Model id default: `Qwen/Qwen-Image-2.1` (`MODEL_ID` in `app/config.py` and `.env.example`).
- Class: whatever `model_index.json` names. That file says `QwenImage21Pipeline`. Diffusers is pinned to commit `bdc2bea37a36038c44452811610489ea30ede229` (2026-09-25) because release 0.40.0 does not contain that class (`docs/adr/0004-diffusers-git-pin.md`).
- Load path: `DiffusionPipeline.from_pretrained` (or the class from `PIPELINE_CLASS`) in `QwenImageGenerator._load` (`app/generator.py`). One call reads every subfolder.
- Runtime: `pipe(**call_kwargs)` under `_gpu_lock`. Gunicorn is `--workers 1`. A second worker would duplicate the weights and OOM (`docs/adr/0008-single-worker-gpu-lock.md`).
- No second model, no sidecar, no RPC between encoder, DiT, and VAE. The only other network call is WebStorage after a successful PNG.

The three modules exist, and the service already places them differently in memory. They stay in one address space:

| Piece | What it is | Where this process puts it |
|---|---|---|
| Text encoder | `Qwen3VLForConditionalGeneration` (Qwen3-VL 8B, 36 text layers, vision tower). Hidden size 4096. | Not all on the GPU. `CpuHostedTextEncoder` (`app/offload.py`) keeps the tail in RAM. Vision tower and the first 24 decoder layers stay in VRAM so reference photos and DeepStack indexes 8/16/24 run on the card. `lm_head` is dropped. From the outside the module still reports `device=cuda`, because diffusers moves encoder inputs onto `pipe._execution_device`. |
| Denoise DiT | `QwenImage21Transformer2DModel`. 32 single-stream layers, 7B, `in_channels`/`out_channels` 64, `context_in_dim` 4096, `causal_condition: true`. | VRAM. Default `MEMORY_MODE=int8` quantizes these weights only (~7 GB instead of ~14 GB). Compute stays fp16. Before VAE decode the whole DiT is parked in RAM, then moved back. |
| VAE | `AutoencoderKLQwenImage21`. 4-channel RGBA in and out, 64-channel latent, 16× spatial compression. | VRAM, fp16, ~1.4 GB. Tiling is a fallback for frames that still do not fit after the DiT has left the card. |

fp16 weights for the whole pipeline are about 33 GB (DiT ~14 GB, encoder ~17.5 GB, VAE ~1.4 GB), which is why they cannot all be resident on a 32 GB V100 (`docs/adr/0003-v100-memory-and-torch-cu126.md`). That split is a memory schedule, not a service boundary. `MEMORY_MODE=offload` is Accelerate sequential offload, still one process. The official pipeline declares the same order: `model_cpu_offload_seq = "text_encoder->transformer->vae"`.

Generate, customize, and mask-fill all call this same generator. A mask is another condition image, not a separate inpaint network.

## What one official call actually does

Pinned pipeline: [pipeline_qwenimage21.py](https://github.com/huggingface/diffusers/blob/bdc2bea37a36038c44452811610489ea30ede229/src/diffusers/pipelines/qwenimage21/pipeline_qwenimage21.py) at the commit this repo installs. Constructor arguments are `scheduler`, `vae`, `text_encoder`, `processor`, `transformer`.

1. **Resize condition images once.** The same pixels go to the text encoder and to the VAE. Default condition area is 1024² (`output_resolution=1024`). This service does not pass that argument, so the default applies. Output size is separate (presets up to 2752×1536, sides multiple of 32).
2. **Text encoder, once.** `Qwen3VLProcessor` builds a chat template. Text-only and image-conditioned templates differ. The encoder returns the last decoder hidden state. The pipeline strips the final RMSNorm with a forward hook, because the DiT was trained on the pre-norm state. Transformers 5 otherwise returns the normalized state, which the pipeline comments describe as about a third of the signal the DiT expects. This service's `CpuHostedTextEncoder` does not install that hook.
3. **VAE encode, once per condition image**, `sample_mode="argmax"`, then `(latent - latents_mean) / latents_std`. These latents are prepended to the noise and concatenated again on every denoise step.
4. **DiT, 40 steps by default.** Flow-match Euler. `true_cfg_scale=1` (this service's default, and Qwen's) is one forward per step. Above 1, with a negative prompt, the DiT runs twice and keeps a second KV cache.
5. **VAE decode, once**, after unnormalizing with `latents_std` and `latents_mean`. `output_type="latent"` skips this.

Condition images are not "VAE only" or "encoder only." The diffusers docstring says each image becomes its own block: the Qwen3-VL encoder sees it as vision context, and the VAE contributes latent tokens. Block-causal attention keeps each block bidirectional inside itself. Prefix KV cache is valid because `causal_condition` modulates those tokens from `t = 0`, so their keys and values do not change across steps. Step 0 stores them (`kv_cache_mode="extract"`). Later steps recompute only the target tokens (`"cached"`).

The official call rejects latents in place of a condition image: the text encoder has to see the pixels.

### Tensors that would cross a network boundary

Shapes below use the published configs ([text encoder](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/text_encoder/config.json), [transformer](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/transformer/config.json), [VAE](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/vae/config.json)). Sizes are fp16 payloads, batch 1, excluding masks and metadata.

| Hop | Tensor | Shape | Approx. bytes |
|---|---|---|---|
| Encoder → DiT | `prompt_embeds` | `(B, S, 4096)` | ~8 MB per 1024² reference (about 1024 vision tokens: patch 16 × merge 2 = 32 px) plus a few MB of text |
| Encoder → DiT | `prompt_embeds_mask` | `(B, S)` | negligible |
| Encoder → DiT | `image_pad_mask` | `(B, S)` bool, marking which positions are image tokens | negligible, required for edits |
| Encoder → DiT | `img_shapes` | per image `(frame=1, H/16, W/16)`, condition images then the target | metadata; must match the VAE grid |
| VAE → DiT | packed condition latents | `(B, H/16 * W/16, 64)` per image | ~0.5 MB per 1024² image |
| DiT internal, not shipped today | prefix KV, 32 layers, K and V | `(B, prefix_tokens, 32 heads, 128)` each | ~0.5 GB of stored K/V per 1024² reference before text tokens. This service measures about 2 GB of KV VRAM per reference in fp16; use that as the planning number, because the live delta includes more than the stored tensors |
| DiT → VAE | denoised target latents | packed `(B, Ht*Wt, 64)`, unpacked `(B, 64, 1, Ht, Wt)` | ~2 MB at 2048² (128×128×64) |

Ten references are on the order of **~80 MB of embeddings** and **~5 MB of condition latents**. That is cheap to send once. The thing that is expensive to move is the **prefix KV cache**, which is born inside the DiT on step 0 and reused for the other 39 steps. A negative prompt at `true_cfg_scale > 1` duplicates it. This service already refuses that combination with ten references on 32 GB.

Each vision-language image slot stands for a 2×2 group of latent tokens. The encoder's token grid and the VAE's latent grid are the same geometry. A resize on one service and not the other changes the picture.

## Weights loaded separately vs services scaled separately

**Separate weights: supported.**

- `model_index.json` lists five components: `processor` (`Qwen3VLProcessor`), `text_encoder` (`Qwen3VLForConditionalGeneration`), `transformer` (`QwenImage21Transformer2DModel`), `vae` (`AutoencoderKLQwenImage21`), `scheduler` (`FlowMatchEulerDiscreteScheduler`). The scheduler is config, not a fourth network.
- On disk, the [model repo](https://huggingface.co/Qwen/Qwen-Image-2.1) is `text_encoder/` (4 safetensors shards), `transformer/` (2 shards), `vae/` (1 shard). vLLM's recipe lists them as bf16 17.5 GB, bf16 14.2 GB, and fp32 1.4 GB.
- Diffusers documents [single-file transformer loading](https://github.com/huggingface/diffusers/blob/bdc2bea37a36038c44452811610489ea30ede229/docs/source/en/api/pipelines/qwenimage21.md) from `Comfy-Org/Qwen-Image-2.1`, then `from_pretrained(..., transformer=transformer)`. ComfyUI graphs use separate UNET, CLIP/text-encoder, and VAE files. Those nodes still run in one process. A lone VAE file is not a generator ([Civitai's repackaged VAE](https://civitai.com/models/2970862/qwen-image-21-vae) says the same).

**Separate network services: not supported for 2.1.**

- `encode_prompt` can accept ready-made `prompt_embeds` and skip the encoder for **text-only**. `__call__` does not take `image_pad_mask`. Passing `prompt_embeds` together with condition images raises, unless that mask is passed into `encode_prompt`. The edit path this service uses (up to 10 photos, plus a mask image) has no public "encoder already ran" argument.
- `__call__` can return latents (`output_type="latent"`) and skip **final** decode. It cannot accept precomputed condition latents. Pixels are mandatory whenever `image` is set, and those pixels are VAE-encoded inside the call.
- There is no official gRPC/HTTP schema for these tensors. vLLM-Omni's [Qwen-Image-2.1 recipe](https://recipes.vllm.ai/Qwen/Qwen-Image-2.1) serves **one** `QwenImage21Pipeline` (`vllm serve Qwen/Qwen-Image-2.1 --omni`). Step execution batches requests that share a KV-cache phase. Admission is all-or-nothing: after a wave passes step 0, a new request waits until the wave finishes. The recipe states no pipeline parallelism. Tensor and Ulysses sequence parallelism shard one DiT; they do not put the encoder on another autoscaling service. VAE tiling and VAE patch parallelism are flags on that same server.
- The three-stage encode / denoise / decode design that does exist is [vLLM-Omni PR 3208](https://github.com/vllm-project/vllm-omni/pull/3208), for **`Qwen/Qwen-Image`**, not 2.1. As of the last update on that PR (2026-06-03) it was still open. Stages load `text encoder+tokenizer`, `transformer`, and `VAE` in different processes and can be replicated. Intermediate tensors move through host serialization, not GPU-to-GPU transfer. On one 1024² / 40-step request the overhead was ~0.3%. On 8 NPUs, 40-step throughput barely moved; the gain showed up on short schedules (4–8 steps). That PR's encode stage does not implement 2.1's `image_pad_mask`, 2×2 latent slots, or prefix KV cache.

## What independent scaling would actually buy

Latency on this V100 is dominated by the DiT loop (README: a 2048 frame at 40 steps is minutes without FlashAttention). The encoder runs once. The VAE runs once per condition image and once at the end.

| Service | Scales with | Sticky state | Practical limit |
|---|---|---|---|
| Text encoder | New requests, and reference count. Idle during the 40 steps. | None after it returns embeds, mask, and `image_pad_mask`. | Needs the pixels and the processor. Must emit the **pre-final-RMSNorm** hidden state. Vision tower is part of this model, not a fourth service. ~17.5 GB bf16. |
| VAE | Condition count at the start, and one decode at the end. | None between requests. Encode and decode are stateless given the image or the latent. | ~1.4 GB weights, but decode activations for a 2K frame are why this service evicts the DiT first. A second GPU holding only the VAE would remove that park/restore. Many VAE replicas do not shorten a 40-step job. |
| DiT | Whole jobs, one replica per in-flight generation. | Prefix KV cache, scheduler step, latents, and the condition latents for all 40 steps. | This is the capacity you multiply. Moving a job mid-loop means moving gigabytes of KV, or setting `use_kv_cache=False` and recomputing the prefix every step. A diffusers report on the edit path put that at roughly 17 s → 32 s per step. Different resolutions do not share a CUDA graph. |

Batching does not fall out of a split. The encoder could batch prompts. The DiT can batch only requests that share the step index and the token layout, which is what vLLM step-execution already does inside one pipeline, and it will not admit a new shape into a wave that has started. The VAE in this service is already serial behind the GPU lock; its batch dimension is one image.

## What it would take, and the blockers

A real split is a new worker, not a compose change.

1. **Encoder service.** Load `text_encoder/` plus `processor/`. Accept prompt, negative prompt, and the condition PIL images. Return `prompt_embeds`, `prompt_embeds_mask`, `image_pad_mask`, and the per-image `img_shapes`. Apply the same RMSNorm bypass the official pipeline uses. Drop `lm_head`, as this service already does.
2. **VAE service, or a VAE kept beside the DiT.** Encode each condition image to normalized latents. After the loop, unpack, unnormalize, decode, postprocess. `output_type="latent"` is the only official hook, and only for the final image.
3. **DiT service.** Load `transformer/` and the scheduler config. Own the 40-step loop and the KV cache. Do not autoscale a single request across replicas. Scale by adding replicas that each take a whole job. int8 (optimum-quanto) is this repo's V100 choice; official serving talks about FP8 on Blackwell, which does not speed the V100 and needs SM 8.0+ for the fast kernels this card does not have.
4. **Orchestrator.** The current Flask process would stop calling `pipe(...)` and would sequence the three calls. Seed, scheduler sigmas, and `true_cfg_scale` stay with the DiT. WebStorage stays where it is.

Blockers, in order:

- **No 2.1 network API.** `__call__` will not accept encoder outputs for an edit. Condition latents cannot replace pixels. You would call private pipeline methods or reimplement the loop.
- **KV cache pins the DiT.** Independent DiT scale-out is per request, not per step. Shipping the cache between steps costs more than the embedding hop.
- **Geometry lock.** One resize feeds both encoder and VAE. `img_shapes` and `image_pad_mask` have to describe that resize, including the 2×2 slot rule.
- **Hidden-state contract.** The DiT expects the last layer before the final RMSNorm. A split encoder that returns Transformers 5 `last_hidden_state` as-is will not match training.
- **One 32 GB V100.** Three resident processes need about 33 GB of fp16 weights before KV. The current design already exists to avoid that. Separate services help when the encoder, the DiT replicas, and the VAE sit on **different GPUs**. On this host they would still queue on one card, which `_gpu_lock` already does.
- **The closest upstream split is the wrong model and is unmerged.** Porting PR 3208 onto 2.1 means redoing encode for Qwen3-VL, `image_pad_mask`, and the prefix cache. At 40 steps that PR's own numbers showed almost no throughput win.
- **V100 dtype.** Official examples use bfloat16. This service forces fp16. A split does not remove that.

## Sources

- This repo: `README.md`, `docs/design.md`, `docs/adr/0003-v100-memory-and-torch-cu126.md`, `docs/adr/0004-diffusers-git-pin.md`, `docs/adr/0008-single-worker-gpu-lock.md`, `app/generator.py`, `app/offload.py`, `app/config.py`.
- Model card and weights: [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1), [model_index.json](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/model_index.json).
- Architecture and the single-pipeline API: [QwenLM/Qwen-Image-2.1 README](https://github.com/QwenLM/Qwen-Image-2.1), diffusers [pipeline](https://github.com/huggingface/diffusers/blob/bdc2bea37a36038c44452811610489ea30ede229/src/diffusers/pipelines/qwenimage21/pipeline_qwenimage21.py), [transformer](https://github.com/huggingface/diffusers/blob/bdc2bea37a36038c44452811610489ea30ede229/src/diffusers/models/transformers/transformer_qwenimage21.py), [pipeline doc](https://github.com/huggingface/diffusers/blob/bdc2bea37a36038c44452811610489ea30ede229/docs/source/en/api/pipelines/qwenimage21.md). Day-0 note: diffusers PR 14804, cited from the Qwen README (2026-09-20).
- Serving: [vLLM-Omni Qwen-Image-2.1 recipe](https://recipes.vllm.ai/Qwen/Qwen-Image-2.1). Older-model three-stage experiment: [vllm-omni#3208](https://github.com/vllm-project/vllm-omni/pull/3208).
- KV-cache shape change across steps: [diffusers#14829](https://github.com/huggingface/diffusers/issues/14829).

## Pausing after 20 denoise steps

**A custom loop can resume. Stock `__call__` cannot.**

`QwenImage21Pipeline.__call__` at `bdc2bea` always builds the full sigma list, calls `scheduler.set_begin_index(0)`, allocates a fresh `QwenImage21KVCache`, and sets `kv_cache_mode="extract"` only on `i == 0` of that call. `latents` means the initial noise, not a mid-run latent. `num_inference_steps=20` builds a new 20-step schedule from sigma 1, not steps 20–39 of the 40-step schedule. The step-end callback may see `latents` and `prompt_embeds` only (`_callback_tensor_inputs`). It cannot see the KV cache, the condition latents, or the scheduler index, and it cannot stop the loop. This service's `generate` calls `pipe(...)` once; its callback only logs.

A copy of the denoise loop can break after 20 `scheduler.step` calls and continue later. `FlowMatchEulerDiscreteScheduler` on this checkpoint has `stochastic_sampling: false`, so each step is `sample + dt * model_output` with no new noise. The CPU generator in `app/generator.py` is consumed only when the initial latent is drawn. VAE encode uses `argmax`. After step 20 that noise is already inside the target latents, so the continuation needs no RNG state.

Save this, or the continuation will not match an uninterrupted 40-step run:

| State | Why |
|---|---|
| Target latents after step 20 | Packed `(B, Ht*Wt, 64)`, fp16, on the DiT device. ~0.5 MB at 1024² output, ~2 MB at 2048². |
| The 40-step sigma schedule and the next index | `set_timesteps` clears `_step_index` and `_begin_index`. Rebuild the same 40 sigmas (`mu` from the target token count), then `set_begin_index(20)` before the next `step`. |
| Positive prefix KV cache | 32 layers, K and V, shape `(B, prefix_tokens, 32, 128)`, fp16, CUDA. Written by `clone()` on the extract step. |
| Negative prefix KV cache | Same shape, only when `true_cfg_scale > 1` and a negative prompt is set. |
| Conditioning | `prompt_embeds`, `prompt_embeds_mask`, `image_pad_mask` (including the appended target slots), `img_shapes`, packed condition latents. Negative embeds and mask when guidance is on. These can be recomputed if the prompt, images, and resize match. |

The prefix KV does **not** have to be restored byte-for-byte. `causal_condition` modulates text and condition tokens from `t = 0`, and block-causal attention keeps those queries off the target tokens, so the stored K/V is a function of the conditioning only. A new `kv_cache_mode="extract"` forward rebuilds it. Use that forward only to fill the cache. The noise prediction for steps 20–39 of a cached run was computed in `"cached"` layout; feeding an extract forward's output in as the next step is the fp16 mismatch the pipeline documents (prefill vs cached layout). Restoring the bytes skips that prefill.

Size of the cache, per 1024² reference: each vision slot expands to a 2×2 latent group, so the prefix holds `(1024/16)² = 4096` image tokens. K and V are `4096 × 32 heads × 128 × 2 bytes × 2 × 32 layers` = **2 GiB fp16**. Text tokens add a few hundred MB once per request, not per photo. Guidance stores a second cache of the same size. That 2 GiB is the checkpoint. The latents and embeddings are megabytes.
