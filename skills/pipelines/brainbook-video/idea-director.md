# Idea Director — BrainBook Video Pipeline

## When To Use

Use this pipeline when the deliverable is a **BrainBook lesson video**: a branded,
narrated module with slides, word-level captions, an optional presenter, and a
quiz — rendered by the `bb_video` Remotion project. It is the right pipeline for
training modules, onboarding lessons, and course content, not for cinematic
trailers or ad-hoc social clips.

## Runtime

Lock `render_runtime = "remotion"`. The renderer is the external `bb_video`
project (`scripts/build-lesson.sh` → Remotion composition `BrainBookLesson`);
HyperFrames (lowercase `hyperframes` in the decision log) is not a viable
runtime for this pipeline — there is no HTML counterpart to the lesson
composition. Per AGENT_GUIDE.md, say so explicitly and record a
`render_runtime_selection` decision with hyperframes as
`rejected_because: "BrainBook Video renders through the bb_video Remotion project"`.

## Reference Inputs

- `bb_video/src/data/bb_exam/m01.json` — the module JSON shape (read it before scripting)
- `bb_video/README.md` and `scripts/build-lesson.sh` — renderer contract and env knobs
- `skills/creative/storytelling.md`

## Lesson Source (check before anything else)

If `artifacts/bb_exam_source.json` exists, the lesson is **not** starting from a
blank page:

- `kind: "bb_video_module"` — `artifacts/lesson_module.json` is already written
  from an existing bb_video lesson. Confirm with the user whether this is a
  **remaster** (same slides/narration, new voice/presenter/music) or a revision
  (edit narration/slides before rendering). Do not rewrite it from scratch
  without approval.
- `kind: "bb_exam"` — the source is a published bb_exam module (title, summary,
  objectives, blocks, conversation, quiz). Treat those as the approved content:
  convert them to slides and narration in the script stage (see the script
  director), do not research the topic again. The public payload omits quiz
  answer keys; keep the LMS quiz authoritative and omit the module `assessment`
  unless the answers are supplied.

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
| Voice | `kokoro` / `af_heart` (local) | `edge`, `elevenlabs`, `openai`, `azure` |
| Presenter | `kenburns` (still portrait, no lip sync) | `placeholder`, `sadtalker_gpu` (Windows GPU service), `heygen`, `d_id` |
| Captions | `auto` (word-level) | `none`, `fast` |
| Music | `none` | `acestep` (local), `elevenlabs`, `fal` |
| B-roll | `none` | `wan` (local), `replicate`, `fal`; `t2v`/`i2v` |

Only promise a provider that is actually available. `bb_video` silently falls
back to a placeholder presenter when a GPU/cloud avatar backend is unavailable —
surface that risk now, not after the render.

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
