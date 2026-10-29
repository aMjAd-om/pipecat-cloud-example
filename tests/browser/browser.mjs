// Automated Chromium-family smoke test with synthetic microphone input.
// No claim of physical microphone quality, acoustic barge-in, or device audibility.
import { chromium } from "../../frontend/voice/node_modules/playwright/index.mjs";
import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";
const base = process.env.TEST_URL || "http://localhost:7860";
const browser = await chromium.launch({
  executablePath: process.env.BROWSER_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  headless: true,
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required"],
});
const context = await browser.newContext({permissions: ["microphone"]});
if (process.env.TEST_DEBUG) {
  context.on("request", request => {
    if (request.url().includes("/api/")) console.log("REQUEST", request.method(), new URL(request.url()).pathname.replace(/sessions\/[^/]+/, "sessions/[id]"));
  });
  context.on("response", response => {
    if (response.url().includes("/api/")) console.log("RESPONSE", response.status(), new URL(response.url()).pathname.replace(/sessions\/[^/]+/, "sessions/[id]"));
  });
}
await context.addInitScript(() => {
  window.__pcs = [];
  const NativePeer = window.RTCPeerConnection;
  window.RTCPeerConnection = class extends NativePeer {
    constructor(...args) { super(...args); window.__pcs.push(this); }
  };
  const nativeGet = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    const permission = await nativeGet(constraints);
    permission.getTracks().forEach(t => t.stop());
    const audio = new AudioContext({sampleRate: 48000});
    const destination = audio.createMediaStreamDestination();
    const silence = audio.createConstantSource();
    silence.offset.value = 0;
    silence.connect(destination);
    silence.start();
    await audio.resume();
    window.__syntheticMic = {audio, destination};
    return destination.stream;
  };
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
page.on("response", response => {
  if (response.url().includes("/api/") && response.status() >= 400)
    errors.push(`HTTP ${response.status()} ${new URL(response.url()).pathname}`);
});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitAudio(target) {
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    const received = await target.evaluate(async () => {
    for (const pc of window.__pcs) {
      if (pc.connectionState !== "connected") continue;
      for (const value of (await pc.getStats()).values()) {
        if (value.type === "inbound-rtp" && value.kind === "audio" && value.totalAudioEnergy > 0.001) return true;
      }
    }
    return false;
    });
    if (received) return;
    await sleep(200);
  }
  throw new Error("Timed out waiting for decoded non-silent bot audio");
}
try {
  for (let i=0; i<60; i++) {
    try { if ((await fetch(`${base}/api/health`)).ok) break; } catch {}
    await sleep(500);
  }
  await page.goto(base);
  await page.getByRole("button", {name: "Start conversation"}).click();
  await page.waitForFunction(() => document.getElementById("status").textContent.startsWith("Connected —"), null, {timeout: 45000});
  console.log("PASS browser connected:", await page.locator("#timing").innerText());
  await waitAudio(page);
  console.log("PASS nonzero remote audio decoded in browser");
  await page.getByRole("button", {name: "Mute microphone", exact: true}).click();
  assert.equal(await page.evaluate(() => window.__syntheticMic.destination.stream.getAudioTracks()[0].enabled), false);
  await page.getByRole("button", {name: "Unmute microphone", exact: true}).click();
  assert.equal(await page.evaluate(() => window.__syntheticMic.destination.stream.getAudioTracks()[0].enabled), true);
  console.log("PASS microphone mute/unmute");
  if (process.env.TEST_SILENCE_SECONDS && process.env.TEST_SERVER_LOG) {
    await sleep(8000); // Let the initial greeting finish first.
    const count = async () => ((await readFile(process.env.TEST_SERVER_LOG, "utf8")).match(/metric=llm_total/g) || []).length;
    const before = await count();
    await sleep(Number(process.env.TEST_SILENCE_SECONDS) * 1000);
    assert.equal(await count(), before);
    console.log(`PASS ${process.env.TEST_SILENCE_SECONDS} s silence created no new LLM completion`);
  }
  if (process.env.TEST_WAV) {
    const encoded = (await readFile(process.env.TEST_WAV)).toString("base64");
    for (let turn = 0; turn < 2; turn++) {
      await page.evaluate(async encoded => {
        const {audio, destination} = window.__syntheticMic;
        const data = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
        const buffer = await audio.decodeAudioData(data.buffer);
        const source = audio.createBufferSource();
        source.buffer = buffer;
        source.connect(destination);
        source.start();
        await new Promise(resolve => { source.onended = resolve; });
      }, encoded);
      await sleep(5000);
    }
    console.log("Injected two synthetic speech turns; verify final STT/LLM/TTS events in server logs");
  }
  const second = await context.newPage();
  await second.goto(base);
  await second.getByRole("button", {name: "Start conversation"}).click();
  await second.waitForFunction(() => document.getElementById("status").textContent.startsWith("Connected —"), null, {timeout: 45000});
  await waitAudio(second);
  assert.equal((await (await page.request.get(`${base}/api/health`)).json()).sessions, 2);
  console.log("PASS two independent browser peer sessions");
  await second.getByRole("button", {name: "Stop", exact: true}).click();
  await second.getByRole("status").filter({hasText: "Disconnected"}).waitFor();
  await page.getByRole("button", {name: "Stop", exact: true}).click();
  await page.getByRole("status").filter({hasText: "Disconnected"}).waitFor();
  for (let i=0; i<30; i++) {
    if ((await (await page.request.get(`${base}/api/health`)).json()).sessions === 0) break;
    await sleep(200);
  }
  assert.equal((await (await page.request.get(`${base}/api/health`)).json()).sessions, 0);
  console.log("PASS stop clears both server sessions");
  await page.getByRole("button", {name: "Start conversation"}).click();
  await page.waitForFunction(() => document.getElementById("status").textContent.startsWith("Connected —"), null, {timeout: 45000});
  await waitAudio(page);
  await page.getByRole("button", {name: "Stop", exact: true}).click();
  await page.getByRole("status").filter({hasText: "Disconnected"}).waitFor();
  assert.deepEqual(errors, []);
  console.log("PASS reconnect after Stop, no browser script/API errors");
} finally {
  await browser.close();
}
