# Idea Director — BrainBook Video Pipeline

## When To Use

Use this pipeline when the deliverable is a **BrainBook lesson video**: a branded,
narrated module with slides, word-level captions, an optional presenter, and a
quiz — rendered by OpenMontage's own `lesson_video` tool. It is the right
pipeline for training modules, onboarding lessons, and course content, not for
cinematic trailers or ad-hoc social clips.

## Runtime

Lock `render_runtime = "remotion"`. The renderer is OpenMontage-native: the
`lesson_video` tool synthesizes narration with an OpenMontage voice provider and
renders the `LessonVideo` composition in `remotion-composer/`. HyperFrames
(lowercase `hyperframes` in the decision log) is not a viable runtime for this
pipeline — there is no HTML counterpart to the lesson deck. Per AGENT_GUIDE.md,
say so explicitly and record a `render_runtime_selection` decision with
hyperframes as `rejected_because: "the lesson deck exists only as the Remotion
LessonVideo composition"`.

## Reference Inputs

- `skills/pipelines/brainbook-video/script-director.md` — the lesson module
  contract (read it before scripting)
- `tools/video/lesson_video.py` — renderer inputs, timeline and caption modes
- `skills/creative/storytelling.md`

## Lesson Source (check before anything else)

If `artifacts/bb_exam_source.json` exists, the lesson is **not** starting from a
blank page. The Studio writes this when the user picks a course module:

- `kind: "bb_exam"` — the source is a published bb_exam module (title, summary,
  objectives, blocks, conversation, quiz). Treat those as the approved content:
  convert them to slides and narration in the script stage (see the script
  director), do not research the topic again. The public payload omits quiz
  answer keys; keep the LMS quiz authoritative and omit the module `assessment`
  unless the answers are supplied.

If the project already contains `artifacts/lesson_module.json` (supplied
manually), treat it as an existing lesson: confirm with the user whether this is
a **remaster** (same slides/narration, new voice/presenter/music) or a revision
before changing it.

Record the source in the brief (`source_kind`, `source_course`, `source_module`).

## Process

### 1. Establish the course context

Capture:

- course title, module title, module index and total (`m01`, 1 of 7),
- audience and level (Entry / Intermediate / Advanced),
- the one outcome the learner should leave with,
- target runtime (3–8 minutes is typical; ~45–60 s per content slide),
- whether a scored quiz travels with the lesson.

### 2. Choose the production plan honestly

Record these choices in the brief — they become provider inputs at compose:

| Choice | Default | Alternatives |
|---|---|---|
| Voice | `piper` / `en_US-lessac-medium` (local, free) | `azure` (`en-US-AndrewMultilingualNeural`), `elevenlabs`, `openai` |
| Presenter | `pip-br` still portrait | `side-right`, `full`, `none`; lip-sync is **not** supported here |
| Captions | `auto` (word-level) | `none` |
| Music | `none` | any music bed file at ≤ 0.15 volume |
| B-roll | `none` | not supported by the lesson renderer |

Only promise a provider that is actually available. Local piper is always the
safe default; cloud voices need their key in the OpenMontage `.env`. Lip-synced
avatar video is a different pipeline (`avatar-spokesperson`) — say so now, not
after the render.

### 3. Build the brief

Recommended metadata keys:

- `course_title`, `module_title`, `module_index`, `module_total`, `level`
- `runtime_target_seconds`, `slide_budget`
- `voice_plan`, `avatar_plan`, `music_plan`, `broll_plan`, `captions_plan`
- `quiz_required`, `passing_percent`
- `missing_capabilities`

### 4. Quality Gate

- course/module identity and position are explicit,
- runtime and slide budget are bounded,
- every provider choice is available or explicitly marked as a fallback,
- the brief names the module JSON as the script deliverable.
