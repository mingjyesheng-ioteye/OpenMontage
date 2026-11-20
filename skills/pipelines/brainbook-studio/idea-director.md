# Idea Director — BrainBook Studio Pipeline

## When To Use

This pipeline makes a **short clip from a shot list**: a product beat, a mood
piece, a title sequence, a social cut, or any piece where the visual is
generated rather than filmed. There is no research phase — the brief is the
creative act.

Use it when the user wants fast, controlled shots on local providers. If the
deliverable needs sourced research, transcripts, or a presenter, use
`animated-explainer`, `documentary-montage`, or `avatar-spokesperson` instead.

## Runtime Selection (MANDATORY)

Check `video_compose.get_info()["render_engines"]` and present **both**
available runtimes before locking one, per AGENT_GUIDE.md. For this pipeline:

- **Remotion** — best when the piece is mostly stills with spring motion,
  captions, stat cards, and reliable audio mixing.
- **HyperFrames** — best when the piece is type-led, kinetic, or needs HTML/CSS
  motion (title sequences, overlays, web-native looks).
- **FFmpeg** — only for straight cut/concat with no authored motion.

Say what each is best at *for this brief*, the tradeoff, and your
recommendation; wait for approval. Record `render_runtime_selection` in the
`decision_log` with both options. Also lock `composition_mode`
(`templated` | `atelier`) and say which you are choosing.

## Provider Plan

Local generation is the point of this pipeline. Name the plan in the brief:

| Need | Local route | Notes |
|---|---|---|
| Stills | `image_selector` (ComfyUI Qwen/FLUX, Unsloth Qwen) | 8 steps at 768–1024 is enough for b-roll |
| Clips | `video_selector` (MiniMax H3 via Unsloth/ComfyUI, WAN) | H3 minimum is **124 frames (5.2 s)** |
| Image → motion | `unsloth_video` / `comfyui_video` with a first frame | Describe motion, not identity |
| Narration | `tts_selector` (piper offline, or a configured cloud voice) | Decide voice source now |
| Music | `music_library` first, then `music_gen` / a configured API | Never defer this to assets |

State the provider explicitly (Unsloth Studio or ComfyUI). If both are running,
lock one for the run. If neither is available, stop and present setup options
instead of designing shots that cannot be produced.

### Speed is a design constraint

Measured on the reference machine (MiniMax H3, Unsloth Studio): 960×544 / 8
steps ≈ 21 min; 864×480 / 6 steps ≈ 12 min; 1344×768 / 20 steps ≈ 1.5–1.6 h.
ComfyUI is roughly 2–2.5× faster at the same settings. Put this in the brief so
the user chooses a budget knowingly:

- `draft` — 8 steps, 960×544
- `balanced` — 6 steps, 864×480
- `quality` — 20 steps, native size

## Build The Brief

Produce `projects/<id>/artifacts/brief.json` per
`schemas/artifacts/brief.schema.json`. Required: `version`, `title`, `hook`,
`key_points`, `tone`, `style`, `target_platform`, `target_duration_seconds`.

Recommended `metadata` keys:

- `aspect_ratio` (e.g. `16:9`, `9:16`)
- `shot_count_hint` (e.g. 6–10)
- `generation_provider` (`unsloth` | `comfyui`)
- `render_runtime`, `composition_mode`
- `music_plan` (`library:<file>` | `generate:<provider>` | `none`)
- `narration_source` (`tts:<voice>` | `supplied` | `none`)
- `budget_preset` (`draft` | `balanced` | `quality`)

## Quality Gate

- one clear hook and one clear audience,
- aspect, duration, and shot count agree with the delivery promise,
- provider + runtime + composition mode are locked and logged,
- music and narration are decided, not "later",
- every shot the user expects is possible on the chosen provider.

Stop at this gate: the manifest requires human approval for `idea`.
