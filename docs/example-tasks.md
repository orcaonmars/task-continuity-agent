# Task examples

Replace bracketed paths and requirements before submitting. Set the time limit separately in the interface. These examples define deliverables and boundaries; adjust them to the project and permissions you intend to grant.

## Review an existing project

> Review [full project folder path] for reliability defects. Use read-only source inspection; do not edit project files, run project code, install dependencies, or publish changes. Save findings.md in this task's folder with prioritized findings, file references, supporting evidence, and suggested fixes. Distinguish confirmed defects from possible risks and note any checks that require execution. Report the saved path when finished.

This review permits reading the source and writing the report in the task folder. It does not authorize project changes or execution.

## Implement a specific fix

> Fix [specific behavior or error] in the project at [full project folder path]. Preserve unrelated behavior and make small reversible changes. Run [approved test command] and inspect the result. Record changed project files as checkpoint deliverables. Save audit.md in this task's folder with the changes, verification results, and remaining issues. Do not install dependencies, publish, or push changes.

State separately if dependency installation or changes outside the project are allowed. File tools retain bounded backups; shell commands and third-party programs need their own backup strategy.

## Build a website

> Build a responsive website for a local repair shop in this task's folder. Include services, opening hours, and a contact section with clearly marked sample details. Keep the source editable and avoid external services. Check desktop and mobile layouts, correct functional and visible defects, and save README.md with run instructions and verification results. Report the output paths and remaining issues. Do not publish the site.

Screenshot review requires a model and backend that accept images. Text-only models can inspect source, page structure, and tests, but cannot approve visual appearance from screenshots.

## Research a question

> Research [specific question] using the web. Save report.md in this task's folder with a concise answer, primary-source links, publication dates, disagreements, and uncertainties. Separate facts from interpretation. Check that each citation supports its associated claim. Report any claims you could not verify.

Web research contacts external sites even when the model runs locally.

## Process a CSV

> Read [full input CSV path] and preserve the original. Save cleaned.csv in this task's folder. Remove exact duplicate rows, retain the existing columns, and flag missing values without inferring replacements. Save summary.md describing the changes. Verify the before-and-after row counts, column names, duplicates, and missing values. Identify any unresolved decisions.

## Create a Blender still scene

Enable `capabilities.blender` and use an image-capable model for visual inspection. See [Blender setup](blender.md).

> Create a Blender still scene of [subject] in [style]. Save scene.blend and preview.png in this task's folder. Build a complete rough scene, render a preview, and inspect composition, scale, materials, and lighting. Correct visible defects and checkpoint each milestone. Verify the current project and its final preview, then report remaining quality limitations.

Saving the project does not establish visual completion. Inspect the current rendered preview.

## Render a Blender animation

> Create a 10-second Blender animation at 24 fps and 1920×1080. Save editable scene.blend and final.mp4 in this task's folder. Benchmark a short preview before the full render. Use a new numbered-frame folder for each scene version, resume only matching frames, and encode a contiguous sequence. Inspect samples decoded from the final video. Verify resolution, frame count, duration, and visible quality. Report remaining problems.

Video encoding and verification require ffmpeg and ffprobe. Rendering settings must leave memory for the model. Separate frame folders prevent mixing scene versions.

## Follow-up instructions

Select an existing task and use **Send follow-up** to refine it. For example:

> Preserve the current layout and sample business details. Improve the contact section at mobile widths, verify the result, and update the run instructions if necessary. Do not publish the site.

An active task retains its deadline; an inactive task receives a fresh budget. **Resume** continues saved work without adding an instruction and starts a fresh budget from now.

After an interruption, inspect the actual files and tracked jobs before retrying. An action may already have completed. Poll an existing running job instead of relaunching it; a changed command needs a new job name.
