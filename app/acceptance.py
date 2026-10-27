"""Durable task identity and independent checks for file-based completion."""

import hashlib, json, re, shutil, subprocess, time
from pathlib import Path
from common import (
    DATA_ROOT,
    atomic_json,
    atomic_text,
    read_json,
    NO_WINDOW,
    file_sha256,
    find_blender,
    resolve_path,
)

KNOWN = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".txt",
    ".csv",
    ".xlsx",
    ".pdf",
    ".docx",
    ".pptx",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".mp4",
    ".mov",
    ".mkv",
    ".blend",
    ".glb",
    ".gltf",
    ".obj",
    ".fbx",
}
VISUAL = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".mp4",
    ".mov",
    ".mkv",
    ".blend",
    ".glb",
    ".gltf",
    ".obj",
    ".fbx",
}


def _positive(text):
    """Remove negative clauses while preserving a later explicit action after a comma.

    Examples:
    - "no assets, research, animation or long brief needed" -> all negative
    - "no video, render preview.png" -> preserves the render request
    """
    action = (
        r"(?:but\s+|then\s+)?"
        r"(?:create|make|render|write|save|export|produce|build|generate|deliver)\b"
    )
    pattern = (
        r"\b(?:no|without|do not|don't|not)\b"
        r".*?"
        r"(?=(?:,\s*" + action + r")|[.;\n]|$)"
    )
    return re.sub(pattern, " ", text, flags=re.I)


def _extensions(text):
    return sorted(
        x.lower()
        for x in set(re.findall(r"(\.[A-Za-z0-9]{1,8})\b", text))
        if x.lower() in KNOWN
    )


def _durable_deliverable_expected(text, required_extensions, visual=False):
    """Classify whether successful completion should point to a durable path."""
    if required_extensions or visual:
        return True
    positive = _positive(str(text).lower())
    durable_terms = (
        " file",
        "folder",
        "directory",
        "code",
        "script",
        "program",
        "application",
        " app ",
        "website",
        "web site",
        "project",
        "repo",
        "repository",
        "document",
        "report",
        "spreadsheet",
        "presentation",
        "dataset",
        "source tree",
        "asset",
        "archive",
    )
    if any(term in (" " + positive + " ") for term in durable_terms):
        return True
    return any(
        re.search(r"\b" + verb + r"\b", positive)
        for verb in ("render", "animate", "export", "download")
    )


def initialize_contract(task, objective):
    task = Path(task)
    original = objective
    prompt_root = DATA_ROOT / "Prompts"
    if prompt_root.exists():
        norm = objective.replace(chr(92), "/").lower()
        for source in prompt_root.glob("*.md"):
            if str(source).replace(chr(92), "/").lower() in norm:
                original += "\n\n" + source.read_text(encoding="utf-8")
    digest = hashlib.sha256(original.encode()).hexdigest()
    atomic_text(task / "user-brief.md", original)
    low = original.lower()
    request = objective.lower()
    positive = _positive(low)
    question = request.lstrip().startswith(
        ("how ", "what ", "why ", "when ", "where ", "who ", "explain ", "tell me ")
    )
    verbs = (
        "create",
        "render",
        "animate",
        "produce",
        "make",
        "finish",
        "execute",
        "build",
        "recreate",
        "generate",
        "write",
        "edit",
        "modify",
        "fix",
        "repair",
        "refactor",
        "install",
        "configure",
        "convert",
        "export",
        "download",
        "organize",
        "process",
        "test",
        "run",
        "deploy",
        "package",
        "prepare",
        "optimize",
    )
    action = not question and any(
        re.search(r"\b" + re.escape(v) + r"\b", request) for v in verbs
    )
    required = _extensions(positive) if not question else []
    video = action and any(
        s in positive for s in ("animation", "video", "finished clip", "cinematic clip")
    )
    blend = action and (
        ".blend" in positive
        or (
            "blender" in positive
            and any(
                s in positive
                for s in ("scene", "project", "render", "animation", "video")
            )
        )
    )
    still = action and any(
        s in positive
        for s in ("still image", "still png", "png render", "preview render")
    )
    if blend and ".blend" not in required:
        required.append(".blend")
    if video and not any(x in required for x in (".mp4", ".mov", ".mkv")):
        required.append(".mp4")
    if (
        still
        and not video
        and not any(x in required for x in (".png", ".jpg", ".jpeg", ".webp"))
    ):
        required.append(".png")
    required = sorted(set(required))
    visual_required = any(x in VISUAL for x in required)
    contract = {
        "objective_id": digest,
        "required_extensions": required,
        "requires_action": bool(action or required),
        "visual_review_required": visual_required,
        "durable_deliverable_required": bool(action or required)
        and _durable_deliverable_expected(original, required, visual_required),
        "summary": objective,
        "video": {},
    }
    if video:
        rate = re.search(r"\b(\d+(?:\.\d+)?)\s*fps\b", request)
        resolution = re.search(r"\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b", request)
        duration = re.search(
            r"\b(\d+(?:\.\d+)?)[- ]?(?:seconds?|secs?)\b.{0,35}\b(?:animation|video|clip)\b",
            request,
        )
        duration = duration or re.search(
            r"\bduration\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:seconds?|secs?)\b", request
        )
        if rate:
            contract["video"]["fps"] = float(rate[1])
        if resolution:
            contract["video"].update(
                width=int(resolution[1]), height=int(resolution[2])
            )
        if duration:
            contract["video"]["duration"] = float(duration[1])
        if rate and duration:
            contract["video"]["frames"] = round(float(rate[1]) * float(duration[1]))
    atomic_json(task / "acceptance.json", contract)
    return contract


def _verify_file_format(path, ext):
    """Cheap structural validation for common non-visual deliverables."""
    path = Path(path)
    if ext == ".py":
        source = path.read_text(encoding="utf-8-sig")
        compile(source, str(path), "exec")
        return

    if ext == ".json":
        json.loads(path.read_text(encoding="utf-8-sig"))
        return

    if ext == ".toml":
        import tomllib

        tomllib.loads(path.read_text(encoding="utf-8-sig"))
        return

    if ext == ".csv":
        import csv

        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            if next(reader, None) is None:
                raise ValueError("CSV contains no rows.")
        return

    if ext in {".zip", ".docx", ".xlsx", ".pptx"}:
        import zipfile

        if not zipfile.is_zipfile(path):
            raise ValueError("File is not a valid ZIP container.")
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"Corrupt ZIP member: {bad}")
            names = set(archive.namelist())
            expected = {
                ".docx": "word/document.xml",
                ".xlsx": "xl/workbook.xml",
                ".pptx": "ppt/presentation.xml",
            }.get(ext)
            if expected and expected not in names:
                raise ValueError(f"Missing required Office package member: {expected}")
        return

    if ext == ".pdf":
        with path.open("rb") as stream:
            header = stream.read(8)
            stream.seek(max(0, path.stat().st_size - 2048))
            tail = stream.read()
        if not header.startswith(b"%PDF-") or b"%%EOF" not in tail:
            raise ValueError("PDF header/trailer is invalid.")
        return

    if ext == ".gltf":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("asset"), dict) or not data["asset"].get("version"):
            raise ValueError("glTF asset metadata is missing.")
        return

    if ext == ".glb":
        import struct

        with path.open("rb") as stream:
            header = stream.read(12)
        if len(header) != 12 or header[:4] != b"glTF":
            raise ValueError("Invalid GLB header.")
        version, declared = struct.unpack("<II", header[4:12])
        if version != 2 or declared != path.stat().st_size:
            raise ValueError("Invalid GLB version or declared length.")
        return

    if ext == ".obj":
        found_vertex = False
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for _ in range(20000):
                line = stream.readline()
                if not line:
                    break
                if line.startswith("v "):
                    found_vertex = True
                    break
        if not found_vertex:
            raise ValueError("OBJ contains no vertex records.")
        return

    if ext == ".fbx":
        with path.open("rb") as stream:
            header = stream.read(256)
        if not (
            header.startswith(b"Kaydara FBX Binary") or b"FBXHeaderExtension" in header
        ):
            raise ValueError("FBX header is not recognizable.")
        return

    if ext in {
        ".txt",
        ".md",
        ".html",
        ".css",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".yaml",
        ".yml",
    }:
        # Text exports can be large; validate encoding without loading the whole
        # file into the supervisor's memory.
        import codecs

        decoder = codecs.getincrementaldecoder("utf-8-sig")()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)


def validate_completion(task, report):
    if not isinstance(report, dict) or report.get("status") not in (
        "completed",
        "blocked",
    ):
        raise ValueError("Completion must have status completed or blocked.")
    if report.get("status") == "blocked":
        if not str(report.get("required_user_action") or "").strip():
            raise ValueError(
                "A blocked task must state the exact user action needed to continue."
            )
        if (
            not str(report.get("summary") or "").strip()
            or not str(report.get("verification") or "").strip()
        ):
            raise ValueError("Completion requires a summary and verification evidence.")
        return
    if (
        not str(report.get("summary") or "").strip()
        or not str(report.get("verification") or "").strip()
    ):
        raise ValueError("Completion requires a summary and verification evidence.")
    task = Path(task).resolve()
    contract = read_json(task / "acceptance.json", {})
    required = contract.get("required_extensions", [])
    paths = [resolve_path(file["path"], base=task) for file in report.get("files", [])]
    durable_required = contract.get("durable_deliverable_required")
    if durable_required is None:
        durable_required = _durable_deliverable_expected(
            contract.get("summary", ""),
            required,
            contract.get("visual_review_required", False),
        )
    if durable_required and not paths:
        raise ValueError("This action requires at least one durable deliverable.")
    from work_tools import job_status

    if any(
        job_status(folder)["status"] == "running"
        for folder in (task / "Jobs").glob("*")
        if (folder / "job.json").exists()
    ):
        raise ValueError("Wait for running background jobs before finishing.")
    for path in paths:
        if not path.exists():
            raise ValueError(f"Reported deliverable disappeared: {path}")
        if path.is_file() and path.stat().st_size <= 0:
            raise ValueError(f"Reported deliverable is empty: {path}")
    for record, path in zip(report.get("files", []), paths, strict=True):
        if (
            record.get("sha256")
            and path.is_file()
            and file_sha256(path) != record["sha256"]
        ):
            raise ValueError(f"Reported deliverable changed after verification: {path}")
    if contract.get("visual_review_required"):
        review = read_json(task / "quality-review.json", {})
        if (
            not review.get("passed")
            or not review.get("images")
            or len(review.get("criteria", [])) < 3
        ):
            raise ValueError(
                "Inspect current previews with view_image and pass quality_review before finishing."
            )
        for rec in review.get("images", []):
            p = Path(rec["path"])
            if not p.is_file() or file_sha256(p) != rec["sha256"]:
                raise ValueError("Reviewed image changed or disappeared.")
        scenes = [p for p in paths if p.suffix.lower() == ".blend" and p.is_file()]
        if scenes and max(p.stat().st_mtime for p in scenes) > review.get(
            "reviewed_at", 0
        ):
            raise ValueError(
                "Scene changed after visual review; review the current output again."
            )
    # A valid sibling must not hide a broken reported output of the same type.
    # Apply cheap structural checks even when the brief did not name extensions.
    for path in paths:
        if path.is_file() and path.suffix.lower() in KNOWN:
            try:
                _verify_file_format(path, path.suffix.lower())
            except (OSError, UnicodeError, ValueError, SyntaxError) as error:
                raise ValueError(
                    f"No structurally valid {path.suffix.lower()} deliverable: {path.name}: {error}"
                ) from error
    for ext in required:
        candidates = [
            p
            for p in paths
            if p.suffix.lower() == ext and p.is_file() and p.stat().st_size > 0
        ]
        if not candidates:
            raise ValueError(f"Missing nonempty {ext} deliverable.")
        if ext == ".blend":
            good = False
            errors = []
            for scene in candidates:
                check = scene.parent / (".scene-check-" + str(time.time_ns()) + ".json")
                code = (
                    "import bpy,json,pathlib;pathlib.Path("
                    + repr(str(check))
                    + ").write_text(json.dumps({'objects':len(bpy.context.scene.objects),'camera':bool(bpy.context.scene.camera)}),encoding='utf-8')"
                )
                try:
                    run = subprocess.run(
                        [
                            find_blender(),
                            "-b",
                            "--factory-startup",
                            "--disable-autoexec",
                            "--python-exit-code",
                            "1",
                            str(scene),
                            "--python-expr",
                            code,
                        ],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=120,
                        creationflags=NO_WINDOW,
                    )
                    info = (
                        json.loads(check.read_text(encoding="utf-8"))
                        if check.is_file()
                        else {}
                    )
                    if not run.returncode and int(info.get("objects", 0)) > 0:
                        good = True
                        break
                    errors.append(run.stderr[-600:])
                except Exception as e:
                    errors.append(str(e))
                finally:
                    try:
                        check.unlink(missing_ok=True)
                    except OSError:
                        pass
            if not good:
                raise ValueError(
                    "Blender project verification failed: " + "; ".join(errors[-3:])
                )
        if ext in (".mp4", ".mov", ".mkv"):
            probe = shutil.which("ffprobe") or (
                r"C:\ffmpeg\bin\ffprobe.exe"
                if Path(r"C:\ffmpeg\bin\ffprobe.exe").is_file()
                else None
            )
            if not probe:
                raise ValueError("ffprobe is required to verify the final video.")
            passed = False
            errs = []
            for p in candidates:
                try:
                    checked = read_json(task / "video-review.json", {})
                    if contract.get("visual_review_required") and checked.get(
                        "sha256"
                    ) != file_sha256(p):
                        raise ValueError(
                            "Review the exact final video with video_review_frames."
                        )
                    if contract.get("visual_review_required") and not any(
                        rec.get("path") == checked.get("contact_sheet")
                        for rec in review["images"]
                    ):
                        raise ValueError(
                            "Inspect and quality-review the final video contact sheet."
                        )
                    run = subprocess.run(
                        [
                            probe,
                            "-v",
                            "error",
                            "-count_frames",
                            "-show_streams",
                            "-show_format",
                            "-of",
                            "json",
                            str(p),
                        ],
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=120,
                        creationflags=NO_WINDOW,
                    )
                    run.check_returncode()
                    data = json.loads(run.stdout)
                    v = next(
                        s
                        for s in data.get("streams", [])
                        if s.get("codec_type") == "video"
                    )
                    spec = contract.get("video", {})
                    rate = v.get("avg_frame_rate", "0/1").split("/")
                    denominator = float(rate[1])
                    fps = float(rate[0]) / denominator if denominator else 0.0
                    if "width" in spec and v.get("width") != spec["width"]:
                        raise ValueError("wrong width")
                    if "height" in spec and v.get("height") != spec["height"]:
                        raise ValueError("wrong height")
                    if "fps" in spec and abs(fps - spec["fps"]) > 0.001:
                        raise ValueError("wrong fps")
                    duration = float(v.get("duration") or data["format"]["duration"])
                    if "duration" in spec and abs(duration - spec["duration"]) > 0.08:
                        raise ValueError("wrong duration")
                    if (
                        "frames" in spec
                        and int(v.get("nb_read_frames") or v.get("nb_frames") or 0)
                        != spec["frames"]
                    ):
                        raise ValueError("wrong frame count")
                    passed = True
                    break
                except Exception as e:
                    errs.append(str(e))
            if not passed:
                raise ValueError("Final video verification failed: " + "; ".join(errs))
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            from PIL import Image

            ok = False
            for p in candidates:
                try:
                    with Image.open(p) as img:
                        img.verify()
                    ok = True
                    break
                except OSError:
                    pass
            if not ok:
                raise ValueError("No valid image deliverable.")
