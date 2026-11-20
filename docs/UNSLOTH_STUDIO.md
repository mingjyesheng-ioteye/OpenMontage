# Unsloth Studio as a local image/video backend

OpenMontage can drive **Unsloth Studio** directly over its local REST API — no
ComfyUI, no diffusers install in the repo venv. This is the alternative to the
ComfyUI path in [`AMD_WINDOWS_COMFYUI.md`](AMD_WINDOWS_COMFYUI.md); run
whichever one you prefer.

## Architecture

```
OpenMontage (repo venv, WSL or Windows)
        │  HTTP + bearer token
        ▼
Unsloth Studio  (127.0.0.1:8888, owns model loading / quantization / engine)
        │  torch 2.x+rocmsdk (or CUDA / MLX)
        ▼
GPU
```

| Capability | Tool | Endpoints |
|---|---|---|
| image | `unsloth_image` | `POST /v1/images/generations` |
| video | `unsloth_video` | `POST /api/inference/video/generate` → `GET /api/inference/video/generate-progress` → `GET /api/inference/video/gallery/{id}/file` |

The tools also read `GET /api/auth/status` (health), `GET /api/inference/{images,video}/status`
(loaded model + family defaults), and `GET /v1/models?output_modalities=…`
(catalog when nothing is resident).

## 1. Auth (one-time password change)

Studio seeds a bootstrap password and **rejects every API call until it is
changed once**:

```json
{"detail": "Password change required"}
```

1. Open the Studio UI and change the password (Settings → Account).
2. Create an API key (Settings → API keys) — optional but recommended — and set
   `UNSLOTH_API_KEY`. Alternatively set `UNSLOTH_USERNAME` / `UNSLOTH_PASSWORD`
   and the client exchanges them for a JWT, refreshing on expiry.

Env vars (both `.env` and `.env.example` carry the block):

```ini
UNSLOTH_SERVER_URL=http://127.0.0.1:8888
UNSLOTH_API_KEY=                      # preferred
# UNSLOTH_USERNAME=unsloth
# UNSLOTH_PASSWORD=
UNSLOTH_IMAGE_SERVER_URL=             # optional per-capability overrides
UNSLOTH_VIDEO_SERVER_URL=
```

`tools/_unsloth/client.py` treats these as fatal-but-actionable: a gated account
or a rejected key produces a `DEGRADED` tool with the exact fix in the message,
never a stack trace.

## 2. Model loading

Studio owns the model. Generation uses the **resident model**; with *media
auto-switch* enabled it loads the requested (or last used) model on demand, so
OpenMontage does not preload anything.

- `unsloth_image` reports `Degraded` when no image model is loaded/downloaded and
  `Available` when one is or the catalog has one.
- `unsloth_video` does the same for video models, and marks `image_to_video`
  `degraded` when the loaded family reports `supports_first_frame: false`.
- **One video slot.** Studio serializes video jobs, so do not run concurrent
  `unsloth_video` calls; they will queue/conflict server-side.

Useful checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8888/api/inference/images/status -Headers $h
Invoke-RestMethod http://127.0.0.1:8888/api/inference/video/status  -Headers $h
```

## 3. Using the tools

```python
# image — width/height/steps/guidance are optional (model defaults apply)
{"prompt": "...", "width": 1024, "height": 1024, "steps": 8, "seed": 42,
 "output_path": "projects/<id>/assets/images/shot.png"}

# text-to-video (MiniMax H3 renders native stereo audio)
{"prompt": "...", "operation": "text_to_video", "duration": 5, "steps": 20}

# image-to-video (first frame)
{"prompt": "...", "operation": "image_to_video",
 "reference_image_path": "assets/images/hero.png"}

# subject/style references (Studio exposes them as <Picture 1>, ...)
{"prompt": "the <Picture 1> character walks through rain",
 "reference_image_paths": ["refs/hero.png", "refs/coat.png"]}
```

Adapter behaviour worth knowing:

| Behaviour | Detail |
|---|---|
| Frame counts | Snapped to the loaded family's lattice (`k * frame_step + frame_offset`). MiniMax H3: `17k+5`, **min 124 / max 345** (5.2–14.4 s at 24 fps). |
| Out-of-range frames | An explicit `num_frames` outside the family range fails fast with the supported range — it is not silently stretched into a 4× longer render. `duration` is approximate, so it clamps and returns `frame_note` + `warnings`. |
| Resolution | Snapped down to the family's `resolution_multiple` (H3/Qwen: 32). Omit width/height to use the family preset (H3 default canvas: 864×480; custom sizes need 414,720–1,048,576 px). |
| Media transport | Local images are sent as `data:` URLs (Studio accepts base64 only, 33 MB/image cap). |
| Image output | Requested as `b64_json` so no second authenticated fetch is needed; actual pixel size is read from the PNG header. |
| Video metadata | Downloaded clip is measured with `ffprobe` (`metadata_source: "ffprobe"`), falling back to the Studio gallery record. |
| Audio | Families with native audio always render it; `generate_audio: false` is reported back as `audio_note` instead of silently dropping the track. |
| Timeouts | `timeout_seconds` (image 1800, video 3600 default). A video timeout does **not** cancel the server job — check `generate-progress` / the Studio gallery before resubmitting. |
| Engine | Studio picks the engine: GGUF video here runs on `sd_cpp`, image on `diffusers`. Both are reported in `workflow_provenance.model_status`. |

## 4. Running Unsloth OR ComfyUI

Both providers are ordinary registry tools, so "either/or" needs no config
change — **stop the one you are not using**. A stopped backend reports
`Unavailable`, and the selectors route to the running one:

```python
image_selector({"prompt": "...", "preferred_provider": "unsloth"})
```

If both are running, pin explicitly (`preferred_provider`, or
`allowed_providers: ["unsloth"]`) so the choice is deliberate rather than
score-driven.

They compete for the same GPU memory, so do not keep both resident. Free one
before switching:

```powershell
# ComfyUI
Invoke-RestMethod -Method Post http://localhost:8188/free -ContentType application/json `
  -Body '{"unload_models":true,"free_memory":true}'

# Unsloth Studio (bearer token in $h)
Invoke-RestMethod -Method Post http://127.0.0.1:8888/api/inference/video/unload -Headers $h
Invoke-RestMethod -Method Post http://127.0.0.1:8888/api/inference/images/unload -Headers $h
```

The GGUF checkpoints themselves are shared on disk (`D:\.cache\huggingface` and
`D:\ai\models` are hard-linked), so switching backends costs no download and no
duplicate storage.

## 5. Verification

Live runs on this machine (`unsloth/Qwen-Image-2.1-Turbo-GGUF`,
`unsloth/MiniMax-H3-GGUF`, AMD Radeon 8060S, Studio with an API key):

| Job | Settings | Wall time |
|---|---|---|
| Qwen-Image-2.1-Turbo Q8_0, first call (loads model) | 768×512, 8 steps | 91 s |
| Qwen-Image-2.1-Turbo Q8_0, warm | 768×512, 8 steps | 12.6 s |
| MiniMax H3 t2v (`sd_cpp`), 960×544 | 124 frames, 8 steps | 21.5 min |
| MiniMax H3 i2v with first frame, 864×480 | 124 frames, 4 steps | 12.5 min |

Both clips came back with `h264` video and **32 kHz stereo AAC**; `ffprobe`
confirmed size, fps, frame count, and audio. Image output was measured from the
PNG header (768×512).

**Speed tradeoff:** normalised for pixels and frames, the Studio's `sd_cpp` H3
path is roughly **2–2.5× slower** than ComfyUI's ROCm PyTorch path on this GPU
(ComfyUI: 640×384 / 39 frames / 8 steps in ~84 s; Studio: 960×544 / 124 frames /
8 steps in ~21 min). Studio is faster for images (12.6 s warm vs ~30–50 s in
ComfyUI). Use Studio for images and memory convenience, ComfyUI when H3
throughput matters.

**Speeding up H3:**

- **8 steps** is the distilled setting (CFG-free; unsloth's own example uses 8).
  20 steps is ~2.5× the render for little gain.
- Use a trained size — **960×544** or **864×480** — instead of 1344×768
  (~2× fewer pixels). Omit width/height to take the family preset.
- **124 frames (5.2 s) is the minimum.** Shorter clips are not supported; trim in
  the edit stage instead.
- Image ↔ video switches reload tens of GB; keep one family loaded per session.

The adapter contract is also covered by a stub-server test (auth rejection, b64
image decode, lattice/dimension snapping, first-frame data URLs, progress
polling, download + ffprobe measurement), so the request shapes are verified
without a GPU run.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `403 Password change required` | Change the seeded password once in the Studio UI, then set a key or password. |
| `401 Invalid API key` | Recreate the key in Studio, or use `UNSLOTH_USERNAME`/`UNSLOTH_PASSWORD`. |
| `degraded` with "no image/video model loaded" | Load or download a model in Studio (or enable media auto-switch). |
| `422` on video | Frame count off the lattice or width/height not on the family multiple — the adapter snaps when the status endpoint reports defaults, so load the model first. |
| `image_to_video` refused | The loaded family has `supports_first_frame: false`; switch models or use `text_to_video`. |
