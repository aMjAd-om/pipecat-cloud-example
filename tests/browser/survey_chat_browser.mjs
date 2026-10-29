import { chromium } from "../../frontend/voice/node_modules/playwright/index.mjs";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";

const python = process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python";
const server = spawn(python, ["tests/browser/serve_survey_chat.py"], {cwd: process.cwd(), stdio: ["ignore", "pipe", "pipe"]});
let errors = ""; server.stderr.on("data", data => { errors += data; });
const url = `http://127.0.0.1:${process.env.CHAT_TEST_PORT || "17862"}`;
let browser;
try {
  let ready = false;
  for (let n = 0; n < 150; n++) {
    try { if ((await fetch(`${url}/api/settings`)).ok) { ready = true; break; } } catch {}
    if (server.exitCode !== null) throw new Error(errors);
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  assert(ready, "Chat fixture failed to start");
  browser = await chromium.launch({headless: true,
    ...(process.env.BROWSER_PATH ? {executablePath: process.env.BROWSER_PATH} : process.platform === "win32"
      ? {executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"} : {})});
  const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
  const pageErrors = []; page.on("pageerror", error => pageErrors.push(error.message));
  await page.goto(url);
  await page.getByText("Ready for customer reply", {exact: true}).waitFor();
  assert.match(await page.locator(".bubble").first().textContent(), /هل تسمح/);
  async function send(text) {
    await page.getByLabel("Customer reply", {exact: true}).fill(text);
    await page.getByRole("button", {name: "Send reply", exact: true}).click();
    await page.waitForFunction(() => !document.getElementById("send").disabled);
  }
  // Measure the visible interval in the page, excluding Playwright scheduling.
  await page.evaluate(() => {
    window.__reminderTiming = {};
    const observer = new MutationObserver(records => {
      for (const record of records) for (const node of record.addedNodes) {
        if (!(node instanceof Element) || !node.matches(".message.agent")) continue;
        const text = node.querySelector(".bubble")?.textContent || "";
        if (text === "من شركة داتامايننج.") window.__reminderTiming.reply = performance.now();
        if (text.startsWith("نرجع لسؤالنا، ")) {
          window.__reminderTiming.reminder = performance.now();
          observer.disconnect();
        }
      }
    });
    observer.observe(document.getElementById("messages"), {childList: true});
  });
  await send("من أي شركة قلت لي؟");
  assert.equal(await page.locator(".message.agent .bubble").nth(1).textContent(), "من شركة داتامايننج.");
  await page.waitForFunction(() => [...document.querySelectorAll(".message.agent .bubble")]
    .some(bubble => bubble.textContent.startsWith("نرجع لسؤالنا، ")));
  const visiblePause = await page.evaluate(() =>
    window.__reminderTiming.reminder - window.__reminderTiming.reply);
  assert(visiblePause >= 950, "Question reminder must wait one second");
  assert.match(await page.locator(".message.agent .bubble").last().textContent(), /هل تسمح/);
  await send("من أي شركة قلت لي؟");
  await send("نعم");
  await new Promise(resolve => setTimeout(resolve, 1100));
  assert.equal(await page.locator(".message.agent .bubble").evaluateAll(bubbles =>
    bubbles.filter(bubble => bubble.textContent.startsWith("نرجع لسؤالنا، ")).length), 1,
    "New customer reply must cancel the older reminder");
  assert.match(await page.locator("#answers").textContent(), /consent/);
  assert.match(await page.locator("#question").textContent(), /بشكل عام/);
  await send("عندي أربعة أطفال");
  assert.match(await page.locator("#question").textContent(), /بشكل عام/);
  await send("أربعة");
  assert.match(await page.locator("#answers").textContent(), /overall_rating/);
  assert.match(await page.locator("#question").textContent(), /السبب/);
  await send("وقف");
  assert.equal(await page.locator("#connection").textContent(), "Survey paused");
  await send("كمل");
  await send("<img src=x onerror=alert('unsafe')>");
  assert.equal(await page.locator("#messages img").count(), 0);
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", {name: "Export chat"}).click();
  const artifact = await downloaded;
  const data = JSON.parse(await readFile(await artifact.path(), "utf8"));
  assert.equal(data.state.answers.overall_rating, 4);
  assert.equal(data.records.length, 8);
  if (process.env.CHAT_TEST_SCREENSHOT)
    await page.screenshot({path: process.env.CHAT_TEST_SCREENSHOT, fullPage: true});
  await page.getByRole("button", {name: "New conversation"}).click();
  await page.getByText("Ready for customer reply", {exact: true}).waitFor();
  assert.equal(await page.locator("#messages .bubble").count(), 1);
  assert.match(await page.locator("#answers").textContent(), /No answers/);
  await send("نعم");
  await send("أربعة");
  await send("كانت الخدمة سريعة");
  await send("أربعة");
  assert.match(await page.locator("#question").textContent(), /الموظفين/);
  await send("ما تعاملت مع الموظفين");
  assert.match(await page.locator("#state").textContent(), /awaiting skip confirmation/);
  await send("نعم");
  assert.match(await page.locator("#question").textContent(), /صعوبات/);
  assert.doesNotMatch(await page.locator("#answers").textContent(), /staff_experience/);
  assert.deepEqual(pageErrors, []);
  console.log("PASS contextual company reply, delayed reminder, reminder cancellation, consent, ratings, pause/resume, safe rendering, export, reset and confirmed skip");
} finally {
  if (browser) await browser.close();
  server.kill();
}
