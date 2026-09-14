# 🎙️ VoxStream — User Manual & Quick Start Guide

Welcome to the **VoxStream Live Captioner & Broadcast Suite** user manual. This guide is designed for AV sound booth operators, church media teams, live streamers, and broadcast technicians. It covers everything from 3-minute quick starts to advanced stage prompters, accessibility tools, and automated scripture cueing.

---

## 📑 Table of Contents

1. [⚡ Quick Start (3-Minute Setup)](#-quick-start-3-minute-setup)
2. [🧭 System Architecture & Screen URLs](#-system-architecture--screen-urls)
3. [🎙️ Choosing & Switching Speech Recognition Engines](#️-choosing--switching-speech-recognition-engines)
4. [🎬 Setting Up in OBS Studio](#-setting-up-in-obs-studio)
5. [📱 Stage Confidence Monitor & Congregation Read-Along (`/display`)](#-stage-confidence-monitor--congregation-read-along-display)
6. [♿ Accessibility & Reading Aid Suite](#-accessibility--reading-aid-suite)
7. [📖 Scripture Studio & Church Lexicon](#-scripture-studio--church-lexicon)
8. [📺 Native Closed Captions (YouTube & Twitch [CC])](#-native-closed-captions-youtube--twitch-cc)
9. [⏺️ Live Subtitle Recording & YouTube Chapters](#️-live-subtitle-recording--youtube-chapters)
10. [⚡ Real-Time Speaking Pace (WPM) Analytics](#-real-time-speaking-pace-wpm-analytics)
11. [🎛️ Keyboard Hotkeys & Stream Deck Automation](#️-keyboard-hotkeys--stream-deck-automation)
12. [🔧 Audio Setup & Tuning](#-audio-setup--tuning)
13. [❓ Troubleshooting & Frequently Asked Questions](#-troubleshooting--frequently-asked-questions)

---

## ⚡ Quick Start (3-Minute Setup)

Get live captions running on your stream and stage in 3 simple steps:

### Step 1: Launch VoxStream
* **macOS / Linux**: Open Terminal in the project directory and run:
  ```bash
  ./run_captioner.sh
  ```
* **Windows**: Double-click:
  ```cmd
  run_captioner.bat
  ```
> [!NOTE]
> Leave the terminal or command window open in the background while broadcasting. It runs the local server and AI models.

### Step 2: Open the Control Dashboard
Open your web browser (Chrome, Edge, Safari, or Firefox) and navigate to:
```
http://127.0.0.1:8765/dashboard
```
*(Or use your machine's LAN IP, e.g., `http://192.168.1.150:8765/dashboard`)*.

### Step 3: Connect to OBS Studio
1. In OBS Studio, go to your active Scene and click **`+` ➔ Browser**.
2. Name it **`Captions Overlay`**.
3. Set **URL** to: `http://127.0.0.1:8765/`
4. Set **Width** to `1920` and **Height** to `1080` (or match your canvas resolution).
5. Check **"Shutdown source when not visible"** and **"Refresh browser when scene becomes active"**.
6. Speak into your microphone — your spoken words will immediately appear as styled captions on screen!

---

## 🧭 System Architecture & Screen URLs

VoxStream operates a unified local web server (port `8765` by default) that drives four synchronized views:

| View | Default URL | Purpose |
| :--- | :--- | :--- |
| **Control Dashboard** | `http://127.0.0.1:8765/dashboard` | Main AV control console, engine selector, styling controls, audio meters, and live transcript history. |
| **OBS Stream Overlay** | `http://127.0.0.1:8765/` | Alpha-transparent browser source designed to be placed over camera feeds in OBS Studio. |
| **Captions Display View** | `http://127.0.0.1:8765/display` | High-contrast, responsive teleprompter and reader display for stage confidence monitors, iPads, and mobile phones. |
| **Scripture Lower-Third** | `http://127.0.0.1:8765/bible` | Dedicated transparent lower-third overlay for automated Bible verse citations and Scripture Studio cueing. |
| **Display QR Code** | `http://127.0.0.1:8765/api/display/qr` | Scalable SVG QR code for instant mobile and tablet connections over local Wi-Fi. |

---

## 🎙️ Choosing & Switching Speech Recognition Engines

VoxStream features **7 multi-tier speech engines** that can be hot-swapped on the fly without restarting the application. You can switch engines at any time from the **🎙️ Audio & Engine** tab in the Dashboard.

### Engine Comparison Matrix

| Engine | Accuracy | Latency | Offline / Private | Ideal Use Case |
| :--- | :---: | :---: | :---: | :--- |
| **🥇 Local Faster-Whisper** *(Default)* | **91.7%** | ~400–600ms | ✅ 100% Offline | **#1 Champion for Church Sermons**. Flawlessly recognizes biblical names (*Melchizedek, Nebuchadnezzar*), smart punctuation, zero word flapping. |
| **🌙 Local Moonshine** | **89.5%** | ~120–200ms | ✅ 100% Offline | Lightweight neural transformer (~5x faster than Whisper). Ideal for modern laptops and Mac Apple Silicon. |
| **⚡ Local Vosk / Kaldi** | **88.0%** | **~20–40ms** | ✅ 100% Offline | **Lowest Latency**. Syllable-by-syllable instant captions on any CPU (< 3% CPU usage). |
| **✨ Gemini 3.5 Transcribe Live** | **98.4%** | ~180–300ms | ☁️ Cloud AI | High accuracy, contextual church vocabulary prompting, smart punctuation, and automated filler-word cleanup. |
| **☁️ Google Cloud STT** | **92.1%** | ~250–400ms | ☁️ Cloud AI | Enterprise streaming (Chirp v2) with custom speech context phrase boosting. |
| **🌐 Bandwidth Labs Live STT** | **90.0%** | ~200–350ms | ☁️ Cloud AI | Low-latency cloud WebSocket streaming engine from labs.bandwidth.com. |
| **🆓 Google Web Speech** | **85.0%** | ~400–800ms | ☁️ Free Cloud | Zero-setup fallback engine (requires active internet connection). |

### How to Switch Engines
1. In the Dashboard, open the **🎙️ Audio & Engine** tab.
2. Select your desired engine from the **Speech-to-Text Engine** dropdown.
3. If using a cloud engine (like Gemini or Bandwidth), enter your API key in the designated input field.
4. Click **💾 Save Audio & Engine Settings**.
5. The audio pipeline immediately transitions to the new model in the background without dropping WebSocket connections.

---

## 🎬 Setting Up in OBS Studio

VoxStream integrates directly into OBS Studio via two browser elements:

### 1. In-OBS Control Panel Dock (Recommended)
Embed the full VoxStream dashboard right into your OBS window alongside your Audio Mixer and Scene list:
1. In OBS Studio, open the top menu: **Docks ➔ Custom Browser Docks...**
2. In the **Dock Name** column, type: `VoxStream Dock`
3. In the **URL** column, enter:
   ```
   http://127.0.0.1:8765/dashboard
   ```
4. Click **Apply**, then close the dialog.
5. Drag and snap the newly opened dock anywhere into your OBS layout (e.g., above or next to the Chat/Mixer).

### 2. Stream Overlay (Captions on Video)
1. Under **Sources** in your active scene, click **`+` ➔ Browser**.
2. Name it `Live Captions`.
3. Set **URL** to: `http://127.0.0.1:8765/`
4. Set **Width** to `1920` and **Height** to `1080` (adjust to match your canvas size if different).
5. Leave the custom CSS blank (all styling is managed in real time by VoxStream).
6. Enable:
   - ☑ **Shutdown source when not visible**
   - ☑ **Refresh browser when scene becomes active**
7. Click **OK**.

### 3. Scripture Lower-Third Overlay
To display dedicated Bible scripture graphics independently of the speech captions:
1. Click **`+` ➔ Browser** in OBS Sources.
2. Name it `Scripture Lower-Third`.
3. Set URL to: `http://127.0.0.1:8765/bible`
4. Set dimensions: `1920` x `1080`.

---

## 📱 Stage Confidence Monitor & Congregation Read-Along (`/display`)

The `/display` URL provides a universal, mobile-optimized live teleprompter and reader screen tailored for:
* **Stage Presenters & Pastors**: Podium iPads or confidence monitor TVs at the back of the auditorium.
* **Congregation & Visitors**: Read-along on smartphones or tablets in the sanctuary or overflow rooms.
* **Deaf & Hard-of-Hearing Attendees**: Real-time accessible transcription on personal devices.

```
┌────────────────────────────────────────────────────────────┐
│ 🎙️ VoxStream | Live Read-Along    [LIVE]      10:45:12 AM  │
│ [📱 Share] [⚙️ Settings] [A-] [A+] [⛶ Fullscreen] [🗑️ Clear] │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  "For God so loved the world that He gave His one and only │
│   Son, that whoever believes in Him shall not perish but   │
│   have eternal life."                                      │
│                                                            │
│  [📖 John 3:16 • BSB]                                      │
└────────────────────────────────────────────────────────────┘
```

### 📲 Sharing with Mobile Devices via QR Code
1. In the **Dashboard header**, click the **`📲 Share Captions QR`** button (or click **`📱 Share`** directly on the `/display` page).
2. A QR code modal will appear containing the machine's local Wi-Fi IP (e.g., `http://192.168.1.150:8765/display`).
3. Point any smartphone, iPad, or tablet camera at the QR code to join instantly!
4. *(Optional)* Select a target language dropdown before scanning (e.g., Spanish or French) to launch the screen locked to that language track.

### 📲 Installing as a Progressive Web App (PWA)
You can install the Stage Display as a standalone app on iPads, iPhones, Android devices, and laptops:
1. Open the `/display` page in Safari (iOS) or Chrome (Android/Desktop).
2. Click **⚙️ Settings** in the top bar and tap **📲 Install Stage Display PWA** (or tap Safari's **Share ➔ Add to Home Screen**).
3. The display will launch in full-screen standalone mode with no browser tabs or URL bars.

### 🔋 Automatic Screen Wake Lock
The display automatically engages the **Screen Wake Lock API**. As long as the page is open, the tablet or monitor will remain awake and never dim or sleep during preaching or presentations.

### 📜 Choosing a Display Mode
Toggle the mode selector in the top bar:
* **`📜 Scrollable History` (Default)**: Maintains a persistent transcript of the entire session. Readers can scroll up with touch or mouse wheel to review earlier statements and scripture passages.
* **`⚡ Live Prompter`**: Focuses on the current 2 active sentences, automatically fading them out on extended silence for a distraction-free teleprompter.

---

## ♿ Accessibility & Reading Aid Suite

VoxStream includes dedicated reading tools designed according to **WCAG 2.2 AAA** guidelines:

### ⚡ Bionic Reading (Guided Word Focus)
* **What it does**: Dynamically bolds the initial 2–3 letters of every word (`<b>Wel</b>come <b>t</b>o <b>our</b> <b>ser</b>vice`).
* **Why it helps**: Creates artificial visual fixation points that guide the eye, significantly improving reading speed and focus for viewers with **ADHD**, dyslexia, or cognitive fatigue.
* **How to enable**: Click **⚙️ Settings** on the Display page and toggle **⚡ Bionic Reading**.

### 👁️ Visual Aid Mode
* **What it does**: Expands letter spacing (`+0.06em`), word spacing (`+0.15em`), and line height (`1.65x`) to eliminate crowded text without altering font choices.

### 🐢 Slow Down Text (Paced Reading)
* **What it does**: Acts as a buffer governor for rapid speech. Delivers words at a comfortable reading pace (~135 WPM) and holds completed thoughts on screen for **10.0 seconds** before cycling.

### 📖 Dyslexia-Aid Fonts
Select from tested typography directly in the styling controls:
* **OpenDyslexic**: Heavily weighted baselines prevent character flipping and letter rotation.
* **Lexend**: Engineered by educational researchers to reduce visual stress and improve word recognition.
* **Inter, Bebas Neue, Oswald, Roboto**: Clean modern sans-serifs optimized for 1080p and 4K screens.

### 🎨 High-Contrast & OLED Color Palettes
* **🌙 OLED Black**: Deep black `#000000` background with crisp white typography for zero glare.
* **🟨 High Vis (Yellow on Black)**: Maximum visual contrast for low-vision environments.
* **🟡 Stage Amber**: Warm amber typography on dark background, gentle on the eyes under stage lighting.
* **☀️ Clean Light**: High-contrast dark text on bright background for daylight settings.

---

## 📖 Scripture Studio & Church Lexicon

VoxStream includes a built-in offline Bible database and an intelligent theological parser:

### 1. Spoken Citation Auto-Detection
When a speaker quotes a scripture passage naturally, VoxStream recognizes it and formats it into standard chapter:verse syntax:
* *"In John three sixteen we read..."* ➔ **`John 3:16`**
* *"Turn to First Corinthians chapter thirteen verses four through seven"* ➔ **`1 Corinthians 13:4-7`**
* *"Romans eight twenty-eight"* ➔ **`Romans 8:28`**
* *"Psalm twenty-three"* ➔ **`Psalm 23`**

### 2. Instant Offline Bible Lookup
Once a reference is spoken, VoxStream queries its local SQLite database in **< 0.1ms** without internet access. It automatically displays the passage on:
* The **OBS Scripture Overlay** (`/bible`) as a styled lower-third banner.
* The **Stage Display** (`/display`) as a floating prompter card.

Supported offline translations:
* **BSB (Berean Standard Bible)**: Modern, highly accurate translation reading virtually identical to the **ESV**.
* **WEB (World English Bible)**: Modern public domain translation reading like the **NIV / NLT**.
* **KJV (King James Version)**: Classic authorized text.

### 3. Interactive Scripture Cue Tool
In the Dashboard under **📖 Scripture Studio**:
* Type any verse reference (e.g., `Romans 12:2`) or keyword in the search bar.
* Preview the text and click **📺 Push to Stream** to display it instantly.
* Click **✕ Dismiss Verse** to clear it when the speaker moves on.

### 4. Church & Theological Lexicon
VoxStream automatically truecases and capitalizes sacred names and titles of deity:
* `God`, `God's`, `Lord`, `Lord's`, `Jesus`, `Jesus Christ`, `Holy Spirit`, `Heavenly Father`, `King of Kings`, `Prince of Peace`, `Lamb of God`, `Messiah`, `Savior`, `Yahweh`.
* Canonical names across Old and New Testaments (`Genesis`, `Exodus`, `1 Kings`, `Matthew`, `Philippians`, `Revelation`).
* Context-aware profanity filtering protects scriptural phrases like *"heaven and hell"* from accidental censoring.

---

## 📺 Native Closed Captions (YouTube & Twitch [CC])

Instead of (or in addition to) burning captions into the video image, VoxStream can inject **native CEA-608 / CEA-708 closed captions** directly into your stream output. Viewers on YouTube Live and Twitch can toggle captions on or off using the player's **[CC]** button.

### How to Enable CEA-608 Captions
1. In OBS Studio, enable the WebSocket server:
   - Go to: **Tools ➔ WebSocket Server Settings**.
   - Check **☑ Enable WebSocket server** (Server Port: `4455`).
   - Note your Server Password.
2. In the VoxStream Dashboard, navigate to **Tab 5: Settings / OBS Integration**.
3. Under **OBS WebSocket Settings**:
   - Host: `127.0.0.1` | Port: `4455`
   - Enter your OBS WebSocket password.
   - Click **🔗 Connect to OBS WebSocket**.
4. Once connected, toggle **☑ Enable CEA-608 Live Captions to Stream**.
5. Start your live stream in OBS. VoxStream will now inject caption packets directly into the video stream headers!

---

## ⏺️ Live Subtitle Recording & YouTube Chapters

VoxStream records synchronized sidecar subtitle files while you broadcast:

### 1. Recording Live Subtitles (.SRT & .VTT)
* In the Dashboard header, click **`⏺ REC Sidecar`**.
* The button turns red (`🔴 REC 00:00 (0)`) and tracks elapsed time and line count.
* When your service or stream finishes, click **`⏹ Stop REC`**.
* The completed `.srt` and `.vtt` files are saved to the `recordings/` folder, ready for direct upload to YouTube, Vimeo, or video editing software (Premiere Pro, DaVinci Resolve, Final Cut Pro).

### 2. Automated YouTube Chapter Generation
VoxStream automatically tracks scripture citations and topic breaks throughout the service. In the **📜 Transcript & Export** tab:
* View timestamped YouTube Chapters formatted as:
  ```
  00:00 Welcome & Opening Prayer
  12:45 Scripture Reading: John 3:1-21
  28:10 Sermon: The Living Hope (1 Peter 1:3)
  52:30 Closing Benediction
  ```
* Click **📋 Copy YouTube Description** to paste chapter timestamps directly into your video description.

---

## ⚡ Real-Time Speaking Pace (WPM) Analytics

VoxStream continuously monitors speech pacing using a 45-second rolling window to help pastors, guest speakers, and presenters maintain optimal delivery speed.

### Qualitative Pace Badges
Displayed in the top header and transcript console:
* 🟢 **Optimal Pace (110–150 WPM)**: Ideal for comprehension, sanctuary acoustics, and comfortable congregation reading.
* 🟡 **Slow Pace (< 100 WPM)**: Useful for meditation, emphasis, or prayer.
* 🟡 **Brisk Pace (151–180 WPM)**: Energetic delivery; approaching threshold for fast reading.
* 🔴 **Rapid Pace (> 180 WPM)**: Visual cue that speech is moving too fast for viewers or read-along participants.

Session statistics track total words spoken, active minutes, and session average WPM.

---

## 🎛️ Keyboard Hotkeys & Stream Deck Automation

### Keyboard Shortcuts
When viewing the **Dashboard** or the **Stage Display (`/display`)**:

| Key | Action | Description |
| :---: | :--- | :--- |
| `S` | **Settings** | Opens/closes the Display & Reading Settings modal. |
| `H` | **Toggle Header** | Hides or reveals the top navigation bar for clean full-screen teleprompting. |
| `C` | **Clear History** | Flushes the visible transcript display. |
| `+` / `=` | **Increase Font** | Scales up caption typography size. |
| `-` / `_` | **Decrease Font** | Scales down caption typography size. |
| `F11` | **Fullscreen** | Toggles browser full-screen mode. |
| `Esc` | **Close Modal** | Closes any open modal dialog (Share, Settings, QR). |

### Stream Deck & Bitfocus Companion Integration
You can control VoxStream from hardware broadcast controllers using HTTP REST calls:

* **1-Button Panic Drop** (Immediately clear all captions across all screens):
  ```http
  POST http://127.0.0.1:8765/api/control/panic
  ```
* **Toggle Speech Recognition Engine** (Pause / Resume):
  ```http
  POST http://127.0.0.1:8765/api/control/toggle
  ```
* **Apply 1-Click Theme Preset**:
  ```http
  POST http://127.0.0.1:8765/api/presets/apply
  Content-Type: application/json

  {"theme_id": "sanctuary_worship"}
  ```
* **Reopen Conference Screen Projector**:
  ```http
  POST http://127.0.0.1:8765/api/control/reopen-screen
  ```

---

## 🔧 Audio Setup & Tuning

### 1. Selecting the Correct Microphone Device
1. Open the Dashboard ➔ **🎙️ Audio & Engine** tab.
2. In the **Audio Input Device** dropdown, select your dedicated audio source (e.g., *Focusrite Scarlett*, *Behringer X32 USB*, *Dante Virtual Soundcard*, or *OBS Virtual Audio Cable*).
3. Check the **MIC VU Meter** in the top header.
4. When speaking at normal preaching volume, the meter should bounce between **-18 dBFS and -6 dBFS** (green to yellow). If it stays in dark blue (-40 dBFS), increase your interface gain.

### 2. Voice Activity Detection (VAD) & Music Suppression
* **Silero VAD**: VoxStream includes a built-in neural Voice Activity Detector that filters out room rumble, air conditioning noise, and silence.
* **Music Suppression**: When worship teams are playing music between preaching, enable **Suppress Music & Organ** in the Audio tab to prevent the AI from generating gibberish lyrics during instrumental segments.

---

## ❓ Troubleshooting & Frequently Asked Questions

### Q1: Captions are not appearing in OBS Studio.
* Verify VoxStream is running: Open `http://127.0.0.1:8765/dashboard` in your browser.
* Check the OBS Browser Source URL: Ensure it is set to `http://127.0.0.1:8765/`.
* Check microphone input: In the dashboard header, look at the **MIC VU meter**. If it reads `-∞ dB`, the wrong input device is selected in the **🎙️ Audio & Engine** tab.
* Click **⏹ Stop Captions** and then **▶ Start Captions** to cycle the audio capture worker.

### Q2: Phones cannot connect to the QR code on local Wi-Fi.
* Ensure the mobile device is connected to the **same local Wi-Fi network** as the host PC (not cellular LTE/5G).
* Verify your host computer's firewall allows incoming connections on port `8765`.
  * *Windows*: Allow `python.exe` through Windows Defender Firewall for Private Networks.
  * *macOS*: System Settings ➔ Network ➔ Firewall ➔ allow incoming connections for Python.

### Q3: How do I change the font size or position of captions in OBS?
* Open the **Dashboard** ➔ **Tab 1: OBS & Captions Live Preview**.
* Adjust the **Font Size**, **Line Spacing**, **Bottom Margin**, or **Max Lines** sliders.
* All changes update in OBS in real time without refreshing the source!

### Q4: Can I use VoxStream completely offline without internet?
* **Yes!** When using **Local Faster-Whisper**, **Local Moonshine**, or **Local Vosk**, all speech recognition, smart punctuation, church lexicon processing, and Bible scripture lookups run 100% locally on your computer with zero internet access required.

### Q5: How do I update VoxStream when a new version is released?
* When an update is available, a green **`✨ Update Available`** button will appear in the Dashboard header.
* Click it, or pull the latest changes via git:
  ```bash
  git pull origin main
  ```
* Restart the app using `./run_captioner.sh` or `run_captioner.bat`.

---

**VoxStream Live Captioner & Broadcast Suite**  
*Built for sanctuaries, broadcasters, and accessible live streams worldwide.*
