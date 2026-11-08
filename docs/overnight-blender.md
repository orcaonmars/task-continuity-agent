# Give it a video prompt before bed

The idea is to describe the video you want, leave the agent working, and come back to an editable Blender project and a preview. It can also render the final video when the scene and remaining time allow. This uses the existing task runner; this preset does not add a scheduler or guarantee a finished video by morning.

## Before the first run

Load your tool-capable Qwen model in LM Studio and start its local server. Enable `web`, `blender`, and `media` under `capabilities` in your configuration. Blender, ffmpeg, and ffprobe must be available; run `scripts/doctor.ps1` to check them. See [Blender setup](blender.md). Background Blender jobs do not need the live bridge add-on.

If your Qwen model accepts images and your server supports them, enable `vision` for render inspection. A text-only model can build and render the scene, but it cannot judge the actual pictures. In that case, review the preview yourself before trusting the visual result.

Keep the PC powered and awake for the run. Leave memory available for Qwen while Blender renders.

## Each night

1. Open the agent and select **New task → Make a Blender video**.
2. Replace the bracketed brief, duration, resolution, and frame rate. Describe the subject, style, camera movement, and anything that must appear. Say whether scene preparation matters more than a full render.
3. Set the time limit to the time you want to allow, then send it. You can close the browser tab while the app continues running.
4. In the morning, open the task folder. Check `handoff.md`, open `scene.blend`, and review `preview.png` or `final.mp4` if present. Use a follow-up to fix anything you dislike.

The preset is an editable draft. Selecting it does not start a render or change the time limit.

## What it asks the agent to do

- Search for suitable free assets, use official downloads, and record source, creator, and stated licence in `assets.md`. Account-gated downloads may need your intervention, so the prompt asks for alternatives instead of getting stuck waiting on them.
- Build a complete rough scene early. Use procedural substitutes when a specific download is unavailable and record the substitution.
- Check imported scale, geometry, materials, and texture paths. Save the editable scene and pack supported resources; retain other required files in the task folder.
- Set up camera movement, animation, lighting, and materials. Render a preview and benchmark rendering before attempting the full sequence.
- Preserve numbered frames and checkpoint progress. If it cannot finish in time, leave usable partial work and continuation instructions rather than claiming completion.

Asset searches, downloads, scene creation, and animation depend on the model and the brief. This is a workflow prompt over existing tools, not a separately validated autonomous video pipeline. Start with a short scene to learn what your model and hardware handle, then increase the scope based on the actual result.

## A brief you can adapt

```text
A cinematic tracking shot through a rainy abandoned greenhouse at night.
Wet leaves, broken glass, warm light from a small lantern, and gentle fog.
The camera moves slowly forward. No characters, speech, or text overlays.
10 seconds, 1280 x 720, 24 fps.
Prioritize a coherent editable scene and a useful preview over a costly final render.
```

Add this description to the preset's brief; keep the rest of its asset, checkpoint, and output instructions. The example is deliberately bounded so that you can assess the workflow before attempting a longer film.
