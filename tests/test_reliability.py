import ctypes, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from acceptance import initialize_contract
from common import atomic_json, read_json, _pending_jsons


class ParserMatrix(unittest.TestCase):
    def test_matrix(self):
        cases = [
            (
                "Create a Blender scene and render a still PNG. No animation needed.",
                {".blend", ".png"},
            ),
            (
                "Create a fresh Blender scene; no assets, research, animation or long brief needed. Render preview.png.",
                {".blend", ".png"},
            ),
            ("Create a Blender animation and finished MP4.", {".blend", ".mp4"}),
            ("Make a 10 second Blender video at 24 fps.", {".blend", ".mp4"}),
            ("Render a Blender scene to image.png without video.", {".blend", ".png"}),
            ("Create a Blender scene only. No video or PNG.", {".blend"}),
            (
                "Build a Blender project without animation; save preview.png.",
                {".blend", ".png"},
            ),
            ("Explain how Blender animation works.", set()),
            ("What is a Blender scene?", set()),
            ("Build an editable .blend project and a still image.", {".blend", ".png"}),
            ("Create report.csv and summary.md.", {".csv", ".md"}),
            ("Explain what a CSV file is.", set()),
        ]
        for text, expected in cases:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as td:
                self.assertEqual(
                    set(initialize_contract(Path(td), text)["required_extensions"]),
                    expected,
                )


@unittest.skipUnless(sys.platform == "win32", "Windows file-lock semantics")
class AtomicState(unittest.TestCase):
    def test_locked_target_uses_recoverable_pending_write(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            self.assertTrue(atomic_json(p, {"v": 1}))
            GENERIC_READ = 0x80000000
            OPEN_EXISTING = 3
            FILE_SHARE_READ = 1
            create = ctypes.windll.kernel32.CreateFileW
            create.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            create.restype = ctypes.c_void_p
            h = create(
                str(p), GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, 0, None
            )
            self.assertNotIn(h, (None, ctypes.c_void_p(-1).value))
            try:
                self.assertFalse(atomic_json(p, {"v": 2}))
                self.assertEqual(read_json(p), {"v": 2})
                self.assertTrue(_pending_jsons(p))
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
            self.assertTrue(atomic_json(p, {"v": 3}))
            self.assertEqual(read_json(p), {"v": 3})
            self.assertFalse(_pending_jsons(p))


if __name__ == "__main__":
    unittest.main()
