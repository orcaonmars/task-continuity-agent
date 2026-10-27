"""Regression coverage for text-only models and bounded recoverable tool context."""

import base64
import copy
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import actor
import common


def instance(folder):
    agent = actor.Agent.__new__(actor.Agent)
    agent.folder = folder
    agent.cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
    agent.cfg["provider"] = "openai-compatible"
    agent.cfg["vision"] = False
    agent.state = {"step": 0, "tool_group": "extra"}
    agent.contract = {"requires_action": False}
    agent.model = "text-model"
    agent.pending = None
    agent.turns = []
    agent.persist = Mock()
    agent.tool_map = {}
    common.atomic_json(folder / "task.json", {"deadline": time.time() + 60})
    return agent


class TextModelAndArchives(unittest.IsolatedAsyncioTestCase):
    def set_response(self, agent, blocks):
        session = AsyncMock()
        session.call_tool.return_value = SimpleNamespace(content=blocks, isError=False)
        agent.tool_map = {
            "extra_echo": (session, SimpleNamespace(inputSchema={"type": "object"}))
        }
        return session

    async def call(self, agent):
        with patch.object(actor, "emit"):
            return await agent.execute(
                {"id": "fixture", "function": {"name": "extra_echo", "arguments": "{}"}}
            )

    async def test_text_model_saves_tool_images_without_sending_image_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            agent = instance(Path(td))
            image = b"saved image fixture"
            self.set_response(
                agent,
                [
                    SimpleNamespace(
                        type="image",
                        data=base64.b64encode(image).decode(),
                        mimeType="image/png",
                    )
                ],
            )
            result = await self.call(agent)
            self.assertEqual(len(result), 1)
            self.assertIn("cannot inspect", result[0]["content"])
            self.assertNotIn("image_url", json.dumps(result))
            self.assertEqual(
                next((agent.folder / "AgentImages").iterdir()).read_bytes(), image
            )

    async def test_text_model_cannot_claim_visual_review(self):
        with tempfile.TemporaryDirectory() as td:
            agent = instance(Path(td))
            agent.state["tool_group"] = "media"
            definitions = [
                {"function": {"name": name}}
                for name in ("view_image", "quality_review", "read_text")
            ]
            self.assertEqual(
                {tool["function"]["name"] for tool in agent.active_tools(definitions)},
                {"read_text", "use_tools"},
            )
            for name in ("view_image", "quality_review"):
                with self.assertRaisesRegex(ValueError, "cannot inspect"):
                    await agent.execute(
                        {"id": name, "function": {"name": name, "arguments": "{}"}}
                    )

    async def test_large_tool_result_is_archived_exactly_with_explicit_pointer(self):
        with tempfile.TemporaryDirectory() as td:
            agent = instance(Path(td))
            text = "full exact response\n" * 1000
            self.set_response(agent, [SimpleNamespace(type="text", text=text)])
            result = await self.call(agent)
            archive = next((agent.folder / "ContextArchive/ToolResults").glob("*.txt"))
            self.assertEqual(archive.read_text(), text)
            self.assertIn("truncated", result[0]["content"])
            self.assertIn(str(archive), result[0]["content"])
            self.assertLess(len(result[0]["content"]), 6500)

    async def test_failed_archive_does_not_claim_output_was_saved(self):
        with tempfile.TemporaryDirectory() as td:
            agent = instance(Path(td))
            self.set_response(agent, [SimpleNamespace(type="text", text="x" * 8000)])
            with patch.object(
                actor, "atomic_text", side_effect=PermissionError("locked")
            ):
                result = await self.call(agent)
            self.assertIn("archival FAILED", result[0]["content"])
            self.assertIn("No full response is claimed saved", result[0]["content"])
            self.assertTrue(agent.state["tool_error"])
            self.assertIsNone(agent.pending)


class RestoredModelContext(unittest.TestCase):
    def test_text_model_drops_retained_image_inputs_without_mutating_history(self):
        with tempfile.TemporaryDirectory() as td:
            agent = instance(Path(td))
            agent.system = Mock(return_value={"role": "system", "content": "facts"})
            agent.initial = Mock(return_value={"role": "user", "content": "objective"})
            agent.turns = [
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "prior observation"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,fixture"},
                            },
                        ],
                    }
                ]
            ]
            original = copy.deepcopy(agent.turns)
            messages = agent.messages()
            self.assertNotIn("image_url", json.dumps(messages))
            self.assertIn("cannot inspect images", json.dumps(messages))
            self.assertEqual(agent.turns, original)

    def test_oversized_optional_group_falls_back_to_core_before_inference(self):
        with tempfile.TemporaryDirectory() as td:
            agent = instance(Path(td))
            agent.compact = Mock(
                side_effect=[actor.ContextBudgetError("tool schemas too large"), None]
            )
            agent.messages = Mock(
                return_value=[{"role": "user", "content": "continue objective"}]
            )
            definitions = [
                {
                    "type": "function",
                    "function": {"name": name, "parameters": {"type": "object"}},
                }
                for name in ("read_text", "extra_echo")
            ]
            response = Mock(ok=True)
            response.iter_content.return_value = [
                b'data: {"choices":[{"delta":{"content":"ready"},"finish_reason":"stop"}]}\ndata: [DONE]\n'
            ]
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            with (
                patch.object(actor.requests, "post", return_value=context) as post,
                patch.object(actor, "emit"),
            ):
                result, _ = agent.predict(definitions)
            self.assertEqual(result["content"], "ready")
            self.assertEqual(agent.compact.call_count, 2)
            self.assertEqual(agent.state["tool_group"], "core")
            self.assertIn("exceeds", agent.state["tool_group_error"])
            self.assertEqual(
                {
                    tool["function"]["name"]
                    for tool in post.call_args.kwargs["json"]["tools"]
                },
                {"read_text", "use_tools"},
            )


if __name__ == "__main__":
    unittest.main()
