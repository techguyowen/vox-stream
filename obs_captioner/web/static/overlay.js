// OBS Live Captions WebSocket Overlay Client (Multi-Theme & Translation Support)

let config = {
    max_lines: 2,
    auto_hide_seconds: 4.0,
    animation_style: "word_pop",
    vertical_align: "bottom",
};

let hideTimer = null;
let fadeWipeTimer = null;
let finalLines = [];
let ws = null;
let controlWs = null;

const captionBox = document.getElementById("caption-box");
const finalLinesEl = document.getElementById("final-lines");
const interimLineEl = document.getElementById("interim-line");

function applyStyles(ov) {
    if (!ov) return;
    const root = document.documentElement;
    
    // Explicit null checks so empty-string values ("none", cleared styles) still apply
    const setVar = (name, val) => { if (val !== undefined && val !== null) root.style.setProperty(name, val); };
    setVar("--font-family", ov.font_family);
    setVar("--font-size", ov.font_size);
    setVar("--font-weight", ov.font_weight);
    setVar("--line-height", ov.line_height);
    setVar("--max-width", ov.max_width);
    setVar("--text-align", ov.text_align);
    setVar("--text-color", ov.text_color);
    setVar("--interim-color", ov.interim_color);
    setVar("--highlight-color", ov.highlight_color);
    setVar("--background-box-color", ov.background_box_color);
    setVar("--border-radius", ov.border_radius);
    setVar("--box-padding", ov.box_padding);
    setVar("--text-shadow", ov.text_shadow);
    setVar("--text-stroke", ov.text_stroke);

    if (ov.vertical_align === "top") {
        document.body.classList.add("align-top");
    } else {
        document.body.classList.remove("align-top");
    }

    if (ov.max_lines) config.max_lines = parseInt(ov.max_lines) || 2;
    if (ov.animation_style) config.animation_style = ov.animation_style;
    if (ov.auto_hide_seconds !== undefined) {
        const newHide = parseFloat(ov.auto_hide_seconds) || 0;
        if (newHide !== config.auto_hide_seconds) {
            config.auto_hide_seconds = newHide;
            // Re-arm an in-flight hide timer with the new duration
            if (hideTimer && !captionBox.classList.contains("hidden")) {
                showBox();
            }
        }
    }
}

async function loadConfig() {
    try {
        const res = await fetch("/api/config");
        if (res.ok) {
            const data = await res.json();
            if (data.overlay) {
                applyStyles(data.overlay);
            }
        }
    } catch (e) {
        console.warn("Could not load /api/config:", e);
    }
}

function clearTimers() {
    if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
    }
    // Cancel a pending post-fade wipe so speech resuming during the 400ms
    // fade window doesn't get erased.
    if (fadeWipeTimer) {
        clearTimeout(fadeWipeTimer);
        fadeWipeTimer = null;
    }
}

function showBox() {
    captionBox.classList.remove("hidden");
    clearTimers();
    if (config.auto_hide_seconds > 0) {
        hideTimer = setTimeout(() => {
            captionBox.classList.add("hidden");
            fadeWipeTimer = setTimeout(() => {
                finalLines = [];
                renderFinalLines(false);
                interimLineEl.innerHTML = "";
                fadeWipeTimer = null;
            }, 400);
        }, config.auto_hide_seconds * 1000);
    }
}

function hideBoxNow() {
    clearTimers();
    captionBox.classList.add("hidden");
    finalLines = [];
    renderFinalLines(false);
    interimLineEl.innerHTML = "";
}

function renderFinalLines(activeInterim = false) {
    const maxFinal = activeInterim ? Math.max(0, config.max_lines - 1) : config.max_lines;
    const linesToDisplay = finalLines.slice(Math.max(0, finalLines.length - maxFinal));

    finalLinesEl.innerHTML = linesToDisplay
        .map(item => {
            if (typeof item === "object" && item.translated) {
                return `
                    <div class="final-line-item">
                        <div class="primary-text">${escapeHtml(item.text)}</div>
                        <div class="translated-subtitle">${escapeHtml(item.translated)}</div>
                    </div>
                `;
            }
            return `<div class="final-line-item">${escapeHtml(typeof item === 'string' ? item : item.text)}</div>`;
        })
        .join("");
}

function renderInterim(text) {
    if (!text) {
        interimLineEl.innerHTML = "";
        return;
    }

    if (config.animation_style === "karaoke") {
        const words = text.split(" ");
        const lastWord = words.pop() || "";
        const prefix = words.join(" ");
        interimLineEl.innerHTML = `${escapeHtml(prefix)} <span class="anim-karaoke-highlight">${escapeHtml(lastWord)}</span>`;
    } else if (config.animation_style === "word_pop") {
        const words = text.split(" ");
        const lastWord = words.pop() || "";
        const prefix = words.join(" ");
        interimLineEl.innerHTML = `${escapeHtml(prefix)} <span class="anim-word-pop">${escapeHtml(lastWord)}</span>`;
    } else {
        interimLineEl.innerText = text;
    }
}

function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function handleCaption(data) {
    // Snapshot replay on (re)connect: reset stale local state, then adopt
    // the server's recent final lines.
    if (data.type === "snapshot") {
        finalLines = [];
        interimLineEl.innerHTML = "";
        for (const line of data.lines || []) {
            const t = (line.text || "").trim();
            if (t) finalLines.push({ text: t, translated: line.translated_text || null });
        }
        while (finalLines.length > config.max_lines) finalLines.shift();
        if (finalLines.length) {
            renderFinalLines(false);
            showBox();
        }
        return;
    }

    const text = (data.text || "").trim();
    const translated = data.translated_text || null;

    if (data.is_final) {
        if (text) {
            showBox();
            interimLineEl.innerHTML = "";
            finalLines.push({ text: text, translated: translated });
            while (finalLines.length > config.max_lines) {
                finalLines.shift();
            }
            renderFinalLines(false);
        } else {
            // Empty final = silence auto-clear signal → hide immediately
            // (never re-show an empty box)
            hideBoxNow();
        }
    } else {
        if (text) {
            showBox();
            renderFinalLines(true);
            renderInterim(text);
        } else {
            // Empty interim (e.g. a dropped sentence): clear the interim line only
            interimLineEl.innerHTML = "";
            renderFinalLines(false);
        }
    }
}

let captionReconnectAttempts = 0;
function connectCaptionWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const urlParams = new URLSearchParams(window.location.search);
    const lang = urlParams.get('lang') || 'en';
    const wsUrl = `${protocol}//${window.location.host}/ws?lang=${encodeURIComponent(lang)}`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => { captionReconnectAttempts = 0; };
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleCaption(data);
        } catch (e) {
            console.error("Error parsing caption event:", e);
        }
    };

    ws.onclose = () => setTimeout(connectCaptionWebSocket, Math.min(10000, (++captionReconnectAttempts) * 2000));
}

let controlReconnectAttempts = 0;
function connectControlWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/api/control/ws`;

    controlWs = new WebSocket(wsUrl);

    controlWs.onopen = () => { controlReconnectAttempts = 0; };
    controlWs.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === "config_updated" && msg.config && msg.config.overlay) {
                applyStyles(msg.config.overlay);
            }
        } catch (e) {}
    };

    controlWs.onclose = () => setTimeout(connectControlWebSocket, Math.min(10000, (++controlReconnectAttempts) * 3000));
}

window.addEventListener("DOMContentLoaded", async () => {
    await loadConfig();
    connectCaptionWebSocket();
    connectControlWebSocket();
});
