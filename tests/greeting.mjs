// Real-browser greeting check with a silent synthetic microphone.
import { chromium } from "../frontend/node_modules/playwright/index.mjs";

const browser = await chromium.launch({
  executablePath: process.env.BROWSER_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  headless: true,
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required"],
});
try {
  const context = await browser.newContext({permissions: ["microphone"]});
  await context.addInitScript(() => {
    window.__pcs = [];
    const NativePeer = window.RTCPeerConnection;
    window.RTCPeerConnection = class extends NativePeer {
      constructor(...args) { super(...args); window.__pcs.push(this); }
    };
    const nativeGet = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      const permission = await nativeGet(constraints);
      permission.getTracks().forEach(t => t.stop());
      const audio = new AudioContext();
      const destination = audio.createMediaStreamDestination();
      const silence = audio.createConstantSource();
      silence.offset.value = 0;
      silence.connect(destination);
      silence.start();
      await audio.resume();
      window.__audio = audio;
      return destination.stream;
    };
  });
  const page = await context.newPage();
  await page.goto(process.env.TEST_URL || "http://localhost:7860");
  await page.getByRole("button", {name: "Start conversation"}).click();
  await page.waitForFunction(() => document.getElementById("status").textContent.startsWith("Connected"),
    null, {timeout: 45000});
  await page.waitForFunction(async () => {
    for (const pc of window.__pcs) {
      for (const value of (await pc.getStats()).values()) {
        if (value.type === "inbound-rtp" && value.kind === "audio" && value.totalAudioEnergy > .001)
          return true;
      }
    }
    return false;
  }, null, {timeout: 45000});
  console.log("PASS browser ready and decoded non-silent greeting audio");
} finally {
  await browser.close();
}
