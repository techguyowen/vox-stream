const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

const wait = ms => new Promise(r => setTimeout(r, ms));

async function capture() {
    console.log("🚀 Launching Headless Chrome to capture updated GitHub screenshots...");
    const browser = await puppeteer.launch({
        executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        headless: 'new',
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--hide-scrollbars'
        ]
    });

    const page = await browser.newPage();
    await page.setViewport({ width: 1360, height: 860, deviceScaleFactor: 2 });

    const screenshotsDir = path.resolve(__dirname, '../docs/screenshots');
    if (!fs.existsSync(screenshotsDir)) {
        fs.mkdirSync(screenshotsDir, { recursive: true });
    }

    // 1. Dashboard Main Overview
    console.log("📸 1. Capturing Dashboard Overview (dashboard.png)...");
    await page.goto("http://127.0.0.1:8765/dashboard", { waitUntil: 'networkidle2' });
    await wait(2000);
    // Inject sample transcript and WPM stats
    await page.evaluate(() => {
        const liveWpm = document.getElementById("header-wpm-val");
        if (liveWpm) liveWpm.textContent = "138 WPM";
        const badge = document.getElementById("header-pace-badge");
        if (badge) {
            badge.textContent = "Optimal Pace";
            badge.style.background = "rgba(16, 185, 129, 0.2)";
            badge.style.color = "#10B981";
        }
    });
    await page.screenshot({ path: path.join(screenshotsDir, 'dashboard.png'), fullPage: false });

    // 2. Audience Display Configuration Tab
    console.log("📸 2. Capturing Audience Display Tab (dashboard_display.png)...");
    await page.evaluate(() => {
        const displayTabBtn = document.querySelector('[data-tab="display"]') || 
                              Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Audience'));
        if (displayTabBtn) displayTabBtn.click();
    });
    await wait(1500);
    await page.screenshot({ path: path.join(screenshotsDir, 'dashboard_display.png'), fullPage: false });

    // 3. Audio & Engine Tab with Church Sermon Leaderboard
    console.log("📸 3. Capturing Audio & Engine Tab with Sermon Leaderboard (dashboard_engine.png)...");
    await page.evaluate(() => {
        const audioTabBtn = document.querySelector('[data-tab="audio"]') || 
                            Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Audio'));
        if (audioTabBtn) audioTabBtn.click();
    });
    await wait(1500);
    await page.screenshot({ path: path.join(screenshotsDir, 'dashboard_engine.png'), fullPage: false });

    // 4. Scripture Studio Dashboard Tab
    console.log("📸 4. Capturing Scripture Studio Tab (scripture_studio.png)...");
    await page.evaluate(async () => {
        const scriptTabBtn = document.querySelector('[data-tab="bible"]') || 
                             Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Scripture'));
        if (scriptTabBtn) scriptTabBtn.click();
        const input = document.getElementById("bible_search_input");
        if (input) {
            input.value = "John 3:16";
            const btn = document.getElementById("btn-bible-search");
            if (btn) btn.click();
        }
    });
    await wait(1500);
    await page.screenshot({ path: path.join(screenshotsDir, 'scripture_studio.png'), fullPage: false });

    // 5. Transcripts Tab with WPM Analytics Card
    console.log("📸 5. Capturing Transcripts & WPM Analytics (wpm_analytics.png)...");
    await page.evaluate(() => {
        const transcriptTabBtn = document.querySelector('[data-tab="transcript"]') || 
                                 Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Transcript'));
        if (transcriptTabBtn) transcriptTabBtn.click();
    });
    await wait(1500);
    await page.screenshot({ path: path.join(screenshotsDir, 'wpm_analytics.png'), fullPage: false });

    // 6. Settings Modal with Modules
    console.log("📸 6. Capturing Features Settings Modal (features_settings_modal.png)...");
    await page.evaluate(() => {
        const btnOpen = document.getElementById("btn-open-features-modal");
        if (btnOpen) btnOpen.click();
    });
    await wait(1000);
    await page.screenshot({ path: path.join(screenshotsDir, 'features_settings_modal.png'), fullPage: false });
    await page.evaluate(() => {
        const btnClose = document.getElementById("btn-close-features-modal");
        if (btnClose) btnClose.click();
    });
    await wait(500);

    // 7. Live Read-Along Display (stage_monitor.png)
    console.log("📸 7. Capturing Live Read-Along Display (stage_monitor.png)...");
    await page.goto("http://127.0.0.1:8765/display", { waitUntil: 'networkidle2' });
    await wait(2000);
    // Inject sample sermon read-along sentence and scripture prompter
    await page.evaluate(() => {
        const sentenceEl = document.getElementById("captionSentence");
        if (sentenceEl) {
            sentenceEl.innerHTML = `“For God so loved the world that He gave His only begotten Son, that whosoever believeth in Him should not perish, but have everlasting life.”`;
        }
        const promptCard = document.getElementById("scripturePrompterCard");
        const promptRef = document.getElementById("prompterVerseRef");
        const promptText = document.getElementById("prompterVerseText");
        if (promptCard && promptRef && promptText) {
            promptRef.textContent = "📖 John 3:16 (Berean Standard Bible)";
            promptText.textContent = "For God so loved the world that He gave His one and only Son, that everyone who believes in Him shall not perish but have eternal life.";
            promptCard.style.display = "block";
        }
    });
    await wait(1000);
    await page.screenshot({ path: path.join(screenshotsDir, 'stage_monitor.png'), fullPage: false });

    // 8. Visual Aid / A11y Mode Display (stage_monitor_a11y.png)
    console.log("📸 8. Capturing Visual Aid Display (stage_monitor_a11y.png)...");
    await page.evaluate(() => {
        const chkA11y = document.getElementById("chkDisplayA11y");
        if (chkA11y) {
            chkA11y.checked = true;
            chkA11y.dispatchEvent(new Event('change'));
        }
        const chkBionic = document.getElementById("chkDisplayBionic");
        if (chkBionic) {
            chkBionic.checked = true;
            chkBionic.dispatchEvent(new Event('change'));
        }
    });
    await wait(1000);
    await page.screenshot({ path: path.join(screenshotsDir, 'stage_monitor_a11y.png'), fullPage: false });

    // 9. OBS Stream Overlay (stream_overlay.png)
    console.log("📸 9. Capturing OBS Transparent Stream Overlay (stream_overlay.png)...");
    await page.goto("http://127.0.0.1:8765/", { waitUntil: 'networkidle2' });
    await wait(1500);
    await page.evaluate(() => {
        const box = document.getElementById("caption-box") || document.querySelector(".caption-container") || document.getElementById("caption");
        if (box) {
            box.style.display = "block";
            box.innerHTML = `<span style="color: #FFFFFF; font-weight: 700; font-size: 36px; text-shadow: 2px 2px 5px rgba(0,0,0,0.9);">“Peace I leave with you; my peace I give to you.”</span> <span style="color: #90CAF9; font-size: 32px;">— John 14:27</span>`;
        }
        document.body.style.backgroundColor = "transparent";
    });
    await wait(1000);
    await page.screenshot({ path: path.join(screenshotsDir, 'stream_overlay.png'), omitBackground: true });

    // 10. Tablet View (iPad 768px)
    console.log("📸 10. Capturing Tablet Views...");
    await page.setViewport({ width: 768, height: 1024, deviceScaleFactor: 2 });
    await page.goto("http://127.0.0.1:8765/dashboard", { waitUntil: 'networkidle2' });
    await wait(1500);
    await page.screenshot({ path: path.join(screenshotsDir, 'dashboard_ipad_768.png'), fullPage: false });

    await page.goto("http://127.0.0.1:8765/display", { waitUntil: 'networkidle2' });
    await wait(1500);
    await page.screenshot({ path: path.join(screenshotsDir, 'display_ipad_768.png'), fullPage: false });

    // 11. Mobile View (iPhone 390px)
    console.log("📸 11. Capturing Mobile Views...");
    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
    await page.goto("http://127.0.0.1:8765/display", { waitUntil: 'networkidle2' });
    await wait(1500);
    await page.screenshot({ path: path.join(screenshotsDir, 'display_iphone_390.png'), fullPage: false });

    await browser.close();
    console.log("🎉 All updated GitHub screenshots successfully captured!");
}

capture().catch(err => {
    console.error("❌ Screenshot capture error:", err);
    process.exit(1);
});
