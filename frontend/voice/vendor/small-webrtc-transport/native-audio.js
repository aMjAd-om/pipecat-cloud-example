// Native browser audio capture for the vendored Pipecat transport. No cloud SDK.
export class NativeAudioMediaManager {
  constructor() {
    this.stream = null;
    this._micEnabled = true;
    this._callbacks = {};
  }
  setRTVIOptions(options) {
    this._callbacks = options.callbacks || {};
    this._micEnabled = options.enableMic !== false;
  }
  async initialize() {
    if (this.stream) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    this.enableMic(this._micEnabled);
    for (const track of this.stream.getAudioTracks()) {
      this._callbacks.onTrackStarted?.(track, { id: "local", local: true });
    }
  }
  async connect() { await this.initialize(); }
  async disconnect() {
    for (const track of this.stream?.getTracks() || []) track.stop();
    this.stream = null;
  }
  enableMic(enabled) {
    this._micEnabled = enabled;
    for (const track of this.stream?.getAudioTracks() || []) track.enabled = enabled;
  }
  enableCam() {}
  get isMicEnabled() { return this._micEnabled; }
  get isCamEnabled() { return false; }
  get isSharingScreen() { return false; }
  tracks() { return { local: { audio: this.stream?.getAudioTracks()[0] }, bot: {} }; }
  async devices(kind) {
    return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === kind);
  }
  getAllMics() { return this.devices("audioinput"); }
  getAllCams() { return this.devices("videoinput"); }
  getAllSpeakers() { return this.devices("audiooutput"); }
  get selectedMic() { return { deviceId: this.stream?.getAudioTracks()[0]?.getSettings().deviceId }; }
  get selectedCam() { return {}; }
  get selectedSpeaker() { return {}; }
  async updateMic(id) {
    await this.disconnect();
    this.stream = await navigator.mediaDevices.getUserMedia({audio: {deviceId: {exact: id}}});
    this.enableMic(this._micEnabled);
  }
  updateCam() {}
  updateSpeaker() {}
  enableScreenShare() {}
}
