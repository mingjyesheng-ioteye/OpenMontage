# Scene Director — BrainBook Studio Pipeline

## When To Use

After the script is approved. Turn the copy into a **shot list** that an
operator could hand to the Studio and run without guessing. This is the stage
that makes the render predictable; vague shots are the main cause of wasted GPU
hours.

## Inputs

- `artifacts/brief.json` (aspect, duration, shot count, provider, budget preset)
- `artifacts/script.json` (sections and timings)
- `decision_log.json` (runtime, composition mode)

## Shot Contract

Every scene must be generateable as written:

| Field | Required | Notes |
|---|---|---|
| `generator` | yes | `image` (still), `video` (text→video), `i2v` (first frame) |
| `provider` | yes | `unsloth` or `comfyui` — one per run |
| `model` | yes | e.g. `unsloth/Qwen-Image-2.1-Turbo-GGUF`, `unsloth/MiniMax-H3-GGUF` |
| `prompt` | yes | subject → action → camera → light → mood → audio |
| `size` | yes | multiple of the family grid (H3/Qwen: 32) |
| `steps` | yes | `draft` 8, `balanced` 6, `quality` 20 |
| `frames` | video | H3 requires `17k+5`, minimum **124** (~5.2 s at 24 fps) |
| `seed` | yes | record it; reuse for continuity |
| `reference` | i2v | project-relative path to the first frame |
| `audio_note` | video | describe dialogue/SFX/ambience the model should synthesize |

## Prompt Structure (images and clips)

Write prompts in this order and keep them under ~60 words for a single shot:

1. **Subject** — who/what, with the one distinguishing detail.
2. **Action** — what happens in this shot (i2v: motion only).
3. **Camera** — framing + movement (`slow push-in`, `locked wide`, `handheld`).
4. **Light** — source, direction, quality (`soft window light from the left`).
5. **Mood/look** — palette, texture, lens (`muted teal, 35mm, light grain`).
6. **Audio** (video) — `ambient rain, distant traffic` or quoted dialogue.

For **image-to-video**, the first frame already carries identity, wardrobe, and
set. The prompt should describe only what changes: `she turns toward camera and
smiles, hair moves slightly`. Re-describing the subject invites drift.

## Budget And Continuity

- Choose one preset for the run and state it: `draft` (8 steps, 960×544),
  `balanced` (6 steps, 864×480), `quality` (20 steps, native).
- H3 cannot render fewer than 124 frames. If the brief needs a 2 s beat, plan a
  trim in the edit, not a shorter generation.
- Keep a **style anchor** (same seed family, same palette phrase, or one
  reference still) across shots that must feel continuous.
- Do not plan more shots than the budget allows. A 20-shot list at 20 steps is
  a multi-hour render; say so before approval.

## Artifact

Write `artifacts/scene_plan.json` per `schemas/artifacts/scene_plan.schema.json`.
Required per scene: `id`, `type`, `description`, `start_seconds`, `end_seconds`.
Also set `style_playbook` at the top level.

Store the generation contract under `metadata` or a per-scene
`generation` object so the asset stage can execute it without re-deriving:

```json
{
  "id": "sc03",
  "type": "video",
  "description": "Rain on the alley floor, neon reflections",
  "start_seconds": 6.0,
  "end_seconds": 11.2,
  "generation": {
    "provider": "unsloth",
    "mode": "video",
    "model": "unsloth/MiniMax-H3-GGUF",
    "prompt": "rain hitting a neon-lit alley floor, reflections ripple, slow tilt down, cinematic",
    "width": 960, "height": 544, "steps": 8, "frames": 124, "seed": 4211,
    "reference": null
  }
}
```

## Quality Gate

- the timeline is continuous (no gaps/overlaps) and matches the script,
- every scene names its generator, model, size, steps, frames, and seed,
- i2v prompts describe motion only and point at an existing reference,
- the total number of shots and steps fits the agreed budget,
- nothing depends on a provider that is not running.

Stop at this gate (human approval is required for `scene_plan`).
