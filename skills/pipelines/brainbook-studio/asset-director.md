# Asset Director — BrainBook Studio Pipeline

## When To Use

After the shot list is approved. Execute the generation contract scene by
scene, record provenance for every take, and stop at the assets gate with files
that exist on disk.

## Inputs

- `artifacts/scene_plan.json` (generation contract per scene)
- `artifacts/script.json` (narration sections)
- `artifacts/brief.json` (provider, budget preset, music/narration plan)
- `decision_log.json`

## Order Of Work

1. **Stills first** (`image` scenes, reference frames for `i2v`). Cheap, fast,
   and they anchor the look.
2. **Clips** (`video`, then `i2v`) in timeline order. One job at a time — the
   local backend serializes anyway.
3. **Narration** via `tts_selector` if the script needs it; write one file per
   section so the edit can align captions.
4. **Music** from `music_library` when a track fits; otherwise generate a bed.
5. **Subtitles** from the script/audio (`subtitle_gen`).

## Execution Rules

- Use the selectors (`image_selector`, `video_selector`) or the explicit
  provider tools; do not invent a new pipeline of shell commands.
- Local generation is free; if a paid provider is ever used, announce the exact
  tool/model/price and get approval first.
- **Retry policy:** a failed or artifact-riddled shot gets at most 2 retries
  with a changed seed or a clarified prompt. After that, log the substitution
  (a still with motion, an alternate take, or dropping the shot) and surface it
  at the gate.
- **Never silently change provider, model, size, steps, or frame count.** If a
  take requires it, log the change in `decision_log` and say why.
- Keep filenames stable and project-relative:
  `assets/images/sc03.png`, `assets/video/sc03.mp4`, `assets/audio/s02.mp3`.

## Provenance (required)

For every generated asset record: `source_tool`, `provider`, `model`,
`quantization` when known, `seed`, `width`/`height`, `steps`, `frames`/`fps`,
`duration_seconds`, and `prompt`. This is what makes a retake reproducible.

The Studio writes the same fields into its job records; copy them rather than
guessing.

## Artifact

Write `artifacts/asset_manifest.json` per
`schemas/artifacts/asset_manifest.schema.json`.

Required per asset: `id`, `type`, `path`, `source_tool`, `scene_id`.

Useful optional fields: `prompt`, `seed`, `model`, `provider`, `cost_usd`
(0 for local), `duration_seconds`, `resolution`, `format`, `subtype`
(`generated`, `narration`, `music`, `subtitle`), `generation_summary`.

## Quality Gate

- every scene in the plan has a corresponding asset,
- every path exists under `projects/<id>/` and probes cleanly with ffprobe,
- video assets have an audio stream when the plan promised one,
- failed shots and substitutions are named in the manifest/log,
- narration length matches the script within a few hundred milliseconds.

Stop at this gate (human approval is required for `assets`).
