# Publish Director — BrainBook Video Pipeline

## Output

`artifacts/publish_log.json` and the delivery package.

## Deliverables

| Artifact | Path |
|---|---|
| Lesson video | `projects/<id>/assets/video/<module>.mp4` |
| Module JSON (source of truth) | `projects/<id>/artifacts/lesson_module.json` |
| Script | `projects/<id>/artifacts/script.json` |
| Quiz data | `assessment.questions[]` in the module JSON (and any scored result) |
| Captions | embedded word-level captions in the render; SRT only if requested |

## Naming

`<course-slug>-<module-id>.mp4` (e.g. `understanding-behavior-m01.mp4`). Keep the
module JSON beside it so the lesson can be re-rendered with another voice or
presenter without rewriting the content.

## Delivery

- **Local package**: copy the video + module JSON into the agreed folder and
  record both absolute paths.
- **BrainBook LMS**: the `bb_video` repo ships `scripts/deliver-to-bb-exam.mjs`
  for pushing media into bb_exam. Only use it when the delivery target and
  credentials are explicitly approved; never publish silently.
- **Library**: add the finished video to the project's `renders/` if the user
  asked for a board-visible deliverable.

## Publish Log

Record:

- deliverable paths and sizes,
- module/course identifiers and the quiz passing threshold,
- provider choices used in the final render (voice, presenter, music, b-roll),
- where it was delivered and how (copy, upload, manual),
- any skipped deliverable and why.

## Quality Gate

- the video plays end to end with narration and captions in sync,
- the quiz questions match the narrated content,
- metadata and filenames follow the naming rule,
- the user has explicitly approved publishing to any shared/LMS destination.
