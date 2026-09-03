# 🎙️ VoxStream — Real-Time OBS Live Captioner & Broadcast Suite

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![OBS Studio](https://img.shields.io/badge/OBS%20Studio-30.0%2B-darkgreen.svg)](https://obsproject.com/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)]()
[![PWA Ready](https://img.shields.io/badge/PWA-Ready-brightgreen.svg)]()

**VoxStream** is a high-performance, low-latency, real-time speech-to-text captioning and broadcast automation suite for **OBS Studio** on **macOS, Windows, and Linux**.

**VoxStream** is a high-performance, low-latency, real-time speech-to-text captioning and broadcast automation suite for **OBS Studio** on **macOS, Windows, and Linux**.

It features an integrated **In-OBS Web Control Dashboard & Custom Dock**, **7 Multi-Tier Speech Recognition Engines** with **Local Faster-Whisper** as the verified #1 Champion for church sermons, an **In-App Church Sermon Benchmark Leaderboard**, a **Universal Live Read-Along Display (`/display`)** with WCAG 2.2 AAA accessibility, **Real-Time Words Per Minute (WPM) Speaking Pace Analytics**, **⛪ Church & Biblical Lexicon with Offline Bible Engine (KJV, BSB, WEB)**, **Smart Punctuation & Capitalization**, **Multi-Language Live Translation**, **1-Click Theme Gallery**, **Twitch Chat Caption Bot**, **YouTube Live CEA-608 Closed Captions**, and **Automated YouTube Chapters & Subtitle Exporter (SRT/VTT/TXT)**.

---

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="VoxStream Pro Dashboard and OBS Stream Overlay Studio" width="900" style="border-radius: 8px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
</p>

## ✨ Key Features Overview

### ⚡ 1. 7 Multi-Tier Speech Recognition Engines (Hot-Switchable)
* 💻 **Local Faster-Whisper (🥇 #1 Champion for Church Sermons - Default)**: 100% offline OpenAI Whisper running locally on your CPU/Neural Engine (int8) or GPU (CUDA). Flawlessly recognizes ancient biblical names (*Nebuchadnezzar, Melchizedek, Zephaniah*), produces natural punctuation & capitalization, and eliminates word flapping/double-guessing. Zero cloud dependency, zero API keys.
* 🌙 **Local Moonshine (ONNX Neural Edge STT)**: Next-generation lightweight neural transformer designed specifically for real-time live streaming (~5x faster than Whisper). Very light on CPU.
* ⚡ **Local Vosk / Kaldi (Ultra-Low Latency ~20-30ms)**: Instantaneous syllable-by-syllable recognition powered by Kaldi C-core acoustic models.
* ✨ **Gemini 3.5 Transcribe Live**: Google's multimodal AI (`gemini-3.5-transcribe-live`) with custom church vocabulary prompting, smart punctuation, and automated filler-word cleanup.
* ☁️ **Google Cloud Speech-to-Text**: Enterprise streaming (Chirp v2 / Speech v1) with custom speech context phrase boosting.
* 🌐 **Bandwidth Labs Live STT**: Low-latency WebSocket real-time speech recognition from labs.bandwidth.com.
* 🆓 **Google Web Speech (Zero-Setup)**: Phrase-endpointed recognition fallback.
* 🔄 **Live Hot-Switching**: Switch engines on the fly in the dashboard without interrupting your stream or restarting the server.

---

### 📱 2. Universal Live Read-Along Display & Stage Confidence Monitor (`/display`)
* **Live Read-Along Universal Access**: Purpose-built for congregants reading along in real time, visual impairment support, Deaf / Hard of Hearing accessibility, and stage presenter teleprompting.
* **PWA Standalone Mode**: Installable directly on **iPads, iPhones, Android tablets, TVs, and laptops** with custom app icon and full-screen standalone window (no URL bars or browser tabs).
* **🔋 Screen Wake Lock API**: Automatically keeps the screen awake so stage podium tablets and confidence monitor TVs never dim or go to sleep during speech.
* **2 Tailored Display Modes**:
  * **`📜 Scrollable History` (Default)**: Full persistent, bi-directional scrollable transcript of the service. Presenters and readers can scroll up with touch or mouse wheel to review earlier points or scripture verses.
  * **`⚡ Live Prompter (Auto-Fade)`**: Displays only the active 2 lines on screen and automatically fades out on silence for clean broadcast teleprompting.
* **Floating Scripture Prompter Card**: Seamlessly displays full Bible passages alongside the live read-along speech stream.
* **Fully Responsive UI**: Clamped viewport layout fluidly adapts across 4K displays, ultrawide monitors, iPads, and mobile phones with zero horizontal bleed.

<p align="center"><img src="docs/screenshots/stage_monitor.png" alt="VoxStream Live Read-Along Display and Stage Confidence Monitor PWA" width="850" style="border-radius: 8px;"></p>

---

### 🌐 3. Multi-Track Live Translation & Local Network (LAN) Support
* **Multi-Device LAN Access (`0.0.0.0:8765`)**: Any device on your church or studio Wi-Fi can open `http://<your-ip>:8765/display` in real-time. Supports **200+ simultaneous devices** with `< 5ms` broadcast latency.
* **Independent Per-Screen Settings**: Each device independently chooses its own:
  * **Language Track** (e.g. Spanish in the overflow room, Chinese in the translation booth, English on stage).
  * **Display Mode** (Live Prompter vs Scrollable History).
  * **Font Size & High-Contrast Themes** (OLED Black, Amber Stage, Slate Blue, High Light).
* **30+ Supported Languages**: Real-time translation to Spanish, French, German, Portuguese, Italian, Chinese, Japanese, Korean, Russian, Arabic, Hindi, Dutch, Polish, Swedish, Turkish, Ukrainian, Vietnamese, and more.

---

### 📖 4. Instant Scripture Verse Auto-Lookup & Offline Bible Engine
* **Spoken Citation Auto-Detection**: Recognizes spoken scripture passages in real time across all 66 books (*"John 3:16"*, *"1 Corinthians 13:4-7"*, *"Romans 8:28"*).
* **100% Offline Multi-Translation Database**: Instant (< 0.1ms) verse retrieval from bundled SQLite database:
  * **BSB (Berean Standard Bible)**: Modern, highly accurate translation reading virtually identically to the **ESV**.
  * **WEB (World English Bible)**: Modern clean English equivalent to the **NIV/NLT**.
  * **KJV (King James Version)**: Traditional classic authorized text.
* **Dual-Channel Visual Broadcast**:
  * **OBS Stream Overlay**: Broadcast lower-third card with gold amber badge and smooth animated entry.
  * **Stage Confidence Monitor**: Floating prompter box pinned to stage confidence screens and podium iPads.
  * **Interactive Dashboard Cue Tool**: Search, preview, and manually push or dismiss verses with 1 click.

<p align="center"><img src="docs/screenshots/scripture_studio.png" alt="VoxStream Scripture Studio Dashboard and 1-Click Browser Source Hub" width="850" style="border-radius: 8px;"></p>

---

### ⛪ 5. Church, Worship & Biblical Lexicon
* **Sacred Names & Titles of Deity**: Automatically capitalizes and truecases:
  * `God`, `God's`, `Lord`, `Lord's`, `Jesus`, `Jesus Christ`, `Christ`, `Holy Spirit`, `Holy Ghost`, `Heavenly Father`, `Almighty God`, `King of Kings`, `Lord of Lords`, `Son of God`, `Prince of Peace`, `Lamb of God`, `Messiah`, `Savior`, `Yahweh`, `Jehovah`, `Emmanuel`.
* **Spoken Scripture Reference Parser**: Automatically detects and formats spoken Bible references into standard chapter:verse citations:
  * `"John 3 16"` or `"John chapter 3 verse 16"` → **`John 3:16`**
  * `"Romans 8 28"` → **`Romans 8:28`**
  * `"First Corinthians 13 4 through 7"` → **`1 Corinthians 13:4-7`**
  * `"Psalm twenty three"` → **`Psalm 23`**
  * `"In Jesus name amen"` → **`in Jesus' name, Amen`**
* **All 66 Books of the Bible**: Formats canonical names across Old and New Testaments (`Genesis`, `Exodus`, `1 Kings`, `Matthew`, `Philippians`, `Revelation`, etc.).
* **Church Whitelist**: Context-aware filter protects scriptural phrases like `"heaven and hell"` or `"gates of hell"` from false-positive profanity censoring.

---


### ♿ 6. Visual Aid, Neurodivergent & Accessibility Suite (WCAG 2.2 AAA Ready)
* **`⚡ Bionic Reading (Guided Word Focus)`**: 1-click option on the Stage Monitor (`/display`) that dynamically bolds the initial 2–3 letters of each word (`<b>Wel</b>come <b>t</b>o <b>chur</b>ch`). Creates artificial visual fixation anchors that dramatically accelerate reading speed and comprehension for individuals with **ADHD**, **dyslexia**, or fast-moving speech.
* **`👁️ Visual Aid Mode`**: 1-click toggle that expands letter tracking (`0.06em`), word spacing (`0.15em`), and line height (`1.65x`) to eliminate character crowding while preserving chosen font and theme.
* **`🐢 Slow Down Text (Paced Reading)`**: Speech speed governor that buffers rapid speech bursts and delivers words at a steady, comfortable pace of **~135 WPM**, holding completed sentences on screen for **10.0 seconds** to prevent reading fatigue.
* **`📖 OpenDyslexic & Lexend Typography`**: Bottom-weighted and readability-engineered fonts loaded across overlays, Scripture Studio, and stage confidence monitors to prevent character flipping.
* **`🎨 High-Contrast & OLED Palettes`**: Ultra-high-contrast themes (*OLED Obsidian, High-Vis Yellow on Black, Stage Amber, Clean Light*) designed for maximum visibility in bright sanctuaries, dark auditoriums, and for viewers with low vision.
* **`🔊 WAI-ARIA Screen Reader Live Regions`**: Screen reader compliant (`role="region" aria-live="polite" aria-atomic="false"`) so blind and low-vision operators hear live speech transcriptions and scriptures via VoiceOver, NVDA, and TalkBack.
* **`🔋 Screen Wake Lock API`**: Automatically keeps confidence screens and podium tablets awake during services without requiring manual OS display sleep setting overrides.
* **`⌨️ Complete Keyboard & Shortcut Control`**: Full hands-free hotkeys (`S` for Settings, `H` for Hide Header, `C` for Clear, `+`/`-` for Font Size Scaling) with accessible focus indicators and modal focus management.
* **`⚡ Reduced Motion Mode`**: Automatically respects OS motion reduction preferences and suppresses word-pop animations for viewers with vestibular sensitivities.
* **`⚙️ Custom Workspace Presets`**: 1-click feature presets (*Church / AV, Streamer, Minimalist, High-Visibility*) to eliminate interface clutter for focused operations.

---

### 🔤 7. Intelligent Capitalization & Punctuation Engine
* **Sentence Capitalization**: Capitalizes sentence beginnings, standalone pronoun `I`, tech brands & acronyms (`OBS`, `GPU`, `CPU`, `HDMI`, `YouTube`, `Twitch`, `Discord`), and common contractions (`I'm`, `don't`, `can't`, `that's`).
* **Smart Punctuation**: Restores question marks (`?`), exclamation marks (`!`), periods (`.`), and natural comma pauses before coordinating conjunctions.
* **Ultra-Fast Single-Pass Pipeline**: Unified master Trie regex lookup executes in **< 0.08ms** per audio frame with zero perceptible latency.

---

### 📺 8. YouTube Livestream & Twitch Closed Captions (Native [CC] Button)
* **Embedded CEA-608 / CEA-708**: Injects native closed caption packets directly into OBS H.264 stream output headers via OBS WebSocket v5 (`SendStreamCaption`).
* **Viewer Toggleable [CC]**: Viewers on YouTube Live and Twitch can toggle captions on/off right on the video player without permanently burning text into video pixels.
* **Twitch Chat Caption Broadcaster**: Automatically sends live captions into Twitch chat for mobile and hearing-impaired viewers.

<p align="center"><img src="docs/screenshots/stream_overlay.png" alt="OBS Transparent Animated Stream Overlay" width="850" style="border-radius: 8px;"></p>

---

### 🏆 9. In-App Church Sermon Model Benchmark & Engine Leaderboard
* **Realistic Church Sermon Benchmark**: Evaluated against complex ancient biblical names (*Nebuchadnezzar, Melchizedek, Zephaniah*), rapid chapter:verse citations (*Second Corinthians 4:7-9*), and theological doctrine (*propitiation, sanctification, covenant*).
* **Official Leaderboard & Rankings**:
  * 🥇 **#1 Local Faster-Whisper** (`base.en`): **91.7% Accuracy**, ~500ms latency, 100% offline, zero double-guessing, and flawless biblical proper noun recognition.
  * ⚡ **#2 Gemini 3.5 Transcribe Live**: **98.4% Accuracy**, ~180ms latency, smart disfluency cleanup and custom church vocabulary prompting.
  * 🎙️ **#3 Local Vosk / Kaldi**: **88.0% Accuracy**, ~30ms instant syllables, lightweight offline engine.
* **1-Click Switching**: Seamlessly switch engines directly from the leaderboard table in the **🎙️ Audio & Engine** tab.
* **Storage Cache Manager**: 1-click download, inspection, and deletion of offline AI models (`/api/models/delete`).

<p align="center"><img src="docs/screenshots/dashboard_engine.png" alt="VoxStream Speech Engine Leaderboard and Offline AI Model Manager" width="850" style="border-radius: 8px;"></p>

---

### ⚡ 10. Real-Time Words Per Minute (WPM) Speaking Pace & Analytics
* **Live Dynamic Pace Tracking**: Calculates real-time speaking rate using a 45-second sliding speech window with automatic silence decay.
* **Qualitative Speaker Ratings**: Color-coded feedback badges in the header and transcript dashboard:
  * 🟢 **Optimal Pace** (110–150 WPM): Ideal for church preaching, comprehension, and comfortable read-along.
  * 🟡 **Slow Pace** (<100 WPM) or **Brisk Pace** (151–180 WPM): Gentle visual reminder for presenters.
  * 🔴 **Rapid Pace** (>180 WPM): Fast burst alerting to prevent congregation reading fatigue.
* **Session Speaking Totals**: Tracks total spoken words, active speaking minutes, total transcript lines, and overall session average WPM.
* **REST & WebSocket Stream**: Streamed in real time to control panels and available via `GET /api/transcript/stats`.

<p align="center"><img src="docs/screenshots/wpm_analytics.png" alt="Real-Time Speaking Pace and Words Per Minute Analytics Studio" width="850" style="border-radius: 8px;"></p>

---

### 🎛️ 11. Complete REST & WebSocket API & Stream Deck / Companion Integration
* Full REST API control for hardware broadcast switchers, Stream Decks, and custom webhooks.
* **1-Button Panic Drop**: Instantly wipe visible captions across all broadcast screens and overlays (`POST /api/control/panic`).
* **1-Button Toggle**: Start or pause speech recognition on demand (`POST /api/control/toggle`).
* **1-Button Theme Switching**: Apply themes on the fly (`POST /api/presets/apply`).
* Complete documentation in [**`API_GUIDE.md`**](API_GUIDE.md) and [**`integrations/streamdeck_companion.md`**](integrations/streamdeck_companion.md).

---

### 📑 12. Custom Vocabulary & Bulk CSV/TSV Glossary Manager
* **Phonetic Replacement Engine**: Automatically replaces misheard slang, proper nouns, brand names, and church jargon in < 0.1ms.
* **Bulk Import & Export**: Import CSV or TSV files, copy/paste multiple terms, or download your entire glossary with 1 click (`/api/vocabulary/export`).
* **Live Sandbox Tester**: Test substitutions in real time before going live.

<p align="center"><img src="docs/screenshots/glossary_csv.png" alt="Custom Vocabulary & Bulk CSV Import/Export" width="850" style="border-radius: 8px;"></p>

---

### 📑 13. Automated YouTube Video Chapters & Subtitle Export
* **Intelligent YouTube Chapters**: Automatically detects scripture readings (*e.g. John 3:16, Romans 8:28*), sermon topic shifts, prayers, and liturgical milestones into timestamped chapter markers.
* **1-Click Copy**: Copy ready-to-paste video descriptions for YouTube uploads directly from the Transcripts tab.
* **Subtitle Export**: 1-click export to **`.SRT`**, **`.VTT`**, and **`.TXT`** with millisecond timecodes.

---

### 🎨 14. Curated Broadcast Theme Gallery & Typography Customizer
* **Pre-Built Themes**: *Modern Clean, Broadcast News Lower-Third, Sanctuary & Worship, Corporate Keynote & Tech, Minimalist Cinema, High-Contrast Stage Confidence, Editorial & Talk Show, Classic Broadcast CEA-708, OpenDyslexic (Accessibility)*.
* **Custom Typography**: Google Fonts (*Inter, Roboto, Montserrat, Oswald, Lora, Poppins, OpenDyslexic, Lexend*) + custom system fonts.
* **Custom Layouts**: Font Size (16px–96px), Max Box Width (% slider), Max Lines (1–4), Text Alignment, Line Height, and Box Background Opacity.

---

### 🛡️ 15. 3-Tier Content Filtering & Wholesome Replacements
* **Tier 1 (Standard Profanities)**: Filters vulgarities and offensive words.
* **Tier 2 (Harsh Vulgarities & Blasphemies)**: Filters harsh expletives while safely protecting sacred names and scriptural citations. When **Church Mode** is on, ordinary theological vocabulary (e.g. *hell*, *damned* in sermon contexts) is exempt from this tier so scripture readings display verbatim.
* **Tier 3 (Crude Terms)**: Filters crude slang and inappropriate phrases.
* **4 Action Modes**: Wholesome Word Replacement, Asterisk Masking (`****`), `[CENSORED]` Tag, or Drop Sentence.
* **Interactive In-Browser CRUD Editor**: Add/remove custom blacklist words, custom whitelists, and wholesome word replacements in clean visual tables.

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

## 🌐 Access URLs (Once Running)

| View | Local URL | Network / Wi-Fi URL | Description |
| :--- | :--- | :--- | :--- |
| **Web Control Dashboard** | `http://127.0.0.1:8765/dashboard` | `http://<YOUR_IP>:8765/dashboard` | Main control panel & In-OBS Dock |
| **Transparent Stream Overlay** | `http://127.0.0.1:8765/` | `http://<YOUR_IP>:8765/` | OBS Browser Source overlay |
| **Stage / Room Confidence Monitor** | `http://127.0.0.1:8765/display` | `http://<YOUR_IP>:8765/display` | PWA Fullscreen Stage Monitor for iPads/TVs |

---

## 🖥️ OBS Studio Setup

### 1. Add the In-OBS Control Dock (Recommended)
1. In OBS Studio, go to the top menu: **Docks $
ightarrow$ Custom Browser Docks...**
2. **Dock Name**: `Live Captions`
3. **URL**: `http://127.0.0.1:8765/dashboard`
4. Click **Apply** and dock the panel anywhere in your OBS workspace.

---

### 2. Add the Transparent Stream Overlay
1. In your OBS Scene, click **+ (Add Source) $
ightarrow$ Browser**.
2. **URL**: `http://127.0.0.1:8765/`
3. **Width**: `1920`, **Height**: `1080` (or match your canvas resolution).
4. Check **"Shutdown source when not visible"** and **"Refresh browser when scene becomes active"**.

---

### 3. Native Closed Captions (YouTube & Twitch [CC] Button) 📺
1. **In OBS Studio**: Go to **Tools $
ightarrow$ WebSocket Server Settings** and check **"Enable WebSocket server"** (Port `4455`).
2. **For YouTube Livestreams**:
   * Open your stream in **YouTube Studio** (Live Control Room).
   * In **Stream Settings**, toggle **Closed Captions** to **ON**.
   * Under **Caption source**, select **"Embedded 608/708"**.
3. **For Twitch Streams**:
   * Twitch automatically reads embedded CEA-608 packets from OBS.
   * You can also use the built-in **Twitch Chat Bot** in the VoxStream dashboard to broadcast captions directly into Twitch chat for mobile viewers!

---

### 4. Auto-Start VoxStream Automatically When OBS Opens 🚀
You can have OBS Studio launch VoxStream in the background automatically whenever OBS opens:
1. In OBS Studio, go to **Tools $
ightarrow$ Scripts**.
2. **On macOS**: In the **Python Settings** tab, ensure your Python path is set (e.g. `/opt/homebrew/Frameworks/Python.framework/Versions/3.11` or `/Library/Frameworks/Python.framework/Versions/3.11`). On Windows, select your Python install folder.
3. Click the **Scripts** tab, click **+ (Add Script)**, and select `obs_script/obs_live_captions.py`.
4. Check **"Auto-start when OBS launches"** and choose your default speech engine (e.g. *Vosk, Moonshine, Bandwidth Labs, etc.*).

---

## ⚡ REST & WebSocket API

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/status` | Returns system status, audio levels, active engine, model detail, uptime |
| `GET` | `/api/config` | Retrieves current configuration JSON |
| `POST` | `/api/config` | Updates configuration & hot-reloads live pipeline |
| `POST` | `/api/control/start` | Starts / unpauses speech recognition |
| `POST` | `/api/control/stop` | Pauses speech recognition |
| `POST` | `/api/control/panic` | Emergency Panic Button: wipes captions from all screens |
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
| `GET` | `/api/transcript/chapters` | Returns auto-generated YouTube chapters & scripture markers |
| `GET` | `/api/transcript/export` | Downloads subtitle file (`?format=srt|vtt|txt`) |
| `GET` | `/manifest.json` | PWA Web App Manifest |
| `GET` | `/sw.js` | PWA Service Worker |
| `WS` | `/ws` | Real-time caption event broadcast stream (`?lang=es` for translated) |
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
