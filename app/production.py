"""Resumable frame rendering, video encoding and evidence-backed visual review."""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from PIL import Image, ImageStat, ImageDraw
from common import (
    task_dir,
    atomic_json,
    read_json,
    NO_WINDOW,
    file_sha256,
    find_blender,
    resolve_path,
    capability_enabled,
)
from work_tools import writable, start_job, write_job_script


def _media_executable(name):
    discovered = shutil.which(name)
    if discovered:
        return discovered
    if __import__("os").name == "nt":
        fallback = Path(r"C:\ffmpeg\bin") / (name + ".exe")
        if fallback.is_file():
            return str(fallback)
    raise RuntimeError(
        f"{name} is required for this media operation but was not found on PATH."
    )


def _ratio(value, fallback=0.0):
    try:
        numerator, denominator = str(value).split("/", 1)
        denominator = float(denominator)
        return float(numerator) / denominator if denominator else fallback
    except (TypeError, ValueError):
        return fallback


def image_record(path):
    p = resolve_path(path)
    return {"path": str(p), "sha256": file_sha256(p), "observed_at": time.time()}


def record_observation(path):
    observations = read_json(task_dir() / "visual-observations.json", [])
    observations.append(image_record(path))
    atomic_json(task_dir() / "visual-observations.json", observations[-100:])


def quality_review(
    images: list[str], criteria: list[dict], remaining_limitations: list[str]
) -> str:
    """Record a visual quality gate after view_image on actual renders/contact sheets.
    criteria needs >=3 objects {criterion:string,passed:bool,evidence:string}. Be critical.
    Failed criteria prevent completion until fixed/reviewed. List honest remaining limitations.
    This records model judgment, not a guarantee of human artistic satisfaction."""
    if not images or len(criteria) < 3:
        raise ValueError(
            "Inspect actual images, then provide at least 3 concrete criteria."
        )
    observed = read_json(task_dir() / "visual-observations.json", [])
    records = []
    for path in images:
        current = image_record(path)
        if not any(
            x["path"] == current["path"] and x["sha256"] == current["sha256"]
            for x in observed
        ):
            raise ValueError(
                "Call view_image on this exact current image before reviewing it: "
                + path
            )
        with Image.open(resolve_path(path)) as im:
            if max(ImageStat.Stat(im.convert("RGB")).stddev) < 2:
                raise ValueError(
                    "Preview is effectively blank; diagnose before quality review."
                )
        records.append(current)
    for c in criteria:
        if (
            not c.get("criterion")
            or not isinstance(c.get("passed"), bool)
            or len(c.get("evidence", "")) < 12
        ):
            raise ValueError(
                "Each criterion needs a name, boolean passed and specific visible evidence."
            )
    report = {
        "images": records,
        "criteria": criteria,
        "passed": all(x["passed"] for x in criteria),
        "remaining_limitations": remaining_limitations,
        "reviewed_at": time.time(),
    }
    atomic_json(task_dir() / "quality-review.json", report)
    return json.dumps(report, ensure_ascii=False)


def render_sequence(
    project: str,
    job_name: str,
    output_folder: str,
    first_frame: int,
    last_frame: int,
    width: int = 1920,
    height: int = 1080,
    samples: int = 32,
    engine: str = "EEVEE",
) -> str:
    """Render numbered PNG frames with resume: skip valid existing PNGs of matching size.
    Use a NEW output folder after scene edits so frames never mix different scene versions.
    Benchmark previews before full rendering. One background render job at a time on this GPU.
    Jobs allow up to 72 hours, bounded by the current task deadline.
    engine is BLENDER_EEVEE or CYCLES (CPU for shared VRAM reliability). jobs(status) reads progress."""
    if not capability_enabled("blender"):
        raise RuntimeError("Blender capability is disabled in config.json.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,70}", job_name):
        raise ValueError("Invalid job_name.")
    p = resolve_path(project)
    out = writable(output_folder)
    if not p.is_file() or p.suffix.lower() != ".blend":
        raise ValueError("Existing .blend needed.")
    engine = str(engine).upper()
    if engine not in ("EEVEE", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "CYCLES"):
        raise ValueError("engine must be EEVEE or CYCLES.")
    if not 1 <= int(first_frame) <= int(last_frame) <= 100000:
        raise ValueError("Invalid frame interval.")
    width = max(64, min(int(width), 7680))
    height = max(64, min(int(height), 4320))
    out.mkdir(parents=True, exist_ok=True)
    identity = {
        "project": str(p),
        "sha256": file_sha256(p),
        "width": width,
        "height": height,
        "samples": int(samples),
        "engine": engine,
    }
    prior = read_json(out / "render-version.json")
    if prior is not None and prior != identity:
        raise ValueError(
            "Scene/settings changed. Use a new output folder to avoid mixing versions."
        )
    if prior is None and any(out.glob("*.png")):
        raise ValueError(
            "Existing frames have no valid render-version.json. Use a new output folder."
        )
    atomic_json(out / "render-version.json", identity)
    code = """import bpy,time,struct
from pathlib import Path
s=bpy.context.scene
requested=ENGINE
if requested=='CYCLES':
    s.render.engine='CYCLES'
else:
    supported=[item.identifier for item in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items]
    eevee=next((name for name in ('BLENDER_EEVEE_NEXT','BLENDER_EEVEE') if name in supported),None)
    if eevee is None:raise RuntimeError('This Blender build exposes no supported EEVEE render engine.')
    s.render.engine=eevee
s.render.resolution_x=WIDTH;s.render.resolution_y=HEIGHT;s.render.resolution_percentage=100
s.render.image_settings.file_format='PNG';s.render.image_settings.color_mode='RGB'
if s.render.engine=='CYCLES':
    s.cycles.device='CPU';s.cycles.samples=SAMPLES;s.cycles.use_denoising=True
elif hasattr(s,'eevee') and hasattr(s.eevee,'taa_render_samples'):
    s.eevee.taa_render_samples=SAMPLES
folder=Path(FOLDER)
for frame in range(FIRST,LAST+1):
    file=folder/f'{frame:06d}.png'
    if file.exists():
        try:
            with file.open('rb') as existing:header=existing.read(24)
            if header[:8]==b'\\x89PNG\\r\\n\\x1a\\n' and struct.unpack('>II',header[16:24])==(WIDTH,HEIGHT):
                image=bpy.data.images.load(str(file),check_existing=False)
                try:
                    pixel=image.pixels[-1]
                    valid=tuple(image.size)==(WIDTH,HEIGHT) and len(image.pixels)==WIDTH*HEIGHT*image.channels
                finally:bpy.data.images.remove(image)
                if valid:print(f'FRAME_SKIPPED:{frame}',flush=True);continue
        except Exception:pass
    start=time.time();s.frame_set(frame);s.render.filepath=str(file)
    bpy.ops.render.render(write_still=True)
    print(f'FRAME_DONE:{frame}:seconds={time.time()-start:.3f}',flush=True)
print('RENDER_SEQUENCE_COMPLETE',flush=True)
"""
    replacements = {
        "ENGINE": repr(engine),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "SAMPLES": str(max(1, min(int(samples), 512))),
        "FOLDER": repr(str(out)),
        "FIRST": str(int(first_frame)),
        "LAST": str(int(last_frame)),
    }
    code = re.sub(
        r"\b(" + "|".join(replacements) + r")\b", lambda m: replacements[m[0]], code
    )
    scripts = task_dir() / "Scripts"
    scripts.mkdir(exist_ok=True)
    script = scripts / ("frames-" + job_name + ".py")
    write_job_script(script, code)
    return json.dumps(
        start_job(
            job_name,
            [
                find_blender(),
                "-b",
                "--factory-startup",
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                str(p),
                "--python",
                str(script),
            ],
            timeout=259200,
        )
    )


def encode_video(
    frame_folder: str,
    output: str,
    job_name: str,
    first_frame: int,
    last_frame: int,
    fps: int = 24,
    crf: int = 18,
) -> str:
    """Encode contiguous verified numbered PNG frames to silent H.264 MP4. No missing frames allowed.
    Uses ffmpeg as a tracked job; await jobs(status). Output must be a new .mp4 path."""
    folder = resolve_path(frame_folder)
    out = writable(output)
    previous = read_json(task_dir() / "Jobs" / job_name / "job.json", {})
    same_output = bool(previous.get("argv") and previous["argv"][-1] == str(out))
    if out.suffix.lower() != ".mp4" or (out.exists() and not same_output):
        raise ValueError("Choose a new .mp4 path.")
    if not 1 <= first_frame <= last_frame or not 1 <= fps <= 120:
        raise ValueError("Invalid frame interval/fps.")
    size = None
    for frame in range(first_frame, last_frame + 1):
        p = folder / f"{frame:06d}.png"
        with Image.open(p) as im:
            im.verify()
        with Image.open(p) as im:
            if size and im.size != size:
                raise ValueError("Frames have mixed dimensions.")
            size = im.size
    if any(x % 2 for x in size):
        raise ValueError("H.264 dimensions must be even.")
    exe = _media_executable("ffmpeg")
    args = [
        exe,
        "-nostdin",
        "-n",
        "-framerate",
        str(fps),
        "-start_number",
        str(first_frame),
        "-i",
        str(folder / "%06d.png"),
        "-frames:v",
        str(last_frame - first_frame + 1),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(max(10, min(crf, 35))),
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        str(out),
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    return json.dumps(start_job(job_name, args, timeout=7200))


def contact_sheet(paths: list[str], output: str, columns: int = 4) -> str:
    """Make a labelled image contact sheet from real rendered frames. Then inspect with view_image.
    Include every shot and boundary frames for an animation review. Up to 32 input images."""
    if not 1 <= len(paths) <= 32:
        raise ValueError("Use 1 to 32 frames.")
    out = writable(output)
    columns = max(1, min(columns, 8))
    w, h = 480, 290
    canvas = Image.new(
        "RGB", (w * columns, h * ((len(paths) + columns - 1) // columns)), (25, 29, 36)
    )
    draw = ImageDraw.Draw(canvas)
    for i, path in enumerate(paths):
        with Image.open(resolve_path(path)) as im:
            im = im.convert("RGB")
            im.thumbnail((w, h - 30))
            x = (i % columns) * w
            y = (i // columns) * h
            canvas.paste(im, (x + (w - im.width) // 2, y + (h - 30 - im.height) // 2))
            draw.text((x + 8, y + h - 24), Path(path).name, fill="white")
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return json.dumps({"path": str(out), "frames": len(paths), "size": canvas.size})


def video_review_frames(video: str, samples: int = 12) -> str:
    """Decode real FINAL video samples to a labelled contact sheet for view_image/quality_review.
    Records the exact video hash and sampled times. Detects an entirely static/blank film.
    For animation delivery this evidence is required, in addition to the project and full video."""
    p = resolve_path(video)
    if not p.is_file():
        raise ValueError("Final video file does not exist.")
    samples = max(3, min(int(samples), 24))
    probe = _media_executable("ffprobe")
    ffmpeg = _media_executable("ffmpeg")
    run = subprocess.run(
        [probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=NO_WINDOW,
    )
    run.check_returncode()
    data = json.loads(run.stdout)
    stream = next(x for x in data["streams"] if x["codec_type"] == "video")
    duration = float(stream.get("duration") or data["format"]["duration"])
    fps = _ratio(stream.get("avg_frame_rate", "24/1"), 24.0)
    if fps <= 0:
        raise ValueError("ffprobe returned an invalid video frame rate.")
    digest = file_sha256(p)
    folder = task_dir() / "VideoReview" / digest[:12]
    folder.mkdir(parents=True, exist_ok=True)
    times = [
        max(0, (duration - 1 / max(1, fps)) * i / (samples - 1)) for i in range(samples)
    ]
    images = []
    previous = None
    differences = []
    from PIL import ImageChops

    for i, t in enumerate(times):
        target = folder / f"sample-{i:02d}-at-{t:.2f}s.png"
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-ss",
                str(t),
                "-i",
                str(p),
                "-frames:v",
                "1",
                "-vf",
                "scale=960:-2",
                str(target),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=NO_WINDOW,
        )
        if result.returncode:
            raise ValueError(result.stderr[-1500:])
        with Image.open(target) as img:
            small = img.convert("RGB").resize((128, 72))
        if previous:
            differences.append(
                sum(ImageStat.Stat(ImageChops.difference(small, previous)).mean) / 3
            )
        previous = small
        images.append(str(target))
    sheet = folder / "final-video-contact-sheet.png"
    contact_sheet(images, str(sheet))
    report = {
        "video": str(p),
        "sha256": digest,
        "duration": duration,
        "fps": fps,
        "samples": times,
        "contact_sheet": str(sheet),
        "mean_frame_differences": differences,
        "has_visual_variation": max(differences, default=0) > 0.1,
        "created_at": time.time(),
    }
    atomic_json(task_dir() / "video-review.json", report)
    return json.dumps(report)
