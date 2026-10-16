import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from obs_captioner.config import AppConfig
from obs_captioner.web.server import WebOverlayServer


class FakeWs:
    def __init__(self, block_send=False):
        self.sent = []
        self.closed = False
        self.block_send = block_send
        self.send_gate = asyncio.Event()

    async def send_str(self, msg: str):
        if self.block_send:
            await self.send_gate.wait()
        self.sent.append(msg)

    async def close(self, *args, **kwargs):
        self.closed = True


class FakeRequest:
    def __init__(self, remote="127.0.0.1", host="localhost:8765", headers=None):
        self.remote = remote
        self.host = host
        self.headers = headers or {}


class TestPerfWeb(unittest.IsolatedAsyncioTestCase):
    async def test_fake_slow_ws_does_not_block_broadcast_caption(self):
        server = WebOverlayServer(AppConfig())
        slow_ws = FakeWs(block_send=True)
        fast_ws = FakeWs(block_send=False)

        server.caption_sockets[slow_ws] = "en"
        server.caption_sockets[fast_ws] = "en"
        server._ensure_caption_queue(slow_ws)
        server._ensure_caption_queue(fast_ws)

        t0 = time.monotonic()
        await server.broadcast_caption({"text": "Hello fast", "is_final": False})
        duration = time.monotonic() - t0

        self.assertLess(duration, 0.1)
        await asyncio.sleep(0.02)
        self.assertEqual(len(fast_ws.sent), 1)
        self.assertIn("Hello fast", fast_ws.sent[0])
        self.assertIn(slow_ws, server.caption_sockets)
        self.assertFalse(slow_ws.closed)

        await server.stop()

    async def test_interim_drop_on_full_queue(self):
        server = WebOverlayServer(AppConfig())
        slow_ws = FakeWs(block_send=True)
        server.caption_sockets[slow_ws] = "en"
        q = server._ensure_caption_queue(slow_ws)

        # Start sender task and block on first send_str
        await server.broadcast_caption({"text": "Initial message", "is_final": True})
        await asyncio.sleep(0.01)

        # Fill queue to capacity with interims
        for i in range(q.maxsize):
            await server.broadcast_caption({"text": f"Interim {i}", "is_final": False})

        self.assertTrue(q.full())

        # Sending another interim drops queued interims and enqueues new one without closing client
        await server.broadcast_caption({"text": "New interim", "is_final": False})
        self.assertIn(slow_ws, server.caption_sockets)
        self.assertFalse(slow_ws.closed)

        await server.stop()

    async def test_final_overflow_closes_client(self):
        server = WebOverlayServer(AppConfig())
        slow_ws = FakeWs(block_send=True)
        server.caption_sockets[slow_ws] = "en"
        q = server._ensure_caption_queue(slow_ws)

        # Initial message starts sender task and blocks it
        await server.broadcast_caption({"text": "Initial final", "is_final": True})
        await asyncio.sleep(0.01)

        # Fill queue to capacity with finals
        for i in range(q.maxsize):
            await server.broadcast_caption({"text": f"Final line {i}", "is_final": True})

        self.assertTrue(q.full())

        # Next final arrives with 0 interims to drop -> client closed
        await server.broadcast_caption({"text": "Overflow final", "is_final": True})
        await asyncio.sleep(0.02)

        self.assertNotIn(slow_ws, server.caption_sockets)
        self.assertTrue(slow_ws.closed)

        await server.stop()

    async def test_non_english_finals_concurrent_unique_translations(self):
        server = WebOverlayServer(AppConfig())
        ws_es1 = FakeWs()
        ws_es2 = FakeWs()
        ws_de = FakeWs()

        server.caption_sockets[ws_es1] = "es"
        server.caption_sockets[ws_es2] = "es"
        server.caption_sockets[ws_de] = "de"

        translated_calls = []

        async def fake_translate(text, target_lang="es", **kwargs):
            translated_calls.append(target_lang)
            await asyncio.sleep(0.02)
            return f"[{target_lang}] {text}"

        server.translator.translate_to_language = fake_translate

        await server.broadcast_caption({"text": "Hallelujah", "is_final": True})

        chain = getattr(server, "_caption_translate_chain", None)
        if chain:
            await chain

        await asyncio.sleep(0.02)

        self.assertEqual(sorted(translated_calls), ["de", "es"])
        self.assertEqual(len(ws_es1.sent), 1)
        self.assertEqual(len(ws_es2.sent), 1)
        self.assertEqual(len(ws_de.sent), 1)
        self.assertIn("[es] Hallelujah", ws_es1.sent[0])
        self.assertIn("[es] Hallelujah", ws_es2.sent[0])
        self.assertIn("[de] Hallelujah", ws_de.sent[0])

        await server.stop()

    async def test_snapshot_on_connect_non_english(self):
        server = WebOverlayServer(AppConfig())
        server._recent_finals = [
            {"text": "Line one", "is_final": True},
            {"text": "Line two", "is_final": True},
        ]

        async def fake_translate(text, target_lang="es", **kwargs):
            await asyncio.sleep(0.02)
            return f"translated_{text}"

        server.translator.translate_to_language = fake_translate

        class MockCaptionWs(FakeWs):
            async def prepare(self, req):
                pass

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(0.05)
                raise StopAsyncIteration

        ws = MockCaptionWs()
        with patch("aiohttp.web.WebSocketResponse", return_value=ws):
            req = SimpleNamespace(
                query={"lang": "es", "role": "display"},
                path="/ws",
                headers={},
                remote="127.0.0.1",
            )
            await server._handle_caption_ws(req)

        await asyncio.sleep(0.08)

        self.assertGreaterEqual(len(ws.sent), 2)
        msg1 = json.loads(ws.sent[0])
        msg2 = json.loads(ws.sent[1])

        self.assertEqual(msg1["type"], "snapshot")
        self.assertEqual(msg1["lines"][0]["text"], "Line one")

        self.assertEqual(msg2["type"], "snapshot")
        self.assertEqual(msg2["lines"][0]["text"], "translated_Line one")

        await server.stop()

    def test_status_cache_returns_same_object_within_ttl(self):
        server = WebOverlayServer(AppConfig())
        req = FakeRequest(host="testserver:8765")

        res1 = server._get_status_payload(req)
        res2 = server._get_status_payload(req)

        self.assertIs(res1, res2)

        with patch("time.monotonic", return_value=time.monotonic() + 3.0):
            res3 = server._get_status_payload(req)
            self.assertIsNot(res3, res1)

    def test_status_cache_keyed_by_request_host(self):
        server = WebOverlayServer(AppConfig())
        with patch("obs_captioner.hardware.get_local_ip", return_value="127.0.0.1"):
            req_a = FakeRequest(host="server-a:8765")
            req_b = FakeRequest(host="server-b:8765")

            res_a = server._get_status_payload(req_a)
            res_b = server._get_status_payload(req_b)

            self.assertIsNot(res_a, res_b)
            self.assertIn("server-a", res_a["display_url"])
            self.assertIn("server-b", res_b["display_url"])

    def test_client_ip_with_and_without_caddy(self):
        cfg = AppConfig()
        cfg.caddy.enabled = False
        server = WebOverlayServer(cfg)

        req1 = FakeRequest(remote="127.0.0.1", headers={"X-Forwarded-For": "203.0.113.195, 10.0.0.1"})
        self.assertEqual(server._client_ip(req1), "127.0.0.1")

        req2 = FakeRequest(remote="192.168.1.100", headers={"X-Forwarded-For": "203.0.113.195"})
        self.assertEqual(server._client_ip(req2), "192.168.1.100")

        cfg.caddy.enabled = True
        server_caddy = WebOverlayServer(cfg)

        req3 = FakeRequest(remote="127.0.0.1", headers={"X-Forwarded-For": "203.0.113.195, 10.0.0.1"})
        self.assertEqual(server_caddy._client_ip(req3), "203.0.113.195")

        req4 = FakeRequest(remote="::1", headers={"X-Forwarded-For": "198.51.100.42"})
        self.assertEqual(server_caddy._client_ip(req4), "198.51.100.42")

        req5 = FakeRequest(remote="127.0.0.1", headers={})
        self.assertEqual(server_caddy._client_ip(req5), "127.0.0.1")

        req6 = FakeRequest(remote="192.168.1.100", headers={"X-Forwarded-For": "203.0.113.195"})
        self.assertEqual(server_caddy._client_ip(req6), "192.168.1.100")

    async def test_broadcast_translated_audio_routes_only_to_target_lang(self):
        cfg = AppConfig()
        cfg.translation.enabled = True
        cfg.translation.target_language = "es"
        server = WebOverlayServer(cfg)

        ws_es = FakeWs(block_send=False)
        ws_en = FakeWs(block_send=False)
        ws_fr = FakeWs(block_send=False)

        server.caption_sockets[ws_es] = "es"
        server.caption_sockets[ws_en] = "en"
        server.caption_sockets[ws_fr] = "fr"
        server.socket_roles[ws_es] = "display"
        server.socket_roles[ws_en] = "display"
        server.socket_roles[ws_fr] = "display"

        server._ensure_caption_queue(ws_es)
        server._ensure_caption_queue(ws_en)
        server._ensure_caption_queue(ws_fr)

        pcm_data = b"\x00\x01\x02\x03\x04\x05"
        server.broadcast_translated_audio(pcm_data, mime_type="audio/pcm;rate=24000")

        await asyncio.sleep(0.02)

        # Only ws_es should have received the translated audio chunk
        self.assertEqual(len(ws_en.sent), 0)
        self.assertEqual(len(ws_fr.sent), 0)
        self.assertEqual(len(ws_es.sent), 1)

        msg = json.loads(ws_es.sent[0])
        self.assertEqual(msg["type"], "translated_audio")
        self.assertEqual(msg["mime"], "audio/pcm;rate=24000")
        import base64
        self.assertEqual(base64.b64decode(msg["data"]), pcm_data)

        # Empty bytes should be ignored
        server.broadcast_translated_audio(b"")
        await asyncio.sleep(0.01)
        self.assertEqual(len(ws_es.sent), 1)

        await server.stop()

