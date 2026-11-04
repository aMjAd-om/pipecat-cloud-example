import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const python = path.join(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const server = spawn(python, ["-m", "tests.browser.serve_survey_chat"], {cwd: root, stdio: ["ignore", "pipe", "pipe"], windowsHide: true});
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
    ...(process.env.BROWSER_PATH ? {executablePath: process.env.BROWSER_PATH} : {})});
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
  await send("من معي؟");
  assert.match(await page.locator(".message.agent .bubble").last().textContent(), /المساعد الذكي/);
  await send("ليش تتصل؟");
  assert.match(await page.locator(".message.agent .bubble").last().textContent(), /نعرف رأيك/);
  await send("من أي شركة قلت لي؟");
  assert.equal(await page.locator(".message.agent .bubble").last().textContent(), "من شركة داتامايننج.");
  await new Promise(resolve => setTimeout(resolve, 1100));
  assert.equal(await page.locator(".message.agent .bubble").count(), 4,
    "Each caller question must produce one answer and no automatic consent reminder");
  assert.match(await page.locator("#state").textContent(), /consent/);
  assert.match(await page.locator("#answers").textContent(), /No answers/);
  await send("نعم");
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
  assert.equal(data.records.length, 9);
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
  await page.selectOption("#agent-select", "bank_feedback");
  await page.selectOption("#customer-select", "demo-ahmed");
  await page.getByRole("button", {name: "New conversation"}).click();
  await page.getByText("Ready for customer reply", {exact: true}).waitFor();
  assert.match(await page.locator("#active-task").textContent(), /bank-nizwa.ar.*Demo data/);
  assert.match(await page.locator("#messages").textContent(), /بنك نزوى/);
  await send("نعم");
  await send("أربعة");
  assert.match(await page.locator("#answers").textContent(), /overall_rating/);
  await page.selectOption("#agent-select", "municipality_complaints");
  assert.equal(await page.locator("#customer-select option[value='demo-ahmed']").count(), 0);
  await page.selectOption("#customer-select", "demo-municipality");
  await page.getByRole("button", {name: "New conversation"}).click();
  await page.getByText("Ready for customer reply", {exact: true}).waitFor();
  assert.match(await page.locator("#active-task").textContent(), /municipality-follow-up.ar/);
  assert.match(await page.locator("#messages").textContent(), /بلاغ/);
  assert.match(await page.locator("#answers").textContent(), /No answers/);
  assert.deepEqual(pageErrors, []);
  console.log("PASS distinct identity/purpose/company replies without consent reminders, consent, ratings, pause/resume, safe rendering, export, reset, confirmed skip, agent/survey/customer selection and isolation");
} finally {
  if (browser) await browser.close();
  server.kill();
}
