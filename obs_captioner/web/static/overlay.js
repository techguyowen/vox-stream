// OBS Live Captions WebSocket Overlay Client (Multi-Theme & Translation Support)

let config = {
    max_lines: 2,
    auto_hide_seconds: 4.0,
    animation_style: "word_pop",
    vertical_align: "bottom",
};

let hideTimer = null;
let finalLines = [];
let ws = null;
let controlWs = null;

const captionBox = document.getElementById("caption-box");
const finalLinesEl = document.getElementById("final-lines");
const interimLineEl = document.getElementById("interim-line");

function applyStyles(ov) {
    if (!ov) return;
    const root = document.documentElement;
    
    if (ov.font_family) root.style.setProperty("--font-family", ov.font_family);
    if (ov.font_size) root.style.setProperty("--font-size", ov.font_size);
    if (ov.font_weight) root.style.setProperty("--font-weight", ov.font_weight);
    if (ov.line_height) root.style.setProperty("--line-height", ov.line_height);
    if (ov.max_width) root.style.setProperty("--max-width", ov.max_width);
    if (ov.text_align) root.style.setProperty("--text-align", ov.text_align);
    if (ov.text_color) root.style.setProperty("--text-color", ov.text_color);
    if (ov.interim_color) root.style.setProperty("--interim-color", ov.interim_color);
    if (ov.highlight_color) root.style.setProperty("--highlight-color", ov.highlight_color);
    if (ov.background_box_color) root.style.setProperty("--background-box-color", ov.background_box_color);
    if (ov.border_radius) root.style.setProperty("--border-radius", ov.border_radius);
    if (ov.box_padding) root.style.setProperty("--box-padding", ov.box_padding);
    if (ov.text_shadow) root.style.setProperty("--text-shadow", ov.text_shadow);
    if (ov.text_stroke) root.style.setProperty("--text-stroke", ov.text_stroke);

    if (ov.vertical_align === "top") {
        document.body.classList.add("align-top");
    } else {
        document.body.classList.remove("align-top");
    }

    if (ov.max_lines) config.max_lines = parseInt(ov.max_lines) || 2;
    if (ov.auto_hide_seconds !== undefined) config.auto_hide_seconds = parseFloat(ov.auto_hide_seconds) || 0;
    if (ov.animation_style) config.animation_style = ov.animation_style;
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

function showBox() {
    captionBox.classList.remove("hidden");
    if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
    }
    if (config.auto_hide_seconds > 0) {
        hideTimer = setTimeout(() => {
            captionBox.classList.add("hidden");
            setTimeout(() => {
                finalLines = [];
                renderFinalLines();
                interimLineEl.innerText = "";
            }, 400);
        }, config.auto_hide_seconds * 1000);
    }
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
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function handleCaption(data) {
    showBox();
    const text = (data.text || "").trim();
    const translated = data.translated_text || null;

    if (data.is_final) {
        interimLineEl.innerHTML = "";
        if (text) {
            finalLines.push({ text: text, translated: translated });
            while (finalLines.length > config.max_lines) {
                finalLines.shift();
            }
            renderFinalLines(false);
        } else {
            // Empty final = silence auto-clear signal → hide box and clear lines
            finalLines = [];
            renderFinalLines(false);
        }
    } else {
        if (text) {
            renderFinalLines(true);
            renderInterim(text);
        } else {
            interimLineEl.innerHTML = "";
            renderFinalLines(false);
        }
    }
}

function connectCaptionWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleCaption(data);
        } catch (e) {
            console.error("Error parsing caption event:", e);
        }
    };

    ws.onclose = () => setTimeout(connectCaptionWebSocket, 2000);
}

function connectControlWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/api/control/ws`;

    controlWs = new WebSocket(wsUrl);

    controlWs.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === "config_updated" && msg.config && msg.config.overlay) {
                applyStyles(msg.config.overlay);
            }
        } catch (e) {}
    };

    controlWs.onclose = () => setTimeout(connectControlWebSocket, 3000);
}

window.addEventListener("DOMContentLoaded", async () => {
    await loadConfig();
    connectCaptionWebSocket();
    connectControlWebSocket();
});
