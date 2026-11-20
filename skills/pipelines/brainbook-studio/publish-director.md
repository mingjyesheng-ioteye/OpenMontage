# Publish Director — BrainBook Studio Pipeline

## When To Use

After the render report is verified. Package the deliverable so it can be
handed to the user without additional digging.

## Inputs

- `artifacts/render_report.json` (final file, verification notes)
- `artifacts/brief.json` (title, platform, aspect, duration)
- `artifacts/asset_manifest.json` (generation stack for provenance)

## Process

1. Run `export_bundle` with the final video path and project name. It copies the
   master and writes a manifest; do not hand-roll a zip.
2. Include, when available: the master mp4, captions (`.srt`/`.vtt`), a poster
   frame extracted at a representative moment, and a short `README`/metadata
   block.
3. Record the generation stack: provider(s), model(s) + quantization, steps,
   seeds, aspect, duration, and the render runtime. This is the provenance users
   need to reproduce or extend a shot.
4. Name files predictably: `<project-id>-master.mp4`, `<project-id>.srt`,
   `<project-id>-poster.png`.

## Artifact

Write `artifacts/publish_log.json` per
`schemas/artifacts/publish_log.schema.json`.

Required: `version`, `entries`. Each entry names the exported file, its type,
size, and destination. Add a `metadata` object with title, duration, aspect,
platform, generation stack, and render runtime.

## Quality Gate

- the export directory contains the master and every promised companion file,
- filenames and titles match the brief,
- provenance is present and accurate,
- no intermediate/cache files are included.

Stop at this gate (human approval is required for `publish`).
