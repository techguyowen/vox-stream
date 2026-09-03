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
    // Inject sample transcript and WPM stats if empty
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

    // 2. Audio & Engine Tab with Church Sermon Leaderboard
    console.log("📸 2. Capturing Audio & Engine Tab with Sermon Leaderboard (dashboard_engine.png)...");
    await page.evaluate(() => {
        const audioTabBtn = document.querySelector('[data-tab="tab-audio"]') || 
                            Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Audio'));
        if (audioTabBtn) audioTabBtn.click();
    });
    await wait(1500);
    await page.screenshot({ path: path.join(screenshotsDir, 'dashboard_engine.png'), fullPage: false });

    // 3. Transcripts Tab with WPM Analytics Card
    console.log("📸 3. Capturing Transcripts & WPM Analytics Card (wpm_analytics.png)...");
    await page.evaluate(() => {
        const transcriptTabBtn = document.querySelector('[data-tab="tab-transcript"]') || 
                                 Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.includes('Transcript'));
        if (transcriptTabBtn) transcriptTabBtn.click();
    });
    await wait(1500);
    await page.screenshot({ path: path.join(screenshotsDir, 'wpm_analytics.png'), fullPage: false });

    // 4. Live Read-Along Display (stage_monitor.png)
    console.log("📸 4. Capturing Live Read-Along Display (stage_monitor.png)...");
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
            promptRef.textContent = "📖 John 3:16 (King James Version)";
            promptText.textContent = "For God so loved the world, that he gave his only begotten Son, that whosoever believeth in him should not perish, but have everlasting life.";
            promptCard.style.display = "block";
        }
    });
    await wait(1000);
    await page.screenshot({ path: path.join(screenshotsDir, 'stage_monitor.png'), fullPage: false });

    // 5. Scripture Studio Dashboard Tab (scripture_studio.png)
    console.log("📸 5. Capturing Scripture Studio Dashboard Tab (scripture_studio.png)...");
    await page.goto("http://127.0.0.1:8765/dashboard", { waitUntil: 'networkidle2' });
    await wait(1500);
    await page.evaluate(async () => {
        const scriptTabBtn = document.querySelector('[data-tab="tab-scripture"]') || 
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

    // 6. OBS Stream Overlay (stream_overlay.png)
    console.log("📸 6. Capturing OBS Transparent Stream Overlay (stream_overlay.png)...");
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

    await browser.close();
    console.log("🎉 All updated GitHub screenshots successfully captured!");
}

capture().catch(err => {
    console.error("❌ Screenshot capture error:", err);
    process.exit(1);
});
