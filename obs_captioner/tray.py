"""Cross-platform System Tray Applet for VoxStream.

Provides a persistent notification icon in the Windows taskbar tray (and the
macOS menu bar via the same pystray code path) with quick-access controls for
opening the Web Dashboard, Stage Confidence Display, OBS Caption Overlay, and
Scripture Projector, as well as toggling live speech recognition and exiting
cleanly.

macOS note: on darwin, pystray drives an NSStatusItem (menu-bar extra, not a
Windows-style tray). Menu-bar icons must be small (~22px); larger PNGs are
downscaled automatically by :func:`get_tray_icon_image`. Retina Dock / Finder
branding uses a ``.icns`` bundle resource when present — see
:func:`get_macos_icns_path`. Dock badge updates go through
:func:`set_dock_badge` (PyObjC ``AppKit`` when installed, otherwise a no-op).
"""

from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("obs_captioner.tray")

#: Pixel size for macOS menu-bar (NSStatusItem) icons. Apple's Human Interface
#: Guidelines recommend ~18-22pt template images; 22px keeps the icon crisp on
#: both 1x and 2x menu bars when downscaled from the 48px PNG assets.
MACOS_MENU_BAR_ICON_SIZE = 22

#: User-facing note shown (logged) on macOS explaining menu-bar behavior.
MACOS_TRAY_NOTE = (
    "macOS: VoxStream runs as a menu-bar extra (right side of the menu bar, "
    "near the clock), not a Windows-style system tray. If the icon is hidden, "
    "check Control Center > Menu Bar Only settings. Dock badge updates require "
    "optional PyObjC (AppKit); without it the menu-bar icon still works."
)


def is_macos() -> bool:
    """Return True when running on macOS (darwin)."""
    return sys.platform == "darwin"


def get_macos_tray_note() -> str:
    """Return the user-facing note describing macOS menu-bar tray behavior."""
    return MACOS_TRAY_NOTE


def get_macos_icns_path() -> Optional[Path]:
    """Return the bundled macOS ``.icns`` icon path when one is packaged.

    Looks next to this module and in the web static assets for
    ``VoxStream.icns`` / ``AppIcon.icns`` (the names used by ``.app`` bundles
    and ``.dmg`` installers). Returns ``None`` when no ``.icns`` is shipped —
    callers then fall back to the generic PNG/pystray path.
    """
    candidates = [
        Path(__file__).parent / "VoxStream.icns",
        Path(__file__).parent / "AppIcon.icns",
        Path(__file__).parent / "web" / "static" / "VoxStream.icns",
        Path(__file__).parent / "web" / "static" / "AppIcon.icns",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def set_dock_badge(text: str) -> bool:
    """Set the macOS Dock tile badge label (e.g. caption status/count).

    Uses ``AppKit.NSApplication`` via PyObjC when available. Returns True when
    the badge was applied, False otherwise (non-macOS platform, PyObjC missing,
    or AppKit error). Never raises — safe to call on any platform.
    """
    if not is_macos():
        return False
    try:
        from AppKit import NSApplication  # type: ignore

        app = NSApplication.sharedApplication()
        app.setApplicationIconImage_(app.applicationIconImage())
        dock_tile = app.dockTile()
        dock_tile.setBadgeLabel_(str(text or ""))
        dock_tile.display()
        return True
    except Exception as e:
        logger.debug(f"Could not set macOS Dock badge: {e}")
        return False

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False


def is_tray_supported() -> bool:
    """Check if system tray dependencies are installed and accessible."""
    return HAS_PYSTRAY


def _create_fallback_icon(size: int = 64) -> "Image.Image":
    """Generate a crisp VoxStream icon programmatically if static files are not found."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background rounded badge: deep blue-slate
    margin = 4
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=12,
        fill=(15, 23, 42, 255),  # Slate 900
        outline=(56, 189, 248, 255),  # Sky 400
        width=2,
    )

    # Soundwave bars in vibrant cyan
    center_y = size // 2
    bar_color = (56, 189, 248, 255)
    bar_specs = [
        (16, 10),
        (24, 20),
        (32, 28),
        (40, 20),
        (48, 10),
    ]
    for x, h in bar_specs:
        y0 = center_y - (h // 2)
        y1 = center_y + (h // 2)
        draw.rounded_rectangle([x - 2, y0, x + 2, y1], radius=2, fill=bar_color)

    return img


def get_tray_icon_image(size: Optional[int] = None) -> Optional["Image.Image"]:
    """Load VoxStream branding icon from static web assets or generate fallback.

    On macOS (darwin) the returned image is downscaled to menu-bar size
    (:data:`MACOS_MENU_BAR_ICON_SIZE`) unless an explicit ``size`` is given,
    since NSStatusItem icons must be small template images.
    """
    if not HAS_PYSTRAY:
        return None

    static_dir = Path(__file__).parent / "web" / "static"
    candidates = [
        static_dir / "favicon-48x48.png",
        static_dir / "favicon-32x32.png",
        static_dir / "apple-touch-icon.png",
        static_dir / "favicon.ico",
    ]

    img: Optional["Image.Image"] = None
    for candidate in candidates:
        if candidate.is_file():
            try:
                img = Image.open(candidate).convert("RGBA")
                break
            except Exception as e:
                logger.debug(f"Could not load icon {candidate}: {e}")

    if img is None:
        img = _create_fallback_icon(64)

    target = size if size is not None else (MACOS_MENU_BAR_ICON_SIZE if is_macos() else None)
    if target is not None and max(img.size) > target:
        img = img.copy()
        img.thumbnail((target, target), Image.LANCZOS)
    return img


class VoxStreamTray:
    """System Tray Manager for VoxStream (Windows taskbar tray / macOS menu bar)."""

    def __init__(
        self,
        port: int = 8765,
        host: str = "localhost",
        on_pause: Optional[Callable[[], None]] = None,
        on_resume: Optional[Callable[[], None]] = None,
        on_restart: Optional[Callable[[], None]] = None,
        on_shutdown: Optional[Callable[[], None]] = None,
    ):
        self.port = port
        self.host = "localhost" if host in ("0.0.0.0", "127.0.0.1", "") else host
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_restart = on_restart
        self.on_shutdown = on_shutdown

        self.is_paused = False
        self.engine_name = "VoxStream"
        self._icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def update_status(self, is_paused: bool, engine_name: Optional[str] = None):
        """Update active status and redraw tray menu."""
        self.is_paused = is_paused
        if engine_name:
            self.engine_name = engine_name
        if self._icon and self._running:
            try:
                self._icon.title = f"VoxStream - {'Paused' if self.is_paused else 'Live Captions Active'}"
                self._icon.update_menu()
            except Exception as e:
                logger.debug(f"Failed updating tray menu: {e}")

    def _open_url(self, path: str):
        """Open a VoxStream web interface in default browser."""
        url = f"{self.base_url}{path}"
        logger.info(f"Opening browser from tray: {url}")
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.error(f"Failed to open browser URL {url}: {e}")

    def _toggle_pause(self, icon, item):
        """Toggle paused/running transcription state."""
        if self.is_paused:
            if self.on_resume:
                self.on_resume()
            self.is_paused = False
        else:
            if self.on_pause:
                self.on_pause()
            self.is_paused = True
        self.update_status(self.is_paused)

    def _handle_restart(self, icon, item):
        """Trigger engine restart callback."""
        logger.info("Restart requested from tray menu.")
        if self.on_restart:
            self.on_restart()

    def _handle_exit(self, icon, item):
        """Stop tray icon and initiate application shutdown."""
        logger.info("Shutdown requested from tray menu.")
        self.stop()
        if self.on_shutdown:
            self.on_shutdown()

    def _build_menu(self) -> "pystray.Menu":
        """Construct tray context menu items."""
        return pystray.Menu(
            pystray.MenuItem("🎙️ Open Control Dashboard", lambda: self._open_url("/dashboard"), default=True),
            pystray.MenuItem("📺 Open Stage Confidence Display", lambda: self._open_url("/display")),
            pystray.MenuItem("🎬 Open OBS Caption Overlay", lambda: self._open_url("/overlay")),
            pystray.MenuItem("📖 Open Scripture Projector", lambda: self._open_url("/bible")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda text: f"Status: {'⏸️ Paused' if self.is_paused else '🟢 Transcribing'}",
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                lambda text: "▶️ Resume Captions" if self.is_paused else "⏸️ Pause Captions",
                self._toggle_pause,
            ),
            pystray.MenuItem("🔄 Restart Engine", self._handle_restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 Exit VoxStream", self._handle_exit),
        )

    def start(self) -> bool:
        """Initialize and run system tray icon on a dedicated background thread."""
        if not HAS_PYSTRAY:
            logger.warning("pystray or Pillow is not installed. System tray icon disabled.")
            return False

        if self._running:
            return True

        img = get_tray_icon_image()
        if not img:
            logger.warning("Could not load or generate tray icon image.")
            return False

        try:
            self._icon = pystray.Icon(
                name="VoxStream",
                icon=img,
                title="VoxStream - Live Speech Captions",
                menu=self._build_menu(),
            )
            self._running = True

            if is_macos():
                logger.info(get_macos_tray_note())

            def _run():
                try:
                    logger.info("🟢 System Tray Applet running.")
                    self._icon.run()
                except Exception as e:
                    logger.debug(f"Tray applet stopped: {e}")
                finally:
                    self._running = False

            self._thread = threading.Thread(target=_run, name="VoxStreamTrayThread", daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            logger.warning(f"Could not start system tray icon: {e}")
            self._running = False
            return False

    def stop(self):
        """Stop and tear down tray icon."""
        self._running = False
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
