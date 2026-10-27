# Example tasks

Choose a concrete result and a time budget in the interface. Replace bracketed text before sending. A budget is a maximum: finish when the work is verified.

## Research and files

> Research [topic] using the web. Save report.md with primary-source links, publication dates, findings, disagreements, and uncertainties. Do not invent citations. Checkpoint progress and verify the final report.

> Clean [input CSV path] into cleaned.csv and write summary.md explaining the changes. Preserve the original. Verify row counts, columns, duplicates, and missing values before finishing.

## Local code project

> Audit the project at [project folder]. Prioritize reliability, then effectiveness, then simplicity. Make small reversible fixes and preserve behavior. Run appropriate tests. Checkpoint changed external project files as deliverables. Save audit.md with changes, test evidence, and remaining limitations. Do not publish or push anything.

Specify whether dependency installation and changes outside the folder are allowed. File-tool edits retain backups; shell commands and third-party tools need their own backup discipline.

## Blender still scene

Enable `capabilities.blender` and load a vision-capable model first.

> Build and refine a Blender still scene of [subject], in [style]. Save scene.blend and preview.png. Start with the complete rough scene, render a small preview, inspect defects, and iterate on composition, scale, materials, and lighting. Checkpoint each milestone. Verify the current project and final preview, then record honest quality limitations.

A project save alone does not pass visual completion. The actual current preview must be inspected and quality-reviewed.

## Blender animation

> Create a 10 second Blender animation at 24 fps and 1920x1080. Save editable scene.blend and final.mp4. Benchmark a short preview before full rendering. Render numbered frames into a new folder for each scene version, resume matching existing frames, encode a contiguous sequence, and inspect samples decoded from the exact final video. Checkpoint progress and verify resolution, frame count, duration, and visible quality.

Use settings that fit alongside the model in memory. A new output folder after a scene edit prevents mixing versions. ffmpeg and ffprobe are required for encoding and verification.

## Follow-up and recovery

Select an existing task to refine it. Follow-ups keep the original brief and archive the prior completion. Resume a paused or blocked task after resolving its cause; enter the additional time budget. Inspect uncertain interrupted jobs before retrying, and use a new job name for a changed command.

Do a small smoke task first: create a text file, read it back, and finish with verification. Try a short version of your real task before an overnight run.
