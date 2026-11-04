"use strict";
const $ = (id) => document.getElementById(id);
const ended = new Set(["completed", "cancelled", "busy", "no_answer", "rejected", "failed"]);
const labels = {dialing:"Calling customer",ringing:"Phone is ringing",connecting:"Connecting agent",connected:"Conversation in progress",cancelling:"Ending call",completed:"Conversation ended",cancelled:"Call cancelled",busy:"Customer is busy",no_answer:"No answer",rejected:"Call declined",failed:"Call failed"};
let current = null, ready = false, submitting = false, stopping = false, version = -1, requestId = null;
let shown = new Set(), selected = false;
const storageKey = "call-desk-active";
function stored() { try { return sessionStorage.getItem(storageKey); } catch { return null; } }
function remember(id) { try { if(id) sessionStorage.setItem(storageKey,id); else sessionStorage.removeItem(storageKey); } catch {} }
function error(text="") { $("error").textContent = text; $("error").hidden = !text; }
async function api(path, options={}) {
  const response = await fetch(path, {credentials:"same-origin",cache:"no-store",...options,headers:{"Content-Type":"application/json",...options.headers},signal:AbortSignal.timeout(12000)});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "The server could not complete this request.");
  return data;
}
function controls() {
  const active = current && !ended.has(current.status);
  $("call-button").disabled = !ready || submitting || active;
  $("phone").disabled = submitting || active;
  $("hangup-button").hidden = !active;
  $("hangup-button").disabled = stopping || current?.status === "cancelling";
}
function render(call) {
  if(current?.id !== call.id) { $("transcript").replaceChildren(); shown = new Set(); version = -1; }
  current = call;
  if(!$("phone").value) $("phone").value=call.number;
  remember(ended.has(call.status) ? null : call.id);
  const active = !ended.has(call.status);
  $("call-status").textContent = labels[call.status] || call.status;
  $("status-detail").textContent = call.detail;
  $("status-dot").className = `dot ${active ? "active" : ""}`;
  $("active-number").hidden = false;
  $("active-number").textContent = `+968 ${call.number}`;
  $("live-badge").className = `live-badge ${call.status === "connected" ? "active" : ""}`;
  $("live-label").textContent = call.status === "connected" ? "LIVE" : active ? "CONNECTING" : "ENDED";
  if (call.version !== version) {
    const feed = $("transcript");
    const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 100;
    for (const message of call.messages) {
      if (shown.has(message.id)) continue;
      shown.add(message.id);
      const row = document.createElement("article"); row.className = `message ${message.role}`;
      const avatar = document.createElement("span"); avatar.className="avatar";avatar.textContent=message.role==="agent"?"AG":"CU";avatar.setAttribute("aria-hidden","true");
      const body=document.createElement("div");body.className="message-body";
      const meta=document.createElement("div");meta.className="message-meta";
      const name=document.createElement("strong");name.textContent=message.role==="agent"?"Agent":"Customer";
      const time=document.createElement("time");time.dateTime=message.at;time.textContent=new Date(message.at).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit"});
      const text=document.createElement("p");text.className="message-text";text.dir="auto";text.textContent=message.text;
      meta.append(name,time);body.append(meta,text);row.append(avatar,body);feed.append(row);
    }
    if(nearBottom) feed.scrollTop=feed.scrollHeight;
    version=call.version;
  }
  $("download").disabled = !call.messages.length;
  $("transcript-note").textContent = call.messages.length ? `${call.messages.length} messages · ${active?"Updating live":"Call finished"}` : active ? "Waiting for the conversation to begin" : "No conversation was captured";
  controls(); clock();
}
function clock() {
  const seconds = current?.answered_at ? Math.max(0, Math.floor(((current.ended_at ? Date.parse(current.ended_at) : Date.now())-Date.parse(current.answered_at))/1000)) : 0;
  $("duration").textContent=`${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(seconds%60).padStart(2,"0")}`;
}
function uuid() {
  // crypto.randomUUID is unavailable on some LAN HTTP origins.
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(b=>b.toString(16).padStart(2,"0")).join("");return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
$("phone").addEventListener("input",()=>{requestId=null;error();});
$("call-form").addEventListener("submit",async(event)=>{
  event.preventDefault(); if(submitting || !ready || (current&&!ended.has(current.status))) return;
  const number=$("phone").value.trim();
  const normalized=number.replace(/[ ()-]/g,"").replace(/^(\+968|00968)/,"");
  if(!/^[0-9]{8}$/.test(normalized)) {error("Enter an eight-digit Oman phone number.");$("phone").focus();return;}
  requestId=requestId||uuid();submitting=true;error();controls();
  try {
    const call=await api("/api/calls",{method:"POST",body:JSON.stringify({number,request_id:requestId})});
    render(call);selected=true;requestId=null;
  } catch(exc) {error(`${exc.message} If the request timed out, retrying the same number will check the original call.`);}
  finally{submitting=false;controls();}
});
$("hangup-button").addEventListener("click",async()=>{
  if(!current||stopping)return;stopping=true;error();controls();
  try{render(await api(`/api/calls/${current.id}`,{method:"DELETE"}));}catch(exc){error(exc.message);}finally{stopping=false;controls();}
});
$("download").addEventListener("click",()=>{
  if(!current)return;
  const text=[`Call to +968 ${current.number}`,`Started: ${current.created_at}`,`Status: ${current.status}`,"",...current.messages.map(m=>`${new Date(m.at).toLocaleTimeString()} ${m.role==="agent"?"Agent":"Customer"}: ${m.text}`)].join("\n");
  const url=URL.createObjectURL(new Blob([text],{type:"text/plain;charset=utf-8"}));const link=document.createElement("a");link.href=url;link.download=`conversation-${current.id}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
async function poll() {
  try {
    const settings=await api("/api/settings");ready=settings.ready;
    $("connection-label").textContent=settings.detail;
    $("connection-dot").className=`dot ${ready?"ready":"offline"}`;
    if(!current && !selected){
      const remembered=stored();
      if(remembered){try{render(await api(`/api/calls/${remembered}`));}catch{remember(null);}selected=true;}
      else if(settings.calls.length){render(settings.calls[0]);selected=true;}
    }
    if(current&&!ended.has(current.status)) render(await api(`/api/calls/${current.id}`));
  } catch(exc) {ready=false;$("connection-label").textContent="Server unavailable";$("connection-dot").className="dot offline";error(exc.message);}
  finally{controls();setTimeout(poll,current&&!ended.has(current.status)?700:2500);}
}
setInterval(clock,1000);poll();
