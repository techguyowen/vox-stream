"""Benchmark harness for caption dispatch latency and WebSocket broadcast performance."""

import asyncio
import json
import statistics
import time
from obs_captioner.config import AppConfig
from obs_captioner.engines.base import TranscriptEvent
from obs_captioner.history import TranscriptHistory
from obs_captioner.obs.caption_sink import CaptionSink


from obs_captioner.web.server import WebServer


class MockWebSocket:
    def __init__(self, artificial_delay: float = 0.0):
        self.artificial_delay = artificial_delay
        self.sent_messages = []

    async def send_str(self, text: str):
        if self.artificial_delay > 0:
            await asyncio.sleep(self.artificial_delay)
        self.sent_messages.append(text)


class MockWebServer:
    def __init__(self, num_clients: int = 5, artificial_delay: float = 0.0005):
        # Borrow real broadcast_caption logic from WebServer
        self.caption_sockets = {
            MockWebSocket(artificial_delay): "en" for _ in range(num_clients)
        }
        self._recent_finals = []
        self._max_snapshot_lines = 10
        self._caption_translate_seq = 0
        self._caption_translate_chain = None
        self.control_sockets = set()
        self.history = TranscriptHistory()
        self._lock = asyncio.Lock()

    _record_snapshot = WebServer._record_snapshot
    broadcast_caption = WebServer.broadcast_caption


class MockObsClient:
    def __init__(self, rtt: float = 0.002):
        self.is_connected = True
        self.rtt = rtt
        self.update_count = 0

    async def update_text_source(self, name: str, text: str):
        if self.rtt > 0:
            await asyncio.sleep(self.rtt)
        self.update_count += 1
        return True


async def measure_round_trip_latency(
    iterations: int = 100,
    num_web_clients: int = 5,
    client_delay: float = 0.0005,
    obs_connected: bool = True,
    obs_delay: float = 0.002,
) -> dict:
    config = AppConfig()
    config.translation.enabled = False
    config.obs.enabled = obs_connected
    config.obs.update_text_source = obs_connected
    config.obs.text_source_name = "Captions"

    web_server = MockWebServer(num_clients=num_web_clients, artificial_delay=client_delay)
    obs_client = MockObsClient(rtt=obs_delay) if obs_connected else None
    sink = CaptionSink(config=config, web_server=web_server, obs_client=obs_client)

    latencies_ms = []

    # Warmup
    for _ in range(10):
        evt = TranscriptEvent(text="Warmup sentence.", is_final=True)
        await sink.handle_transcript(evt)

    for i in range(iterations):
        t0 = time.perf_counter()
        is_final = (i % 5 == 0)
        evt = TranscriptEvent(text=f"Benchmark sentence number {i}.", is_final=is_final)
        await sink.handle_transcript(evt)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(dt_ms)

    return {
        "iterations": iterations,
        "mean_ms": round(statistics.mean(latencies_ms), 3),
        "median_ms": round(statistics.median(latencies_ms), 3),
        "p95_ms": round(statistics.quantiles(latencies_ms, n=20)[18], 3),
        "min_ms": round(min(latencies_ms), 3),
        "max_ms": round(max(latencies_ms), 3),
    }


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    res = loop.run_until_complete(measure_round_trip_latency(iterations=200))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
