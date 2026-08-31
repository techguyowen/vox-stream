"""OBS Studio Python Script for Real-Time Live Captioner.

Install in OBS Studio via:
  Tools -> Scripts -> Python Settings (Configure Python 3.10/3.11 path) -> Scripts -> (+) Add obs_live_captions.py
"""

import os
import subprocess
import sys
from pathlib import Path

# Note: obspython is injected by OBS Studio when loaded as a script
try:
    import obspython as obs
except ImportError:
    obs = None

# Global state
process = None
python_path = sys.executable or "python"
auto_start_obs = True
auto_start_stream = True
selected_engine = "google_web"
text_source_name = "Live Captions"


def script_description():
    return """<h2>OBS Real-Time Live Captioner</h2>
<p>Automatically manages low-latency real-time live captions powered by Google STT, Gemini Live, or Local Whisper.</p>
<hr/>
"""


def start_captioner():
    global process
    if process is not None and process.poll() is None:
        print("[Live Captions] Captioner is already running.")
        return

    script_dir = Path(__file__).parent.parent.resolve()
    venv_python_win = script_dir / ".venv" / "Scripts" / "python.exe"
    venv_python_unix = script_dir / ".venv" / "bin" / "python"

    if venv_python_win.exists():
        py_exe = str(venv_python_win)
    elif venv_python_unix.exists():
        py_exe = str(venv_python_unix)
    else:
        py_exe = python_path

    cmd = [py_exe, "-m", "obs_captioner.main", "--engine", selected_engine]
    print(f"[Live Captions] Launching captioner process: {' '.join(cmd)}")

    try:
        creationflags = 0
        if sys.platform == "win32":
            # CREATE_NO_WINDOW = 0x08000000 to run silently in background
            creationflags = 0x08000000

        process = subprocess.Popen(
            cmd,
            cwd=str(script_dir),
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[Live Captions] Captioner started (PID: {process.pid})")
    except Exception as e:
        print(f"[Live Captions] Failed to launch captioner: {e}")


def stop_captioner():
    global process
    if process is not None:
        print("[Live Captions] Terminating captioner process...")
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        process = None
        print("[Live Captions] Captioner stopped.")


def on_event(event):
    if obs is None:
        return

    if event == obs.OBS_FRONTEND_EVENT_FINISHED_LOADING:
        if auto_start_obs:
            print("[Live Captions] OBS loaded. Auto-starting captioner...")
            start_captioner()

    elif event in (obs.OBS_FRONTEND_EVENT_STREAMING_STARTED, obs.OBS_FRONTEND_EVENT_RECORDING_STARTED):
        if auto_start_stream:
            print("[Live Captions] Stream/Recording started. Ensuring captioner is active...")
            start_captioner()

    elif event in (obs.OBS_FRONTEND_EVENT_STREAMING_STOPPED, obs.OBS_FRONTEND_EVENT_RECORDING_STOPPED):
        if auto_start_stream and not auto_start_obs:
            print("[Live Captions] Stream/Recording stopped. Stopping captioner...")
            stop_captioner()

    elif event == obs.OBS_FRONTEND_EVENT_EXIT:
        stop_captioner()


def script_load(settings):
    if obs:
        obs.obs_frontend_add_event_callback(on_event)
    print("[Live Captions] Script loaded.")


def script_unload():
    stop_captioner()
    print("[Live Captions] Script unloaded.")


def script_properties():
    if obs is None:
        return None

    props = obs.obs_properties_create()

    # Engine selection
    engine_list = obs.obs_properties_add_list(
        props,
        "engine",
        "STT Engine",
        obs.OBS_COMBO_TYPE_LIST,
        obs.OBS_COMBO_FORMAT_STRING,
    )
    obs.obs_property_list_add_string(engine_list, "Google Speech (Free / Zero-Setup - Recommended)", "google_web")
    obs.obs_property_list_add_string(engine_list, "Local Vosk / Kaldi (Fastest Offline Streaming)", "vosk")
    obs.obs_property_list_add_string(engine_list, "Local Moonshine (5x Faster than Whisper)", "moonshine")
    obs.obs_property_list_add_string(engine_list, "Local Faster-Whisper (Offline GPU/CPU)", "local_whisper")
    obs.obs_property_list_add_string(engine_list, "Gemini Live API", "gemini_live")
    obs.obs_property_list_add_string(engine_list, "Google Cloud STT (API Key)", "google_stt")
    obs.obs_property_list_add_string(engine_list, "Bandwidth Labs Live STT (Streaming)", "bandwidth")

    # Auto start options
    obs.obs_properties_add_bool(props, "auto_start_obs", "Auto-start when OBS launches")
    obs.obs_properties_add_bool(props, "auto_start_stream", "Auto-start when Streaming/Recording starts")

    # Text source name
    obs.obs_properties_add_text(props, "text_source_name", "OBS Text Source Name", obs.OBS_TEXT_DEFAULT)

    # Control buttons
    obs.obs_properties_add_button(props, "btn_start", "▶ Start Captioner Now", lambda p, b: (start_captioner(), True)[1])
    obs.obs_properties_add_button(props, "btn_stop", "⏹ Stop Captioner", lambda p, b: (stop_captioner(), True)[1])

    return props


def script_update(settings):
    global auto_start_obs, auto_start_stream, selected_engine, text_source_name
    if obs is None:
        return

    auto_start_obs = obs.obs_data_get_bool(settings, "auto_start_obs")
    auto_start_stream = obs.obs_data_get_bool(settings, "auto_start_stream")
    selected_engine = obs.obs_data_get_string(settings, "engine") or "google_web"
    text_source_name = obs.obs_data_get_string(settings, "text_source_name") or "Live Captions"


def script_defaults(settings):
    if obs is None:
        return

    obs.obs_data_set_default_bool(settings, "auto_start_obs", True)
    obs.obs_data_set_default_bool(settings, "auto_start_stream", True)
    obs.obs_data_set_default_string(settings, "engine", "google_web")
    obs.obs_data_set_default_string(settings, "text_source_name", "Live Captions")
