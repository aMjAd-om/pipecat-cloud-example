"use strict";
const $ = id => document.getElementById(id);
let token = "", session = null, busy = false, restarting = false, generation = 0;
const history = [];

async function api(path, {method = "GET", body} = {}) {
  const response = await fetch(path, {method, cache: "no-store", headers: {
    "Content-Type": "application/json", "X-Survey-Chat-Token": token,
  }, ...(method === "GET" ? {} : {body: JSON.stringify(body ?? {})})});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The request was rejected. Check your reply and try again.");
  return result;
}

function message(who, text, meta = "") {
  const item = document.createElement("div");
  item.className = `message ${who}`;
  if (who === "notice") item.textContent = text;
  else {
    const name = document.createElement("div"); name.className = "speaker";
    name.textContent = who === "customer" ? "Customer" : "Agent";
    const bubble = document.createElement("div"); bubble.className = "bubble";
    bubble.dir = "auto"; bubble.textContent = text;
    item.append(name, bubble);
    if (meta) { const extra = document.createElement("div"); extra.className = "message-meta"; extra.textContent = meta; item.append(extra); }
  }
  $("messages").append(item);
  while ($("messages").children.length > 250) $("messages").firstChild.remove();
  $("messages").scrollTop = $("messages").scrollHeight;
}

function controls() {
  const ended = session && (session.completed || session.cancelled);
  $("send").disabled = !session || busy;
  $("reply").disabled = !session;
  $("interrupt").disabled = !session || !busy;
  $("export").disabled = !session;
  $("connection").textContent = busy ? "Agent is thinking…" : ended ? (session.cancelled ? "Survey cancelled" : "Survey complete") : session?.paused ? "Survey paused" : session ? "Ready for customer reply" : "Connecting…";
}

function render(state) {
  if (session?.session_id === state.session_id && state.version < session.version) return;
  session = state;
  const q = state.current_question;
  $("question").textContent = q.text;
  $("state").textContent = `${q.id} · ${q.kind} · version ${state.version}` +
    (q.kind === "rating" ? ` · range ${q.minimum}–${q.maximum}` : "") +
    (q.choices.length ? ` · ${q.choices.join(" / ")}` : "") +
    (state.pending_confirmation ? ` · awaiting ${state.pending_confirmation.action} confirmation` : "");
  $("answers").replaceChildren();
  const entries = Object.entries(state.answers);
  if (!entries.length) $("answers").textContent = "No answers accepted yet.";
  for (const [id, value] of entries) {
    const row = document.createElement("div"); row.className = "answer";
    const key = document.createElement("div"); key.className = "answer-id"; key.textContent = id;
    const answer = document.createElement("div"); answer.dir = "auto"; answer.textContent = String(value);
    row.append(key, answer); $("answers").append(row);
  }
  controls();
}

async function restart() {
  if (restarting) return;
  restarting = true; $("restart").disabled = true;
  const mine = ++generation;
  const old = session; session = null; busy = false; controls(); $("error").textContent = "";
  try {
    if (old) await api(`/api/sessions/${old.session_id}`, {method: "DELETE"});
    const data = await api("/api/sessions", {method: "POST"});
    if (mine !== generation) return;
    history.length = 0; history.push({agent: data.spoken_response});
    $("messages").replaceChildren(); $("decision").textContent = "";
    $("result").textContent = "Send a reply to inspect the result.";
    render(data.state); message("agent", data.spoken_response); $("reply").value = ""; $("reply").focus();
  } catch (error) { if (mine === generation) $("error").textContent = error.message; }
  finally { restarting = false; $("restart").disabled = false; }
}

$("composer").addEventListener("submit", async event => {
  event.preventDefault();
  const text = $("reply").value.trim();
  if (!text || !session || busy) return;
  const mine = generation, before = session;
  busy = true; controls(); $("error").textContent = ""; $("reply").value = "";
  message("customer", text);
  try {
    const data = await api(`/api/sessions/${before.session_id}/turn`, {method: "POST", body: {
      text, turn_id: crypto.randomUUID(), expected_version: before.version,
    }});
    if (mine !== generation) return;
    history.push(data); if (history.length > 100) history.shift();
    if (data.spoken_response) message("agent", data.spoken_response, `${Math.round(data.elapsed_ms)} ms`);
    else message("notice", data.validation.reason === "stale" ? "Interrupted reply discarded; survey did not advance." : "Duplicate turn ignored.");
    $("decision").textContent = JSON.stringify(data.proposed_decision, null, 2);
    $("result").replaceChildren();
    const badge = document.createElement("span"); badge.className = `badge ${data.validation.accepted ? "" : "rejected"}`;
    badge.textContent = data.validation.committed ? "Answer accepted" : data.validation.accepted ? "Conversation only" : "Proposal rejected";
    const detail = document.createElement("div");
    detail.textContent = `${data.validation.reason} · ${Math.round(data.elapsed_ms)} ms · ${data.metrics.total_tokens ?? 0} tokens`;
    $("result").append(badge, detail); render(data.state);
  } catch (error) { if (mine === generation) { $("error").textContent = error.message; $("reply").value = text; } }
  finally { if (mine === generation) { busy = false; controls(); if (!$("reply").disabled) $("reply").focus(); } }
});

$("reply").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("composer").requestSubmit(); }
});
$("suggestions").addEventListener("click", event => {
  if (event.target.dataset.reply && !$("reply").disabled) { $("reply").value = event.target.dataset.reply; $("reply").focus(); }
});
$("restart").addEventListener("click", restart);
$("interrupt").addEventListener("click", async () => {
  const mine = generation, sid = session?.session_id;
  $("interrupt").disabled = true;
  try {
    const data = await api(`/api/sessions/${sid}/interrupt`, {method: "POST"});
    if (mine === generation) { render(data.state); message("notice", "Interruption sent. Pending decisions for the old state will be discarded."); }
  } catch (error) { if (mine === generation) $("error").textContent = error.message; }
});
$("export").addEventListener("click", async () => {
  const mine = generation, sid = session?.session_id;
  try {
    const data = await api(`/api/sessions/${sid}/export`);
    if (mine !== generation) return;
    const blob = new Blob([JSON.stringify({...data, conversation: history}, null, 2)], {type: "application/json"});
    const url = URL.createObjectURL(blob), anchor = document.createElement("a");
    anchor.href = url; anchor.download = "survey-chat.json"; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { $("error").textContent = error.message; }
});
window.addEventListener("pagehide", () => {
  if (session && token) fetch(`/api/sessions/${session.session_id}`, {method: "DELETE", keepalive: true,
    headers: {"Content-Type": "application/json", "X-Survey-Chat-Token": token}, body: "{}"}).catch(() => {});
});
(async () => {
  try {
    const config = await api("/api/settings"); token = config.token;
    $("configuration").textContent = `${config.model} · ${config.language === "ar" ? "Arabic" : "English"}`;
    await restart();
  } catch (error) { $("error").textContent = error.message; }
})();
