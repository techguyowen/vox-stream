# 🎛️ Elgato Stream Deck & Bitfocus Companion Integration Guide

VoxStream exposes a high-speed, local REST API designed for hardware broadcast controllers, Stream Decks, and Bitfocus Companion.

---

## ⚡ Quick Reference: Action Endpoints

| Button Action | HTTP Method | Endpoint URL | Payload (JSON) | Description |
| :--- | :---: | :--- | :--- | :--- |
| **⏯️ Toggle Captions** | `POST` | `http://127.0.0.1:8765/api/control/toggle` | None | Toggles speech-to-text on/off |
| **🚨 Emergency Panic Drop** | `POST` | `http://127.0.0.1:8765/api/control/panic` | None | Instantly wipes captions from all screens |
| **📺 Reopen Screen Projector** | `POST` | `http://127.0.0.1:8765/api/control/reopen-screen` | `{"monitor_index": 1}` | Sends preview to external stage display & resumes STT |
| **🎨 Switch Theme (Worship)** | `POST` | `http://127.0.0.1:8765/api/presets/apply` | `{"theme_id": "sanctuary_worship"}` | Applies Liturgical Amber styling |
| **🎨 Switch Theme (Lower-Third)** | `POST` | `http://127.0.0.1:8765/api/presets/apply` | `{"theme_id": "broadcast_news"}` | Applies Newsroom Lower-Third bar |
| **🎨 Switch Theme (Cinema)** | `POST` | `http://127.0.0.1:8765/api/presets/apply` | `{"theme_id": "minimalist_cinema"}` | Applies Netflix/BBC clean drop-shadow subtitles |
| **🗑️ Clear Screen History** | `POST` | `http://127.0.0.1:8765/api/transcript/clear` | None | Clears stage monitor & transcript log |
| **🔄 Restart Backend** | `POST` | `http://127.0.0.1:8765/api/control/restart` | None | Cleanly reboots speech engines |

---

## 🎮 1. Elgato Stream Deck Setup

### Option A: Using the Free "API Request" Plugin (Recommended)
1. In the **Elgato Stream Deck Store**, install the free **API Request** (or **HTTP Request** / **Advanced REST Client**) plugin by *BarRaider* or *Fred Emmott*.
2. Drag an **API Request** action onto any key.
3. Configure the key:
   * **Title**: `Panic Drop` (or `Toggle CC`)
   * **URL**: `http://127.0.0.1:8765/api/control/panic`
   * **Method**: `POST`
   * **Content-Type**: `application/json`

### Option B: Using Built-In macOS / Windows Shortcuts
* **macOS**: Use the built-in **Shortcuts** app with a `Get Contents of URL` action (`POST http://127.0.0.1:8765/api/control/toggle`) and assign it to a Stream Deck Shortcut key.
* **Windows**: Use a `curl -X POST http://127.0.0.1:8765/api/control/panic` batch trigger.

---

## 🎛️ 2. Bitfocus Companion Setup

Bitfocus Companion connects to broadcast hardware like Blackmagic ATEM, Behringer X32, and OBS Studio.

### Step 1: Add the `generic-http` Connection
1. Open Bitfocus Companion GUI (`http://127.0.0.1:8000` or `http://localhost:8888`).
2. Go to the **Connections** tab.
3. Search for and add the **Generic HTTP Requests** (`generic-http`) module.
4. Set the **Target Base URL**: `http://127.0.0.1:8765`.

### Step 2: Create Buttons

#### Button 1: 🚨 Emergency Panic Censor Button
* **Button Text**: `PANIC\nCENSOR`
* **Color**: Red background (`#FF0000`), White text (`#FFFFFF`)
* **Press Action**: `generic-http: POST`
  * **URI**: `/api/control/panic`

#### Button 2: ⏯️ Toggle Live Captions
* **Button Text**: `TOGGLE\nCAPTIONS`
* **Color**: Green background (`#00AA44`), White text (`#FFFFFF`)
* **Press Action**: `generic-http: POST`
  * **URI**: `/api/control/toggle`

#### Button 3: 🎨 Switch Theme to Worship Amber
* **Button Text**: `THEME\nWORSHIP`
* **Color**: Amber background (`#F59E0B`), Black text (`#000000`)
* **Press Action**: `generic-http: POST (JSON)`
  * **URI**: `/api/presets/apply`
  * **Body**: `{"theme_id": "sanctuary_worship"}`
  * **Header**: `Content-Type: application/json`

#### Button 4: 📺 Restore External Stage Screen
* **Button Text**: `STAGE\nSCREEN`
* **Color**: Blue background (`#0284C7`), White text (`#FFFFFF`)
* **Press Action**: `generic-http: POST`
  * **URI**: `/api/control/reopen-screen`

---

## 🌐 3. Multi-Track Language Routing on Stage Monitors
VoxStream allows every stage screen or mobile device to pick its own independent target language in real-time:

* **English Default**: `http://127.0.0.1:8765/display`
* **Spanish Track**: `http://127.0.0.1:8765/display?lang=es`
* **French Track**: `http://127.0.0.1:8765/display?lang=fr`
* **Portuguese Track**: `http://127.0.0.1:8765/display?lang=pt`
* **German Track**: `http://127.0.0.1:8765/display?lang=de`
* **Chinese Track**: `http://127.0.0.1:8765/display?lang=zh`

You can also use this for **OBS Browser Sources** (`http://127.0.0.1:8765/?lang=es`) to broadcast multiple language streams simultaneously!
