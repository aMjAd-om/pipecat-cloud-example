import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),"../..");
const python=process.env.TEST_PYTHON || path.join(root,".venv",process.platform==="win32"?"Scripts/python.exe":"bin/python");
const server=spawn(python,["-m","tests.browser.serve_dialer"],{cwd:root,stdio:["ignore","pipe","pipe"],windowsHide:true});
let logs="";server.stdout.on("data",data=>logs+=data);server.stderr.on("data",data=>logs+=data);
let browser;
try {
  const authorization=`Basic ${Buffer.from("operator:offline_operator_password_123").toString("base64")}`;
  let healthy=false;
  for(let i=0;i<180;i++){
    try{healthy=(await fetch("http://127.0.0.1:17860/api/settings",{headers:{authorization}})).ok;}catch{}
    if(healthy)break;
    if(server.exitCode!==null)throw new Error(`Fixture exited: ${logs}`);
    await new Promise(resolve=>setTimeout(resolve,300));
  }
  assert(healthy,`Fixture did not start: ${logs}`);
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({httpCredentials:{username:"operator",password:"offline_operator_password_123"},viewport:{width:1440,height:1000}});
  const page=await context.newPage();
  const errors=[];page.on("pageerror",error=>errors.push(error.message));
  await page.goto("http://127.0.0.1:17860");
  await page.waitForFunction(()=>!document.getElementById("call-button").disabled);
  assert.equal(await page.locator("audio,video").count(),0);
  await page.locator("#phone").fill("bad number");
  await page.locator("#call-button").click();
  await page.locator("#error").filter({hasText:"eight-digit"}).waitFor();
  await page.locator("#phone").fill("+968 12345678");
  await page.locator("#call-button").click();
  await page.locator(".message.agent").first().waitFor();
  await page.locator(".message.customer").first().waitFor();
  await page.waitForFunction(()=>document.querySelectorAll(".message").length===5);
  assert.equal(await page.locator(".message img").count(),0);
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  assert(await page.locator("#call-button").isDisabled());
  assert(await page.locator("#download").isEnabled());
  await mkdir(path.join(root,"build"),{recursive:true});
  await page.screenshot({path:path.join(root,"build/dialer-desktop.png"),fullPage:true});
  const downloadWait=page.waitForEvent("download");await page.locator("#download").click();
  const download=await downloadWait;assert(download.suggestedFilename().endsWith(".txt"));
  await page.reload();
  await page.waitForFunction(()=>document.querySelectorAll(".message").length===5);
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.screenshot({path:path.join(root,"build/dialer-mobile.png"),fullPage:true});
  await page.locator("#hangup-button").click();
  await page.waitForFunction(()=>document.getElementById("call-status").textContent==="Call cancelled");
  await page.waitForFunction(()=>!document.getElementById("call-button").disabled);
  await page.locator("#call-button").click();
  await page.waitForFunction(()=>!document.getElementById("hangup-button").hidden);
  await page.locator("#hangup-button").click();
  assert.deepEqual(errors,[]);
  console.log("PASS: dialer login, validation, call, live Arabic transcript, XSS escaping, download, refresh, mobile, hangup and repeat call.");
} finally {
  if(browser)await browser.close();
  server.kill();
}
