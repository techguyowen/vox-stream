
// ⏱️ Minimum On-Screen Caption Governor
let lastLineDisplayedAt = 0;
let deferredHideTimer = null;
let pendingFinalQueue = [];
let queueAdvanceTimer = null;

function processPendingQueue() {
    queueAdvanceTimer = null;
    if (pendingFinalQueue.length === 0) return;

    const minDisp = Math.max(0, parseFloat(config.min_display_seconds) || 0);

    // If box has reached max_lines, ensure oldest line has met min_display_seconds
    if (finalLines.length >= config.max_lines && finalLines.length > 0) {
        const oldest = finalLines[0];
        const age = (Date.now() - (oldest.displayedAt || 0)) / 1000;
        const wait = minDisp - age;
        if (wait > 0.05) {
            queueAdvanceTimer = setTimeout(processPendingQueue, Math.max(50, wait * 1000));
            return;
        }
        finalLines.shift();
    }

    const next = pendingFinalQueue.shift();
    next.displayedAt = Date.now();
    lastLineDisplayedAt = next.displayedAt;
    finalLines.push(next);
    while (finalLines.length > config.max_lines) {
        finalLines.shift();
    }

    renderFinalLines(false);
    interimLineEl.innerHTML = "";
    showBox();

    // If more items remain in queue, pace them smoothly
    if (pendingFinalQueue.length > 0) {
        const pace = pendingFinalQueue.length > 2 ? Math.min(minDisp, 1.2) : minDisp;
        queueAdvanceTimer = setTimeout(processPendingQueue, Math.max(50, pace * 1000));
    }
}


// Scripture Verse Card Management
let scriptureTimer = null;
const scriptureCard = document.getElementById("scripture-card");
const scriptureCitationEl = document.getElementById("scripture-citation");
const scriptureTextEl = document.getElementById("scripture-text");

function showScriptureVerse(data) {
    if (!scriptureCard || !scriptureCitationEl || !scriptureTextEl) return;
    if (scriptureTimer) clearTimeout(scriptureTimer);

    scriptureCitationEl.textContent = `📖 ${data.citation || ''} • ${data.version || 'BSB'}`;
    scriptureTextEl.textContent = `"${data.text || ''}"`;
    scriptureCard.classList.remove("hidden");

    const dur = (parseFloat(data.duration_seconds) || 14.0) * 1000;
    scriptureTimer = setTimeout(() => {
        dismissScriptureVerse();
    }, dur);
}

function dismissScriptureVerse() {
    if (scriptureTimer) clearTimeout(scriptureTimer);
    scriptureTimer = null;
    if (scriptureCard) {
        scriptureCard.classList.add("hidden");
    }
}

// OBS Live Captions WebSocket Overlay Client (Multi-Theme & Translation Support)

let config = {
    max_lines: 2,
    auto_hide_seconds: 4.0,
    min_display_seconds: 2.0,
    animation_style: "word_pop",
    vertical_align: "bottom",
    final_only: false,
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
    setVar("--letter-spacing", ov.letter_spacing || "normal");

    const italics = ov.use_italics ? "italic" : "normal";
    setVar("--font-style", italics);

    if (ov.high_contrast_outline) {
        setVar("--text-stroke", "3.5px #000000");
        setVar("--text-shadow", "0 0 8px #000000, 2px 2px 6px #000000");
    }

    if (ov.vertical_align === "top") {
        document.body.classList.add("align-top");
    } else {
        document.body.classList.remove("align-top");
    }

    if (ov.max_lines) config.max_lines = parseInt(ov.max_lines) || 2;
    if (ov.final_only !== undefined) {
        config.final_only = Boolean(ov.final_only);
    }
    // Allow URL query parameter ?final_only=1 or ?final_only=true to override
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has("final_only")) {
        const p = urlParams.get("final_only").toLowerCase();
        config.final_only = p === "1" || p === "true" || p === "yes";
    }

    if (ov.reduce_motion) {
        config.animation_style = "instant";
    } else if (ov.animation_style) {
        config.animation_style = ov.animation_style;
    }
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
    if (ov.min_display_seconds !== undefined) {
        config.min_display_seconds = Math.max(0, parseFloat(ov.min_display_seconds) || 0);
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
    if (deferredHideTimer) {
        clearTimeout(deferredHideTimer);
        deferredHideTimer = null;
    }
}

function showBox() {
    captionBox.classList.remove("hidden");
    clearTimers();
    const autoHide = parseFloat(config.auto_hide_seconds) || 0;
    const minDisp = parseFloat(config.min_display_seconds) || 0;
    const hideDelay = Math.max(autoHide, minDisp);

    if (hideDelay > 0) {
        hideTimer = setTimeout(() => {
            captionBox.classList.add("hidden");
            fadeWipeTimer = setTimeout(() => {
                finalLines = [];
                pendingFinalQueue = [];
                renderFinalLines(false);
                interimLineEl.innerHTML = "";
                fadeWipeTimer = null;
            }, 400);
        }, hideDelay * 1000);
    }
}

function hideBoxNow(force = false) {
    if (!force) {
        const minDisp = Math.max(0, parseFloat(config.min_display_seconds) || 0);
        const elapsed = (Date.now() - lastLineDisplayedAt) / 1000;
        const remaining = minDisp - elapsed;
        if (remaining > 0.05) {
            if (!deferredHideTimer) {
                deferredHideTimer = setTimeout(() => {
                    deferredHideTimer = null;
                    hideBoxNow(true);
                }, remaining * 1000);
            }
            return;
        }
    }

    clearTimers();
    if (queueAdvanceTimer) {
        clearTimeout(queueAdvanceTimer);
        queueAdvanceTimer = null;
    }
    pendingFinalQueue = [];
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
        pendingFinalQueue = [];
        interimLineEl.innerHTML = "";
        const now = Date.now();
        for (const line of data.lines || []) {
            const t = (line.text || "").trim();
            if (t) finalLines.push({ text: t, translated: line.translated_text || null, displayedAt: now });
        }
        while (finalLines.length > config.max_lines) finalLines.shift();
        if (finalLines.length) {
            lastLineDisplayedAt = now;
            renderFinalLines(false);
            showBox();
        }
        return;
    }

    const text = (data.text || "").trim();
    const translated = data.translated_text || null;

    if (data.is_final) {
        if (text) {
            if (deferredHideTimer) {
                clearTimeout(deferredHideTimer);
                deferredHideTimer = null;
            }

            const lineItem = { text: text, translated: translated, displayedAt: 0 };
            const minDisp = Math.max(0, parseFloat(config.min_display_seconds) || 0);

            // If box has space and no lines are waiting in queue, display immediately
            if (finalLines.length < config.max_lines && pendingFinalQueue.length === 0) {
                lineItem.displayedAt = Date.now();
                lastLineDisplayedAt = lineItem.displayedAt;
                finalLines.push(lineItem);
                interimLineEl.innerHTML = "";
                renderFinalLines(false);
                showBox();
            } else if (minDisp > 0) {
                // When box is full, check if oldest line has satisfied min_display_seconds
                const oldest = finalLines[0];
                const age = oldest ? (Date.now() - (oldest.displayedAt || 0)) / 1000 : 999;
                if (age >= minDisp && pendingFinalQueue.length === 0) {
                    finalLines.shift();
                    lineItem.displayedAt = Date.now();
                    lastLineDisplayedAt = lineItem.displayedAt;
                    finalLines.push(lineItem);
                    interimLineEl.innerHTML = "";
                    renderFinalLines(false);
                    showBox();
                } else {
                    pendingFinalQueue.push(lineItem);
                    if (pendingFinalQueue.length > 5) {
                        pendingFinalQueue.shift();
                    }
                    if (!queueAdvanceTimer) {
                        const wait = Math.max(0.05, minDisp - age);
                        queueAdvanceTimer = setTimeout(processPendingQueue, wait * 1000);
                    }
                }
            } else {
                // Instant shift when min_display_seconds == 0
                lineItem.displayedAt = Date.now();
                lastLineDisplayedAt = lineItem.displayedAt;
                finalLines.push(lineItem);
                while (finalLines.length > config.max_lines) {
                    finalLines.shift();
                }
                interimLineEl.innerHTML = "";
                renderFinalLines(false);
                showBox();
            }
        } else {
            // Empty final = silence auto-clear signal
            hideBoxNow(false);
        }
    } else {
        if (config.final_only) {
            // Final-Only Mode: do not display live in-progress speech
            return;
        }
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
            if (data.type === "scripture_verse") {
                showScriptureVerse(data);
                return;
            } else if (data.type === "scripture_dismiss") {
                dismissScriptureVerse();
                return;
            }
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
