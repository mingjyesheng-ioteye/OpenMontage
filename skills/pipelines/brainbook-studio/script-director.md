# Script Director — BrainBook Studio Pipeline

## When To Use

After the brief is approved. This stage writes the spoken and on-screen copy
that the shots will carry — not a screenplay, not a treatment. If the piece is
music-only, produce a minimal script that records that decision and the on-screen
text, then move on.

## Inputs

- `artifacts/brief.json` (hook, tone, duration, platform, aspect, voice plan)
- `decision_log.json` (runtime, provider, composition mode)

## Process

1. **One idea per section.** A section is a shot-sized beat, typically 3–8 s.
   Two ideas in one sentence become two sections.
2. **Write for the ear.** Short clauses, concrete nouns, active verbs. Read it
   aloud mentally at the target duration.
3. **Map sections to shots.** Give each section an `id` (`s01`, `s02`, …) that
   the scene plan can reference. The scene plan must not invent copy.
4. **On-screen text is separate.** Keep captions/titles under ~8 words and note
   them in `speaker_directions` or `delivery_cues`, not in the narration text.
5. **Respect the voice plan.** If narration is TTS, record the voice and pace in
   `voice_performance`; if supplied, note that no TTS runs; if none, say so.

## Artifact

Write `artifacts/script.json` per `schemas/artifacts/script.schema.json`.
Required: `version`, `title`, `total_duration_seconds`, `sections`.

Each section needs `id`, `text`, `start_seconds`, `end_seconds`. Keep the
timeline continuous: `start` of the next equals `end` of the previous unless
there is a deliberate beat of silence.

Optional but useful per section: `label`, `speaker_directions`,
`delivery_cues` (pace/emphasis), `pronunciation_guides` for names.

## Quality Gate

- total duration within ±10% of the brief,
- every section maps to a shot-sized beat,
- copy is speakable at the chosen pace,
- no line depends on visuals that the provider plan cannot make,
- narration source and voice are explicit.

Stop at this gate (human approval is required for `script`).
