# ⚡ OBS Live Captioner PRO — Complete REST & WebSocket API Guide

The **OBS Live Captioner PRO Suite** includes a built-in REST API and real-time WebSocket server on port `8765` (`http://127.0.0.1:8765`).

You can use this API to control the captioner, automate projectors, manage transcripts, and trigger emergency display restorations from **Elgato Stream Deck**, **Bitfocus Companion**, **Touch Portal**, **Home Assistant**, or custom scripts.

---

## 📋 Table of Contents
1. [Base URL & Authentication](#1-base-url--authentication)
2. [📺 Display & Emergency Recovery Endpoints](#2--display--emergency-recovery-endpoints)
3. [🎙️ Captioner Control Endpoints](#3-🎙️-captioner-control-endpoints)
4. [📊 Status & Hardware Info Endpoints](#4-📊-status--hardware-info-endpoints)
5. [📜 Transcript & Export Endpoints](#5-📜-transcript--export-endpoints)
6. [🛡️ Content & Safety Filter Endpoints](#6-🛡️-content--safety-filter-endpoints)
7. [🌐 WebSocket Streaming API](#7-🌐-websocket-streaming-api)
8. [🎮 Hardware Integrations (Stream Deck, Companion, Curl)](#8-🎮-hardware-integrations)

---

## 1. Base URL & Authentication

* **Default Base URL**: `http://127.0.0.1:8765`
* **Content-Type**: `application/json`
* **Authentication** *(Optional)*:
  If an `api_key` is set in `config.json` under `"api": { "api_key": "YOUR_KEY" }`, send it via:
  * Header: `Authorization: Bearer YOUR_KEY`
  * Or Header: `X-API-Key: YOUR_KEY`
  * Or URL parameter: `?api_key=YOUR_KEY`
  *(If `api_key` is left blank, authentication is disabled for easy local network access).*

---

## 2. 📺 Display & Emergency Recovery Endpoints

### 🚨 Emergency Restore Screen & Captions (1-Click Trigger)
Forcefully connects to OBS, launches the video preview on the conference room screen (LONTIUM / Monitor 1), and starts live captioning.

* **Endpoint**: `POST /api/control/reopen-screen` *(or `POST /api/control/restore-display`)*
* **Query / Body Parameters** *(Optional)*:
  * `monitor` (`int`): Target monitor index (default: `1` for LONTIUM / Secondary display).
  * `mix_type` (`string`): `"preview"` (default), `"program"`, or `"source"`.
* **Example cURL**:
  ```bash
  curl -X POST http://127.0.0.1:8765/api/control/reopen-screen
  ```
* **Response**:
  ```json
  {
    "status": "success",
    "action": "reopen_screen",
    "message": "Screen Projector (preview on Monitor 1) triggered and Live Captions active.",
    "monitor_index": 1,
    "projector_opened": true,
    "captions_active": true
  }
  ```

---

### 📺 Open Projector (Custom Target)
Opens a fullscreen or windowed projector in OBS Studio.

* **Endpoint**: `POST /api/obs/projector/open`
* **Request Body**:
  ```json
  {
    "mix_type": "preview",
    "monitor_index": 1,
    "source_name": "Captions Overlay"
  }
  ```
* **Response**:
  ```json
  {
    "status": "success",
    "message": "Projector (preview) opened on monitor 1."
  }
  ```

---

### 🖥️ Query Connected Monitors
Returns all displays detected by OBS Studio with their monitor index, names, and resolutions.

* **Endpoint**: `GET /api/obs/monitors`
* **Response**:
  ```json
  {
    "monitors": [
      {
        "monitorIndex": 0,
        "monitorName": "Main Display",
        "monitorWidth": 1920,
        "monitorHeight": 1080
      },
      {
        "monitorIndex": 1,
        "monitorName": "LONTIUM (Conference Room)",
        "monitorWidth": 1920,
        "monitorHeight": 1080
      }
    ]
  }
  ```

---

## 3. 🎙️ Captioner Control Endpoints

### 🟢 Start Captioning
* **Endpoint**: `POST /api/control/start`
* **Response**:
  ```json
  { "status": "success", "message": "Captioner started." }
  ```

### 🔴 Stop Captioning
* **Endpoint**: `POST /api/control/stop`
* **Response**:
  ```json
  { "status": "success", "message": "Captioner stopped." }
  ```

---

## 4. 📊 Status & Hardware Info Endpoints

### 📈 Get Live Status
Returns engine state, audio levels, active model, and uptime.
* **Endpoint**: `GET /api/status`
* **Response**:
  ```json
  {
    "status": "online",
    "engine": "gemini_live",
    "model": "gemini-3.5-transcribe-live",
    "audio_level_db": -24.5,
    "obs_connected": true,
    "uptime_seconds": 3600
  }
  ```

### ⚙️ Get / Update Configuration
* **Get Config**: `GET /api/config`
* **Save Config**: `POST /api/config` *(Hot-reloads configuration without restarting the app)*

### 🎤 Get Audio Devices
* **Endpoint**: `GET /api/devices` (Returns all available microphones and audio interface indices).

### 🎨 Theme Presets & Application
* **List Themes**: `GET /api/presets`
* **Apply Preset**: `POST /api/presets/apply?theme_id=cyberpunk_neon`

---

## 5. 📜 Transcript & Export Endpoints

### 📥 Download Subtitle File (.SRT / .VTT / .TXT)
Downloads formatted subtitle files from the current stream session.
* **Endpoint**: `GET /api/transcript/export?format=srt`
* **Parameters**: `format=srt`, `format=vtt`, or `format=txt`

### 📜 Get Recent Transcript Lines
* **Endpoint**: `GET /api/transcript/history?limit=50&search=keyword`

### 🗑️ Clear Transcript History
* **Endpoint**: `POST /api/transcript/clear`

---

## 6. 🛡️ Content & Safety Filter Endpoints

* **Test Filter**: `POST /api/filter/test` (Payload: `{"text": "test phrase"}`)
* **Add Blacklist Term**: `POST /api/filter/blacklist/add` (Payload: `{"term": "badword"}`)
* **Add Whitelist Term**: `POST /api/filter/whitelist/add` (Payload: `{"term": "Jesus Christ"}`)

---

## 7. 🌐 WebSocket Streaming API

### Real-Time Subtitles Stream (`/ws`)
Connect your frontend or external display widgets to receive instant live subtitle events:
* **URL**: `ws://127.0.0.1:8765/ws`
* **Message Payload (JSON)**:
  ```json
  {
    "text": "Welcome to our live stream",
    "is_final": true,
    "confidence": 0.98,
    "translated_text": "Bienvenidos a nuestra transmisión en vivo",
    "is_censored": false,
    "timestamp": 1724773800.12
  }
  ```

### Control & VU Meter Stream (`/api/control/ws`)
* **URL**: `ws://127.0.0.1:8765/api/control/ws`
* Streams real-time VU audio levels (`{"type": "vu_level", "db": -18.2}`) and engine state updates.

---

## 8. 🎮 Hardware Integrations

### A. Elgato Stream Deck
To create an emergency restore button on your Stream Deck:
1. In the Stream Deck software, drag a **System ➔ Website** (or **API Ninja / HTTP Request**) action to a key.
2. Set **URL**: `http://127.0.0.1:8765/api/control/reopen-screen`
3. Set **Method**: `POST`
4. Label: `REOPEN SCREEN` / `LONTIUM`
5. Pressing this key will immediately wake up the conference room screen and start captions!

### B. Bitfocus Companion
1. Add a **generic-http** connection with base URL: `http://127.0.0.1:8765`
2. Create a button with action: `POST /api/control/reopen-screen`

### C. Windows PowerShell Shortcut
Run this in PowerShell to trigger the screen:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/control/reopen-screen" -Method Post
```
