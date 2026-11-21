# Scene Director — BrainBook Video Pipeline

## Output

`artifacts/scene_plan.json` — the slide-by-slide plan that the module JSON and
the asset plan both follow.

Each scene maps 1:1 to a module slide:

```json
{
  "scene_id": "s02",
  "kind": "content",
  "heading": "Four Functions",
  "sub": null,
  "narration_words": 52,
  "estimated_seconds": 21.0,
  "on_screen_text": ["Escape", "Attention", "Tangible", "Sensory"],
  "notes": "one concept per bullet; no full sentences on screen"
}
```

## Rules

- **One idea per slide.** If the narration needs "and also", split the slide.
- **On-screen text is not the script.** Keep it to ≤ 8 words or 4 short labels;
  the narration carries the explanation.
- **Kinds alternate deliberately.** `title` → `objectives` → `content…` → `recap`.
  Do not open with a content slide or end without a recap.
- **Timing is narration-driven.** Estimate seconds from words at ~2.5 words/sec
  plus 0.4 s of breathing room; the lesson renderer replaces the estimate with
  the real audio duration.
- **Brand stays consistent.** Accent color, course label and module position come
  from the brief and appear on the title slide.

## Slide Budget

| Runtime target | Slides |
|---|---|
| 3–4 min | 5–6 |
| 5–7 min | 7–9 |
| 8–10 min | 10–12 |

Longer than that is two modules, not one.

## Quality Gate

- every scene has a kind, heading, narration reference and duration estimate,
- the on-screen text is shorter than the narration by design,
- the ordering reads as one continuous lesson,
- the scene plan and `lesson_module.json` have the same slide count and order.
