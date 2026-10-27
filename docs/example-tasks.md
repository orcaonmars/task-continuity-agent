# Tasks to try

Start with the [first-file walkthrough](../00-START-HERE.md). Then choose a small task below and replace anything in brackets before sending it.

A useful brief says what you want saved, where the work belongs, how to check it, and what the agent should leave alone. Set the time limit in the app; it is a maximum, so checked work can finish early.

## Make a simple website

> Build a small responsive website for a local repair shop in this task's folder. Include services, opening hours, and a contact section using clearly marked sample details. Keep the source editable and avoid external services. Make the complete first version, check it at desktop and mobile sizes, and fix visible and functional problems. Save README.md with run instructions and a short account of what you checked. Finish with the file paths and any remaining issues. Do not publish anything.

This is a useful first project after the file test. A vision-capable model can inspect screenshots; a text-only model can still work with source, the page structure, and tests, but cannot approve the appearance from images.

## Research a question

> Research [specific question] using the web. Save report.md in this task's folder with a short answer, primary-source links, publication dates, disagreements, and uncertainties. Separate facts from your interpretation. Do not invent citations. Check that each link supports the claim beside it, then finish with the report's path and anything you could not verify.

A narrow question is easier to check than “research everything about this topic.” Web access sends requests to external sites; local model hosting does not make those requests private.

## Clean a copy of a CSV

> Read [full input CSV path]. Preserve that original and save cleaned.csv in this task's folder. Remove exact duplicate rows, keep the existing columns, and flag missing values without guessing replacements. Save summary.md explaining the changes. Verify the before-and-after row counts, column names, duplicates, and missing values. Report anything that needs my decision.

Start with a copy of a small, non-sensitive dataset so you can compare the result yourself.

## Improve an existing code project

> Review the project at [full project folder path]. The problem I want fixed is [specific behavior or error]. Make small reversible changes, preserve unrelated behavior, and run the relevant tests. Record the changed project files as checkpoint deliverables. Save audit.md in this task's folder with the changes, test results, and remaining issues. Do not install dependencies, publish, or push anything without my instruction.

Name any dependencies or changes outside the project that you already allow. File tools keep bounded backups; shell commands and third-party programs need their own backup plan.

## Build a Blender still scene

First enable `capabilities.blender` in your local settings and use a vision-capable model. See [Blender setup](blender.md).

> Build a Blender still scene of [subject] in [style]. Save scene.blend and preview.png in this task's folder. Make a complete rough scene, render a small preview, and inspect it. Improve the composition, scale, materials, and lighting where needed. Save progress at each milestone. Verify the current project and its final preview, then report the file paths and any visible issues that remain.

A saved project is only part of the result: the current rendered preview must also be inspected. Try a still before a long animation.

## Make a short Blender animation

> Create a 10-second Blender animation at 24 fps and 1920×1080. Save editable scene.blend and final.mp4 in this task's folder. Benchmark a short preview before rendering the whole animation. Use a new numbered-frame folder for each scene version, resume only matching existing frames, and encode a contiguous sequence. Inspect samples decoded from the final video. Verify its resolution, frame count, duration, and visible quality, then report any remaining problems.

Video work needs ffmpeg and ffprobe. Leave enough memory for both Blender and the model. New frame folders after scene edits avoid mixing old and new versions.

## Keep working on a saved task

Select the task in the sidebar, write what you want changed, and select **Send follow-up**. For example:

> Keep the existing layout. Make the contact section easier to read on a narrow screen, check the result at mobile size, and update the run instructions if needed. Do not change the sample business details or publish the site.

Use **Resume** to continue saved work without adding a new instruction. It starts a fresh budget from now. A follow-up sent to an active task keeps that task's current deadline; a follow-up to an inactive task gets a fresh budget.

If work was interrupted, inspect the actual files and tracked jobs before retrying. An action may already have finished. Keep a running job and check its status; a changed command needs a new job name.
