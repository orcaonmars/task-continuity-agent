"""Build and verify a reproducible source-only release using an explicit allowlist."""

import hashlib
import json
import os
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.0"
ROOT_FILES = {
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "00-START-HERE.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "RELEASE-STATUS.md",
    "SECURITY.md",
    "config.example.json",
    "requirements.txt",
    "install.ps1",
    "start-agent.ps1",
    "watch-agent.ps1",
    "uninstall.ps1",
}
PUBLIC_DIRS = {"app", "docs", "scripts", "tests", ".github"}
EXTENSIONS = {
    ".py",
    ".cjs",
    ".ps1",
    ".md",
    ".html",
    ".json",
    ".yml",
    ".yaml",
    ".txt",
    ".png",
    ".jpg",
    ".svg",
}
EXCLUDED = {
    ".git",
    ".venv",
    "data",
    "dist",
    "work",
    "outputs",
    "__pycache__",
    "node_modules",
}


def is_link(path):
    return path.is_symlink() or bool(
        getattr(path.lstat(), "st_file_attributes", 0) & 0x400
    )


def source_files(root=ROOT):
    root = Path(root)
    found = []
    for name in sorted(ROOT_FILES):
        path = root / name
        if path.is_file() and not is_link(path):
            found.append(path)
    for name in sorted(PUBLIC_DIRS):
        top = root / name
        if not top.is_dir() or is_link(top):
            continue
        for base, dirs, files in os.walk(top, followlinks=False):
            dirs[:] = sorted(
                d for d in dirs if d not in EXCLUDED and not is_link(Path(base) / d)
            )
            for filename in sorted(files):
                path = Path(base) / filename
                if path.suffix.lower() not in EXTENSIONS or is_link(path):
                    continue
                if (
                    filename == "_modernize.py"
                    or ".pending-" in filename
                    or ".writing-" in filename
                ):
                    continue
                found.append(path)
    return sorted(found)


def verify_archive(path, root=ROOT):
    expected = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source_files(root)
    }
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("Archive file inventory does not match public source.")
        if archive.testzip():
            raise ValueError("Archive CRC validation failed.")
        for name in names:
            if hashlib.sha256(archive.read(name)).hexdigest() != expected[name]:
                raise ValueError("Archive content mismatch: " + name)
    return expected


def build(root=ROOT):
    root = Path(root)
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / f"task-continuity-agent-{VERSION}.zip"
    temporary = output.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in source_files(root):
                info = zipfile.ZipInfo(
                    path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0)
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes(), compresslevel=9)
        manifest = verify_archive(temporary, root)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        for attempt in range(10):
            try:
                os.replace(temporary, output)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.1)
        with output.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        (dist / "SHA256SUMS.txt").write_text(
            f"{digest}  {output.name}\n", encoding="ascii"
        )
        (dist / "release-manifest.json").write_text(
            json.dumps(
                {
                    "version": VERSION,
                    "archive": output.name,
                    "sha256": digest,
                    "files": manifest,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return output, digest
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    output, digest = build()
    print(output)
    print(digest)
