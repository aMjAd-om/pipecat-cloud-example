// Pipecat 0.0.72 speaks RTVI 0.3; the matching client exports RTVIClient.
import { RTVIClient as PipecatClient, logger, LogLevel } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

logger.setLevel(LogLevel.ERROR);
const el = id => document.getElementById(id);
let client, transport, sessionId, started, busy = false, stopping = false;
let generation = 0;
function status(text) { el("status").textContent = text; }
function buttons(connected = false) {
  el("start").disabled = busy || connected;
  el("stop").disabled = !client && !busy;
  el("mute").disabled = !connected;
  document.body.dataset.connected = String(connected);
}
async function release() {
  if (stopping) return;
  stopping = true;
  generation++;
  const oldClient = client, oldId = sessionId;
  client = transport = sessionId = null;
  try { await oldClient?.disconnect(); } catch {}
  if (oldId) await fetch(`/api/sessions/${oldId}`, { method: "DELETE", keepalive: true }).catch(() => {});
  el("speaker").srcObject = null;
  busy = false;
  stopping = false;
  status("Disconnected");
  buttons();
}
el("start").addEventListener("click", async () => {
  if (busy || client) return;
  el("error").textContent = "";
  el("timing").textContent = "";
  el("mute").textContent = "Mute microphone";
  started = performance.now();
  busy = true;
  const attempt = ++generation;
  buttons();
  status("Requesting microphone and connecting…");
  try {
    if (!window.isSecureContext) throw new Error("Microphone access requires HTTPS or localhost.");
    const response = await fetch("/api/sessions", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Unable to start a session.");
    const session = await response.json();
    if (attempt !== generation) {
      await fetch(`/api/sessions/${session.id}`, {method: "DELETE"});
      return;
    }
    sessionId = session.id;
    transport = new SmallWebRTCTransport({iceServers: session.iceServers, waitForICEGathering: true});
    client = new PipecatClient({
      transport, enableMic: true, enableCam: false, timeout: 40000,
      // This SDK's auth phase is distinct from its SDP request; sessions are already created.
      customConnectHandler: async () => ({}),
      params: {baseUrl: location.origin, endpoints: {connect: `/api/sessions/${sessionId}/offer`}},
      callbacks: {
        onBotReady: () => {
          busy = false;
          status("Connected — speak now");
          el("timing").textContent = `Connection ready in ${((performance.now() - started)/1000).toFixed(2)} s`;
          buttons(true);
        },
        onDisconnected: () => { if (!stopping) void release(); },
        onError: () => { el("error").textContent = "A voice service or connection failed. Stop and try again."; },
        onTrackStarted: (track, participant) => {
          if (track.kind === "audio" && !participant?.local) {
            el("speaker").srcObject = new MediaStream([track]);
            el("speaker").play().catch(() => {
              el("error").textContent = "Audio playback was blocked. Click the page, then restart.";
            });
          }
        },
      },
    });
    buttons();
    // Catch denied permission outside the older client's internal connect promise.
    await client.initDevices();
    if (attempt !== generation) return;
    await client.connect();
  } catch (error) {
    if (attempt !== generation) return;
    el("error").textContent = error.name === "NotAllowedError"
      ? "Microphone permission was denied. Allow it in browser settings, then try again."
      : "Could not connect. Check microphone permission and the server/network configuration.";
    await release();
  }
});
el("stop").addEventListener("click", () => void release());
el("mute").addEventListener("click", () => {
  if (!client) return;
  const enabled = !client.isMicEnabled;
  client.enableMic(enabled);
  el("mute").textContent = enabled ? "Mute microphone" : "Unmute microphone";
  el("mute").setAttribute("aria-pressed", String(!enabled));
});
window.addEventListener("pagehide", () => { void release(); });
