# 🎙️ OBS Live Captioner PRO: The Complete Setup Guide

Welcome! This suite offers **two different ways** to run live captions in OBS Studio. You can choose the method that best fits your workflow. 

---

## 🏗️ The Two Architectures Explained

### Method 1: The Python App + Web Dashboard (⭐ Highly Recommended)
**Best for**: Streamers, content creators, and 99% of users.
**How it works**: The captioner runs as an independent Python background app. You control it through a beautiful web-based dashboard that docks directly inside OBS. 
**Pros**:
- Includes the full visual theme customizer (Google Fonts, Word-Pop animations).
- Includes the built-in **Twitch Chat Bot** integration.
- Very stable: Heavy AI models run in a separate process, meaning if the AI spikes your CPU, it won't crash OBS.
- Easiest to install (1-click installer).

### Method 2: The Native OBS C++ Plugin (`.dll`)
**Best for**: C++ Developers and advanced power-users.
**How it works**: The captioner compiles into a native `.dll` plugin that runs directly *inside* the OBS Studio memory space as an Audio Filter.
**Pros**:
- Captures raw audio instantly from the OBS audio pipeline with absolute zero latency.
- Uses native Windows/Qt pop-up menus for settings instead of a web browser.
- **Note**: Because heavy AI libraries (like Python/CTranslate2) are massive, this native plugin acts as a fast front-end that still routes audio to the Python backend for heavy lifting.

---

## 🚀 Setup Guide: Method 1 (Python App & Web Dashboard)

### 1. Installation
* **On macOS / Linux**: Open Terminal in this folder and run:
  ```bash
  ./setup_mac.sh
  ```
* **On Windows**: Double-click **`setup_windows.bat`** (automatically installs Python 3.11 if needed and installs all AI dependencies).

### 2. Launching the App
* **On macOS / Linux**:
  ```bash
  ./run_captioner.sh
  ```
* **On Windows**: Double-click **`run_captioner.bat`**. 
*(Leave this running in the background while broadcasting!)*

### 3. Setting Up OBS Studio
**A. Add the Control Panel Dock:**
1. In OBS, go to the top menu: **Docks ➔ Custom Browser Docks...**
2. Name: `Live Captions` | URL: `http://127.0.0.1:8765/dashboard`
3. Click Apply, then drag the new panel anywhere into your OBS layout.

**B. Add the Stream Overlay (The Captions):**
1. In your active Scene, click **+ ➔ Browser**.
2. Name it `Captions Overlay`.
3. URL: `http://127.0.0.1:8765/`
4. Set Width to `1920` and Height to `1080` (or match your canvas).
5. **Check** "Shutdown source when not visible" and "Refresh browser when scene becomes active".

---

## 🛠️ Setup Guide: Method 2 (Native C++ Plugin)

### 1. Compiling the Plugin
1. Ensure you have **CMake** and **Visual Studio (C++ Desktop Development)** installed on your Windows PC.
2. Double-click **`build_plugin_windows.bat`**.
3. The script will compile the C++ source code into a `.dll` file located at: `build\Release\obs-live-captions.dll`.

### 2. Installing into OBS
1. Copy the `obs-live-captions.dll` file.
2. Paste it into your OBS plugins folder, typically located at:
   `C:\Program Files\obs-studio\obs-plugins\64bit\`
3. Restart OBS Studio.

### 3. Using the Plugin
1. To access settings, go to the top menu: **Tools ➔ Live Speech Captions Settings...**
2. To capture audio, right-click your Microphone in the Audio Mixer ➔ **Filters ➔ Add "Live Speech Captions (AI)"**.

---

## ✨ How to Setup Google Gemini 3.5 Transcribe Live

Google's newest streaming AI provides the highest accuracy, automatically cleans up "ums/ahs", and allows you to teach it custom gamer tags and church vocabulary.

**Step 1: Get a Free API Key**
1. Go to **[Google AI Studio (aistudio.google.com)](https://aistudio.google.com/)** and sign in.
2. Click **"Get API key"** in the left menu.
3. Click **"Create API key"** and copy your new key (it starts with `AIzaSy...`).

**Step 2: Configure the App**
*(If using Method 1 - Web Dashboard)*
1. Go to your **Live Captions Dock** inside OBS (or open `http://127.0.0.1:8765/dashboard` in Chrome).
2. Click the **🎙️ Audio & Engine** tab.
3. Under **Speech-to-Text Engine**, select: **✨ Gemini 3.5 Transcribe Live (Google AI Studio / Live API)**
4. Paste your `AIzaSy...` key into the **Google AI Studio API Key** box.
5. *(Optional)* Add your **Custom Vocabulary** (e.g. `OBS Studio, Twitch, Pastor Mike, Jesus Christ`).
6. Click **💾 Save Audio & Engine**.

*(If using Method 2 - Native C++ Plugin)*
1. Go to **Tools ➔ Live Speech Captions Settings...** in OBS.
2. Select **Gemini 3.5 Transcribe Live** from the Speech Engine dropdown.
3. Paste your API key into the credentials field and click Apply.

---

## 🎮 Playing on a GTX 1660? (Free Offline Option)
If you don't want to use cloud APIs, you can run **Local Faster-Whisper** 100% offline using your NVIDIA GTX 1660. 
In the Web Dashboard, select **Local Faster-Whisper** and simply click the **🎮 GTX 1660 (Recommended)** 1-click preset button. This configures the AI to use less than 2GB of VRAM, leaving your graphics card plenty of power for gaming!

---

## 🤖 Advanced OBS Automation (Auto-Launch & Projectors)

To make your setup completely automated (zero-touch projector launching and no annoying crash popups), use the built-in tools included right inside your suite:

### 1. Emergency "Start / Reopen Screen" Button (Always Visible in Top Bar)
If the conference room display ever shows a blank desktop or someone accidentally closes the window:
* Simply look at the **top right bar of the in-OBS Dock or Web Dashboard**.
* Click the cyan **`📺 Start / Reopen Screen`** button.
* This button instantly:
  1. Forcefully connects to OBS over WebSocket.
  2. Re-launches the video preview directly onto the **LONTIUM** screen (Monitor 1).
  3. Ensures live captions are running.
  4. Flashes a green confirmation toast: *"✅ LONTIUM Screen (Monitor 1) & Captions Active!"*.

### 2. Built-in 1-Click Projector Trigger (In the Web Dashboard / OBS Dock)
You don't need to dig through right-click menus anymore!
1. Open the **Live Captions** Dock inside OBS (or go to `http://127.0.0.1:8765/dashboard`).
2. Go to the new **📺 Projectors & Displays** tab.
3. Select your display (**Monitor 1: LONTIUM / Conference Screen**).
4. Click **📺 Open to Screen** — the preview is immediately sent to the conference room with 1 click!
5. ✅ Check **"Auto-Open Fullscreen Projector on OBS Launch / Stream Start"** so it happens automatically without anyone touching it.

### 3. Disable the "Didn't Close Correctly / Safe Mode" Popup
If your PC reboots or OBS closes ungracefully, OBS will halt your startup automation to ask if you want to run in Safe Mode. 
**The Fix**: Always use **`launch_obs_clean.bat`** in the project folder to start OBS. 
* It automatically clears the temporary `.sentinel` crash flags and launches OBS with `--disable-shutdown-check`, guaranteeing zero popup interruptions.

### 3. Native OBS Auto-Restore Setting
1. In OBS, go to **Settings ➔ General ➔ Projectors**.
2. ✅ Check **"Save projectors on exit"** (OBS will remember which monitors your projectors were on and reopen them automatically).
3. ✅ Check **"Hide cursor over projectors"**.
