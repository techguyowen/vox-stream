"""Performance and concurrency tests for OBS output worker in CaptionSink.

Verifies non-blocking dispatch, latest-value interim coalescing, lossless
finals, strict FIFO CEA-608 streaming, and clean shutdown.
"""

import asyncio
import time
import unittest

from obs_captioner.config import AppConfig
from obs_captioner.engines.base import TranscriptEvent
from obs_captioner.obs.caption_sink import CaptionSink


class FakeSlowOBSClient:
    """Mock OBS WebSocket client with asyncio.Event-gated RPCs."""

    def __init__(self):
        self.is_connected = True
        self.text_updates = []
        self.cea_captions = []
        self.update_gate = asyncio.Event()
        self.cea_gate = asyncio.Event()
        self.update_gate.set()
        self.cea_gate.set()
        self.update_call_count = 0
        self.cea_call_count = 0

    async def update_text_source(self, source_name: str, text: str) -> bool:
        self.update_call_count += 1
        await self.update_gate.wait()
        self.text_updates.append((source_name, text))
        return True

    async def send_stream_caption(self, caption_text: str) -> bool:
        self.cea_call_count += 1
        await self.cea_gate.wait()
        self.cea_captions.append(caption_text)
        return True


class TestPerfOBSOutputWorker(unittest.IsolatedAsyncioTestCase):
    """Behavioral tests verifying OBS worker performance and semantics."""

    def setUp(self):
        self.cfg = AppConfig()
        self.cfg.obs.enabled = True
        self.cfg.obs.update_text_source = True
        self.cfg.obs.text_source_name = "Live Captions"
        self.cfg.overlay.final_only = False
        self.cfg.obs.send_cea608_captions = True
        self.cfg.overlay.auto_hide_seconds = 0.0  # disable timer for deterministic tests
        self.obs = FakeSlowOBSClient()
        self.sink = CaptionSink(self.cfg, obs_client=self.obs)

    async def asyncTearDown(self):
        await self.sink.stop()

    async def test_handle_transcript_returns_promptly_while_obs_is_blocked(self):
        """handle_transcript must enqueue and return without awaiting slow OBS RPC."""
        self.obs.update_gate.clear()  # Block OBS text RPC

        evt = TranscriptEvent(text="Hello church family", is_final=False)
        t0 = time.perf_counter()
        await self.sink.handle_transcript(evt)
        elapsed = time.perf_counter() - t0

        # Must return promptly (< 100ms) even though OBS RPC is blocked indefinitely
        self.assertLess(elapsed, 0.1)
        self.assertEqual(self.obs.update_call_count, 1)
        self.assertEqual(len(self.obs.text_updates), 0)

        # Unblock and verify RPC completes
        self.obs.update_gate.set()
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.obs.text_updates), 1)
        self.assertEqual(self.obs.text_updates[0][1], "Hello church family")

    async def test_burst_of_interims_coalesces_to_latest_value(self):
        """While an update is in flight, newer interims overwrite pending one so only latest is sent."""
        self.obs.update_gate.clear()

        # Initial interim: enters worker and blocks at update_gate
        await self.sink.handle_transcript(TranscriptEvent(text="Welcome", is_final=False))
        self.assertEqual(self.obs.update_call_count, 1)

        # Burst of subsequent interims while update 1 is in flight
        for partial in ["Welcome to", "Welcome to our", "Welcome to our Sunday", "Welcome to our Sunday service"]:
            t0 = time.perf_counter()
            await self.sink.handle_transcript(TranscriptEvent(text=partial, is_final=False))
            self.assertLess(time.perf_counter() - t0, 0.1)

        # Still only update 1 has been called because it is in flight
        self.assertEqual(self.obs.update_call_count, 1)

        # Unblock OBS RPC
        self.obs.update_gate.set()
        await asyncio.sleep(0.05)

        # Update 1 ("Welcome") and latest coalesced interim ("Welcome to our Sunday service")
        # Intermediate partials must have been coalesced and dropped!
        sent_texts = [t[1] for t in self.obs.text_updates]
        self.assertEqual(sent_texts, ["Welcome", "Welcome to our Sunday service"])
        self.assertEqual(len(self.obs.text_updates), 2)

    async def test_finals_are_never_lost_or_overwritten_by_later_interims(self):
        """Finals must never be dropped or overwritten by later interims."""
        self.obs.update_gate.clear()

        # Update 1 in flight
        await self.sink.handle_transcript(TranscriptEvent(text="Initial speech", is_final=False))

        # While blocked, send interims, then a final, then interims for next utterance
        await self.sink.handle_transcript(TranscriptEvent(text="Initial speech partial", is_final=False))
        await self.sink.handle_transcript(TranscriptEvent(text="Initial speech completed.", is_final=True))
        await self.sink.handle_transcript(TranscriptEvent(text="Next sentence part A", is_final=False))
        await self.sink.handle_transcript(TranscriptEvent(text="Next sentence part B", is_final=False))

        # Unblock OBS RPC
        self.obs.update_gate.set()
        await asyncio.sleep(0.05)

        sent_texts = [t[1] for t in self.obs.text_updates]
        # Must include:
        # 1. "Initial speech" (first in-flight)
        # 2. "Initial speech completed." (FINAL - never lost!)
        # 3. "Next sentence part B" (latest coalesced interim of second utterance)
        self.assertIn("Initial speech completed.", sent_texts)
        self.assertEqual(sent_texts, ["Initial speech", "Initial speech completed.", "Next sentence part B"])

    async def test_cea608_sends_are_fifo_and_complete_without_blocking_sink(self):
        """CEA-608 stream captions must be strict FIFO, lossless, and non-blocking."""
        self.obs.cea_gate.clear()

        finals = [
            "Alpha Scripture reading.",
            "Beta Scripture reading.",
            "Gamma Scripture reading.",
        ]

        # Enqueue finals while CEA-608 is blocked
        for text in finals:
            t0 = time.perf_counter()
            await self.sink.handle_transcript(TranscriptEvent(text=text, is_final=True))
            self.assertLess(time.perf_counter() - t0, 0.1)

        # Sink returned promptly for all
        self.assertEqual(len(self.obs.cea_captions), 0)
        self.assertEqual(self.obs.cea_call_count, 1)

        # Unblock and verify strict FIFO completion
        self.obs.cea_gate.set()
        await asyncio.sleep(0.05)

        self.assertEqual(self.obs.cea_captions, finals)

    async def test_boundary_stitch_rewrites_and_clears_preserve_ordering(self):
        """Boundary stitch rewrites and auto-clears preserve ordering through text queue."""
        # Setup history with split entry ('author' + 'forty' -> 'authority')
        self.sink.history.add_entry(text="The author", start_time=1.0, end_time=2.0)
        self.sink._last_final_time = time.time()

        # Send second chunk that triggers stitch absorption
        stitch_event = TranscriptEvent(text="forty", is_final=True)
        await self.sink.handle_transcript(stitch_event)

        await asyncio.sleep(0.05)
        sent_texts = [t[1] for t in self.obs.text_updates]
        self.assertIn("The authority", sent_texts)

    async def test_disconnected_obs_silently_noops(self):
        """When OBS is disconnected, calls must skip cleanly without raising."""
        self.obs.is_connected = False

        evt = TranscriptEvent(text="Speaking without OBS", is_final=False)
        await self.sink.handle_transcript(evt)
        await asyncio.sleep(0.05)

        self.assertEqual(len(self.obs.text_updates), 0)
        self.assertEqual(self.obs.update_call_count, 0)

    async def test_rpc_errors_logged_at_debug_never_crash_worker(self):
        """RPC exceptions from OBS must not crash the worker loop."""
        call_count = 0

        async def failing_update(source_name: str, text: str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionResetError("OBS disconnected abruptly")
            self.obs.text_updates.append((source_name, text))
            return True

        self.obs.update_text_source = failing_update

        # First event triggers error
        await self.sink.handle_transcript(TranscriptEvent(text="Event alpha", is_final=False))
        await asyncio.sleep(0.05)

        # Second event must still succeed through the resilient worker
        await self.sink.handle_transcript(TranscriptEvent(text="Event beta", is_final=True))
        await asyncio.sleep(0.05)

        sent_texts = [t[1] for t in self.obs.text_updates]
        self.assertEqual(sent_texts, ["Event beta."])

    async def test_sink_stop_cancels_background_workers_cleanly(self):
        """sink.stop() cancels worker tasks and empties queues."""
        self.obs.update_gate.clear()
        await self.sink.handle_transcript(TranscriptEvent(text="Hanging event", is_final=False))

        self.assertIsNotNone(self.sink._obs_text_worker_task)
        await self.sink.stop()

        self.assertIsNone(self.sink._obs_text_worker_task)
        self.assertEqual(len(self.sink._obs_text_queue), 0)
        self.assertEqual(len(self.sink._obs_cea_queue), 0)
