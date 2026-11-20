# Script Director — BrainBook Video Pipeline

## Output

Two artifacts:

1. `artifacts/script.json` — the canonical script artifact (schema-valid).
2. `artifacts/lesson_module.json` — the bb_video module the renderer consumes.

The module JSON is the contract with `bb_video`; if it is wrong, the render
fails or the narration drifts off the slides. Copy the shape from
`bb_video/src/data/bb_exam/m01.json`.

## Module Schema

```json
{
  "id": "m01",
  "brand": "Arc Mercer — Center of Excellence",
  "courseTitle": "Understanding Behavior",
  "moduleTitle": "Functions and Causes of Behavior",
  "level": "Entry (E)",
  "moduleIndex": 1,
  "moduleTotal": 7,
  "presenter": { "mode": "none", "layout": "pip-br", "name": "Avery Quinn",
                 "role": "Training Instructor", "accentColor": "#FFD166" },
  "voice": "af_heart",
  "voiceProvider": "kokoro",
  "slides": [
    { "kind": "title", "heading": "…", "sub": "…", "narration": "…" },
    { "kind": "objectives", "heading": "…", "narration": "…" },
    { "kind": "content", "heading": "…", "narration": "…" },
    { "kind": "recap", "heading": "…", "narration": "…" }
  ],
  "assessment": {
    "passingPercent": 75,
    "questions": [ { "q": "…", "options": ["…","…","…","…"], "answer": 2 } ]
  }
}
```

Do **not** hand-write `durationInFrames`, `audioSrc` or `captions` — `bb_video`
fills those during TTS/captioning.

## Narration Rules

- Spoken copy, second person, one idea per slide.
- 25–60 words per content slide (~10–24 s at the default rate).
- Do not read the heading verbatim; add the explanation the slide cannot show.
- Spell out numbers and acronyms the first time they are spoken.
- Open with a welcome that names the course and module; close with a recap that
  restates the outcome, not a list of topics.

## Slide Kinds

| Kind | Purpose | Typical count |
|---|---|---|
| `title` | course/module identity | 1 |
| `objectives` | what the learner will do | 1 |
| `content` | one concept each | 3–6 |
| `recap` | close the loop | 1 |

Aim for 6–10 slides; 8 is a good default for a 5–7 minute module.

## Assessment

- 10 questions, four options each, exactly one correct `answer` index.
- Questions test the narration, not trivia the lesson never covers.
- `passingPercent` defaults to 75.

## Quality Gate

- module JSON parses and has `id`, `slides[]` (non-empty), `assessment.questions[]`,
- every content slide's narration is speakable in 25–60 words,
- the recap and quiz agree with the body,
- `artifacts/script.json` validates and mirrors the slide narration.
