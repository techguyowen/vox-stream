
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

    // If more items remain in queue, pace them smoothly, draining faster
    // under backlog so queued finals catch up instead of going stale.
    // Nothing is ever dropped from the queue.
    if (pendingFinalQueue.length > 0) {
        const backlog = pendingFinalQueue.length;
        const pace = backlog > 8 ? Math.min(minDisp, 0.35)
            : (backlog > 2 ? Math.min(minDisp, 1.2) : minDisp);
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

    // Check if scripture should appear on this general captions overlay.
    // By default, scripture is isolated exclusively to the dedicated /bible overlay.
    const urlParams = new URLSearchParams(window.location.search);
    let allow = false;
    if (urlParams.has("scripture")) {
        const p = urlParams.get("scripture").toLowerCase();
        allow = (p === "1" || p === "true" || p === "yes");
    } else if (data && data.show_on_stream_overlay !== undefined) {
        allow = Boolean(data.show_on_stream_overlay);
    } else if (config && config.show_bible_on_stream !== undefined) {
        allow = Boolean(config.show_bible_on_stream);
    }

    if (!allow) {
        // Scripture is disabled on the main caption overlay; keep subtitles clean.
        dismissScriptureVerse();
        return;
    }

    if (scriptureTimer) clearTimeout(scriptureTimer);

    document.body.classList.add("enable-stream-scripture");
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
    document.body.classList.remove("enable-stream-scripture");
}

// OBS Live Captions WebSocket Overlay Client (Multi-Theme & Translation Support)

let config = {
    max_lines: 2,
    auto_hide_seconds: 4.0,
    min_display_seconds: 2.0,
    animation_style: "word_pop",
    vertical_align: "bottom",
    final_only: false,
    dual_subtitle_color: "#FFD700",
    dual_subtitle_scale: 0.85,
    dual_subtitle_format: "clean",
    show_bible_on_stream: false,
};

let hideTimer = null;
let fadeWipeTimer = null;
let finalLines = [];
let ws = null;
let controlWs = null;
let lastCaptionSeq = 0;
let lastFinalUtterance = 0;
let currentInterimText = "";

function isStaleCaption(data) {
    if (typeof data.seq === "number" && data.seq > 0) {
        if (data.seq <= lastCaptionSeq) return true;
        lastCaptionSeq = data.seq;
    }
    if (!data.is_final && typeof data.utterance_id === "number" && data.utterance_id > 0
        && data.utterance_id <= lastFinalUtterance) {
        return true;
    }
    if (data.is_final && typeof data.utterance_id === "number" && data.utterance_id > 0) {
        lastFinalUtterance = Math.max(lastFinalUtterance, data.utterance_id);
    }
    return false;
}

function isInterimContinuation(oldText, newText) {
    if (!oldText || !newText) return false;
    const normalize = (s) => s.replace(/[^\w\s]/g, "").toLowerCase().trim().replace(/\s+/g, " ");
    const a = normalize(oldText);
    const b = normalize(newText);
    if (!a || !b) return false;
    if (a === b) return true;
    if (a.startsWith(b) || b.startsWith(a)) return true;
    const aWords = a.split(" ");
    const bWords = b.split(" ");
    const maxOverlap = Math.min(aWords.length, bWords.length);
    for (let n = maxOverlap; n >= 1; n--) {
        if (aWords.slice(-n).join(" ") === bWords.slice(0, n).join(" ")) return true;
    }
    if (aWords.length >= 2 && bWords.length >= 2
        && aWords[0] === bWords[0] && aWords[1] === bWords[1]) return true;
    const setA = new Set(aWords);
    const setB = new Set(bWords);
    let intersection = 0;
    for (const w of setA) {
        if (setB.has(w)) intersection++;
    }
    const union = new Set([...setA, ...setB]).size;
    if (union > 0 && intersection / union >= 0.4) return true;
    return false;
}

function promoteInterimToFinal(promotedText) {
    const trimmed = (promotedText || "").trim();
    if (!trimmed) return;
    if (finalLines.length > 0) {
        const last = finalLines[finalLines.length - 1];
        const lastText = (typeof last === "string" ? last : last.text || "").trim();
        if (lastText === trimmed) return;
    }
    const lineItem = {
        text: trimmed,
        translated: null,
        dual_color: config.dual_subtitle_color,
        dual_scale: config.dual_subtitle_scale,
        dual_format: config.dual_subtitle_format,
        displayedAt: Date.now(),
    };
    lastLineDisplayedAt = lineItem.displayedAt;
    finalLines.push(lineItem);
    if (finalLines.length > config.max_lines) finalLines.shift();
}

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
    setVar("--bottom-offset", (ov.bottom_offset_px !== undefined ? ov.bottom_offset_px : 40) + "px");
    setVar("--backdrop-blur", ov.backdrop_blur || "8px");
    setVar("--accent-color", ov.accent_color || "#38BDF8");

    const italics = ov.use_italics ? "italic" : "normal";
    setVar("--font-style", italics);

    if (ov.high_contrast_outline) {
        setVar("--text-stroke", "3.5px #000000");
        setVar("--text-shadow", "0 0 8px #000000, 2px 2px 6px #000000");
    }

    // Layout positioning classes
    document.body.classList.remove("layout-bar", "layout-boxless", "layout-chyron-left");
    if (ov.box_layout === "bar") {
        document.body.classList.add("layout-bar");
    } else if (ov.box_layout === "boxless") {
        document.body.classList.add("layout-boxless");
    } else if (ov.box_layout === "pill" && ov.text_align === "left") {
        document.body.classList.add("layout-chyron-left");
    }

    // Accent line divider classes
    if (captionBox) {
        captionBox.classList.remove("accent-top-divider", "accent-bottom-divider", "accent-left-marker");
        if (ov.accent_line === "top_divider") {
            captionBox.classList.add("accent-top-divider");
        } else if (ov.accent_line === "bottom_divider") {
            captionBox.classList.add("accent-bottom-divider");
        } else if (ov.accent_line === "left_marker") {
            captionBox.classList.add("accent-left-marker");
        }
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
            if (data.translation) {
                if (data.translation.dual_subtitle_color) config.dual_subtitle_color = data.translation.dual_subtitle_color;
                if (data.translation.dual_subtitle_scale) config.dual_subtitle_scale = parseFloat(data.translation.dual_subtitle_scale) || 0.85;
                if (data.translation.dual_subtitle_format) config.dual_subtitle_format = data.translation.dual_subtitle_format;
            }
            if (data.bible) {
                config.show_bible_on_stream = Boolean(data.bible.show_on_stream_overlay);
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
    currentInterimText = "";
    if (pendingFinalQueue.length > 0) {
        // Finals are still waiting to be shown: pump the queue now (which
        // re-arms the hide timer) instead of discarding them unshown.
        if (queueAdvanceTimer) {
            clearTimeout(queueAdvanceTimer);
            queueAdvanceTimer = null;
        }
        processPendingQueue();
        return;
    }
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
                const color = item.dual_color || config.dual_subtitle_color || "#FFD700";
                const scale = item.dual_scale || config.dual_subtitle_scale || 0.85;
                const fmt = item.dual_format || config.dual_subtitle_format || "clean";
                const transText = (fmt === "parentheses")
                    ? `(${escapeHtml(item.translated)})`
                    : escapeHtml(item.translated);
                return `
                    <div class="final-line-item">
                        <div class="primary-text">${escapeHtml(item.text)}</div>
                        <div class="translated-subtitle" style="color: ${color}; font-size: calc(var(--font-size) * ${scale});">${transText}</div>
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
    if (data.dual_color) config.dual_subtitle_color = data.dual_color;
    if (data.dual_scale) config.dual_subtitle_scale = parseFloat(data.dual_scale) || 0.85;
    if (data.dual_format) config.dual_subtitle_format = data.dual_format;

    // Snapshot replay on (re)connect: reset stale local state, then adopt
    // the server's recent final lines.
    if (data.type === "snapshot") {
        currentInterimText = "";
        finalLines = [];
        pendingFinalQueue = [];
        interimLineEl.innerHTML = "";
        // Adopt the snapshot's high-water marks wholesale: it is the first
        // message on a fresh connection, so after a server restart
        // (seq/utterance ids reset to 1) stale old marks would otherwise
        // discard every new caption.
        lastCaptionSeq = 0;
        lastFinalUtterance = 0;
        const now = Date.now();
        for (const line of data.lines || []) {
            if (line && typeof line.seq === "number" && line.seq > lastCaptionSeq) lastCaptionSeq = line.seq;
            if (line && typeof line.utterance_id === "number" && line.utterance_id > lastFinalUtterance) lastFinalUtterance = line.utterance_id;
            const t = (line.text || "").trim();
            if (t) finalLines.push({
                text: t,
                translated: line.translated_text || null,
                dual_color: line.dual_color || data.dual_color || config.dual_subtitle_color,
                dual_scale: line.dual_scale || data.dual_scale || config.dual_subtitle_scale,
                dual_format: line.dual_format || data.dual_format || config.dual_subtitle_format,
                displayedAt: now,
            });
        }
        while (finalLines.length > config.max_lines) finalLines.shift();
        if (finalLines.length) {
            lastLineDisplayedAt = now;
            renderFinalLines(false);
            showBox();
        }
        return;
    }

    if (isStaleCaption(data)) return;

    const text = (data.text || "").trim();
    const translated = data.translated_text || null;

    if (data.is_final) {
        currentInterimText = "";
        if (text) {
            if (deferredHideTimer) {
                clearTimeout(deferredHideTimer);
                deferredHideTimer = null;
            }

            if (finalLines.length > 0 && !data.replace_last) {
                const normalize = (s) => (s || "").replace(/[^\w\s]/g, "").toLowerCase().trim().replace(/\s+/g, " ");
                const last = finalLines[finalLines.length - 1];
                const lastNorm = normalize(typeof last === "string" ? last : last.text);
                if (lastNorm && lastNorm === normalize(text)) {
                    if (typeof last !== "string") {
                        last.text = text;
                        if (translated) last.translated = translated;
                    }
                    interimLineEl.innerHTML = "";
                    renderFinalLines(false);
                    showBox();
                    return;
                }
            }

            const lineItem = {
                text: text,
                translated: translated,
                dual_color: data.dual_color || config.dual_subtitle_color,
                dual_scale: data.dual_scale || config.dual_subtitle_scale,
                dual_format: data.dual_format || config.dual_subtitle_format,
                displayedAt: 0,
            };
            const minDisp = Math.max(0, parseFloat(config.min_display_seconds) || 0);

            // If replacing the last finalized line (e.g. clause or boundary stitch)
            if (data.replace_last && (finalLines.length > 0 || pendingFinalQueue.length > 0)) {
                if (pendingFinalQueue.length > 0) {
                    pendingFinalQueue[pendingFinalQueue.length - 1].text = text;
                    if (translated) {
                        pendingFinalQueue[pendingFinalQueue.length - 1].translated = translated;
                    }
                } else {
                    finalLines[finalLines.length - 1].text = text;
                    if (translated) {
                        finalLines[finalLines.length - 1].translated = translated;
                    }
                    finalLines[finalLines.length - 1].displayedAt = Date.now();
                }
                lastLineDisplayedAt = Date.now();
                interimLineEl.innerHTML = "";
                renderFinalLines(false);
                showBox();
                return;
            }

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
                    interimLineEl.innerHTML = "";
                    pendingFinalQueue.push(lineItem);
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
            currentInterimText = "";
            return;
        }
        if (text) {
            if (currentInterimText && currentInterimText.trim().split(/\s+/).length >= 3
                && !isInterimContinuation(currentInterimText, text)) {
                promoteInterimToFinal(currentInterimText);
            }
            currentInterimText = text;
            showBox();
            renderFinalLines(true);
            renderInterim(text);
        } else {
            currentInterimText = "";
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
    const wsUrl = `${protocol}//${window.location.host}/ws?lang=${encodeURIComponent(lang)}&role=caption`;

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
            if (msg.type === "config_updated" && msg.config) {
                if (msg.config.overlay) {
                    applyStyles(msg.config.overlay);
                }
                if (msg.config.bible) {
                    config.show_bible_on_stream = Boolean(msg.config.bible.show_on_stream_overlay);
                }
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
