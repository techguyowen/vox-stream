"""Auto-stop captioning scheduler for VoxStream.

Handles:
- One-time countdown timers (duration or clock end-time).
- Persistent recurring weekly schedules (e.g. stop every Sunday at 12:30).
- Background ticker that checks timers and fires callbacks.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from .config import WeeklySchedule, SchedulerConfig

logger = logging.getLogger("obs_captioner.scheduler")

# Map full weekday names (datetime.strftime) to short labels for the UI
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_time_str(time_str: str) -> Optional[tuple]:
    """Parse a time string like '12:30', '12:30 PM', '09:00 AM' into (hour, minute) in 24hr format.
    Returns None if unparsable.
    """
    if not time_str or not time_str.strip():
        return None
    t = time_str.strip().upper()
    is_pm = "PM" in t
    is_am = "AM" in t
    t = t.replace("AM", "").replace("PM", "").strip()
    parts = t.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None

    if is_pm and hour != 12:
        hour += 12
    elif is_am and hour == 12:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return (hour, minute)


def _resolve_next_occurrence(hour: int, minute: int) -> float:
    """Return the Unix timestamp of the next occurrence of HH:MM local time.
    If the time already passed today, returns tomorrow's occurrence.
    """
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp()


class CaptionScheduler:
    """Manages auto-stop timers and recurring weekly schedules for VoxStream.

    Callbacks:
        on_stop_callback: Invoked to stop captioning.
        on_start_callback: Invoked to start captioning.
        on_broadcast: Invoked with a dict payload to broadcast to WebSocket clients.
    """

    def __init__(
        self,
        scheduler_config: SchedulerConfig,
        on_stop_callback: Callable[[], None],
        on_start_callback: Callable[[], None],
        on_broadcast: Optional[Callable[[dict], None]] = None,
    ):
        self._config = scheduler_config
        self._on_stop = on_stop_callback
        self._on_start = on_start_callback
        self._on_broadcast = on_broadcast

        # One-time timer state
        self._timer_end: Optional[float] = None   # Unix timestamp when timer should fire
        self._timer_cancelled = False

        # Recurring schedule tracking – store last-fired tuple (weekday_name, "HH:MM") per rule_id
        # so we don't double-fire within the same minute
        self._last_fired: Dict[str, str] = {}  # rule_id -> "Weekday HH:MM"

        # Background task handle
        self._task: Optional[asyncio.Task] = None

    # ──────────────────────────────────────────────────────────────
    # One-time Timer API
    # ──────────────────────────────────────────────────────────────

    def set_duration(self, seconds: float) -> float:
        """Start a countdown timer that fires after `seconds`. Returns target timestamp."""
        target = time.time() + max(1.0, float(seconds))
        self._timer_end = target
        self._timer_cancelled = False
        logger.info(f"⏱️ Auto-stop timer set: fires in {seconds:.0f}s at {datetime.fromtimestamp(target).strftime('%H:%M:%S')}")
        self._broadcast_status()
        return target

    def set_end_time(self, time_str: str) -> Optional[float]:
        """Start a timer that fires at the next occurrence of a given clock time (HH:MM or HH:MM AM/PM).
        Returns target timestamp, or None if parsing fails.
        """
        parsed = _parse_time_str(time_str)
        if not parsed:
            logger.warning(f"⏱️ Could not parse time string: '{time_str}'")
            return None
        hour, minute = parsed
        target = _resolve_next_occurrence(hour, minute)
        self._timer_end = target
        self._timer_cancelled = False
        logger.info(f"⏱️ Auto-stop timer set: fires at {datetime.fromtimestamp(target).strftime('%A %H:%M')} (local)")
        self._broadcast_status()
        return target

    def cancel_timer(self):
        """Cancel the active one-time countdown."""
        if self._timer_end is not None:
            self._timer_end = None
            self._timer_cancelled = True
            logger.info("⏱️ Auto-stop timer cancelled.")
            self._broadcast_status()

    def get_timer_status(self) -> dict:
        """Return current timer state suitable for API responses."""
        now = time.time()
        if self._timer_end is not None and self._timer_end > now:
            remaining = self._timer_end - now
            return {
                "active": True,
                "remaining_seconds": round(remaining, 1),
                "target_timestamp": self._timer_end,
                "target_formatted": datetime.fromtimestamp(self._timer_end).strftime("%I:%M %p"),
            }
        return {
            "active": False,
            "remaining_seconds": 0,
            "target_timestamp": None,
            "target_formatted": None,
        }

    # ──────────────────────────────────────────────────────────────
    # Weekly Schedule API
    # ──────────────────────────────────────────────────────────────

    def get_schedules(self) -> List[WeeklySchedule]:
        return list(self._config.schedules)

    def add_schedule(self, data: dict) -> WeeklySchedule:
        """Create and register a new WeeklySchedule from a dict payload."""
        schedule = WeeklySchedule(
            id=data.get("id") or str(uuid.uuid4()),
            name=data.get("name", "Weekly Schedule"),
            days=data.get("days") or [],
            stop_time=data.get("stop_time", ""),
            start_time=data.get("start_time", ""),
            enabled=data.get("enabled", True),
        )
        self._config.schedules.append(schedule)
        logger.info(f"📅 Added schedule '{schedule.name}' (id={schedule.id}) days={schedule.days} stop={schedule.stop_time}")
        return schedule

    def update_schedule(self, schedule_id: str, data: dict) -> Optional[WeeklySchedule]:
        """Update an existing schedule by id. Returns updated schedule or None."""
        for s in self._config.schedules:
            if s.id == schedule_id:
                if "name" in data:
                    s.name = data["name"]
                if "days" in data:
                    s.days = data["days"]
                if "stop_time" in data:
                    s.stop_time = data["stop_time"]
                if "start_time" in data:
                    s.start_time = data["start_time"]
                if "enabled" in data:
                    s.enabled = bool(data["enabled"])
                logger.info(f"📅 Updated schedule '{s.name}' (id={s.id})")
                return s
        return None

    def remove_schedule(self, schedule_id: str) -> bool:
        """Remove a schedule by id. Returns True if removed."""
        before = len(self._config.schedules)
        self._config.schedules = [s for s in self._config.schedules if s.id != schedule_id]
        if len(self._config.schedules) < before:
            self._last_fired.pop(schedule_id, None)
            logger.info(f"📅 Removed schedule id={schedule_id}")
            return True
        return False

    def get_next_event(self) -> Optional[dict]:
        """Return the next scheduled start or stop event across all active rules."""
        now = datetime.now()
        best = None
        best_dt = None

        for s in self._config.schedules:
            if not s.enabled:
                continue

            for action, tstr in [("stop", s.stop_time), ("start", s.start_time)]:
                if not tstr:
                    continue
                parsed = _parse_time_str(tstr)
                if not parsed:
                    continue
                h, m = parsed

                for day_name in (s.days or WEEKDAY_NAMES):
                    try:
                        target_wd = WEEKDAY_NAMES.index(day_name)
                    except ValueError:
                        continue
                    current_wd = now.weekday()
                    # Days until next occurrence (0 = today)
                    days_ahead = (target_wd - current_wd) % 7
                    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=days_ahead)
                    if candidate <= now:
                        candidate += timedelta(days=7)
                    if best_dt is None or candidate < best_dt:
                        best_dt = candidate
                        best = {
                            "action": action,
                            "schedule_name": s.name,
                            "day": day_name,
                            "time_formatted": candidate.strftime("%I:%M %p"),
                            "datetime_formatted": candidate.strftime("%A at %I:%M %p"),
                            "timestamp": candidate.timestamp(),
                        }

        return best

    # ──────────────────────────────────────────────────────────────
    # Background Ticker
    # ──────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop):
        """Launch the background ticker coroutine."""
        self._task = loop.create_task(self._ticker_loop())

    def stop(self):
        """Cancel the background ticker task."""
        if self._task and not self._task.done():
            self._task.cancel()

    async def _ticker_loop(self):
        """Checks timers and weekly schedules every second."""
        logger.info("📅 Caption Scheduler started.")
        while True:
            try:
                await asyncio.sleep(1.0)
                self._check_one_time_timer()
                if self._config.enabled:
                    self._check_weekly_schedules()
            except asyncio.CancelledError:
                logger.info("📅 Caption Scheduler stopped.")
                break
            except Exception as e:
                logger.error(f"Caption Scheduler error: {e}", exc_info=True)

    def _check_one_time_timer(self):
        """Fire one-time timer if it has expired."""
        if self._timer_end is None:
            return
        now = time.time()
        if now >= self._timer_end:
            logger.info("⏱️ Auto-stop timer expired — stopping captioning.")
            self._timer_end = None
            try:
                self._on_stop()
            except Exception as e:
                logger.error(f"Auto-stop callback error: {e}")
            self._broadcast({
                "type": "scheduler_status",
                "event": "timer_expired",
                "message": "⏱️ Auto-Stop timer expired — captioning stopped.",
                "timer": self.get_timer_status(),
            })
        else:
            # Periodic tick update every ~5 seconds (roughly; tick is 1s so filter by modulo)
            remaining = self._timer_end - now
            if int(remaining) % 5 == 0:
                self._broadcast_status()

    def _check_weekly_schedules(self):
        """Check recurring weekly rules and fire stop/start callbacks when matched."""
        now = datetime.now()
        weekday_name = now.strftime("%A")   # e.g. "Sunday"
        current_hhmm = now.strftime("%H:%M")

        for s in self._config.schedules:
            if not s.enabled:
                continue

            for action, tstr in [("stop", s.stop_time), ("start", s.start_time)]:
                if not tstr:
                    continue
                parsed = _parse_time_str(tstr)
                if not parsed:
                    continue
                h, m = parsed
                rule_hhmm = f"{h:02d}:{m:02d}"

                # Day match
                if weekday_name not in (s.days or WEEKDAY_NAMES):
                    continue

                # Time match (within the current minute)
                if current_hhmm != rule_hhmm:
                    continue

                # Deduplication: fire at most once per rule per minute
                fire_key = f"{weekday_name} {rule_hhmm} {action}"
                last = self._last_fired.get(s.id + action)
                if last == fire_key:
                    continue
                self._last_fired[s.id + action] = fire_key

                logger.info(f"📅 Recurring schedule '{s.name}': {action} at {weekday_name} {rule_hhmm}")
                try:
                    if action == "stop":
                        self._on_stop()
                    else:
                        self._on_start()
                except Exception as e:
                    logger.error(f"Recurring schedule callback error: {e}")
                self._broadcast({
                    "type": "scheduler_status",
                    "event": f"recurring_{action}",
                    "schedule_name": s.name,
                    "message": f"📅 Scheduled {action}: '{s.name}' fired at {weekday_name} {rule_hhmm}",
                    "timer": self.get_timer_status(),
                })

    def _broadcast_status(self):
        """Broadcast current scheduler status to all WebSocket clients."""
        self._broadcast({
            "type": "scheduler_status",
            "event": "status",
            "timer": self.get_timer_status(),
            "next_event": self.get_next_event(),
            "scheduler_enabled": self._config.enabled,
        })

    def _broadcast(self, payload: dict):
        """Invoke the broadcast callback if set."""
        if self._on_broadcast:
            try:
                self._on_broadcast(payload)
            except Exception as e:
                logger.debug(f"Scheduler broadcast error: {e}")
