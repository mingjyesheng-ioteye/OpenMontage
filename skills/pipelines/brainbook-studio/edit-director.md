# Edit Director — BrainBook Studio Pipeline

## When To Use

After assets pass the gate. Turn the shot list and takes into a cut list the
composer can execute without interpretation.

## Inputs

- `artifacts/scene_plan.json` (intended timeline)
- `artifacts/asset_manifest.json` (what actually exists)
- `artifacts/script.json` (narration + on-screen text)
- `artifacts/brief.json` + `decision_log.json` (locked runtime and mode)

## Process

1. **Reconcile plan vs reality.** Any scene without a usable take is either
   replaced by a logged substitute or cut. Say which in `reason`.
2. **Carry the locked choices forward.** `render_runtime` and
   `composition_mode` come from the brief decision. Do not change them here; if
   the runtime is now unavailable, stop and escalate (no silent swap).
3. **Build one cut per shot.** Use the asset path or manifest ID as `source`,
   and trim `in_seconds`/`out_seconds` to the best part of the take. Generated
   clips often have a weak first/last half-second.
4. **Transitions serve meaning.** Hard cuts for energy and shot-to-shot
   continuity; a fade only at a real chapter change. Keep durations short
   (0.2–0.5 s) unless the brief asks for a slow dissolve.
5. **Audio plan.** Place narration per section, set a music bed with ducking
   under speech, and leave headroom. Record levels/ducking in `audio`.
6. **Captions.** Burn or sidecar per the brief; align to the spoken phrase, not
   the section start. Use the transcriber when word timing matters.

## Artifact

Write `artifacts/edit_decisions.json` per
`schemas/artifacts/edit_decisions.schema.json`.

Required: `version`, `cuts`, `render_runtime`.

Each cut requires `id`, `source`, `in_seconds`, `out_seconds`. Recommended:
`speed`, `transition_in`/`transition_out`, `transition_duration`, `reason`.

`audio` should describe narration tracks, music file, and ducking; `subtitles`
should say `burn` or `sidecar` and the style.

## Quality Gate

- every cut references a file that exists,
- total duration matches the brief within ±10%,
- runtime/mode match the locked decision,
- narration and music levels are specified,
- captions are planned (or explicitly declined).

No human gate on this stage — proceed to compose, but keep the decisions
inspectable.
