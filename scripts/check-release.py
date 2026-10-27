"""Check distributable source without scanning runtime data or installed dependencies."""

import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_packager", ROOT / "scripts/package-release.py"
)
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def check():
    required = packager.ROOT_FILES | {
        "app/agent.py",
        "app/actor.py",
        "app/tools.py",
        "app/job_runner.py",
        "tests/test_agent_core.py",
        "tests/test_reliability.py",
        "tests/test_unattended.py",
        "tests/test_edges.py",
        "tests/test_release.py",
        "scripts/doctor.ps1",
        ".github/workflows/validate.yml",
        "docs/blender.md",
    }
    paths = packager.source_files(ROOT)
    present = {p.relative_to(ROOT).as_posix() for p in paths}
    if missing := required - present:
        raise ValueError("Missing public files: " + ", ".join(sorted(missing)))
    forbidden = (
        "C:" + "\\Qwen" + "Agent",
        "Hao" + " Ran",
        "qwen3.8" + "-27b@",
        "Qwen" + "AgentChat",
    )
    for path in paths:
        if path.suffix.lower() not in {
            ".py",
            ".ps1",
            ".md",
            ".html",
            ".json",
            ".yml",
            ".yaml",
            ".txt",
        }:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if any(needle in text for needle in forbidden):
            raise ValueError("Private machine/runtime value in " + path.name)
        if path.suffix == ".py":
            ast.parse(text, filename=str(path))
    sys.path.insert(0, str(ROOT / "app"))
    from common import DEFAULT_SETTINGS, validate_settings

    example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8-sig"))
    if set(example) != set(DEFAULT_SETTINGS):
        raise ValueError("Example config keys differ from supported defaults.")
    validate_settings(example)
    print(
        f"Public source verified: {len(paths)} files; config and privacy checks passed."
    )


if __name__ == "__main__":
    check()
