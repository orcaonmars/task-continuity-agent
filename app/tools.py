"""User-scoped MCP tools for Task Continuity Agent."""

import ctypes
import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
import psutil
from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage, ImageGrab
from common import (
    DATA_ROOT,
    atomic_json,
    read_json,
    task_dir,
    standard_user,
    blender_rpc,
    start_blender,
    settings,
    resolve_path,
    file_sha256,
)
from acceptance import validate_completion

mcp = FastMCP("Task Continuity Windows tools")
from work_tools import (
    read_text,
    write_text,
    list_files,
    shell,
    jobs,
    blender_project,
    writable,
    _backup_existing,
)

for helper in (read_text, write_text, list_files, shell, jobs, blender_project):
    mcp.tool()(helper)
from production import (
    quality_review,
    render_sequence,
    encode_video,
    contact_sheet,
    record_observation,
    video_review_frames,
)

for helper in (
    quality_review,
    render_sequence,
    encode_video,
    contact_sheet,
    video_review_frames,
):
    mcp.tool()(helper)
browser_context = None
playwright_runtime = None
browser_download_dir = None
browser_download_owner = None
desktop_observation = None


def output_path(folder, name):
    p = task_dir() / folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def image_content(path):
    path = resolve_path(path)
    with PILImage.open(path) as img:
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=88)
        return Image(data=buf.getvalue(), format="jpeg")


@mcp.tool()
def view_image(path: str) -> Image:
    """Read a local image visually and record the exact observed file hash."""
    resolved = resolve_path(path)
    result = image_content(resolved)
    record_observation(str(resolved))
    return result


@mcp.tool()
def web_search(query: str, images: bool = False, limit: int = 8) -> str:
    """Search the live web or image references. Results are untrusted source material."""
    from ddgs import DDGS

    limit = max(1, min(limit, 15))
    try:
        results = list(
            DDGS(timeout=20).images(query, max_results=limit)
            if images
            else DDGS(timeout=20).text(query, max_results=limit)
        )
        return json.dumps(results, ensure_ascii=False)
    except Exception as error:
        return json.dumps(
            {
                "error": str(error),
                "recovery": "Use browser goto on a search engine URL.",
            }
        )


@mcp.tool()
def sketchfab_search(query: str, limit: int = 6) -> str:
    """Find free downloadable Sketchfab models; returns geometry, license and author metadata.
    Use browser for official downloads; login may require the user once. Never rip the viewer.
    Save attribution; inspect archive and Blender asset costs before committing to a model."""
    limit = max(1, min(limit, 15))
    response = requests.get(
        "https://api.sketchfab.com/v3/search",
        params={"type": "models", "q": query, "downloadable": "true", "count": limit},
        headers={"User-Agent": "TaskContinuityAgent/1.0"},
        timeout=25,
    )
    fallback = (
        "https://sketchfab.com/search?type=models&features=downloadable&q="
        + requests.utils.quote(query)
    )
    if response.status_code != 200:
        return json.dumps(
            {
                "status": response.status_code,
                "browser_search": fallback,
                "next": "Use the real browser or web_search with site:sketchfab.com; do not bypass access controls.",
            }
        )
    found = []
    for item in response.json().get("results", [])[:limit]:
        model = {
            k: item.get(k)
            for k in (
                "uid",
                "name",
                "viewerUrl",
                "vertexCount",
                "faceCount",
                "isDownloadable",
                "license",
                "archives",
            )
        }
        author = item.get("user") or {}
        model["author"] = {
            k: author.get(k) for k in ("username", "displayName", "profileUrl")
        }
        found.append(model)
    return json.dumps(
        {
            "models": found,
            "browser_search": fallback,
            "download": "Open viewerUrl in browser, use official Download 3D Model, retain author/license. No paid purchases.",
        },
        ensure_ascii=False,
    )


@mcp.tool()
def asset_archive_inspect(path: str) -> str:
    """Read ZIP asset costs without extracting. Reports compressed/unpacked bytes and largest files.
    Use before import; multi-GB assets are exceptional, not a default. Does not estimate VRAM."""
    import zipfile

    p = resolve_path(path)
    result = {"path": str(p), "file_bytes": p.stat().st_size}
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as archive:
            entries = archive.infolist()
            result.update(
                {
                    "unpacked_bytes": sum(x.file_size for x in entries),
                    "file_count": len(entries),
                    "largest_files": [
                        {"name": x.filename, "bytes": x.file_size}
                        for x in sorted(
                            entries, key=lambda x: x.file_size, reverse=True
                        )[:20]
                    ],
                }
            )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def blender_asset_audit() -> str:
    """Read-only audit of the configured live Blender bridge: biggest meshes, shared mesh users, textures and subdivision.
    Base topology and rough float RGBA texture estimates only, not measured/evaluated GPU memory.
    Follow Docs/Asset Optimization.md; benchmark previews and inspect appearance before final render."""
    code = """import bpy, json
meshes = sorted([{'name': m.name, 'vertices': len(m.vertices), 'polygons': len(m.polygons), 'users': m.users} for m in bpy.data.meshes], key=lambda m:m['polygons'], reverse=True)
images = [{'name': i.name, 'width': i.size[0], 'height': i.size[1], 'rough_float_rgba_bytes': i.size[0]*i.size[1]*16, 'source': i.source} for i in bpy.data.images]
images.sort(key=lambda i:i['rough_float_rgba_bytes'], reverse=True)
mods = [{'object': o.name, 'type': m.type, 'render_levels': getattr(m, 'render_levels', None)} for o in bpy.data.objects for m in o.modifiers if m.type in ('SUBSURF','MULTIRES')]
print(json.dumps({'unique_meshes': len(meshes), 'base_polygons': sum(m['polygons'] for m in meshes), 'largest_meshes': meshes[:20], 'textures': images[:30], 'rough_texture_bytes': sum(i['rough_float_rgba_bytes'] for i in images), 'subdivision': mods[:40], 'render_engine': bpy.context.scene.render.engine, 'resolution': [bpy.context.scene.render.resolution_x,bpy.context.scene.render.resolution_y], 'note': 'Base topology excludes evaluated modifiers and instances. Texture estimate excludes caches, compression, channels, mipmaps and renderer behavior.'}))
"""
    return json.dumps(
        blender_rpc("execute_code", {"code": code}, 90), ensure_ascii=False
    )


@mcp.tool()
def download_file(url: str, destination: str, sha256: str = "") -> str:
    """Download HTTP(S) content with a configured hard size limit and optional SHA-256."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only valid HTTP(S) downloads are supported.")

    dest = writable(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".part-" + str(os.getpid()))
    maximum = int(settings().get("max_download_mb", 4096)) * 1024 * 1024
    digest = hashlib.sha256()
    written = 0

    try:
        with requests.get(
            url,
            stream=True,
            timeout=(15, 120),
            headers={"User-Agent": "TaskContinuityAgent/1.0"},
        ) as response:
            response.raise_for_status()
            advertised = response.headers.get("Content-Length")
            if advertised:
                try:
                    if int(advertised) > maximum:
                        raise ValueError(
                            f"Download is {int(advertised) / 1024**2:.1f} MB; "
                            f"configured limit is {maximum / 1024**2:.0f} MB."
                        )
                except ValueError as error:
                    if "configured limit" in str(error):
                        raise
                    # Invalid Content-Length is ignored; streamed byte accounting still applies.

            with temporary.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > maximum:
                        raise ValueError(
                            f"Download exceeded the configured {maximum / 1024**2:.0f} MB limit."
                        )
                    digest.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())

        actual = digest.hexdigest()
        if sha256 and actual.lower() != sha256.lower():
            raise ValueError(f"Checksum mismatch. Expected {sha256}, got {actual}.")

        _backup_existing(dest)
        os.replace(temporary, dest)
        return json.dumps({"path": str(dest), "bytes": written, "sha256": actual})
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


async def close_browser():
    """Release only this tool server's browser, driver and temporary downloads."""
    global \
        browser_context, \
        playwright_runtime, \
        browser_download_dir, \
        browser_download_owner
    context, runtime, scratch, owner = (
        browser_context,
        playwright_runtime,
        browser_download_dir,
        browser_download_owner,
    )
    browser_context = playwright_runtime = browser_download_dir = None
    browser_download_owner = None
    for operation in (
        context.close if context else None,
        runtime.stop if runtime else None,
    ):
        if operation:
            try:
                await asyncio.wait_for(operation(), timeout=5)
            except Exception:
                pass
    if scratch:
        scratch.cleanup()
        if owner and not Path(scratch.name).exists():
            try:
                owner.unlink(missing_ok=True)
            except OSError:
                pass


def _process_identity():
    process = psutil.Process()
    return {"pid": process.pid, "created": process.create_time()}


def _identity_dead(info):
    """Unknown/inaccessible process identity is never authority to delete scratch."""
    try:
        process = psutil.Process(int(info["pid"]))
        return (
            abs(process.create_time() - float(info["created"])) >= 0.1
            or not process.is_running()
        )
    except psutil.NoSuchProcess:
        return True
    except (KeyError, TypeError, ValueError, psutil.Error):
        return False


def cleanup_browser_scratch(root):
    """Remove only marked scratch from this task after its exact owners have exited."""
    root = Path(root).resolve()
    for marker in root.glob("session-*.owner.json"):
        try:
            if marker.is_symlink() or marker.stat().st_size > 16384:
                continue
            owner = json.loads(marker.read_text(encoding="utf-8"))
            if (
                owner.get("kind") != "browser-download-scratch-v1"
                or owner.get("task") != str(task_dir())
                or owner.get("root") != str(root)
                or not owner.get("ready")
                or not isinstance(owner.get("processes"), list)
                or not owner["processes"]
            ):
                continue
            name = owner.get("session", "")
            if (
                not re.fullmatch(r"session-[A-Za-z0-9_-]+", name)
                or marker.name != name + ".owner.json"
            ):
                continue
            folder = root / name
            if folder.resolve().parent != root or folder.is_symlink():
                continue
            if (
                folder.exists()
                and getattr(folder.lstat(), "st_file_attributes", 0) & 0x400
            ):
                continue
            if not all(_identity_dead(info) for info in owner["processes"]):
                continue
            # Resolved target is one immediate marked child of the private scratch root.
            if folder.exists():
                shutil.rmtree(folder)
            marker.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError, AttributeError):
            continue


async def save_browser_download(download):
    """Commit a verified download atomically; cancel failed/oversized transfers."""
    cfg = settings()
    maximum = int(cfg["max_download_mb"]) * 1024 * 1024
    # Prefixing also neutralizes Windows device names and avoids name collisions.
    name = Path(download.suggested_filename).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ")
    target = writable(
        output_path("Downloads", f"{time.time_ns()}-{name or 'download'}")
    )
    temporary = target.with_name(target.name + ".downloading-" + uuid.uuid4().hex)
    saving = asyncio.create_task(download.save_as(str(temporary)))
    try:
        async with asyncio.timeout(min(1200, cfg["tool_timeout_seconds"])):
            while not saving.done():
                await asyncio.wait({saving}, timeout=0.1)
                # Chromium downloads into the configured scratch directory before
                # save_as copies it. Monitor both locations during transfer.
                candidates = [temporary]
                if browser_download_dir:
                    candidates.extend(Path(browser_download_dir.name).glob("*"))
                for path in candidates:
                    try:
                        if path.is_file() and path.stat().st_size > maximum:
                            raise ValueError(
                                "Browser download exceeds max_download_mb."
                            )
                    except FileNotFoundError:
                        continue
            await saving
            if not temporary.is_file() or temporary.stat().st_size > maximum:
                raise ValueError(
                    "Browser download is missing or exceeds max_download_mb."
                )
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            return json.dumps(
                {
                    "download": str(target),
                    "bytes": target.stat().st_size,
                    "sha256": file_sha256(target),
                }
            )
    except BaseException:
        try:
            await asyncio.wait_for(download.cancel(), timeout=5)
        except Exception:
            pass
        raise
    finally:
        saving.cancel()
        await asyncio.gather(saving, return_exceptions=True)
        try:
            await asyncio.wait_for(download.delete(), timeout=5)
        except Exception:
            pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


async def get_browser():
    global \
        browser_context, \
        playwright_runtime, \
        browser_download_dir, \
        browser_download_owner
    if browser_context is not None:
        try:
            if browser_context.pages:
                await browser_context.pages[0].title()
            else:
                await browser_context.new_page()
            return browser_context
        except Exception:
            await close_browser()
    from playwright.async_api import async_playwright

    try:
        scratch = DATA_ROOT / "BrowserDownloads"
        scratch.mkdir(parents=True, exist_ok=True)
        cleanup_browser_scratch(scratch)
        try:
            prior = {
                process.pid for process in psutil.Process().children(recursive=True)
            }
        except psutil.Error:
            prior = None
        playwright_runtime = await async_playwright().start()
        browser_download_dir = tempfile.TemporaryDirectory(
            prefix="session-", dir=scratch, ignore_cleanup_errors=True
        )
        browser_download_owner = scratch / (
            Path(browser_download_dir.name).name + ".owner.json"
        )
        owner = {
            "kind": "browser-download-scratch-v1",
            "root": str(scratch.resolve()),
            "task": str(task_dir()),
            "session": Path(browser_download_dir.name).name,
            "ready": False,
            "processes": [_process_identity()],
        }
        atomic_json(browser_download_owner, owner)
        browser_context = await playwright_runtime.chromium.launch_persistent_context(
            str(DATA_ROOT / "BrowserProfile"),
            channel=settings().get("browser_channel", "chrome"),
            headless=False,
            accept_downloads=True,
            downloads_path=browser_download_dir.name,
            chromium_sandbox=True,
            ignore_default_args=[
                "--disable-client-side-phishing-detection",
                "--unsafely-disable-devtools-self-xss-warnings",
                "--enable-unsafe-swiftshader",
            ],
            viewport={"width": 1440, "height": 960},
        )
        complete = prior is not None
        try:
            for process in psutil.Process().children(recursive=True):
                if prior is not None and process.pid not in prior:
                    try:
                        owner["processes"].append(
                            {"pid": process.pid, "created": process.create_time()}
                        )
                    except psutil.NoSuchProcess:
                        pass
        except psutil.Error:
            complete = False
        owner["ready"] = complete
        atomic_json(browser_download_owner, owner)
    except BaseException:
        await close_browser()
        raise
    return browser_context


async def browser_state(page):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    return json.dumps(
        {
            "url": page.url,
            "title": await page.title(),
            "elements": (await page.locator("body").aria_snapshot())[:24000],
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def browser(
    action: str, value: str = "", selector: str = "", text: str = "", tab: int = 0
):
    """Real visible Chromium with persistent cookies. Actions: tabs, new, goto(url in value),
    snapshot, screenshot, click(selector), fill(selector,text), key(value), scroll(value pixels),
    download(selector to click), close. Selectors: Playwright CSS, text=, role=.
    Observe snapshot before acting; fresh state follows actions. Stop for login/CAPTCHA."""
    context = await get_browser()
    if action == "tabs":
        return json.dumps(
            [{"tab": i, "url": p.url} for i, p in enumerate(context.pages)]
        )
    if action == "new":
        if value and urlparse(value).scheme not in ("http", "https", "file"):
            raise ValueError("Use an http, https or local file URL.")
        page = await context.new_page()
        if value:
            await page.goto(value, wait_until="domcontentloaded", timeout=45000)
        return await browser_state(page)
    pages = context.pages
    if tab < 0 or (pages and tab >= len(pages)) or (not pages and tab != 0):
        raise ValueError("Choose a current tab index from browser tabs.")
    page = pages[tab] if pages else await context.new_page()
    if action == "goto":
        if urlparse(value).scheme not in ("http", "https", "file"):
            raise ValueError("Use an http, https or local file URL.")
        await page.goto(value, wait_until="domcontentloaded", timeout=45000)
    elif action == "screenshot":
        p = output_path("Screenshots", f"browser-{time.time_ns()}.png")
        await page.screenshot(path=str(p))
        return [f"Screenshot saved: {p}", image_content(p)]
    elif action == "click":
        await page.locator(selector).click(timeout=15000)
    elif action == "fill":
        await page.locator(selector).fill(text, timeout=15000)
    elif action == "key":
        await page.keyboard.press(value)
    elif action == "scroll":
        await page.mouse.wheel(0, int(value or "700"))
    elif action == "download":
        async with page.expect_download(timeout=60000) as event:
            await page.locator(selector).click()
        download = await event.value
        return await save_browser_download(download)
    elif action == "close":
        await page.close()
        return "Tab closed."
    elif action != "snapshot":
        raise ValueError("Unknown browser action.")
    return await browser_state(page)


def unlocked_desktop():
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    handle = user32.OpenInputDesktop(0, False, 0x0100)
    if not handle:
        raise RuntimeError(
            "Desktop locked or UAC secure desktop active; user must unlock/respond."
        )
    name = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong()
    user32.GetUserObjectInformationW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    valid = user32.GetUserObjectInformationW(
        handle, 2, name, ctypes.sizeof(name), ctypes.byref(size)
    )
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    user32.CloseDesktop(handle)
    if not valid or name.value.lower() != "default":
        raise RuntimeError(
            "Desktop locked or secure desktop active; user must unlock/respond."
        )


def desktop_snapshot():
    global desktop_observation
    import win32gui

    unlocked_desktop()
    handle = win32gui.GetForegroundWindow()
    p = output_path("Screenshots", f"desktop-{time.time_ns()}.png")
    ImageGrab.grab().save(p)
    token = str(time.time_ns())
    desktop_observation = (token, handle, time.monotonic())
    tree = []
    try:
        from pywinauto import Desktop

        window = Desktop(backend="uia").window(handle=handle)
        for element in window.descendants()[:220]:
            info = element.element_info
            if info.name or info.automation_id:
                tree.append(
                    {
                        "name": info.name[:180],
                        "type": info.control_type,
                        "automation_id": info.automation_id,
                        "rect": str(info.rectangle),
                    }
                )
    except Exception as error:
        tree.append({"accessibility_error": str(error)})
    return [
        json.dumps(
            {
                "observation": token,
                "window_handle": handle,
                "title": win32gui.GetWindowText(handle),
                "screenshot": str(p),
                "elements": tree,
            },
            ensure_ascii=False,
        ),
        image_content(p),
    ]


@mcp.tool()
def desktop(
    action: str,
    observation: str = "",
    window_handle: int = 0,
    x: int = 0,
    y: int = 0,
    x2: int = 0,
    y2: int = 0,
    text: str = "",
    keys: list[str] | None = None,
    amount: int = 3,
):
    """Windows screen and accessibility feedback. Actions: windows, focus(window_handle),
    screenshot, click, double_click, right_click, type(text), hotkey(keys), scroll(amount), drag.
    Coordinates are physical screen pixels. For input, supply observation from latest screenshot
    (expires in 120s), then inspect returned screenshot. Mouse upper-left aborts input.
    Stop for UAC, login or CAPTCHA. Prefer browser tools or app APIs when available."""
    import win32gui
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.2
    unlocked_desktop()
    if action == "windows":
        windows = []

        def collect(handle, _):
            if win32gui.IsWindowVisible(handle) and win32gui.GetWindowText(handle):
                windows.append(
                    {"handle": handle, "title": win32gui.GetWindowText(handle)}
                )

        win32gui.EnumWindows(collect, None)
        return json.dumps(windows, ensure_ascii=False)
    if action == "focus":
        if not win32gui.IsWindow(window_handle):
            raise ValueError("Choose a current handle from windows.")
        win32gui.ShowWindow(window_handle, 9)
        win32gui.SetForegroundWindow(window_handle)
        return desktop_snapshot()
    if action == "screenshot":
        return desktop_snapshot()
    if not desktop_observation or desktop_observation[0] != observation:
        raise ValueError("Observe a fresh screenshot before input.")
    if (
        time.monotonic() - desktop_observation[2] > 120
        or win32gui.GetForegroundWindow() != desktop_observation[1]
    ):
        raise ValueError(
            "Observation expired or foreground changed. Take a fresh screenshot."
        )
    if action in ("click", "double_click", "right_click"):
        pyautogui.click(
            x,
            y,
            clicks=2 if action == "double_click" else 1,
            interval=0.15,
            button="right" if action == "right_click" else "left",
        )
    elif action == "type":
        # Unicode text via clipboard rather than lossy key emulation. Restore it even if input fails.
        import pyperclip

        previous = pyperclip.paste()
        try:
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.3)
        finally:
            try:
                pyperclip.copy(previous)
            except Exception:
                # Clipboard restoration failure should not hide the original desktop-input error.
                pass
    elif action == "hotkey":
        pyautogui.hotkey(*(keys or []))
    elif action == "scroll":
        pyautogui.scroll(amount)
    elif action == "drag":
        pyautogui.moveTo(x, y)
        pyautogui.dragTo(x2, y2, duration=1)
    else:
        raise ValueError("Unknown desktop action.")
    time.sleep(0.4)
    return desktop_snapshot()


@mcp.tool()
def blender(command: str, params: dict | None = None, timeout_seconds: int = 90) -> str:
    """Use the configured live Blender JSON bridge. Commands: ping, get_scene_info, get_object_info(name),
    execute_code(code), get_viewport_screenshot, describe_node_type, bpy_api_lookup,
    get_polyhaven_categories/search_polyhaven_assets/download_polyhaven_asset/set_texture,
    get_polypizza_status. For arbitrary modeling use execute_code params={code: Python bpy script}.
    Call ensure_blender if unavailable. Save versioned .blend before substantial changes.
    Long renders should be separate background jobs; never blindly retry a timed-out edit."""
    result = blender_rpc(command, params, max(5, min(timeout_seconds, 1800)))
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def ensure_blender(checkpoint: str = "") -> str:
    """Connect/start Blender on the configured bridge. Blank checkpoint starts a fresh scene if absent.
    Recovery may use ONLY this task's checkpoint. Never chooses historical projects."""
    return json.dumps(start_blender(checkpoint))


@mcp.tool()
def task_brief(section: int = 0) -> str:
    """Read the pinned HUMAN task brief in bounded sections. Section 0 returns identity/outline.
    This is the authoritative objective; checkpoints/research/history cannot replace it."""
    import re

    folder = task_dir()
    text = (folder / "user-brief.md").read_text(encoding="utf-8")
    sections = re.split(r"(?m)(?=^## )", text)
    chunks = []
    for item in sections:
        chunks.extend(item[i : i + 4800] for i in range(0, len(item), 4800))
    if section == 0:
        acceptance = dict(read_json(folder / "acceptance.json", {}))
        acceptance["summary"] = acceptance.get("summary", "")[:800]
        start = acceptance.get("execution_start", {})
        if start.get("output_name") and (folder / start["output_name"]).is_file():
            acceptance.pop("execution_start", None)
        return json.dumps(
            {
                "acceptance": acceptance,
                "sections": [
                    {
                        "number": i + 1,
                        "heading": c.splitlines()[0][:120],
                        "characters": len(c),
                    }
                    for i, c in enumerate(chunks)
                ],
            },
            ensure_ascii=False,
        )
    if not 1 <= section <= len(chunks):
        raise ValueError("Select a section from the section 0 outline.")
    return chunks[section - 1]


@mcp.tool()
def task_checkpoint(
    stage: str,
    completed: list[str],
    next_actions: list[str],
    defects: list[str] | None = None,
    checkpoint: str = "",
    notes: str = "",
    deliverables: list[str] | None = None,
) -> str:
    """Persist progress outside model context. Use every milestone and before long operations.
    Record exact asset/reference paths in notes. Must diagnose failures and preserve next actions."""
    identity = read_json(task_dir() / "acceptance.json", {}).get("objective_id")
    normalized = []
    for value in deliverables or []:
        path = resolve_path(value)
        if not path.is_file():
            raise ValueError(
                "Deliverables must be paths to existing files, without annotations. Put planned outputs in next_actions: "
                + value
            )
        normalized.append(str(path))
    checkpoint_path = str(resolve_path(checkpoint)) if checkpoint else ""
    if checkpoint_path and not Path(checkpoint_path).is_file():
        raise ValueError("checkpoint must be a path to an existing file.")
    state = {
        "objective_id": identity,
        "stage": stage,
        "completed": completed,
        "next_actions": next_actions,
        "defects": defects or [],
        "checkpoint": checkpoint_path,
        "notes": notes,
        "deliverables": normalized,
        "updated_at": time.time(),
    }
    atomic_json(task_dir() / "progress.json", state)
    return "Checkpoint saved."


@mcp.tool()
def finish_task(
    summary: str,
    deliverables: list[str],
    verification: str,
    blocked: bool = False,
    required_user_action: str = "",
) -> str:
    """Finish only with checked outputs and specific verification evidence. For blocked task,
    state exact blocker/required action. Text-only questions may have no file deliverables.
    Creating a .blend alone is insufficient: inspect final render and list quality limitations."""
    if not summary.strip() or not verification.strip():
        raise ValueError("Summary and verification evidence required.")
    if blocked and not required_user_action.strip():
        raise ValueError(
            "A blocked task must state the exact user action needed to continue."
        )

    files = []
    for value in deliverables:
        p = resolve_path(value)
        if not p.exists():
            raise ValueError(f"Deliverable does not exist: {p}")
        if p.is_file() and p.stat().st_size == 0:
            raise ValueError(f"Deliverable is empty: {p}")
        record = {
            "path": str(p),
            "bytes": p.stat().st_size if p.is_file() else None,
        }
        if p.is_file():
            record["sha256"] = file_sha256(p)
        files.append(record)
    report = {
        "status": "blocked" if blocked else "completed",
        "summary": summary,
        "files": files,
        "verification": verification,
        "required_user_action": required_user_action,
        "finished_at": time.time(),
    }
    meta = read_json(task_dir() / "task.json", {})
    if meta.get("created"):
        report["started_at"] = meta["created"]
        report["elapsed_seconds"] = round(report["finished_at"] - meta["created"])
        elapsed_minutes = report["elapsed_seconds"] // 60
        report["elapsed"] = (
            f"{elapsed_minutes // 60} hours {elapsed_minutes % 60} minutes"
        )
    validate_completion(task_dir(), report)
    atomic_json(task_dir() / "result.json", report)
    return json.dumps(report, ensure_ascii=False)


if __name__ == "__main__":
    standard_user()
    if "TASK_CONTINUITY_TASK_DIR" not in os.environ:
        raise RuntimeError(
            "Launch tools through Task Continuity Agent; a task directory is required."
        )
    task_dir().mkdir(parents=True, exist_ok=True)
    mcp.run(transport="stdio")
