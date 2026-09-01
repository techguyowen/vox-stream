# ⚡ OBS Live Captioner PRO — Complete REST & WebSocket API Reference Manual

The **OBS Live Captioner PRO Suite** provides a high-performance, asynchronous REST and WebSocket API running on port `8765` (`http://127.0.0.1:8765` / `http://0.0.0.0:8765`).

This API enables hardware controllers (**Elgato Stream Deck**, **Bitfocus Companion**, **Touch Portal**, **Loupedeck**), broadcast automation tools (**OBS Studio**, **vMix**, **ProPresenter**), and custom scripts to manage captions, trigger panic censor drops, manage offline AI speech models, manipulate custom glossaries, switch themes, and stream audio.

---

## 📋 Table of Contents
1. [Base URL & Authentication](#1-base-url--authentication)
2. [🎙️ Speech-to-Text & Engine Control](#2-speech-to-text--engine-control)
3. [🚨 Panic Censor & Safety Controls](#3-panic-censor--safety-controls)
4. [🤖 Offline AI Model Downloader & Cache Manager](#4-offline-ai-model-downloader--cache-manager)
5. [📖 Custom Vocabulary & Bulk Glossary API](#5-custom-vocabulary--bulk-glossary-api)
6. [🎨 Visual Themes & Custom Presets API](#6-visual-themes--custom-presets-api)
7. [📜 Transcripts, Exports & YouTube Chapters](#7-transcripts-exports--youtube-chapters)
8. [🌐 Live Translation API](#8-live-translation-api)
9. [📺 OBS Projector, Monitors & External Screen Automation](#9-obs-projector-monitors--external-screen-automation)
10. [🔊 Live Audio Distribution & Stream API](#10-live-audio-distribution--stream-api)
11. [🌐 WebSocket Streaming APIs](#11-websocket-streaming-apis)
12. [🎮 Hardware Integrations (Stream Deck, Companion, Curl, PowerShell)](#12-hardware-integrations)

---

## 1. Base URL & Authentication

* **Default Base URL**: `http://127.0.0.1:8765` (or LAN IP: `http://192.168.1.145:8765`)
* **Content-Type**: `application/json` (for POST/PUT requests)
* **Authentication** *(Optional)*:
  If `api_key` is set in `config.json` under `"api": { "api_key": "YOUR_SECRET_KEY" }`, provide it using any of:
  * HTTP Header: `Authorization: Bearer YOUR_SECRET_KEY`
  * HTTP Header: `X-API-Key: YOUR_SECRET_KEY`
  * URL Query Parameter: `?api_key=YOUR_SECRET_KEY`
  *(If `api_key` is empty, authentication is bypassed for local network simplicity).*

---

## 2. 🎙️ Speech-to-Text & Engine Control

### 🟢 Start Captioning
Starts the speech recognition engine and audio capture stream.
* **Endpoint**: `POST /api/control/start`
* **cURL**:
  ```bash
  curl -X POST http://127.0.0.1:8765/api/control/start
  ```
* **Response (200 OK)**:
  ```json
  { "status": "success", "message": "Captioner started." }
  ```

---

### 🔴 Stop Captioning
Pauses/stops audio capture and recognition.
* **Endpoint**: `POST /api/control/stop`
* **cURL**:
  ```bash
  curl -X POST http://127.0.0.1:8765/api/control/stop
  ```
* **Response (200 OK)**:
  ```json
  { "status": "success", "message": "Captioner stopped." }
  ```

---

### ⏯️ Toggle Captioning (Play/Pause)
1-click toggle between active and stopped states.
* **Endpoint**: `POST /api/control/toggle`
* **cURL**:
  ```bash
  curl -X POST http://127.0.0.1:8765/api/control/toggle
  ```
* **Response (200 OK)**:
  ```json
  { "status": "success", "is_running": true, "message": "Captioner started." }
  ```

---

### 🔄 Restart Backend Engine
Gracefully recycles the audio capture pipeline and model inference workers.
* **Endpoint**: `POST /api/control/restart`
* **cURL**:
  ```bash
  curl -X POST http://127.0.0.1:8765/api/control/restart
  ```

---

### 🛑 Shutdown Application
Gracefully closes OBS connections, WebSockets, audio streams, and shuts down the process.
* **Endpoint**: `POST /api/control/shutdown`

---

### 📊 Get System Status
* **Endpoint**: `GET /api/status`
* **Response (200 OK)**:
  ```json
  {
    "status": "online",
    "is_running": true,
    "engine": "local_whisper",
    "model": "base.en",
    "device": "MacBook Pro Microphone",
    "audio_level_db": -22.4,
    "obs_connected": true,
    "uptime_seconds": 1420
  }
  ```

---

### ⚙️ Get / Save Configuration
* **Get Config**: `GET /api/config`
* **Save Config (Hot-Reload)**: `POST /api/config`
  ```bash
  curl -X POST http://127.0.0.1:8765/api/config \
    -H "Content-Type: application/json" \
    -d '{
      "general": { "auto_punctuation": true, "church_mode": true },
      "overlay": { "font_size": "36px", "text_color": "#FFFFFF" }
    }'
  ```

---

## 3. 🚨 Panic Censor & Safety Controls

### 🚨 Emergency Panic Caption Drop
Instantly purges all visible text from OBS overlays, Stage confidence monitors, and WebSockets, and emits a blank frame.
* **Endpoint**: `POST /api/control/panic`
* **cURL**:
  ```bash
  curl -X POST http://127.0.0.1:8765/api/control/panic
  ```
* **Response (200 OK)**:
  ```json
  {
    "status": "success",
    "action": "panic_wipe",
    "message": "Emergency panic drop triggered: Captions instantly wiped."
  }
  ```

---

### 🛡️ Content Filter State & Whitelist / Blacklist CRUD
* **Get Filter State**: `GET /api/filter/state`
* **Test Filter Phrase**: `POST /api/filter/test` (`{"text": "phrase to evaluate"}`)
* **Add Blacklist Term**: `POST /api/filter/blacklist/add` (`{"term": "badword"}`)
* **Remove Blacklist Term**: `POST /api/filter/blacklist/remove` (`{"term": "badword"}`)
* **Add Whitelist Exception**: `POST /api/filter/whitelist/add` (`{"term": "Jesus Christ"}`)
* **Remove Whitelist Exception**: `POST /api/filter/whitelist/remove` (`{"term": "Jesus Christ"}`)
* **Set Custom Word Replacement**: `POST /api/filter/replacements/set` (`{"original": "swear", "replacement": "[censor]"}`)

---

## 4. 🤖 Offline AI Model Downloader & Cache Manager

Manage local offline speech recognition models on disk.

| Model ID | Engine | Model Name | Disk Size | Target Speed / Accuracy |
| :--- | :--- | :--- | :--- | :--- |
| `vosk_small` | Vosk | `vosk-model-small-en-us-0.15` | ~40 MB | Ultra-fast (~30ms), ~0% CPU |
| `vosk_accurate` | Vosk | `vosk-model-en-us-0.22` | ~1.8 GB | High-vocabulary acoustic model |
| `whisper_tiny` | Faster-Whisper | `tiny.en` | ~75 MB | Fast neural transformer |
| `whisper_base` | Faster-Whisper | `base.en` | ~140 MB | **#1 Recommended for Broadcast** |
| `whisper_small` | Faster-Whisper | `small.en` | ~460 MB | High-precision accents & jargon |
| `moonshine_tiny` | Moonshine | `moonshine/tiny` | ~60 MB | Variable-length ONNX / PyTorch |
| `moonshine_base` | Moonshine | `moonshine/base` | ~180 MB | Smart phonetic transformer |

---

### 📊 Query Model Download Status
Returns disk cache verification for every model and total disk consumption.
* **Endpoint**: `GET /api/models/status`
* **Response (200 OK)**:
  ```json
  {
    "total_models": 7,
    "cached_models": 6,
    "cached_size_mb": 955,
    "models": [
      {
        "id": "whisper_base",
        "engine": "local_whisper",
        "name": "Faster-Whisper Base.en",
        "size_mb": 140,
        "is_cached": true,
        "cache_path": "/Users/user/.cache/huggingface/hub/models--Systran--faster-whisper-base.en",
        "status": "ready"
      }
    ]
  }
  ```

---

### 📥 Pre-Download Model(s)
Downloads a single model or all catalog models in the background. Progress broadcasts in real-time over `/api/control/ws`.
* **Endpoint**: `POST /api/models/download`
* **Request Body**:
  ```json
  { "model_id": "whisper_base" }
  ```
  *(Or `{ "model_id": "all" }` to batch download all models).*
* **Response (200 OK)**:
  ```json
  {
    "status": "started",
    "model_id": "whisper_base",
    "message": "Pre-download for model 'whisper_base' started in background."
  }
  ```

---

### 🛑 Cancel In-Flight Download
* **Endpoint**: `POST /api/models/cancel`
* **Response (200 OK)**:
  ```json
  { "status": "canceled", "message": "Model download cancellation requested." }
  ```

---

### 🗑️ Delete Model from Disk Cache
Deletes a specific model or clears the entire cache to free storage space.
* **Endpoint**: `POST /api/models/delete` *(or `DELETE /api/models`)*
* **Request Body**:
  ```json
  { "model_id": "vosk_accurate" }
  ```
  *(Or `{ "model_id": "all" }` to wipe all downloaded models).*
* **Response (200 OK)**:
  ```json
  {
    "status": "success",
    "model_id": "vosk_accurate",
    "freed_mb": 1800,
    "message": "Deleted Vosk Accurate (Large) from local cache (freed ~1800 MB)."
  }
  ```

---

## 5. 📖 Custom Vocabulary & Bulk Glossary API

Custom phonetics and proper noun replacement engine.

### ➕ Set Single Term
* **Endpoint**: `POST /api/vocabulary/set`
* **Request Body**:
  ```json
  {
    "original": "box stream",
    "replacement": "VoxStream"
  }
  ```

---

### 📑 Bulk CSV / TSV Import
* **Endpoint**: `POST /api/vocabulary/bulk`
* **Request Body**:
  ```json
  {
    "csv_data": "box stream, VoxStream\npastor mike, Pastor Mike\nk8s -> Kubernetes",
    "replace_all": false
  }
  ```
* **Response (200 OK)**:
  ```json
  {
    "status": "success",
    "imported_count": 3,
    "total_count": 42
  }
  ```

---

### 📥 Export Glossary as CSV
* **Endpoint**: `GET /api/vocabulary/export`
* **Response**: `text/csv` attachment (`Misheard Phrase,Correct Replacement\n...`).

---

### 🗑️ Clear All Terms
* **Endpoint**: `POST /api/vocabulary/clear`

---

### 🧪 Test Glossary Substitution
* **Endpoint**: `POST /api/vocabulary/test`
* **Request Body**: `{"text": "welcome to box stream with pastor mike"}`
* **Response (200 OK)**:
  ```json
  {
    "original": "welcome to box stream with pastor mike",
    "modified": "welcome to VoxStream with Pastor Mike",
    "was_modified": true
  }
  ```

---

## 6. 🎨 Visual Themes & Custom Presets API

### 🎨 List Themes & Presets
* **Endpoint**: `GET /api/presets`
* **Response (200 OK)**:
  ```json
  {
    "presets": [
      { "id": "modern_clean", "name": "Modern Clean (Glassmorphism)", "is_custom": false },
      { "id": "broadcast_news", "name": "Broadcast Lower-Third", "is_custom": false },
      { "id": "sanctuary_worship", "name": "Sanctuary & Worship", "is_custom": false },
      { "id": "minimal_cinema", "name": "Minimalist Cinema (Netflix / BBC)", "is_custom": false },
      { "id": "stage_confidence", "name": "High-Contrast Stage Confidence", "is_custom": false },
      { "id": "corporate_keynote", "name": "Corporate Keynote & Tech", "is_custom": false },
      { "id": "editorial_nordic", "name": "Editorial & Talk Show", "is_custom": false },
      { "id": "youtube_cc", "name": "Classic Broadcast CEA-708", "is_custom": false },
      { "id": "opendyslexic", "name": "📖 OpenDyslexic (Accessibility)", "is_custom": false }
    ]
  }
  ```

---

### ✨ Apply Theme Preset
* **Endpoint**: `POST /api/presets/apply`
* **Request Body**:
  ```json
  { "theme_id": "sanctuary_worship" }
  ```

---

### 💾 Save Custom Theme Preset
* **Endpoint**: `POST /api/presets/save`
* **Request Body**:
  ```json
  {
    "id": "sunday_morning_gold",
    "name": "Sunday Morning Gold",
    "description": "Warm golden typography for worship services",
    "font_family": "'Montserrat', sans-serif",
    "font_size": "36px",
    "font_weight": "700",
    "line_height": "1.35",
    "text_color": "#FFFBEB",
    "interim_color": "#FEF3C7",
    "highlight_color": "#F59E0B",
    "background_box_color": "rgba(20, 20, 26, 0.85)",
    "border_radius": "12px",
    "box_padding": "14px 26px",
    "text_shadow": "2px 2px 6px rgba(0,0,0,0.9)",
    "text_stroke": "1.5px #000000",
    "animation_style": "word_pop"
  }
  ```

---

### 🗑️ Delete Custom Theme Preset
* **Endpoint**: `POST /api/presets/delete`
* **Request Body**: `{"id": "sunday_morning_gold"}` *(or `{"preset_id": "sunday_morning_gold"}`)*

---

## 7. 📜 Transcripts, Exports & YouTube Chapters

### 📥 Export Subtitles (.SRT / .VTT / .TXT)
* **Endpoint**: `GET /api/transcript/export?format=srt`
* **Parameters**: `format=srt` (default), `format=vtt`, or `format=txt`

---

### 📑 Automated YouTube Description Chapter Markers
Automatically detects scripture passages, prayer moments, and sermon points with formatted timecodes.
* **Endpoint**: `GET /api/transcript/chapters`
* **Response (200 OK)**:
  ```json
  {
    "count": 4,
    "chapters": [
      { "timecode": "00:00:00", "seconds": 0.0, "title": "Introduction & Welcome" },
      { "timecode": "00:04:12", "seconds": 252.0, "title": "Scripture Reading (1 Thessalonians 5:16-18)" },
      { "timecode": "00:18:30", "seconds": 1110.0, "title": "Sermon Discussion" },
      { "timecode": "00:45:00", "seconds": 2700.0, "title": "Benediction & Closing" }
    ],
    "formatted": "00:00:00 - Introduction & Welcome\n00:04:12 - Scripture Reading (1 Thessalonians 5:16-18)\n00:18:30 - Sermon Discussion\n00:45:00 - Benediction & Closing"
  }
  ```

---

### 📜 Get Recent Transcript History
* **Endpoint**: `GET /api/transcript/history?limit=100&search=keyword`

---

### 🗑️ Clear Transcript History
* **Endpoint**: `POST /api/transcript/clear`

---

## 8. 🌐 Live Translation API

### 🌐 Translate Text On-Demand
* **Endpoint**: `GET /api/translate?text=Hello+world&target_lang=es`
* **Response (200 OK)**:
  ```json
  {
    "original": "Hello world",
    "translated": "Hola mundo",
    "source_lang": "en",
    "target_lang": "es"
  }
  ```

---

### 🌐 Supported Languages List
* **Endpoint**: `GET /api/languages` (Returns 100+ BCP-47 language codes and native display names).

---

## 9. 📺 OBS Projector, Monitors & External Screen Automation

### 🚨 Emergency 1-Click Screen Re-Open
Re-launches OBS fullscreen projector to secondary screen (LONTIUM / Monitor 1) and ensures captioning is active.
* **Endpoint**: `POST /api/control/reopen-screen` *(or `POST /api/control/restore-display`)*
* **Body / Query Parameters** *(Optional)*: `{"monitor": 1, "mix_type": "preview"}`

---

### 📺 Open Custom OBS Projector
* **Endpoint**: `POST /api/obs/projector/open`
* **Request Body**:
  ```json
  {
    "mix_type": "preview",
    "monitor_index": 1,
    "source_name": "Captions Overlay"
  }
  ```

---

### 🖥️ List Connected OBS Displays
* **Endpoint**: `GET /api/obs/monitors`

---

## 10. 🔊 Live Audio Distribution & Stream API

Broadcasts low-latency audio for hearing assistance devices and mobile web listeners.

* **WebSocket Audio Stream**: `GET /api/audio/stream` (Streams 16-bit 16kHz linear PCM / WAV chunks).
* **Chunk Post Ingestion**: `POST /api/audio/chunk` (Accepts raw binary audio buffers).

---

## 11. 🌐 WebSocket Streaming APIs

### A. Live Subtitles Stream (`/ws`)
Connect web browsers, OBS browser sources, or stage monitors.
* **URL**: `ws://127.0.0.1:8765/ws`
* **Incoming Message (JSON)**:
  ```json
  {
    "type": "caption",
    "text": "Rejoice always, pray continually, give thanks in all circumstances.",
    "is_final": true,
    "confidence": 0.99,
    "translated_text": "Estén siempre alegres, oren sin cesar, den gracias a Dios en toda situación.",
    "is_censored": false,
    "timestamp": 1724773800.12,
    "speaker": "Speaker 1"
  }
  ```

---

### B. Pro Control & Telemetry Stream (`/api/control/ws`)
Real-time state synchronization for the Dashboard and hardware docks.
* **URL**: `ws://127.0.0.1:8765/api/control/ws`
* **Event Types Emitted**:
  * `vu_level`: Real-time audio dB meter (`{"type": "vu_level", "db": -18.4, "linear": 0.35}`)
  * `model_download_progress`: Live download progress (`{"type": "model_download_progress", "model_id": "whisper_base", "status": "downloading", "progress_pct": 75.0}`)
  * `model_cache_updated`: Model added or removed (`{"type": "model_cache_updated", "model_id": "vosk_accurate", "freed_mb": 1800}`)
  * `state_change`: Engine started/stopped (`{"type": "state_change", "is_running": true}`)
  * `panic_triggered`: Emergency drop triggered (`{"type": "panic_triggered"}`)
  * `config_updated`: Live config reload (`{"type": "config_updated"}`)

---

## 12. 🎮 Hardware Integrations

### A. Elgato Stream Deck
1. In the Stream Deck software, add a **System ➔ Website** (or **API Ninja / HTTP Request**) action.
2. **Panic Censor Key**:
   * URL: `http://127.0.0.1:8765/api/control/panic`
   * Method: `POST`
   * Label: `PANIC DROP` 🚨
3. **Play/Pause Key**:
   * URL: `http://127.0.0.1:8765/api/control/toggle`
   * Method: `POST`
   * Label: `CAPTIONS` ⏯️
4. **Reopen Projector Key**:
   * URL: `http://127.0.0.1:8765/api/control/reopen-screen`
   * Method: `POST`
   * Label: `LONTIUM SCREEN` 📺

---

### B. Bitfocus Companion
1. Add a **generic-http** connection with Base URL: `http://127.0.0.1:8765`.
2. Map buttons to:
   * `POST /api/control/panic`
   * `POST /api/control/toggle`
   * `POST /api/control/reopen-screen`
   * `POST /api/presets/apply` with JSON `{"theme_id": "sanctuary_worship"}`

---

### C. PowerShell / Windows Shortcut
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/control/panic" -Method Post
```

---

### D. Unix / macOS cURL
```bash
curl -X POST http://127.0.0.1:8765/api/control/panic
```
