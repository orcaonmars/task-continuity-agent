"""Streaming compatibility, durable compaction, and optional MCP fault isolation."""

import asyncio
import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from builtins import ExceptionGroup
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import actor  # noqa: E402
import common  # noqa: E402


class ProviderStreams(unittest.TestCase):
    def make_agent(self, folder):
        instance = actor.Agent.__new__(actor.Agent)
        instance.folder = folder
        instance.cfg = copy.deepcopy(common.DEFAULT_SETTINGS)
        instance.cfg["provider"] = "openai-compatible"
        instance.model = "test"
        instance.contract = {"requires_action": False}
        instance.state = {"step": 0}
        instance.persist = instance.compact = Mock()
        instance.active_tools = Mock(return_value=[])
        instance.messages = Mock(return_value=[{"role": "user", "content": "test"}])
        return instance

    def response(self, chunks):
        response = Mock(ok=True)
        response.iter_content.return_value = chunks
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        return context

    def test_keepalive_comments_obey_inference_wall_clock_limit(self):
        with tempfile.TemporaryDirectory() as td:
            instance = self.make_agent(Path(td))
            instance.cfg["prediction_timeout_seconds"] = 30
            with (
                patch.object(actor, "emit"),
                patch.object(actor.time, "monotonic", side_effect=[0, 0, 31]),
                patch.object(
                    actor.requests,
                    "post",
                    return_value=self.response([b": keepalive\n\n"]),
                ),
                self.assertRaisesRegex(RuntimeError, "wall-clock limit"),
            ):
                instance.predict([])

    def test_newline_free_chunks_still_check_limits(self):
        response = Mock()
        response.iter_content.return_value = [b"x", b"y", b"z"]
        check = Mock(side_effect=[None, RuntimeError("deadline")])
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            list(actor.stream_lines(response, check=check))
        self.assertEqual(check.call_count, 2)

    def test_actual_unchunked_http_keepalives_cannot_hide_timeout(self):
        stopped = threading.Event()

        class Heartbeats(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    while not stopped.is_set():
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        stopped.wait(0.025)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Heartbeats)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                instance = self.make_agent(Path(td))
                instance.cfg["api_base"] = f"http://127.0.0.1:{server.server_port}/v1"
                instance.cfg["prediction_timeout_seconds"] = 0.12
                started = time.monotonic()
                with (
                    patch.object(actor, "emit"),
                    self.assertRaisesRegex(RuntimeError, "wall-clock limit"),
                ):
                    instance.predict([])
                self.assertLess(time.monotonic() - started, 2)
        finally:
            stopped.set()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_completed_open_sse_connection_allows_consecutive_predictions(self):
        for include_done in (True, False):
            with self.subTest(include_done=include_done):
                stopped = threading.Event()
                requests_seen = []

                class OpenCompletion(BaseHTTPRequestHandler):
                    protocol_version = "HTTP/1.1"

                    def do_POST(
                        self,
                        stopped=stopped,
                        include_done=include_done,
                        requests_seen=requests_seen,
                    ):
                        self.rfile.read(int(self.headers["Content-Length"]))
                        requests_seen.append(True)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.end_headers()
                        body = (
                            b'data: {"choices":[{"index":0,"delta":{"content":"ok"},'
                            b'"finish_reason":"stop"}]}\n\n'
                        )
                        if include_done:
                            body += b"data: [DONE]\n\n"
                        try:
                            # Split a real response across writes, then keep the
                            # connection open until test cleanup, beyond the budget.
                            for part in (body[:9], body[9:36], body[36:]):
                                self.wfile.write(part)
                                self.wfile.flush()
                                stopped.wait(0.01)
                            stopped.wait(5)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        self.close_connection = True

                    def log_message(self, *_):
                        pass

                server = ThreadingHTTPServer(("127.0.0.1", 0), OpenCompletion)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                streams = []
                try:
                    with tempfile.TemporaryDirectory() as td:
                        instance = self.make_agent(Path(td))
                        instance.cfg["api_base"] = (
                            f"http://127.0.0.1:{server.server_port}/v1"
                        )
                        instance.cfg["prediction_timeout_seconds"] = 1
                        started = time.monotonic()
                        with patch.object(actor, "emit"):
                            for _ in range(2):
                                reply, finish = instance.predict([])
                                streams.append(instance._http_stream)
                                self.assertEqual(reply["content"], "ok")
                                self.assertEqual(finish, "stop")
                                self.assertFalse(
                                    instance._http_stream.worker.is_alive()
                                )
                        self.assertEqual(len(requests_seen), 2)
                        self.assertLess(time.monotonic() - started, 1)
                        if not include_done:
                            self.assertTrue(
                                instance.state["stream_missing_done_sentinel"]
                            )
                finally:
                    stopped.set()
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=3)
                    for stream in streams:
                        stream.worker.join(timeout=3)

    def test_fragmented_terminal_event_preserves_already_received_usage(self):
        def chunks():
            yield b"da"
            yield (
                b'ta: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                b'data: {"choices":[],"usage":{"total_tokens":9}}\n\n'
            )
            self.fail("A completed response must not start another socket read")

        with tempfile.TemporaryDirectory() as td:
            instance = self.make_agent(Path(td))
            with (
                patch.object(actor, "emit"),
                patch.object(
                    actor.requests, "post", return_value=self.response(chunks())
                ),
            ):
                reply, finish = instance.predict([])
            self.assertEqual(reply["content"], "ok")
            self.assertEqual(finish, "stop")
            self.assertEqual(instance.state["last_usage"], {"total_tokens": 9})
            self.assertFalse(instance._http_stream.worker.is_alive())

    def test_network_timeouts_cannot_exceed_remaining_task_budget(self):
        with tempfile.TemporaryDirectory() as td:
            instance = self.make_agent(Path(td))
            common.atomic_json(instance.folder / "task.json", {"deadline": 103})
            with (
                patch.object(actor, "emit"),
                patch.object(actor.time, "time", return_value=100),
                patch.object(
                    actor.requests,
                    "post",
                    return_value=self.response(
                        [b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n']
                    ),
                ) as post,
            ):
                instance.predict([])
            self.assertEqual(post.call_args.kwargs["timeout"], (3, 3))

    def test_partial_http_headers_obey_total_budget_and_allow_only_one_io_worker(self):
        stopped = threading.Event()
        requests_seen = []

        class SlowHeaders(BaseHTTPRequestHandler):
            def do_POST(self):
                requests_seen.append(True)
                try:
                    self.wfile.write(b"HTTP/1.0 200 OK\r\nX-Slow: ")
                    self.wfile.flush()
                    while not stopped.wait(0.025):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHeaders)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        instance = None
        try:
            with tempfile.TemporaryDirectory() as td:
                instance = self.make_agent(Path(td))
                instance.cfg["api_base"] = f"http://127.0.0.1:{server.server_port}/v1"
                instance.cfg["prediction_timeout_seconds"] = 0.2
                started = time.monotonic()
                with (
                    patch.object(actor, "emit"),
                    self.assertRaisesRegex(RuntimeError, "wall-clock limit"),
                ):
                    instance.predict([])
                self.assertLess(time.monotonic() - started, 1)
                first = instance._http_stream
                self.assertTrue(first.worker.is_alive())
                with (
                    patch.object(actor, "emit"),
                    self.assertRaisesRegex(RuntimeError, "actor restart required"),
                ):
                    instance.predict([])
                self.assertIs(instance._http_stream, first)
                self.assertEqual(len(requests_seen), 1)
        finally:
            stopped.set()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
            if instance is not None:
                instance._http_stream.worker.join(timeout=3)
                self.assertFalse(instance._http_stream.worker.is_alive())

    def test_stream_becoming_silent_near_deadline_does_not_restart_inactivity_budget(
        self,
    ):
        stopped = threading.Event()

        class BecomesSilent(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    until = time.monotonic() + 0.8
                    while time.monotonic() < until and not stopped.wait(0.025):
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    stopped.wait(5)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), BecomesSilent)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        instance = None
        try:
            with tempfile.TemporaryDirectory() as td:
                instance = self.make_agent(Path(td))
                instance.cfg["api_base"] = f"http://127.0.0.1:{server.server_port}/v1"
                instance.cfg["prediction_timeout_seconds"] = 1
                started = time.monotonic()
                with (
                    patch.object(actor, "emit"),
                    self.assertRaisesRegex(RuntimeError, "wall-clock limit"),
                ):
                    instance.predict([])
                self.assertLess(time.monotonic() - started, 1.5)
        finally:
            stopped.set()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
            if instance is not None:
                instance._http_stream.worker.join(timeout=3)
                self.assertFalse(instance._http_stream.worker.is_alive())

    def test_multiple_choices_are_never_merged_into_one_tool_turn(self):
        with tempfile.TemporaryDirectory() as td:
            instance = self.make_agent(Path(td))
            instance.cfg["request_overrides"] = {"n": 2}
            chunk = {
                "choices": [
                    {"index": 0, "delta": {"content": "first"}},
                    {
                        "index": 1,
                        "delta": {"content": "second"},
                        "finish_reason": "stop",
                    },
                ]
            }
            with (
                patch.object(actor, "emit"),
                patch.object(
                    actor.requests,
                    "post",
                    return_value=self.response(
                        [b"data: " + json.dumps(chunk).encode() + b"\n"]
                    ),
                ) as post,
                self.assertRaisesRegex(RuntimeError, "Multiple completion choices"),
            ):
                instance.predict([])
            self.assertNotIn("n", post.call_args.kwargs["json"])
            settings = copy.deepcopy(common.DEFAULT_SETTINGS)
            settings["request_overrides"] = {"n": 2}
            self.assertNotIn(
                "n", common.validate_settings(settings)["request_overrides"]
            )

    def test_compaction_archives_exact_retained_tool_payload(self):
        with tempfile.TemporaryDirectory() as td:
            instance = actor.Agent.__new__(actor.Agent)
            instance.folder = Path(td)
            instance.cfg = {"context_length": 8192, "max_output_tokens": 1024}
            instance.state = {}
            instance.pending = None
            instance.persist = Mock()
            instance.system = Mock(
                return_value={"role": "system", "content": "fixed facts"}
            )
            instance.initial = Mock(
                return_value={"role": "user", "content": "Create out.txt"}
            )
            original = "the full executed source\n" * 1500
            instance.turns = [
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "write",
                                "function": {
                                    "name": "write_text",
                                    "arguments": json.dumps(
                                        {"path": "out.txt", "text": original}
                                    ),
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "write", "content": "saved"},
                ]
            ]
            with patch.object(actor, "emit"):
                instance.compact([])
            archives = list((instance.folder / "ContextArchive").glob("*.json"))
            self.assertEqual(len(archives), 1)
            archived = common.read_json(archives[0])
            saved_args = json.loads(
                archived[0][0]["tool_calls"][0]["function"]["arguments"]
            )
            self.assertEqual(saved_args["text"], original)
            active_args = json.loads(
                instance.turns[0][0]["tool_calls"][0]["function"]["arguments"]
            )
            self.assertNotEqual(active_args["text"], original)
            instance.system.assert_called_once()


# A small real JSON-RPC peer exercises MCP's own AnyIO cancellation scopes.
MCP_PEER = r"""
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        continue
    if method == "initialize":
        result = {"protocolVersion": request["params"]["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        if sys.argv[1] == "crash":
            sys.stdout.buffer.write(b"\xff\n")
            sys.stdout.buffer.flush()
            break
        result = {"content": [{"type": "text", "text": request["params"]["arguments"]["text"]}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""


class OptionalMcpIsolation(unittest.IsolatedAsyncioTestCase):
    def params(self, mode):
        return actor.StdioServerParameters(
            command=sys.executable, args=["-u", "-c", MCP_PEER, mode]
        )

    async def test_optional_transport_crash_preserves_core_session(self):
        async with actor.stdio_client(self.params("healthy")) as (read, write):
            async with actor.ClientSession(read, write) as core:
                await core.initialize()
                async with actor.ExtraMcpConnection(self.params("crash")) as extra:
                    self.assertEqual(extra.tools[0].name, "echo")
                    with self.assertRaisesRegex(
                        RuntimeError, "Extra MCP server disconnected"
                    ):
                        await asyncio.wait_for(
                            extra.call_tool("echo", {"text": "fail"}), timeout=10
                        )
                    result = await asyncio.wait_for(
                        core.call_tool("echo", {"text": "core survived"}), timeout=10
                    )
                    self.assertEqual(result.content[0].text, "core survived")
                self.assertTrue(extra.worker.done())

    async def test_optional_startup_failure_does_not_cancel_caller(self):
        params = actor.StdioServerParameters(
            command=sys.executable,
            args=[
                "-u",
                "-c",
                "import sys;sys.stdout.buffer.write(b'\\xff\\n');sys.stdout.buffer.flush()",
            ],
        )
        connection = actor.ExtraMcpConnection(params)
        with self.assertRaises(ExceptionGroup):
            async with connection:
                self.fail("Corrupt MCP peer must not initialize")
        await asyncio.sleep(0)
        self.assertTrue(connection.worker.done())
        self.assertIsNotNone(connection.error)


class OptionalToolSchemas(unittest.TestCase):
    def test_schema_compaction_preserves_named_arguments_and_literal_json_values(self):
        schema = {
            "title": "Create a document",
            "type": "object",
            "properties": {
                "title": {"type": "string", "title": "Document title"},
                "default": {"const": {"title": "literal", "default": True}},
                "choice": {"enum": [{"title": "literal", "default": False}]},
            },
            "required": ["title", "default"],
            "$defs": {"title": {"type": "string", "default": "unused"}},
        }
        saved = copy.deepcopy(schema)
        compacted = actor.concise_schema(schema)
        self.assertNotIn("title", compacted)
        self.assertIn("title", compacted["properties"])
        self.assertIn("default", compacted["properties"])
        self.assertNotIn("title", compacted["properties"]["title"])
        self.assertEqual(
            compacted["properties"]["default"]["const"],
            {"title": "literal", "default": True},
        )
        self.assertEqual(
            compacted["properties"]["choice"]["enum"],
            [{"title": "literal", "default": False}],
        )
        self.assertEqual(compacted["$defs"]["title"], {"type": "string"})
        self.assertEqual(schema, saved)
        actor.validate(
            {"title": "Report", "default": {"title": "literal", "default": True}},
            compacted,
        )

    def test_reserved_duplicate_and_invalid_extra_names_are_rejected(self):
        for names in (
            ["use_tools"],
            ["shell"],
            ["echo", "echo"],
            ["bad.name"],
            ["x" * 65],
        ):
            with self.subTest(names=names), self.assertRaises(ValueError):
                actor.validate_extra_tools(
                    [SimpleNamespace(name=name) for name in names], {"shell"}
                )

    def test_distinct_compatible_extra_names_are_accepted(self):
        actor.validate_extra_tools(
            [SimpleNamespace(name="extra_echo"), SimpleNamespace(name="other-tool")],
            {"shell"},
        )


if __name__ == "__main__":
    unittest.main()
