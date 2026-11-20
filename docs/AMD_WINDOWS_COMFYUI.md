# Windows + AMD GPU with ComfyUI (GGUF models)

How OpenMontage drives local generation on a Windows machine with an AMD GPU
(ROCm/HIP PyTorch build) through a natively installed ComfyUI, including the
unsloth GGUF stacks for **Qwen-Image-2.1-Turbo** and **MiniMax H3**.

> Running Unsloth Studio instead of ComfyUI? See
> [`UNSLOTH_STUDIO.md`](UNSLOTH_STUDIO.md) — OpenMontage supports either
> backend, and they share the same on-disk weights.

## Architecture

```
OpenMontage (repo venv, WSL or Windows)
        │  HTTP  POST /prompt → GET /history → GET /view
        ▼
ComfyUI 0.44+ (native Windows process, C:\ComfyUI)
        │  torch 2.x+rocmsdk  (torch.version.hip != None)
        ▼
AMD Radeon GPU (gfx11xx / Strix Halo class unified memory)
```

OpenMontage never loads the weights itself. It submits an API-format graph and
downloads the artifact, so the GPU stack only has to work for ComfyUI.

Verify the wiring:

```powershell
# GPU + backend as ComfyUI sees it
Invoke-WebRequest -UseBasicParsing http://localhost:8188/system_stats |
  Select-Object -ExpandProperty Content

# Must print a non-None torch.version.hip (None means CUDA/CPU build)
C:\ComfyUI\.venv\Scripts\python.exe -c "import torch;print(torch.__version__, torch.version.hip, torch.cuda.get_device_name(0))"
```

From inside WSL, `http://localhost:8188` still resolves because WSL2 forwards
Windows loopback addresses. If it does not, start ComfyUI with
`--listen 0.0.0.0` and point `COMFYUI_SERVER_URL` at the Windows host IP.

## 1. ComfyUI-GGUF

GGUF checkpoints need the community loader:

```powershell
git clone --depth 1 https://github.com/city96/ComfyUI-GGUF C:\ComfyUI\custom_nodes\ComfyUI-GGUF
C:\ComfyUI\.venv\Scripts\python.exe -m pip install gguf
```

Restart ComfyUI afterwards (`stop-comfyui.cmd` then
`start-comfyui-hidden.vbs`, or your usual launcher). Confirm the nodes exist:

```powershell
(Invoke-RestMethod http://localhost:8188/object_info).PSObject.Properties.Name |
  Where-Object { $_ -match 'GGUF' }
# UnetLoaderGGUF, CLIPLoaderGGUF, ...
```

### Known ComfyUI-GGUF limitations

| Symptom | Cause | Fix |
|---|---|---|
| `This model is not currently supported - (Unknown model architecture!)` | The GGUF has no `general.architecture` key and `detect_arch` does not know the tensor layout (MiniMax H3). | Apply the generic fallback patch below. |
| `This gguf file is incompatible with llama.cpp!` | `CLIPLoaderGGUF` is llama.cpp-based; it cannot load MiniMax H3 or Qwen-Image Qwen3-VL **text encoder** GGUFs. | Use the ComfyUI-format safetensors text encoder (see the model table). |
| `size mismatch for encoder.conv_in.weight` when loading the VAE | A diffusers-layout VAE (`vae/diffusion_pytorch_model.safetensors`) was used instead of ComfyUI's single-file VAE. | Use the Comfy-Org single-file VAE. |

Generic fallback patch (already applied to this machine) in
`C:\ComfyUI\custom_nodes\ComfyUI-GGUF\loader.py`:

```python
        try:
            arch_str = detect_arch(set(val[0] for val in tensors)).arch
        except Exception as e:
            # OpenMontage patch: models such as MiniMax H3 ship GGUF with no
            # general.architecture key and tensor names that already match
            # ComfyUI's state dict. Fall back to generic loading.
            logging.warning(f"Could not detect GGUF architecture ({e}); loading with generic tensor names.")
            arch_str = "generic"
```

Keep the patch in a local branch or re-apply it after updating the node pack.

## 2. Model files

ComfyUI reads extra model folders from `extra_model_paths.yaml`. On this
machine `D:/ai/models/` is the model root:

| Tool `model_family` | Files | Folder |
|---|---|---|
| `qwen_image_2_1_turbo_gguf` | `qwen-image-2.1-turbo-Q8_0.gguf` | `diffusion_models/` |
| | `qwen3vl_8b_int8_convrot.safetensors` | `text_encoders/` |
| | `qwen_image_2.1_vae_bf16.safetensors` | `vae/` |
| `minimax_h3_local_gguf` | `minimax_h3_fl2va_pruned-Q8_0.gguf` | `diffusion_models/` |
| | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | `text_encoders/` |
| | `minimax_h3_video_vae_fp16.safetensors` | `vae/` |
| | `minimax_h3_audio_vae_fp32.safetensors` | `vae/` |
| `minimax_h3_ref2va_gguf` | `minimax_h3_ref2va_pruned-Q8_0.gguf` | `diffusion_models/` |
| | same encoder + VAEs as the fl2va row | |

Sources:

- Denoisers: [unsloth/Qwen-Image-2.1-Turbo-GGUF](https://huggingface.co/unsloth/Qwen-Image-2.1-Turbo-GGUF),
  [unsloth/MiniMax-H3-GGUF](https://huggingface.co/unsloth/MiniMax-H3-GGUF)
  (`fl2va` = text plus 0/1/2 anchor frames, `ref2va` = reference-conditioned).
- Qwen text encoder / VAE: [unsloth/Qwen-Image-2.1-FP8](https://huggingface.co/unsloth/Qwen-Image-2.1-FP8)
  and [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1).
- MiniMax text encoder / VAEs: [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3)
  and the `vae/` folder of the unsloth repo.

### Reuse the Unsloth Studio download (no second copy)

Unsloth Studio keeps its Hugging Face cache in one place (here
`D:\.cache\huggingface`). When that cache and the model folder are the same
volume, hard-link the blobs instead of re-downloading ~55 GB:

```python
import os
src = os.path.realpath(r"D:\.cache\huggingface\hub\models--unsloth--Qwen-Image-2.1-Turbo-GGUF\snapshots\<rev>\qwen-image-2.1-turbo-Q8_0.gguf")
os.link(src, r"D:\ai\models\diffusion_models\qwen-image-2.1-turbo-Q8_0.gguf")
```

Hard links share the bytes, so deleting the ComfyUI file does not delete the
Unsloth copy (and vice versa). Downloading a missing rung with
`hf download <repo> <file>` while `HF_HUB_CACHE` points at the same cache keeps
both apps in sync.

## 3. Using the families

```python
# image (8-step distilled Turbo)
{"model_family": "qwen_image_2_1_turbo_gguf", "prompt": "...", "width": 1280, "height": 720}

# video with native stereo audio (24 fps, 5s default)
{"model_family": "minimax_h3_local_gguf", "prompt": "...", "duration": 5, "steps": 20}

# reference-conditioned video (needs reference_image_path)
{"model_family": "minimax_h3_ref2va_gguf", "prompt": "...", "reference_image_path": "ref.png"}
```

Bundled graphs:

- `tools/_comfyui/workflows/qwen-image-2.1-turbo-gguf-t2i.json` — mirrors
  ComfyUI's `image_qwen_image_2_1_turbo_t2i` template: `UnetLoaderGGUF`,
  `TextEncodeQwenImage21`, `QwenImage21Cache`, `CFGGuider` (cfg 1.0),
  `ManualSigmas` (the shipped 8-step schedule), `euler`, `VAEDecode`.
- `tools/_comfyui/workflows/minimax-h3-fl2va-gguf-t2v.json` — mirrors
  `video_minimax_h3_t2v`: `MiniMaxH3ImageToVideo` → `BasicGuider` →
  `SamplerCustomAdvanced` (`res_multistep` + `simple` scheduler) →
  `VAEDecode` + `VAEDecodeAudio` → `CreateVideo` (24 fps) → `SaveVideo`.
- `tools/_comfyui/workflows/minimax-h3-ref2va-gguf-i2v.json` — mirrors
  `video_minimax_h3_r2v`: `MiniMaxH3ReferenceToVideo` with
  `ref_images.ref_image_0`, same sampler/audio tail. Reference images stay in
  the sequence for every sampling step, so identity is carried through the
  whole clip (slower than a first-frame anchor).

Notes:

- H3 frame counts snap to its `17k + 5` grid (124 ≈ 5.2 s at 24 fps).
  `Operation` `image_to_video` attaches the reference as `first_frame`.
- Qwen Turbo's 8-step sigma schedule is fixed by the checkpoint; `steps` is
  reported from the schedule rather than patched.
- `generate_audio: false` on the H3 path drops the audio decode/mux.

## 4. Measured on this machine

AMD Radeon 8060S, 68 GB RAM, 107 GB unified VRAM, torch 2.9.1+rocmsdk:

| Job | Settings | Wall time |
|---|---|---|
| Qwen-Image-2.1-Turbo Q8_0 (cold) | 512², 8 steps | ~51 s |
| Qwen-Image-2.1-Turbo Q8_0 | 768×512, 8 steps | ~50 s |
| MiniMax H3 fl2va Q8_0 (cold, incl. 27 GB encoder) | 640×384, 39 frames, 8 steps | ~2 min |
| MiniMax H3 fl2va Q8_0 | 640×384, 39 frames, 8 steps (warm encoder) | ~2.5 min |

## 5. Troubleshooting checklist

1. `comfyui_image` / `comfyui_video` report `degraded` → a required file name is
   missing; the tool returns `data.missing_models[]` with role, folder and URL.
2. Model was just added but the loader menu does not show it → ComfyUI caches
   folder listings; restart the server.
3. `No ComfyUI server found` → set `COMFYUI_SERVER_URL` in `.env` (default
   `http://localhost:8188`).
4. Video rejects the resolution → use multiples of 32 (H3/Qwen latent grid).
5. Long renders: the client waits up to `timeout_seconds` (3600 s default) and
   the error carries `prompt_id` for `resume_prompt_id` recovery.
