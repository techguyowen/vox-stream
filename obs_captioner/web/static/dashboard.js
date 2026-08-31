// OBS Live Captions Dashboard & Dock Controller (PRO Suite)

// --- API auth -------------------------------------------------------------
// When api.api_key is set in config.json the backend returns 401 on control
// endpoints. Wrap fetch for same-origin /api/ calls so a stored key is sent
// as a Bearer token, prompting for it once when the server rejects us.
const API_KEY_STORAGE = "voxstream_api_key";
const nativeFetch = window.fetch.bind(window);
let authPromptShown = false;
window.fetch = async (url, opts = {}) => {
    if (typeof url === "string" && url.startsWith("/api/")) {
        const key = localStorage.getItem(API_KEY_STORAGE);
        if (key) {
            opts.headers = Object.assign({}, opts.headers, { "Authorization": `Bearer ${key}` });
        }
        let res = await nativeFetch(url, opts);
        if (res.status === 401 && !authPromptShown) {
            authPromptShown = true;
            const entered = prompt("This VoxStream server requires an API key (config.json → api.api_key):");
            if (entered && entered.trim()) {
                localStorage.setItem(API_KEY_STORAGE, entered.trim());
                opts.headers = Object.assign({}, opts.headers, { "Authorization": `Bearer ${entered.trim()}` });
                res = await nativeFetch(url, opts);
                if (res.ok) authPromptShown = false;
            }
        }
        return res;
    }
    return nativeFetch(url, opts);
};

function apiKeyQuerySuffix() {
    const key = localStorage.getItem(API_KEY_STORAGE);
    return key ? `?api_key=${encodeURIComponent(key)}` : "";
}
// --------------------------------------------------------------------------

let currentConfig = null;
let controlWs = null;
let captionWs = null;
let isRunning = true;
let activeThemeId = "modern_clean";

// DOM Elements
const vuBar = document.getElementById("vu-meter-bar");
const statusPill = document.getElementById("status-pill");
const statusText = document.getElementById("status-text");
const btnToggleEngine = document.getElementById("btn-toggle-engine");

// Preview Elements
const previewBox = document.getElementById("preview-box");
const previewFinal = document.getElementById("preview-final");
const previewInterim = document.getElementById("preview-interim");

// Initialize Tabs
document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
        
        btn.classList.add("active");
        const tabId = `tab-${btn.dataset.tab}`;
        const pane = document.getElementById(tabId);
        if (pane) pane.classList.add("active");

        if (btn.dataset.tab === "transcript") {
            loadTranscriptHistory();
        } else if (btn.dataset.tab === "filter") {
            loadFilterState();
        }
    });
});

// Load Config from Server
async function loadConfig() {
    try {
        const res = await fetch("/api/config");
        if (res.ok) {
            currentConfig = await res.json();
            populateFormFields(currentConfig);
            updatePreviewStyles();
        }
    } catch (e) {
        console.error("Failed to load config:", e);
    }
}

// Load Theme Presets
async function loadThemes() {
    try {
        const res = await fetch("/api/presets");
        if (res.ok) {
            const data = await res.json();
            renderThemeGrid(data.presets || []);
        }
    } catch (e) {
        console.error("Failed to load themes:", e);
    }
}

function renderThemeGrid(presets) {
    const grid = document.getElementById("theme-grid");
    if (!grid) return;

    grid.innerHTML = presets.map(p => `
        <div class="theme-card ${p.id === activeThemeId ? 'active' : ''}" data-theme-id="${escapeHtml(p.id)}">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
                <div class="theme-card-name">${escapeHtml(p.name)}</div>
                ${p.is_custom ? `<button class="btn-icon-del btn-del-preset" data-del-preset="${escapeHtml(p.id)}" title="Delete Custom Preset" style="padding: 2px 6px; font-size: 11px; border: 1px solid rgba(239, 68, 68, 0.4); border-radius: 4px; background: rgba(239, 68, 68, 0.15); color: #EF4444; cursor: pointer;">🗑️</button>` : ''}
            </div>
            <div class="theme-card-desc">${escapeHtml(p.description)}</div>
            ${p.is_custom ? '<span style="font-size: 10px; background: rgba(99, 102, 241, 0.2); color: #A5B4FC; padding: 2px 6px; border-radius: 4px; display: inline-block; margin-top: 6px; font-weight: 600;">Custom Preset</span>' : ''}
        </div>
    `).join("");

    grid.querySelectorAll(".theme-card").forEach(card => {
        card.addEventListener("click", async (e) => {
            if (e.target.closest(".btn-del-preset")) return;
            const themeId = card.dataset.themeId;
            await applyThemePreset(themeId);
        });
    });

    grid.querySelectorAll(".btn-del-preset").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const presetId = btn.dataset.delPreset;
            if (!confirm(`Are you sure you want to delete this custom preset?`)) return;
            try {
                const res = await fetch("/api/presets/delete", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id: presetId }),
                });
                if (res.ok) {
                    showToast("🗑️ Custom preset deleted.", "info");
                    await loadThemes();
                } else {
                    const err = await res.json();
                    showToast(`⚠️ Failed to delete preset: ${err.error || "Unknown error"}`, "error");
                }
            } catch (err) {
                console.error("Error deleting preset:", err);
            }
        });
    });
}

async function applyThemePreset(themeId) {
    try {
        const res = await fetch("/api/presets/apply", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ theme_id: themeId }),
        });
        if (res.ok) {
            activeThemeId = themeId;
            await loadConfig();
            await loadThemes();
            showToast("🎨 Theme preset applied!", "success", 2000);
        }
    } catch (e) {
        console.error("Error applying theme:", e);
    }
}

// Save Custom Preset Handlers
const btnOpenSavePreset = document.getElementById("btn-open-save-preset");
const savePresetCard = document.getElementById("save-preset-card");
const btnCancelSavePreset = document.getElementById("btn-cancel-save-preset");
const btnSaveNewPreset = document.getElementById("btn-save-new-preset");

if (btnOpenSavePreset && savePresetCard) {
    btnOpenSavePreset.addEventListener("click", () => {
        const isHidden = savePresetCard.style.display === "none";
        savePresetCard.style.display = isHidden ? "block" : "none";
        if (isHidden) {
            document.getElementById("preset-new-name")?.focus();
        }
    });
}

if (btnCancelSavePreset && savePresetCard) {
    btnCancelSavePreset.addEventListener("click", () => {
        savePresetCard.style.display = "none";
    });
}

if (btnSaveNewPreset) {
    btnSaveNewPreset.addEventListener("click", async () => {
        const nameInput = document.getElementById("preset-new-name");
        const descInput = document.getElementById("preset-new-desc");
        const name = nameInput ? nameInput.value.trim() : "";
        const desc = descInput ? descInput.value.trim() : "";

        if (!name) {
            showToast("⚠️ Please enter a name for your custom preset.", "error");
            return;
        }

        // Collect current styling: form controls for what the form edits, and
        // the currently-applied overlay config for everything else (so saving
        // a preset doesn't silently reset weight/line-height/radius/etc.)
        const bgPicker = document.getElementById("bg_color_picker")?.value || "#0f0f14";
        const bgOpacity = (parseInt(document.getElementById("bg_opacity_slider")?.value || 72, 10) / 100).toFixed(2);
        const pr = parseInt(bgPicker.slice(1, 3), 16);
        const pg = parseInt(bgPicker.slice(3, 5), 16);
        const pb = parseInt(bgPicker.slice(5, 7), 16);
        const ov = (currentConfig && currentConfig.overlay) || {};
        const presetData = {
            name: name,
            description: desc || "Custom user preset.",
            font_family: document.getElementById("font_family")?.value || ov.font_family || "Inter, sans-serif",
            font_size: (document.getElementById("font_size_slider")?.value || "32") + "px",
            font_weight: ov.font_weight || "700",
            line_height: ov.line_height || "1.35",
            text_color: document.getElementById("text_color")?.value || "#FFFFFF",
            interim_color: document.getElementById("interim_color")?.value || "#90CAF9",
            highlight_color: document.getElementById("highlight_color")?.value || "#FFD166",
            background_box_color: `rgba(${pr}, ${pg}, ${pb}, ${bgOpacity})`,
            border_radius: ov.border_radius || "12px",
            box_padding: ov.box_padding || "14px 26px",
            text_shadow: ov.text_shadow || "2px 2px 5px rgba(0, 0, 0, 0.95)",
            text_stroke: ov.text_stroke || "2px #000000",
            animation_style: document.getElementById("animation_style")?.value || "word_pop",
        };

        try {
            const res = await fetch("/api/presets/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(presetData),
            });
            if (res.ok) {
                const resData = await res.json();
                showToast(`✅ Saved custom preset "${name}"!`, "success");
                if (nameInput) nameInput.value = "";
                if (descInput) descInput.value = "";
                if (savePresetCard) savePresetCard.style.display = "none";
                await applyThemePreset(resData.preset.id);
            } else {
                const errData = await res.json();
                showToast(`⚠️ Failed to save preset: ${errData.error || "Unknown error"}`, "error");
            }
        } catch (e) {
            console.error("Error saving preset:", e);
            showToast(`❌ Error saving preset: ${e.message || e}`, "error");
        }
    });
}

// Load Available Audio Devices
async function loadAudioDevices() {
    try {
        const res = await fetch("/api/devices");
        if (res.ok) {
            const data = await res.json();
            const select = document.getElementById("audio_device_select");
            select.innerHTML = '<option value="default">Default Input Device</option>';
            
            data.devices.forEach(dev => {
                const opt = document.createElement("option");
                opt.value = dev.name;
                opt.textContent = `[${dev.index}] ${dev.name} (${dev.hostapi})`;
                select.appendChild(opt);
            });

            if (currentConfig && currentConfig.audio && currentConfig.audio.device_name_filter) {
                select.value = currentConfig.audio.device_name_filter;
            }
        }
    } catch (e) {
        console.error("Failed to load audio devices:", e);
    }
}

// Set a <select> value, adding the option dynamically if it's missing so an
// unlisted config value (e.g. a theme's font) can't collapse to "" and get
// written back to config.json as an empty string on the next auto-sync.
function setSelectValue(select, value) {
    if (!select || value === undefined || value === null || value === "") return;
    select.value = String(value);
    if (select.value !== String(value)) {
        const opt = document.createElement("option");
        opt.value = String(value);
        opt.textContent = String(value);
        select.appendChild(opt);
        select.value = String(value);
    }
}

// Parse a stored background color ("rgba(r,g,b,a)" or "#rrggbb") into the
// color picker + opacity slider so style tweaks preserve the theme's box.
function populateBackgroundControls(bgColor) {
    const picker = document.getElementById("bg_color_picker");
    const opacitySlider = document.getElementById("bg_opacity_slider");
    const opacityLabel = document.getElementById("val-bg-opacity");
    if (!picker || !opacitySlider || !bgColor) return;

    const toHex = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
    const rgbaMatch = bgColor.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)/i);
    if (rgbaMatch) {
        picker.value = `#${toHex(+rgbaMatch[1])}${toHex(+rgbaMatch[2])}${toHex(+rgbaMatch[3])}`;
        const alpha = rgbaMatch[4] !== undefined ? parseFloat(rgbaMatch[4]) : 1.0;
        opacitySlider.value = Math.round(alpha * 100);
    } else if (/^#[0-9a-f]{6}$/i.test(bgColor)) {
        picker.value = bgColor;
        opacitySlider.value = 100;
    } else {
        return;
    }
    if (opacityLabel) opacityLabel.textContent = `${opacitySlider.value}%`;
}

// Populate Inputs with Config Values
function populateFormFields(cfg) {
    // Style tab
    if (cfg.overlay) {
        const ov = cfg.overlay;
        activeThemeId = ov.theme_id || "modern_clean";
        setSelectValue(document.getElementById("font_family"), ov.font_family);
        if (ov.font_size) {
            const sizeVal = parseInt(ov.font_size) || 32;
            document.getElementById("font_size_slider").value = sizeVal;
            document.getElementById("val-font-size").textContent = `${sizeVal}px`;
        }
        if (ov.max_width) {
            const wVal = parseInt(ov.max_width) || 90;
            document.getElementById("max_width_slider").value = wVal;
            document.getElementById("val-max-width").textContent = `${wVal}%`;
        }
        if (ov.max_lines) setSelectValue(document.getElementById("max_lines"), ov.max_lines);
        if (ov.text_align) document.getElementById("text_align").value = ov.text_align;
        if (ov.animation_style) setSelectValue(document.getElementById("animation_style"), ov.animation_style);
        if (ov.auto_hide_seconds !== undefined) {
            document.getElementById("auto_hide_slider").value = ov.auto_hide_seconds;
            document.getElementById("val-auto-hide").textContent = `${ov.auto_hide_seconds}s`;
        }
        if (ov.text_color) document.getElementById("text_color").value = ov.text_color;
        if (ov.interim_color) document.getElementById("interim_color").value = ov.interim_color;
        if (ov.highlight_color) document.getElementById("highlight_color").value = ov.highlight_color;
        populateBackgroundControls(ov.background_box_color);
    }

    // Filter tab
    if (cfg.censor) {
        const c = cfg.censor;
        document.getElementById("censor_enabled").checked = !!c.enabled;
        document.getElementById("censor_mode").value = c.mode || "replacement";
        document.getElementById("filter_standard_profanity").checked = !!c.filter_standard_profanity;
        document.getElementById("filter_church_blasphemy").checked = !!c.filter_church_blasphemy;
        document.getElementById("filter_crude_terms").checked = !!c.filter_crude_terms;
    }

    // Translation tab
    if (cfg.translation) {
        document.getElementById("translation_enabled").checked = !!cfg.translation.enabled;
        document.getElementById("translation_target").value = cfg.translation.target_language || "es";
        document.getElementById("translation_mode").value = cfg.translation.display_mode || "dual";
    }

    // Audio tab
    if (cfg.general) {
        document.getElementById("engine_select").value = cfg.general.engine || "vosk";
        document.getElementById("language_select").value = cfg.general.language || "en-US";
        if (cfg.general.auto_capitalization !== undefined) {
            document.getElementById("auto_capitalization").checked = !!cfg.general.auto_capitalization;
        }
        if (cfg.general.auto_punctuation !== undefined) {
            document.getElementById("auto_punctuation").checked = !!cfg.general.auto_punctuation;
        }
        if (cfg.general.church_mode !== undefined) {
            document.getElementById("church_mode").checked = !!cfg.general.church_mode;
        }
        toggleEngineFields(cfg.general.engine || "vosk");
    }
    if (cfg.audio) {
        document.getElementById("noise_gate_slider").value = cfg.audio.noise_gate_db || -45;
        document.getElementById("val-noise-gate").textContent = `${cfg.audio.noise_gate_db || -45} dB`;
        document.getElementById("vad_slider").value = cfg.audio.vad_threshold || 0.5;
        document.getElementById("val-vad").textContent = cfg.audio.vad_threshold || 0.5;
    }
    if (cfg.google_stt) {
        document.getElementById("google_creds_path").value = cfg.google_stt.credentials_path || "";
    }
    if (cfg.gemini_live) {
        document.getElementById("gemini_api_key").value = cfg.gemini_live.api_key || "";
        if (cfg.gemini_live.model) document.getElementById("gemini_model").value = cfg.gemini_live.model;
        if (cfg.gemini_live.custom_vocabulary) {
            document.getElementById("gemini_custom_vocab").value = cfg.gemini_live.custom_vocabulary.join(", ");
        }
        document.getElementById("gemini_smart_transcription").checked = cfg.gemini_live.smart_transcription !== false;
    }
    if (cfg.local_whisper) {
        document.getElementById("whisper_model").value = cfg.local_whisper.model_size || "base.en";
        document.getElementById("whisper_device").value = cfg.local_whisper.device || "cuda";
        if (cfg.local_whisper.compute_type) document.getElementById("whisper_compute").value = cfg.local_whisper.compute_type;
        if (cfg.local_whisper.beam_size) document.getElementById("whisper_beam").value = cfg.local_whisper.beam_size;
        updateWhisperStats();
    }
    if (cfg.vosk) {
        document.getElementById("vosk_model").value = cfg.vosk.model_name || "small";
        document.getElementById("vosk_model_path").value = cfg.vosk.model_path || "";
    }
    if (cfg.moonshine) {
        document.getElementById("moonshine_model").value = cfg.moonshine.model_name || "moonshine/tiny";
    }

    // Projector & OBS Display Automation tab
    if (cfg.obs) {
        document.getElementById("obs_auto_projector").checked = !!cfg.obs.auto_open_projector;
        if (cfg.obs.projector_type) document.getElementById("obs_projector_type").value = cfg.obs.projector_type;
        if (cfg.obs.projector_monitor_index !== undefined) {
            document.getElementById("obs_projector_monitor").value = cfg.obs.projector_monitor_index;
            document.getElementById("quick_projector_monitor").value = cfg.obs.projector_monitor_index;
        }
        if (cfg.obs.projector_source_name) document.getElementById("obs_projector_source_name").value = cfg.obs.projector_source_name;
        toggleProjectorSourceField();
    }

    // Twitch tab
    if (cfg.twitch) {
        document.getElementById("twitch_enabled").checked = !!cfg.twitch.enabled;
        document.getElementById("twitch_channel").value = cfg.twitch.channel || "";
        document.getElementById("twitch_username").value = cfg.twitch.bot_username || "";
        document.getElementById("twitch_oauth").value = cfg.twitch.oauth_token || "";
    }
}

// Live Preview Style Updates
let autoSaveStyleTimer = null;

function buildOverlayStylePayload() {
    const bgPicker = document.getElementById("bg_color_picker").value;
    const opacity = document.getElementById("bg_opacity_slider").value / 100.0;
    const r = parseInt(bgPicker.slice(1, 3), 16);
    const g = parseInt(bgPicker.slice(3, 5), 16);
    const b = parseInt(bgPicker.slice(5, 7), 16);

    const overlay = {
        font_size: `${document.getElementById("font_size_slider").value}px`,
        max_width: `${document.getElementById("max_width_slider").value}%`,
        max_lines: parseInt(document.getElementById("max_lines").value, 10) || 2,
        text_align: document.getElementById("text_align").value,
        animation_style: document.getElementById("animation_style").value,
        auto_hide_seconds: parseFloat(document.getElementById("auto_hide_slider").value) || 0,
        text_color: document.getElementById("text_color").value,
        interim_color: document.getElementById("interim_color").value,
        highlight_color: document.getElementById("highlight_color").value,
        background_box_color: `rgba(${r}, ${g}, ${b}, ${opacity})`,
    };
    // Never persist an empty font family (e.g. a select in a transient state)
    const fontFamily = document.getElementById("font_family").value;
    if (fontFamily) overlay.font_family = fontFamily;
    return { overlay };
}

function autoSyncStyleChanges() {
    updatePreviewStyles();
    if (autoSaveStyleTimer) clearTimeout(autoSaveStyleTimer);
    // 800ms debounce: every sync writes config.json to disk and broadcasts to
    // all connected clients, so slider drags shouldn't fire dozens of them.
    autoSaveStyleTimer = setTimeout(async () => {
        try {
            await fetch("/api/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(buildOverlayStylePayload()),
            });
        } catch (e) {
            console.warn("Auto-sync visual settings failed:", e);
        }
    }, 800);
}

function updatePreviewStyles() {
    const font = document.getElementById("font_family").value;
    const fontSize = `${document.getElementById("font_size_slider").value}px`;
    const maxWidth = `${document.getElementById("max_width_slider").value}%`;
    const textAlign = document.getElementById("text_align").value;
    const textColor = document.getElementById("text_color").value;
    const interimColor = document.getElementById("interim_color").value;
    const bgPicker = document.getElementById("bg_color_picker").value;
    const opacity = document.getElementById("bg_opacity_slider").value / 100.0;
    const maxLines = parseInt(document.getElementById("max_lines")?.value) || 2;

    const r = parseInt(bgPicker.slice(1, 3), 16);
    const g = parseInt(bgPicker.slice(3, 5), 16);
    const b = parseInt(bgPicker.slice(5, 7), 16);
    const bgColorRgba = `rgba(${r}, ${g}, ${b}, ${opacity})`;

    previewBox.style.fontFamily = font;
    previewBox.style.maxWidth = maxWidth;
    previewBox.style.textAlign = textAlign;
    previewBox.style.background = bgColorRgba;

    previewFinal.style.fontSize = fontSize;
    previewFinal.style.color = textColor;

    previewInterim.style.fontSize = fontSize;
    previewInterim.style.color = interimColor;

    // Show realistic lines in the preview according to max_lines
    if (maxLines === 1) {
        previewFinal.innerHTML = "";
        previewInterim.textContent = "Live speech displays here in a clean 1-line ticker.";
    } else if (maxLines === 2) {
        previewFinal.innerHTML = "<div>Welcome to the stream! Today we are exploring live captions.</div>";
        previewInterim.textContent = "Here is the second live interim sentence...";
    } else {
        const demoSentences = [
            "Welcome to the live stream broadcast.",
            "Captions are streaming in real-time with ultra-low latency.",
            "All previous speech history remains visible on screen.",
            "Multi-line paragraph view is active.",
            "Continuous rolling speech displays seamlessly without cutting off."
        ];
        const linesToShow = demoSentences.slice(0, Math.min(maxLines - 1, demoSentences.length));
        previewFinal.innerHTML = linesToShow.map(s => `<div style="margin-bottom: 4px;">${s}</div>`).join("");
        previewInterim.textContent = "Speaking next sentence here in real-time...";
    }
}

// Attach Input Listeners with Auto-Sync
document.getElementById("font_family").addEventListener("change", autoSyncStyleChanges);
document.getElementById("max_lines").addEventListener("change", autoSyncStyleChanges);
document.getElementById("text_align").addEventListener("change", autoSyncStyleChanges);
document.getElementById("animation_style").addEventListener("change", autoSyncStyleChanges);
document.getElementById("text_color").addEventListener("input", autoSyncStyleChanges);
document.getElementById("interim_color").addEventListener("input", autoSyncStyleChanges);
document.getElementById("highlight_color").addEventListener("input", autoSyncStyleChanges);
document.getElementById("bg_color_picker").addEventListener("input", autoSyncStyleChanges);

document.getElementById("font_size_slider").addEventListener("input", (e) => {
    document.getElementById("val-font-size").textContent = `${e.target.value}px`;
    autoSyncStyleChanges();
});
document.getElementById("max_width_slider").addEventListener("input", (e) => {
    document.getElementById("val-max-width").textContent = `${e.target.value}%`;
    autoSyncStyleChanges();
});
document.getElementById("bg_opacity_slider").addEventListener("input", (e) => {
    document.getElementById("val-bg-opacity").textContent = `${e.target.value}%`;
    autoSyncStyleChanges();
});
document.getElementById("auto_hide_slider").addEventListener("input", (e) => {
    document.getElementById("val-auto-hide").textContent = `${e.target.value}s`;
    autoSyncStyleChanges();
});
document.getElementById("noise_gate_slider").addEventListener("input", (e) => {
    document.getElementById("val-noise-gate").textContent = `${e.target.value} dB`;
});
document.getElementById("vad_slider").addEventListener("input", (e) => {
    document.getElementById("val-vad").textContent = e.target.value;
});

function toggleEngineFields(engine) {
    document.getElementById("google-stt-fields").style.display = engine === "google_stt" ? "block" : "none";
    document.getElementById("gemini-live-fields").style.display = engine === "gemini_live" ? "block" : "none";
    document.getElementById("whisper-fields").style.display = engine === "local_whisper" ? "block" : "none";
    document.getElementById("vosk-fields").style.display = (engine === "vosk" || engine === "local_vosk") ? "block" : "none";
    document.getElementById("moonshine-fields").style.display = (engine === "moonshine" || engine === "local_moonshine") ? "block" : "none";
    const bwFields = document.getElementById("bandwidth-fields");
    if (bwFields) bwFields.style.display = engine === "bandwidth" ? "block" : "none";
}

document.getElementById("engine_select").addEventListener("change", (e) => {
    toggleEngineFields(e.target.value);
});

// Faster-Whisper Hardware Preset Handlers
function updateWhisperStats() {
    const modelEl = document.getElementById("whisper_model");
    const deviceEl = document.getElementById("whisper_device");
    const computeEl = document.getElementById("whisper_compute");
    if (!modelEl || !deviceEl) return;
    const model = modelEl.value;
    const device = deviceEl.value;
    const compute = computeEl ? computeEl.value : "float16";

    let vram = "~1.8 GB";
    let latency = "~120ms";
    let headroom = "~4.2 GB Free (on 6GB GTX 1660)";

    if (model.includes("tiny")) {
        vram = "~500 MB";
        latency = "~40ms";
        headroom = "~5.5 GB Free";
    } else if (model === "base.en" || model === "base") {
        vram = "~900 MB";
        latency = "~70ms";
        headroom = "~5.1 GB Free";
    } else if (model.includes("distil-small")) {
        vram = "~1.2 GB";
        latency = "~90ms";
        headroom = "~4.8 GB Free";
    } else if (model.includes("small")) {
        vram = "~1.8 GB";
        latency = "~120ms";
        headroom = "~4.2 GB Free";
    } else if (model.includes("distil-medium")) {
        vram = "~2.2 GB";
        latency = "~160ms";
        headroom = "~3.8 GB Free";
    } else if (model.includes("medium")) {
        vram = "~3.8 GB";
        latency = "~280ms";
        headroom = "~2.2 GB Free";
    } else if (model.includes("large")) {
        vram = "~4.5 GB";
        latency = "~380ms";
        headroom = "~1.5 GB Free";
    }

    if (device === "cpu") {
        vram = "0 MB (CPU RAM: " + vram + ")";
        latency = "+50-150ms slower on CPU";
        headroom = "100% GPU free for Gaming/OBS";
    }

    const vramEl = document.getElementById("val-est-vram");
    const latEl = document.getElementById("val-est-latency");
    const headEl = document.getElementById("val-est-headroom");
    if (vramEl) vramEl.textContent = vram;
    if (latEl) latEl.textContent = latency;
    if (headEl) headEl.textContent = headroom;
}

document.getElementById("whisper_model").addEventListener("change", updateWhisperStats);
document.getElementById("whisper_device").addEventListener("change", updateWhisperStats);
document.getElementById("whisper_compute").addEventListener("change", updateWhisperStats);

// 1-Click Preset Buttons
document.getElementById("preset-gtx1660").addEventListener("click", () => {
    document.getElementById("whisper_model").value = "small.en";
    document.getElementById("whisper_device").value = "cuda";
    document.getElementById("whisper_compute").value = "float16";
    document.getElementById("whisper_beam").value = "1";
    updateWhisperStats();
});

document.getElementById("preset-ultrafast").addEventListener("click", () => {
    document.getElementById("whisper_model").value = "base.en";
    document.getElementById("whisper_device").value = "cuda";
    document.getElementById("whisper_compute").value = "float16";
    document.getElementById("whisper_beam").value = "1";
    updateWhisperStats();
});

document.getElementById("preset-maxacc").addEventListener("click", () => {
    document.getElementById("whisper_model").value = "distil-medium.en";
    document.getElementById("whisper_device").value = "cuda";
    document.getElementById("whisper_compute").value = "float16";
    document.getElementById("whisper_beam").value = "2";
    updateWhisperStats();
});

document.getElementById("preset-cpu").addEventListener("click", () => {
    document.getElementById("whisper_model").value = "base.en";
    document.getElementById("whisper_device").value = "cpu";
    document.getElementById("whisper_compute").value = "int8";
    document.getElementById("whisper_beam").value = "1";
    updateWhisperStats();
});

// Preview Text Button
document.getElementById("btn-test-preview").addEventListener("click", () => {
    const txt = document.getElementById("preview-test-input").value.trim();
    if (txt) {
        previewFinal.textContent = txt;
        previewInterim.textContent = "";
    }
});

// Custom Glossary & Vocabulary Management
async function loadVocabularyState() {
    try {
        const res = await fetch("/api/vocabulary");
        if (res.ok) {
            const data = await res.json();
            renderVocabularyTable(data.terms || {});
        }
    } catch (e) {
        console.error("Error loading vocabulary:", e);
    }
}

function renderVocabularyTable(terms) {
    const tbody = document.querySelector("#table-vocabulary tbody");
    if (!tbody) return;
    const keys = Object.keys(terms);

    if (keys.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color: var(--text-muted);">No custom glossary terms configured yet.</td></tr>';
    } else {
        tbody.innerHTML = keys.map(k => `
            <tr>
                <td><code>${escapeHtml(k)}</code></td>
                <td><strong style="color: #38BDF8;">${escapeHtml(terms[k])}</strong></td>
                <td><button class="btn-icon-del" data-del-vocab="${escapeHtml(k)}" title="Delete">🗑️</button></td>
            </tr>
        `).join("");

        tbody.querySelectorAll("[data-del-vocab]").forEach(btn => {
            btn.addEventListener("click", async () => {
                await fetch("/api/vocabulary/remove", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ original: btn.dataset.delVocab }),
                });
                await loadVocabularyState();
            });
        });
    }
}

const btnAddVocab = document.getElementById("btn-add-vocab");
if (btnAddVocab) {
    btnAddVocab.addEventListener("click", async () => {
        const orig = document.getElementById("new-vocab-orig").value.trim();
        const sub = document.getElementById("new-vocab-sub").value.trim();
        if (orig && sub) {
            await fetch("/api/vocabulary/set", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ original: orig, replacement: sub }),
            });
            document.getElementById("new-vocab-orig").value = "";
            document.getElementById("new-vocab-sub").value = "";
            await loadVocabularyState();
        }
    });
}

const btnRunVocabTest = document.getElementById("btn-run-vocab-test");
if (btnRunVocabTest) {
    btnRunVocabTest.addEventListener("click", async () => {
        const text = document.getElementById("vocab-test-text").value;
        const resultBox = document.getElementById("vocab-test-result");
        resultBox.textContent = "Testing...";

        try {
            const res = await fetch("/api/vocabulary/test", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text }),
            });
            if (res.ok) {
                const data = await res.json();
                const tag = data.was_modified ? "✨ [REPLACED]" : "✓ [UNCHANGED]";
                resultBox.innerHTML = `<strong>${tag}</strong><br/>${escapeHtml(data.modified)}`;
            }
        } catch (e) {
            resultBox.textContent = `Error: ${e}`;
        }
    });
}

// Filter CRUD Management
async function loadFilterState() {
    try {
        const res = await fetch("/api/filter/state");
        if (res.ok) {
            const data = await res.json();
            renderFilterTables(data);
        }
    } catch (e) {
        console.error("Error loading filter state:", e);
    }
}

function renderFilterTables(state) {
    // 1. Replacements Table
    const tbody = document.querySelector("#table-replacements tbody");
    const reps = state.custom_replacements || {};
    const repKeys = Object.keys(reps);

    if (repKeys.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color: var(--text-muted);">No custom substitutions configured.</td></tr>';
    } else {
        tbody.innerHTML = repKeys.map(k => `
            <tr>
                <td><code>${escapeHtml(k)}</code></td>
                <td><strong>${escapeHtml(reps[k])}</strong></td>
                <td><button class="btn-icon-del" data-del-rep="${escapeHtml(k)}" title="Delete">🗑️</button></td>
            </tr>
        `).join("");

        tbody.querySelectorAll("[data-del-rep]").forEach(btn => {
            btn.addEventListener("click", async () => {
                await fetch("/api/filter/replacements/remove", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ original: btn.dataset.delRep }),
                });
                await loadFilterState();
            });
        });
    }

    // 2. Blacklist Tags
    const blackTags = document.getElementById("blacklist-tags");
    const bl = state.custom_blacklist || [];
    blackTags.innerHTML = bl.map(t => `
        <span class="tag-item">
            ${escapeHtml(t)}
            <span class="tag-remove" data-del-bl="${escapeHtml(t)}">&times;</span>
        </span>
    `).join("");
    blackTags.querySelectorAll("[data-del-bl]").forEach(span => {
        span.addEventListener("click", async () => {
            await fetch("/api/filter/blacklist/remove", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ term: span.dataset.delBl }),
            });
            await loadFilterState();
        });
    });

    // 3. Whitelist Tags
    const whiteTags = document.getElementById("whitelist-tags");
    const wl = state.custom_whitelist || [];
    whiteTags.innerHTML = wl.map(t => `
        <span class="tag-item">
            ${escapeHtml(t)}
            <span class="tag-remove" data-del-wl="${escapeHtml(t)}">&times;</span>
        </span>
    `).join("");
    whiteTags.querySelectorAll("[data-del-wl]").forEach(span => {
        span.addEventListener("click", async () => {
            await fetch("/api/filter/whitelist/remove", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ term: span.dataset.delWl }),
            });
            await loadFilterState();
        });
    });
}

// Add Replacement Button
document.getElementById("btn-add-replacement").addEventListener("click", async () => {
    const orig = document.getElementById("new-rep-orig").value.trim();
    const sub = document.getElementById("new-rep-sub").value.trim();
    if (orig && sub) {
        await fetch("/api/filter/replacements/set", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ original: orig, replacement: sub }),
        });
        document.getElementById("new-rep-orig").value = "";
        document.getElementById("new-rep-sub").value = "";
        await loadFilterState();
    }
});

// Add Blacklist Word
document.getElementById("btn-add-blacklist").addEventListener("click", async () => {
    const term = document.getElementById("new-blacklist-term").value.trim();
    if (term) {
        await fetch("/api/filter/blacklist/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ term }),
        });
        document.getElementById("new-blacklist-term").value = "";
        await loadFilterState();
    }
});

// Add Whitelist Word
document.getElementById("btn-add-whitelist").addEventListener("click", async () => {
    const term = document.getElementById("new-whitelist-term").value.trim();
    if (term) {
        await fetch("/api/filter/whitelist/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ term }),
        });
        document.getElementById("new-whitelist-term").value = "";
        await loadFilterState();
    }
});

// Filter Sandbox Test
document.getElementById("btn-run-filter-test").addEventListener("click", async () => {
    const text = document.getElementById("filter-test-text").value;
    const resultBox = document.getElementById("filter-test-result");
    resultBox.textContent = "Testing...";

    try {
        const res = await fetch("/api/filter/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text }),
        });
        if (res.ok) {
            const data = await res.json();
            const tag = data.was_censored ? "🛡️ [FILTERED]" : "✓ [CLEAN]";
            resultBox.innerHTML = `<strong>${tag}</strong><br/>${escapeHtml(data.filtered || "(Dropped)")}`;
        }
    } catch (e) {
        resultBox.textContent = `Error: ${e}`;
    }
});

// Save Handlers
document.getElementById("btn-save-style").addEventListener("click", async () => {
    await saveConfigPayload(buildOverlayStylePayload(), "Visual settings saved successfully!");
});

document.getElementById("btn-save-filter").addEventListener("click", async () => {
    const payload = {
        censor: {
            enabled: document.getElementById("censor_enabled").checked,
            mode: document.getElementById("censor_mode").value,
            filter_standard_profanity: document.getElementById("filter_standard_profanity").checked,
            filter_church_blasphemy: document.getElementById("filter_church_blasphemy").checked,
            filter_crude_terms: document.getElementById("filter_crude_terms").checked,
        }
    };
    await saveConfigPayload(payload, "Filter categories saved successfully!");
});

document.getElementById("btn-save-translation").addEventListener("click", async () => {
    const payload = {
        translation: {
            enabled: document.getElementById("translation_enabled").checked,
            target_language: document.getElementById("translation_target").value,
            display_mode: document.getElementById("translation_mode").value,
        }
    };
    await saveConfigPayload(payload, "Translation settings saved successfully!");
});

document.getElementById("btn-save-audio").addEventListener("click", async () => {
    const engineSelect = document.getElementById("engine_select");
    const selectedEngine = engineSelect.value;
    const selectedEngineName = engineSelect.options[engineSelect.selectedIndex].text;

    showToast(`🔄 Switching recognition engine to ${selectedEngineName}...`, "info", 4000);

    const payload = {
        general: {
            engine: selectedEngine,
            language: document.getElementById("language_select").value,
            auto_capitalization: document.getElementById("auto_capitalization").checked,
            auto_punctuation: document.getElementById("auto_punctuation").checked,
            church_mode: document.getElementById("church_mode").checked,
        },
        audio: {
            device_name_filter: document.getElementById("audio_device_select").value,
            noise_gate_db: parseFloat(document.getElementById("noise_gate_slider").value),
            vad_threshold: parseFloat(document.getElementById("vad_slider").value),
        },
        bandwidth: {
            api_key: document.getElementById("bandwidth_api_key") ? document.getElementById("bandwidth_api_key").value.trim() : "",
        },
        google_stt: {
            credentials_path: document.getElementById("google_creds_path").value.trim(),
        },
        gemini_live: {
            api_key: document.getElementById("gemini_api_key").value.trim(),
            model: document.getElementById("gemini_model").value,
            custom_vocabulary: document.getElementById("gemini_custom_vocab").value.split(",").map(s => s.trim()).filter(Boolean),
            smart_transcription: document.getElementById("gemini_smart_transcription").checked,
        },
        local_whisper: {
            model_size: document.getElementById("whisper_model").value,
            device: document.getElementById("whisper_device").value,
            compute_type: document.getElementById("whisper_compute").value,
            beam_size: parseInt(document.getElementById("whisper_beam").value) || 1,
        },
        vosk: {
            model_name: document.getElementById("vosk_model").value,
            model_path: document.getElementById("vosk_model_path").value.trim(),
        },
        moonshine: {
            model_name: document.getElementById("moonshine_model").value,
        }
    };

    // Toast the new engine name when the backend broadcasts engine_changed
    // (the async hot-swap takes longer than any fixed timer). Fallback timeout
    // clears the flag if the engine didn't actually change.
    pendingEngineSwitchToast = true;
    setTimeout(() => { pendingEngineSwitchToast = false; }, 15000);

    await saveConfigPayload(payload, null);
    await refreshEngineStatus();
});

document.getElementById("btn-save-twitch").addEventListener("click", async () => {
    const payload = {
        twitch: {
            enabled: document.getElementById("twitch_enabled").checked,
            channel: document.getElementById("twitch_channel").value.trim(),
            bot_username: document.getElementById("twitch_username").value.trim(),
            oauth_token: document.getElementById("twitch_oauth").value.trim(),
        }
    };
    await saveConfigPayload(payload, "Twitch bot settings saved!");
});

// Projector Automation Handlers
function toggleProjectorSourceField() {
    const type = document.getElementById("obs_projector_type").value;
    const group = document.getElementById("group-projector-source-name");
    if (group) group.style.display = (type === "source") ? "block" : "none";
}

document.getElementById("obs_projector_type").addEventListener("change", toggleProjectorSourceField);

document.getElementById("btn-save-projector").addEventListener("click", async () => {
    const payload = {
        obs: {
            auto_open_projector: document.getElementById("obs_auto_projector").checked,
            projector_type: document.getElementById("obs_projector_type").value,
            projector_monitor_index: parseInt(document.getElementById("obs_projector_monitor").value) || 1,
            projector_source_name: document.getElementById("obs_projector_source_name").value.trim(),
        }
    };
    await saveConfigPayload(payload, "Projector & Display automation settings saved!");
});

document.getElementById("btn-quick-open-projector").addEventListener("click", async () => {
    const monitorIndex = parseInt(document.getElementById("quick_projector_monitor").value) || 1;
    const statusMsg = document.getElementById("projector-status-msg");
    statusMsg.style.display = "block";
    statusMsg.style.color = "#38BDF8";
    statusMsg.textContent = `Opening Preview Projector on Monitor ${monitorIndex}...`;

    try {
        const res = await fetch("/api/obs/projector/open", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                mix_type: "preview",
                monitor_index: monitorIndex,
            }),
        });
        const data = await res.json();
        if (res.ok && data.status === "success") {
            statusMsg.style.color = "#10B981";
            statusMsg.textContent = `✅ Successfully projected to Screen (Monitor ${monitorIndex})!`;
        } else {
            statusMsg.style.color = "#EF4444";
            statusMsg.textContent = `❌ ${data.message || "Failed to open projector. Check OBS WebSocket."}`;
        }
    } catch (e) {
        statusMsg.style.color = "#EF4444";
        statusMsg.textContent = `❌ Error: ${e.message}`;
    }
});

async function loadObsMonitors() {
    try {
        const res = await fetch("/api/obs/monitors");
        if (res.ok) {
            const data = await res.json();
            if (data.monitors && data.monitors.length > 0) {
                const populate = (id) => {
                    const sel = document.getElementById(id);
                    if (!sel) return;
                    const prev = sel.value;
                    sel.innerHTML = "";
                    data.monitors.forEach(m => {
                        const opt = document.createElement("option");
                        opt.value = m.monitorIndex;
                        const name = m.monitorName || `Display ${m.monitorIndex}`;
                        opt.textContent = `Monitor ${m.monitorIndex}: ${name} (${m.monitorWidth}x${m.monitorHeight})`;
                        sel.appendChild(opt);
                    });
                    if (prev !== undefined) sel.value = prev;
                };
                populate("quick_projector_monitor");
                populate("obs_projector_monitor");
            }
        }
    } catch (e) {
        console.debug("Could not query OBS monitors:", e);
    }
}

async function saveConfigPayload(payload, successMsg) {
    try {
        const res = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (res.ok) {
            if (successMsg) {
                showToast(successMsg, "success");
            }
            await loadConfig();
        } else {
            showToast("⚠️ Error saving configuration.", "error");
        }
    } catch (e) {
        showToast(`❌ Error saving configuration: ${e.message || e}`, "error");
    }
}

// Toast Notification System
function showToast(message, type = "success", durationMs = 4000) {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateY(20px)";
        setTimeout(() => toast.remove(), 350);
    }, durationMs);
}

// Persistent Start / Reopen Screen Emergency Button (Header)
const btnReopenProjector = document.getElementById("btn-reopen-projector");
if (btnReopenProjector) {
    btnReopenProjector.addEventListener("click", async () => {
        btnReopenProjector.disabled = true;
        btnReopenProjector.textContent = "🔄 Reopening...";
        showToast("🔄 Connecting to OBS and sending preview to Screen...", "info", 3000);

        try {
            // 1. Determine target monitor index (default to 1 / LONTIUM)
            const targetMon = parseInt(document.getElementById("obs_projector_monitor")?.value || document.getElementById("quick_projector_monitor")?.value) || 1;

            // 2. Trigger OBS Projector Open
            const projRes = await fetch("/api/obs/projector/open", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    mix_type: "preview",
                    monitor_index: targetMon,
                }),
            });
            const projData = await projRes.json();

            // 3. Ensure live captions are also active
            if (!isRunning) {
                await fetch("/api/control/start", { method: "POST" });
                isRunning = true;
                updateStatusUI();
            }

            if (projRes.ok && projData.status === "success") {
                showToast(`✅ LONTIUM Screen (Monitor ${targetMon}) & Captions Active!`, "success", 5000);
            } else {
                showToast(`⚠️ Projector sent. (If screen is dark, verify OBS WebSocket is enabled on port 4455).`, "info", 5000);
            }
        } catch (e) {
            showToast(`❌ Error triggering screen: ${e.message}`, "error", 5000);
        } finally {
            setTimeout(() => {
                btnReopenProjector.disabled = false;
                btnReopenProjector.textContent = "📺 Start / Reopen Screen";
            }, 1000);
        }
    });
}

// Toggle Start / Stop Engine
btnToggleEngine.addEventListener("click", async () => {
    const endpoint = isRunning ? "/api/control/stop" : "/api/control/start";
    btnToggleEngine.disabled = true;
    try {
        const res = await fetch(endpoint, { method: "POST" });
        if (res.ok) {
            isRunning = !isRunning;
            updateStatusUI();
            showToast(isRunning ? "▶ Captioning started." : "⏹ Captioning stopped.", "info", 2500);
        } else {
            showToast("⚠️ Could not toggle captioning (server rejected the request).", "error");
        }
    } catch (e) {
        console.error("Error toggling engine:", e);
        showToast("❌ Could not reach the server to toggle captioning.", "error");
    } finally {
        btnToggleEngine.disabled = false;
    }
});

function updateStatusUI() {
    if (isRunning) {
        statusPill.querySelector(".status-dot").className = "status-dot online";
        statusText.textContent = "Active";
        btnToggleEngine.textContent = "⏹ Stop Captions";
        btnToggleEngine.className = "btn btn-danger";
    } else {
        statusPill.querySelector(".status-dot").className = "status-dot offline";
        statusText.textContent = "Stopped";
        btnToggleEngine.textContent = "▶ Start Captions";
        btnToggleEngine.className = "btn btn-primary";
    }
}

// Live Active Engine & Status Manager
let currentModelDetail = "";
let currentEngineName = "";
let currentInstanceId = null;

// Restart Application Handler
const btnRestartApp = document.getElementById("btn-restart-app");
const btnRestartRetry = document.getElementById("btn-restart-retry");

if (btnRestartRetry) {
    btnRestartRetry.addEventListener("click", () => {
        window.location.reload();
    });
}

let restartMonitorActive = false;

// Shows the restart modal and polls /api/status until a NEW backend instance
// answers (instance-ID change or fresh uptime). Used by the restart button and
// by the server_restarting broadcast (so restarts triggered elsewhere — a
// second dashboard tab, a Stream Deck — also show progress here).
function beginRestartMonitor(targetOldInstanceId) {
    if (restartMonitorActive) return;
    restartMonitorActive = true;

    const modal = document.getElementById("restart-modal");
    const modalTitle = document.getElementById("restart-modal-title");
    const modalSub = document.getElementById("restart-modal-sub");
    const modalIcon = document.getElementById("restart-modal-icon");
    const progressBar = document.getElementById("restart-progress-bar");
    const stepStatus = document.getElementById("restart-step-status");
    const manualActions = document.getElementById("restart-manual-actions");

    modal.style.display = "flex";
    modalTitle.textContent = "Restarting VoxStream...";
    modalSub.textContent = "Re-initializing speech recognition engines, audio capture, and web services...";
    modalIcon.textContent = "🔄";
    modalIcon.style.animation = "spin 1.5s linear infinite";
    if (progressBar) progressBar.style.width = "25%";
    if (stepStatus) stepStatus.textContent = "Step 1/3: Sending restart signal to backend...";
    if (manualActions) manualActions.style.display = "none";

    // Cosmetic step advancement
    setTimeout(() => {
        if (progressBar) progressBar.style.width = "60%";
        if (stepStatus) stepStatus.textContent = "Step 2/3: Reloading audio streams and AI models...";
    }, 800);
    setTimeout(() => {
        if (progressBar) progressBar.style.width = "85%";
        if (stepStatus) stepStatus.textContent = "Step 3/3: Reconnecting to new server instance...";
    }, 1600);

    let attempts = 0;
    const maxAttempts = 35;
    const pollInterval = setInterval(async () => {
        attempts++;
        try {
            const res = await fetch("/api/status", { cache: "no-store" });
            if (res.ok) {
                const data = await res.json();
                // Only a genuinely new instance counts as success — no
                // attempt-count escape hatch that fakes a completed restart.
                const isNewInstance = targetOldInstanceId ? (data.instance_id !== targetOldInstanceId) : false;
                const isFreshUptime = data.uptime_seconds !== undefined && data.uptime_seconds < 8.0;

                if (isNewInstance || isFreshUptime) {
                    clearInterval(pollInterval);
                    restartMonitorActive = false;
                    modalTitle.textContent = "✅ VoxStream Ready!";
                    modalSub.textContent = `Connected to active instance (${data.engine_name || 'Speech Engine'}). Reloading interface...`;
                    modalIcon.textContent = "✨";
                    modalIcon.style.animation = "none";
                    if (progressBar) {
                        progressBar.style.width = "100%";
                        progressBar.style.background = "#10B981";
                    }
                    if (stepStatus) stepStatus.textContent = "Restart complete!";
                    setTimeout(() => {
                        window.location.reload();
                    }, 500);
                    return;
                }
            }
            // Non-OK responses (429/500 while the server cycles) fall through
            // to the attempts check below instead of polling forever.
        } catch (e) {
            // Network failure while the server cycles — keep polling
        }
        if (attempts >= maxAttempts) {
            clearInterval(pollInterval);
            restartMonitorActive = false;
            modalTitle.textContent = "⚠️ Restart Taking Longer";
            modalSub.textContent = "The server is taking longer than expected to reload. You can refresh manually.";
            modalIcon.textContent = "⏳";
            modalIcon.style.animation = "none";
            if (manualActions) manualActions.style.display = "block";
        }
    }, 600);
}

if (btnRestartApp) {
    btnRestartApp.addEventListener("click", async () => {
        if (!confirm("Are you sure you want to restart the VoxStream backend?")) {
            return;
        }

        const targetOldInstanceId = currentInstanceId;
        beginRestartMonitor(targetOldInstanceId);

        try {
            await fetch("/api/control/restart", { method: "POST" });
        } catch (e) {
            console.debug("Restart request sent (server cycling)");
        }
    });
}

// Shutdown Application Handler
const btnShutdownApp = document.getElementById("btn-shutdown-app");
if (btnShutdownApp) {
    btnShutdownApp.addEventListener("click", async () => {
        if (!confirm("Are you sure you want to completely shut down VoxStream? The web server and live captions will stop.")) {
            return;
        }

        const modal = document.getElementById("restart-modal");
        const modalTitle = document.getElementById("restart-modal-title");
        const modalSub = document.getElementById("restart-modal-sub");
        const modalIcon = document.getElementById("restart-modal-icon");
        const progressBar = document.getElementById("restart-progress-bar");
        const stepStatus = document.getElementById("restart-step-status");

        modal.style.display = "flex";
        modalTitle.textContent = "VoxStream Stopped";
        modalSub.textContent = "The application has been shut down cleanly. You can close this browser tab.";
        modalIcon.textContent = "🛑";
        modalIcon.style.animation = "none";
        if (progressBar) progressBar.style.width = "100%";
        if (stepStatus) stepStatus.textContent = "All services stopped.";

        try {
            await fetch("/api/control/shutdown", { method: "POST" });
        } catch (e) {
            console.debug("Shutdown request sent");
        }
    });
}

async function refreshEngineStatus() {
    try {
        const res = await fetch("/api/status", { cache: "no-store" });
        if (res.ok) {
            const data = await res.json();
            if (data.instance_id) {
                currentInstanceId = data.instance_id;
            }
            isRunning = !!data.is_running;
            updateStatusUI();

            currentEngineName = data.engine_name || data.engine || "Unknown";
            currentModelDetail = data.model_detail || currentEngineName;

            // Update top header badge
            const badgeText = document.getElementById("active-engine-badge-text");
            if (badgeText) {
                badgeText.textContent = `Active: ${currentEngineName}`;
            }

            // Update hero card in Audio & Engine tab
            const heroName = document.getElementById("hero-active-model-name");
            const heroDesc = document.getElementById("hero-active-model-desc");
            const heroBadge = document.getElementById("hero-active-badge");

            if (heroName) {
                heroName.textContent = currentEngineName;
            }
            if (heroDesc) {
                heroDesc.textContent = `${currentModelDetail} • Input: ${data.audio_device || 'Default Mic'}`;
            }
            if (heroBadge) {
                if (data.is_switching_engine) {
                    heroBadge.style.background = "rgba(245, 158, 11, 0.2)";
                    heroBadge.style.color = "#F59E0B";
                    heroBadge.style.borderColor = "rgba(245, 158, 11, 0.4)";
                    heroBadge.innerHTML = `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#F59E0B;"></span> SWITCHING...`;
                } else {
                    heroBadge.style.background = "rgba(16, 185, 129, 0.2)";
                    heroBadge.style.color = "#10B981";
                    heroBadge.style.borderColor = "rgba(16, 185, 129, 0.4)";
                    heroBadge.innerHTML = `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#10B981;"></span> LIVE IN MEMORY`;
                }
            }
        }
    } catch (e) {
        console.debug("Status poll error:", e);
    }
}

// WebSockets
let pendingEngineSwitchToast = false;

let controlReconnectAttempts = 0;
function connectControlWs() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/api/control/ws${apiKeyQuerySuffix()}`;
    controlWs = new WebSocket(wsUrl);

    controlWs.onopen = () => { controlReconnectAttempts = 0; };
    controlWs.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === "vu_meter") {
                // 0 dB (full scale) is falsy — must not collapse to -100
                const db = (typeof msg.level_db === "number") ? msg.level_db : -100;
                const pct = Math.max(0, Math.min(100, ((db + 60) / 60) * 100));
                vuBar.style.width = `${pct}%`;
            } else if (msg.type === "engine_changed") {
                if (pendingEngineSwitchToast) {
                    pendingEngineSwitchToast = false;
                    showToast(`✅ Active recognition engine: ${msg.engine_name || msg.engine || "ready"}`, "success", 4000);
                }
                refreshEngineStatus();
            } else if (msg.type === "config_updated") {
                refreshEngineStatus();
            } else if (msg.type === "server_restarting") {
                // Restart triggered elsewhere (another tab, API, Stream Deck)
                beginRestartMonitor(msg.instance_id || currentInstanceId);
            }
        } catch (e) {}
    };

    controlWs.onclose = () => setTimeout(connectControlWs, Math.min(10000, (++controlReconnectAttempts) * 3000));
}

let captionReconnectAttempts = 0;
function connectCaptionWs() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    captionWs = new WebSocket(wsUrl);

    captionWs.onopen = () => { captionReconnectAttempts = 0; };
    captionWs.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === "snapshot") {
                // Show the most recent line from the replayed history, if any
                const lines = data.lines || [];
                const last = lines[lines.length - 1];
                if (last && last.text) previewFinal.textContent = last.text;
                return;
            }
            if (data.is_final) {
                let displayText = data.text;
                if (data.translated_text) {
                    displayText = `${data.text} (${data.translated_text})`;
                }
                previewFinal.textContent = displayText;
                previewInterim.textContent = "";
                appendTranscriptItem(data);
            } else {
                previewInterim.textContent = data.text;
            }
        } catch (e) {}
    };

    captionWs.onclose = () => setTimeout(connectCaptionWs, Math.min(10000, (++captionReconnectAttempts) * 3000));
}

// Transcript History
async function loadTranscriptHistory() {
    const search = document.getElementById("transcript-search").value;
    try {
        const res = await fetch(`/api/transcript/history?search=${encodeURIComponent(search)}`);
        if (res.ok) {
            const data = await res.json();
            renderTranscriptList(data.history);
        }
    } catch (e) {}
}

// Shared markup for one transcript row. Server history entries provide
// {relative_time, text, is_censored}; live caption events provide {text, is_censored}.
function transcriptItemHtml(timeLabel, entry) {
    return `
        <span class="transcript-time">[${escapeHtml(timeLabel)}]</span>
        <span class="transcript-text">${escapeHtml(entry.text)}</span>
        ${entry.is_censored ? '<span title="This line was filtered" style="margin-left: 6px;">🛡️</span>' : ''}
    `;
}

function renderTranscriptList(entries) {
    const list = document.getElementById("transcript-list");
    if (!entries || entries.length === 0) {
        list.innerHTML = '<div class="empty-state">No transcript lines found.</div>';
        return;
    }

    list.innerHTML = entries.map(e => `
        <div class="transcript-item">${transcriptItemHtml(e.relative_time || "", e)}</div>
    `).join("");
}

const MAX_LIVE_TRANSCRIPT_ITEMS = 200;

function appendTranscriptItem(entry) {
    const list = document.getElementById("transcript-list");
    const empty = list.querySelector(".empty-state");
    if (empty) empty.remove();

    const div = document.createElement("div");
    div.className = "transcript-item";
    div.innerHTML = transcriptItemHtml("Just now", entry);
    list.prepend(div);

    // Cap DOM growth: a multi-hour service would otherwise accumulate
    // thousands of nodes in a dock that stays open all day.
    while (list.children.length > MAX_LIVE_TRANSCRIPT_ITEMS) {
        list.removeChild(list.lastChild);
    }
}

document.getElementById("transcript-search").addEventListener("input", () => {
    loadTranscriptHistory();
});

document.getElementById("btn-clear-transcript").addEventListener("click", async () => {
    if (confirm("Clear transcript history?")) {
        await fetch("/api/transcript/clear", { method: "POST" });
        loadTranscriptHistory();
    }
});

function escapeHtml(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Initialize on page load
window.addEventListener("DOMContentLoaded", async () => {
    await loadConfig();
    await loadThemes();
    await loadAudioDevices();
    await loadObsMonitors();
    await loadVocabularyState();
    await loadFilterState();
    await refreshEngineStatus();
    connectControlWs();
    connectCaptionWs();

    // Periodic status poll (every 4s)
    setInterval(refreshEngineStatus, 4000);
});
