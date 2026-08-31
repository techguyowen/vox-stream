# 🎙️ VoxStream — Real-Time OBS Live Captioner & Broadcast Suite

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![OBS Studio](https://img.shields.io/badge/OBS%20Studio-30.0%2B-darkgreen.svg)](https://obsproject.com/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)]()

**VoxStream** is a high-performance, low-latency, real-time speech-to-text captioning and broadcast automation suite for **OBS Studio** on **macOS, Windows, and Linux**.

It features an integrated **In-OBS Web Control Dashboard & Custom Dock**, **6 Powerful Speech Engines** *(Local Vosk / Kaldi, Moonshine, Gemini 3.5 Live, Faster-Whisper, Google Web, Google Cloud STT)*, **⛪ Church & Biblical Lexicon with Scripture Citation Parsing**, **Smart Punctuation & Capitalization**, **Multi-Language Live Translation**, **1-Click Theme Gallery**, **Twitch Chat Caption Bot**, **Stage & Room Confidence Monitor**, and **Subtitle Exporter (SRT/VTT/TXT)**.

---

## ✨ Features Overview

### ⚡ 1. 6 Multi-Tier Speech Recognition Engines (Hot-Switchable)
* ⚡ **Local Vosk / Kaldi (Ultra-Low Latency ~20ms)**: 100% offline, instantaneous speech recognition powered by Kaldi C-core acoustic models. Zero cloud dependency, zero API keys.
* 🌙 **Local Moonshine (ONNX Neural STT)**: Next-generation lightweight neural speech-to-text designed specifically for real-time live streaming.
* ✨ **Gemini 3.5 Transcribe Live**: Google's newest multimodal AI (`gemini-3.5-transcribe-live`) with custom vocabulary support, smart punctuation, and automated filler-word cleanup.
* 💻 **Local Faster-Whisper**: 100% offline OpenAI Whisper running locally on your GPU (CUDA) or CPU with beam search and voice activity filtering.
* 🆓 **Free Google Speech (Zero-Setup)**: 100% Free, zero-setup Google Speech engine requiring no API keys or accounts.
* ☁️ **Google Cloud Speech-to-Text**: High-accuracy enterprise streaming with automatic diarization and punctuation.
* 🔄 **Live Hot-Switching**: Switch engines on the fly in the dashboard without interrupting your stream or restarting the server.

---

### ⛪ 2. Church, Worship & Biblical Lexicon
* **Sacred Names & Titles of Deity**: Automatically capitalizes and truecases:
  * `God`, `God's`, `Lord`, `Lord's`, `Jesus`, `Jesus Christ`, `Christ`, `Holy Spirit`, `Holy Ghost`, `Heavenly Father`, `Almighty God`, `King of Kings`, `Lord of Lords`, `Son of God`, `Prince of Peace`, `Lamb of God`, `Messiah`, `Savior`, `Yahweh`, `Jehovah`, `Emmanuel`.
* **Spoken Scripture Reference Parser**: Automatically detects and formats spoken Bible references into standard chapter:verse citations:
  * `"John 3 16"` or `"John chapter 3 verse 16"` $\rightarrow$ **`John 3:16`**
  * `"Romans 8 28"` $\rightarrow$ **`Romans 8:28`**
  * `"First Corinthians 13 4 through 7"` $\rightarrow$ **`1 Corinthians 13:4-7`**
  * `"Psalm twenty three"` $\rightarrow$ **`Psalm 23`**
  * `"In Jesus name amen"` $\rightarrow$ **`in Jesus' name, Amen`**
* **All 66 Books of the Bible**: Formats canonical names across Old and New Testaments (`Genesis`, `Exodus`, `1 Kings`, `Matthew`, `Philippians`, `Revelation`, etc.).
* **Church Whitelist**: Context-aware filter protects scriptural phrases like `"heaven and hell"` or `"gates of hell"` from false-positive profanity censoring.

---

### 🔤 3. Intelligent Capitalization & Punctuation Engine
* **Sentence Capitalization**: Capitalizes sentence beginnings, standalone pronoun `I`, tech brands & acronyms (`OBS`, `GPU`, `CPU`, `HDMI`, `YouTube`, `Twitch`, `Discord`), and common contractions (`I'm`, `don't`, `can't`, `that's`).
* **Smart Punctuation**: Restores interrogative question marks (`?`), exclamation marks (`!`), periods (`.`), and natural comma pauses before coordinating conjunctions.
* **Ultra-Fast Single-Pass Pipeline**: Unified master Trie regex lookup executes in **$< 0.08$ms** per audio frame with zero perceptible latency.

---

### 🎛️ 4. In-OBS Control Dashboard & Custom Browser Dock
* Embeddable directly inside OBS Studio (`Docks -> Custom Browser Docks`) or accessible in any web browser at `http://127.0.0.1:8765/dashboard`.
* **⚡ Active Engine & Model Card**: Real-time status indicator showing which AI model and acoustic backend is actively processing audio.
* **Live Audio VU Meter**: Visual audio level monitor with Silero neural VAD and dB noise gate sliders.
* **Non-Blocking Toast System**: Modern notification system for instant feedback on setting changes and model switches.
* **Interactive Live Preview**: Instant visual preview of styling, fonts, and word-pop animations.

---

### 📺 5. Transparent Stream Overlay & Stage Monitor
* **Transparent Web Overlay (`http://127.0.0.1:8765/`)**: Zero-background transparent HTML5 overlay designed for OBS Browser Sources.
* **Stage & Room Confidence Monitor (`http://127.0.0.1:8765/display`)**: High-contrast, large-format confidence monitor for stage screens, sanctuary confidence monitors, and speakers.
* **OpenDyslexic & Accessibility**: Includes built-in support for OpenDyslexic and high-legibility fonts.

---

### 🌐 6. Real-Time Live Translation
* Automatically translates spoken words into 30+ languages (*Spanish, French, German, Japanese, Portuguese, Chinese, Korean, Italian, etc.*).
* **Dual Subtitles Mode**: Displays original spoken words with translated subtitles simultaneously underneath.

---

### 🎨 7. 1-Click Theme Gallery & Typography Customizer
* **Pre-Built Themes**: *Modern Clean, Cyberpunk Neon, Minimalist Cinema, Twitch Purple, Comic/Gaming Pop, Retro Terminal, YouTube CC, OpenDyslexic*.
* **Custom Typography**: Google Fonts (*Montserrat, Inter, Roboto, Poppins, Oswald, Bebas Neue, Bangers*) + custom system fonts.
* **Custom Layouts**: Font Size (16px–72px), Max Box Width (% slider), Max Lines (1–4), Text Alignment, Line Height, and Box Background Opacity.

---

### 🛡️ 8. 3-Tier Content Filtering & Wholesome Replacements
* **Tier 1 (Standard Profanities)**: Filters vulgarities and offensive words.
* **Tier 2 (Harsh Vulgarities & Blasphemies)**: Filters harsh expletives while safely protecting sacred names and scriptural citations.
* **Tier 3 (Crude Terms)**: Filters crude slang and inappropriate phrases.
* **4 Action Modes**: Wholesome Word Replacement, Asterisk Masking (`****`), `[CENSORED]` Tag, or Drop Sentence.
* **Interactive In-Browser CRUD Editor**: Add/remove custom blacklist words, custom whitelists, and wholesome word replacements in clean visual tables.

---

### 🔄 9. Self-Healing In-Process Restart
* Native `while True:` supervisor loop ensures clean, instantaneous reboots in **$< 1.5$ seconds**.
* **Instance ID Verification**: UI verifies the new server instance ID before reloading, preventing premature reloads or dropped connections.
* **Multi-Step Visual Progress Modal**: Displays real-time progress through restart stages.

---

### 👾 10. Twitch Chat Caption Broadcaster & CEA-608
* Automatically broadcasts finalized captions into Twitch chat for mobile and hearing-impaired viewers.
* Native CEA-608 Closed Captions via OBS WebSocket v5 (`SendStreamCaption`).

---

### 📜 11. Transcript History & Subtitle Export
* Rolling transcript history log with live search filter.
* 1-click export to **`.SRT`**, **`.VTT`**, and **`.TXT`** with millisecond timecodes for Premiere Pro, DaVinci Resolve, or YouTube upload.

---

## 📋 Quick Start Guide

### 🍏 macOS & Linux
```bash
# 1. Clone repository
git clone https://github.com/techguyowen/vox-stream.git
cd vox-stream

# 2. Run automated setup (creates virtual environment & installs dependencies)
./setup_mac.sh

# 3. Start VoxStream
./run_captioner.sh
```

### 🪟 Windows
1. Double-click `setup_windows.bat` (automatically installs Python dependencies).
2. Double-click `run_captioner.bat`.

---

### 🌐 Access URLs (Once Running)
| View | URL | Description |
| :--- | :--- | :--- |
| **Web Control Dashboard** | `http://127.0.0.1:8765/dashboard` | Main control panel & OBS Dock |
| **Transparent Stream Overlay** | `http://127.0.0.1:8765/` | OBS Browser Source overlay |
| **Room / Stage Monitor** | `http://127.0.0.1:8765/display` | High-visibility stage confidence monitor |

---

## 🖥️ OBS Studio Setup

### 1. Add the In-OBS Control Dock (Recommended)
1. In OBS Studio, go to the top menu: **Docks $\rightarrow$ Custom Browser Docks...**
2. **Dock Name**: `Live Captions`
3. **URL**: `http://127.0.0.1:8765/dashboard`
4. Click **Apply** and dock the panel anywhere in your OBS workspace.

---

### 2. Add the Transparent Stream Overlay
1. In your OBS Scene, click **+ (Add Source) $\rightarrow$ Browser**.
2. **URL**: `http://127.0.0.1:8765/`
3. **Width**: `1920`, **Height**: `1080` (or match your canvas resolution).
4. Check **"Shutdown source when not visible"** and **"Refresh browser when scene becomes active"**.

---

### 3. Native Closed Captions (CEA-608 for Twitch / YouTube)
1. In OBS Studio, enable **Tools $\rightarrow$ WebSocket Server Settings** (Port `4455`).
2. VoxStream connects automatically to send `SendStreamCaption` events. Viewers can toggle captions on/off via the player's native `[CC]` button.

---

## ⚡ REST & WebSocket API

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/status` | Returns system status, audio levels, active engine, model detail, uptime |
| `GET` | `/api/config` | Retrieves current configuration JSON |
| `POST` | `/api/config` | Updates configuration & hot-reloads live pipeline |
| `POST` | `/api/control/start` | Starts / unpauses speech recognition |
| `POST` | `/api/control/stop` | Pauses speech recognition |
| `POST` | `/api/control/restart` | Cleanly restarts backend process |
| `POST` | `/api/control/shutdown` | Gracefully shuts down application |
| `GET` | `/api/presets` | Returns all pre-built visual theme presets |
| `POST` | `/api/presets/apply` | Applies a visual theme preset by ID |
| `GET` | `/api/devices` | Returns list of available audio input devices |
| `GET` | `/api/filter/state` | Returns full censorship rules dictionary |
| `POST` | `/api/filter/blacklist/add` | Adds word to custom blacklist |
| `POST` | `/api/filter/whitelist/add` | Adds word to custom whitelist |
| `POST` | `/api/filter/replacements/set`| Adds/updates wholesome word substitution |
| `GET` | `/api/transcript/history` | Returns recent session transcript entries |
| `GET` | `/api/transcript/export` | Downloads subtitle file (`?format=srt\|vtt\|txt`) |
| `WS` | `/ws` | Real-time caption event broadcast stream |
| `WS` | `/api/control/ws` | Real-time telemetry, VU meter, and control stream |

---

## 🧪 Testing & Verification

Run the automated test suite:
```bash
.venv/bin/python test_captioner.py
```

---

## 📄 License
This project is licensed under the [MIT License](LICENSE).
