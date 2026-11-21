# Script Director — BrainBook Video Pipeline

## Output

Two artifacts:

1. `artifacts/script.json` — the canonical script artifact (schema-valid).
2. `artifacts/lesson_module.json` — the lesson module the renderer consumes.

The module JSON is the contract with `lesson_video`; if it is wrong, the render
fails or the narration drifts off the slides. The shape below is the whole
contract — no extra keys are required.

## Converting a bb_exam module

When `artifacts/bb_exam_source.json` has `kind: "bb_exam"`, the module content is
already authored (title, summary, objectives, blocks, conversation, quiz). Map
it — do not re-research:

| bb_exam field | Lesson module field |
|---|---|
| course/module title | `title` slide (`heading`, `sub` = course · module) |
| `objectives[]` | `objectives` slide; narration reads them as outcomes; put them in `bullets[]` |
| each `blocks[]` | one `content` slide: `heading` = block title, narration = block body, tightened to 25–60 spoken words; short labels in `bullets[]` |
| `conversation[]` | examples/phrasing for narration; instructor turns can become the opening or recap voice |
| `quiz[]` | carry prompts/options only if `answer` indexes are known; otherwise omit `assessment` (the LMS quiz remains authoritative) |
| `summary` | recap narration |

Keep the LMS module title and section order. Do not invent new sections the
source does not support.

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
  "slides": [
    { "kind": "title", "heading": "…", "sub": "…", "narration": "…" },
    { "kind": "objectives", "heading": "What you will be able to do",
      "bullets": ["…", "…"], "narration": "…" },
    { "kind": "content", "heading": "…", "sub": "…",
      "bullets": ["Escape", "Attention"],
      "broll_query": "support worker observing a learner", "narration": "…" },
    { "kind": "recap", "heading": "Recap",
      "bullets": ["…"], "narration": "…" }
  ],
  "assessment": {
    "passingPercent": 75,
    "questions": [ { "q": "…", "options": ["…","…","…","…"], "answer": 2 } ]
  }
}
```

`bullets[]` (≤ 5) is the on-screen text; `narration` is spoken only. A slide with
no narration renders as a still card for ~4.5 s (handy for a quiz-only slide).
`broll_query` is an optional search phrase for stock/generated b-roll; a local
file path in `broll_path` wins when provided.

Do **not** hand-write `durationMs`, `audioSrc` or `captionWords` — `lesson_video`
fills those during TTS/captioning.

## Course Information

Course description, module position, level and passing threshold come from the
Studio source artifact (`artifacts/bb_exam_source.json`), which the renderer
reads automatically. Do not copy those fields into the module by hand; if the
project has no source, the module's own `courseTitle` / `level` /
`moduleIndex` / `moduleTotal` are used.

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
