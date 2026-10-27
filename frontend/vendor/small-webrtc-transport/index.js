// Adapted from @pipecat-ai/small-webrtc-transport 0.4.0 (BSD-2-Clause).
// See README.md for exact changes; WebRTC protocol remains upstream.
import {logger as $99wTV$logger, RTVIMessage as $99wTV$RTVIMessage, Transport as $99wTV$Transport} from "@pipecat-ai/client-js";
import {NativeAudioMediaManager} from "./native-audio.js";
class $b31644dc78dca54a$var$TrackStatusMessage {
    constructor(receiver_index, enabled){
        this.type = "trackStatus";
        this.receiver_index = receiver_index;
        this.enabled = enabled;
    }
}
const $b31644dc78dca54a$var$RENEGOTIATE_TYPE = "renegotiate";
class $b31644dc78dca54a$var$RenegotiateMessage {
    constructor(){
        this.type = $b31644dc78dca54a$var$RENEGOTIATE_TYPE;
    }
}
const $b31644dc78dca54a$var$PEER_LEFT_TYPE = "peerLeft";
class $b31644dc78dca54a$var$PeerLeftMessageMessage {
    constructor(){
        this.type = $b31644dc78dca54a$var$PEER_LEFT_TYPE;
    }
}
// Interface for the structure of the signalling message
const $b31644dc78dca54a$var$SIGNALLING_TYPE = "signalling";
class $b31644dc78dca54a$var$SignallingMessageObject {
    constructor(message){
        this.type = $b31644dc78dca54a$var$SIGNALLING_TYPE;
        this.message = message;
    }
}
const $b31644dc78dca54a$var$AUDIO_TRANSCEIVER_INDEX = 0;
const $b31644dc78dca54a$var$VIDEO_TRANSCEIVER_INDEX = 1;
class $b31644dc78dca54a$export$62043589d053a879 extends (0, $99wTV$Transport) {
    constructor({ iceServers: iceServers = [], waitForICEGathering: waitForICEGathering = false } = {}){
        super();
        // Trigger when the peer connection is finally ready or in case it has failed all the attempts to connect
        this._connectResolved = null;
        this._connectFailed = null;
        this.pc = null;
        this._stopped = false;
        this.dc = null;
        this.audioCodec = null;
        this.videoCodec = null;
        this.pc_id = null;
        this.reconnectionAttempts = 0;
        this.maxReconnectionAttempts = 3;
        this.isReconnecting = false;
        this.keepAliveInterval = null;
        this._iceServers = [];
        this._iceServers = iceServers;
        this._waitForICEGathering = waitForICEGathering;
        this.mediaManager = new NativeAudioMediaManager();
    }
    initialize(options, messageHandler) {
        this._options = options;
        this._callbacks = options.callbacks ?? {};
        this._onMessage = messageHandler;
        this.mediaManager.setRTVIOptions(options);
        if (this._options.params.config?.length || false) {
            let config = this._options.params.config[0];
            if (config.service == $b31644dc78dca54a$export$62043589d053a879.SERVICE_NAME && config.options.length > 0) config.options.forEach((option)=>{
                if (option.name == "audioCodec") this.audioCodec = option.value;
                else if (option.name == "videoCodec") this.videoCodec = option.value;
            });
        }
        this.state = "disconnected";
        (0, $99wTV$logger).debug("[RTVI Transport] Initialized");
    }
    async initDevices() {
        this.state = "initializing";
        await this.mediaManager.initialize();
        this.state = "initialized";
    }
    setAudioCodec(audioCodec) {
        this.audioCodec = audioCodec;
    }
    setVideoCodec(videoCodec) {
        this.videoCodec = videoCodec;
    }
    async connect(authBundle, abortController) {
        if (abortController.signal.aborted) return;
        this._stopped = false;
        this.state = "connecting";
        await this.mediaManager.connect();
        if (abortController.signal.aborted || this._stopped) { await this.mediaManager.disconnect(); return; }
        // Install ready callbacks before negotiation to avoid a fast-localhost race.
        const connected = new Promise((resolve, reject)=>{
            this._connectResolved = resolve;
            this._connectFailed = reject;
        });
        await this.startNewPeerConnection();
        if (abortController.signal.aborted) return;
        await connected;
        this.state = "connected";
        this._callbacks.onConnected?.();
    }
    syncTrackStatus() {
        // Sending the current status from the tracks to Pipecat
        this.sendSignallingMessage(new $b31644dc78dca54a$var$TrackStatusMessage($b31644dc78dca54a$var$AUDIO_TRANSCEIVER_INDEX, this.mediaManager.isMicEnabled));
        this.sendSignallingMessage(new $b31644dc78dca54a$var$TrackStatusMessage($b31644dc78dca54a$var$VIDEO_TRANSCEIVER_INDEX, this.mediaManager.isCamEnabled));
    }
    sendReadyMessage() {
        this.state = "ready";
        // Sending message that the client is ready, just for testing
        //this.dc?.send(JSON.stringify({id: 'clientReady', label: 'rtvi-ai', type:'client-ready'}))
        this.sendMessage((0, $99wTV$RTVIMessage).clientReady());
    }
    sendMessage(message) {
        if (!this.dc || this.dc.readyState !== "open") {
            (0, $99wTV$logger).warn(`Datachannel is not ready. Message not sent: ${message}`);
            return;
        }
        this.dc?.send(JSON.stringify(message));
    }
    sendSignallingMessage(message) {
        if (!this.dc || this.dc.readyState !== "open") {
            (0, $99wTV$logger).warn(`Datachannel is not ready. Message not sent: ${message}`);
            return;
        }
        const signallingMessage = new $b31644dc78dca54a$var$SignallingMessageObject(message);
        this.dc?.send(JSON.stringify(signallingMessage));
    }
    async disconnect() {
        this.state = "disconnecting";
        await this.stop();
        this.state = "disconnected";
    }
    createPeerConnection() {
        const config = {
            iceServers: this._iceServers
        };
        let pc = new RTCPeerConnection(config);
        pc.addEventListener("icegatheringstatechange", ()=>{
            (0, $99wTV$logger).debug(`iceGatheringState: ${this.pc.iceGatheringState}`);
        });
        (0, $99wTV$logger).debug(`iceGatheringState: ${pc.iceGatheringState}`);
        pc.addEventListener("iceconnectionstatechange", ()=>this.handleICEConnectionStateChange());
        (0, $99wTV$logger).debug(`iceConnectionState: ${pc.iceConnectionState}`);
        pc.addEventListener("signalingstatechange", ()=>{
            (0, $99wTV$logger).debug(`signalingState: ${this.pc.signalingState}`);
            if (this.pc.signalingState == "stable") this.handleReconnectionCompleted();
        });
        (0, $99wTV$logger).debug(`signalingState: ${pc.signalingState}`);
        pc.addEventListener("track", (evt)=>{
            (0, $99wTV$logger).debug(`Received new track ${evt.track.kind}`);
            this._callbacks.onTrackStarted?.(evt.track);
        });
        return pc;
    }
    handleICEConnectionStateChange() {
        if (!this.pc) return;
        (0, $99wTV$logger).debug(`ICE Connection State: ${this.pc.iceConnectionState}`);
        if (this.pc.iceConnectionState === "failed") {
            (0, $99wTV$logger).debug("ICE connection failed, attempting restart.");
            this.attemptReconnection(true);
        } else if (this.pc.iceConnectionState === "disconnected") // Waiting before trying to reconnect to see if it handles it automatically
        setTimeout(()=>{
            if (this.pc?.iceConnectionState === "disconnected") {
                (0, $99wTV$logger).debug("Still disconnected, attempting reconnection.");
                this.attemptReconnection(true);
            }
        }, 5000);
    }
    handleReconnectionCompleted() {
        this.reconnectionAttempts = 0;
        this.isReconnecting = false;
    }
    async attemptReconnection(recreatePeerConnection = false) {
        if (this._stopped) return;
        if (this.isReconnecting) {
            (0, $99wTV$logger).debug("Reconnection already in progress, skipping.");
            return;
        }
        if (this.reconnectionAttempts >= this.maxReconnectionAttempts) {
            (0, $99wTV$logger).debug("Max reconnection attempts reached. Stopping transport.");
            await this.stop();
            return;
        }
        this.isReconnecting = true;
        this.reconnectionAttempts++;
        (0, $99wTV$logger).debug(`Reconnection attempt ${this.reconnectionAttempts}...`);
        // aiortc does not seem to work when just trying to restart the ice
        // so for this case we create a new peer connection on both sides
        if (recreatePeerConnection) {
            const oldPC = this.pc;
            await this.startNewPeerConnection(recreatePeerConnection);
            if (oldPC) {
                (0, $99wTV$logger).debug("closing old peer connection");
                this.closePeerConnection(oldPC);
            }
        } else await this.negotiate();
    }
    async negotiate(recreatePeerConnection = false) {
        if (!this.pc) return Promise.reject("Peer connection is not initialized");
        try {
            // Create offer
            const offer = await this.pc.createOffer();
            await this.pc.setLocalDescription(offer);
            // Wait for ICE gathering to complete
            if (this._waitForICEGathering) await new Promise((resolve)=>{
                if (this.pc.iceGatheringState === "complete") resolve();
                else {
                    const checkState = ()=>{
                        if (this.pc.iceGatheringState === "complete") {
                            this.pc.removeEventListener("icegatheringstatechange", checkState);
                            resolve();
                        }
                    };
                    this.pc.addEventListener("icegatheringstatechange", checkState);
                }
            });
            let offerSdp = this.pc.localDescription;
            // Filter audio codec
            if (this.audioCodec && this.audioCodec !== "default") // @ts-ignore
            offerSdp.sdp = this.sdpFilterCodec("audio", this.audioCodec, offerSdp.sdp);
            // Filter video codec
            if (this.videoCodec && this.videoCodec !== "default") // @ts-ignore
            offerSdp.sdp = this.sdpFilterCodec("video", this.videoCodec, offerSdp.sdp);
            (0, $99wTV$logger).debug(`Will create offer for peerId: ${this.pc_id}`);
            const url = `${this._options.params.baseUrl}${this._options.params.endpoints?.connect || ""}`;
            // Send offer to server
            const response = await fetch(url, {
                body: JSON.stringify({
                    sdp: offerSdp.sdp,
                    type: offerSdp.type,
                    pc_id: this.pc_id,
                    restart_pc: recreatePeerConnection
                }),
                headers: {
                    "Content-Type": "application/json"
                },
                method: "POST"
            });
            const answer = await response.json();
            // @ts-ignore
            this.pc_id = answer.pc_id;
            // @ts-ignore
            (0, $99wTV$logger).debug(`Received answer for peer connection id ${answer.pc_id}`);
            await this.pc.setRemoteDescription(answer);
            (0, $99wTV$logger).debug(`Remote candidate supports trickle ice: ${this.pc.canTrickleIceCandidates}`);
        } catch (e) {
            (0, $99wTV$logger).debug(`Reconnection attempt ${this.reconnectionAttempts} failed: ${e}`);
            this.isReconnecting = false;
            setTimeout(()=>this.attemptReconnection(true), 2000);
        }
    }
    addInitialTransceivers() {
        // Transceivers always appear in creation-order for both peers
        // For now we are only considering that we are going to have 02 transceivers,
        // one for audio and one for video
        this.pc.addTransceiver("audio", {
            direction: "sendrecv"
        });
        this.pc.addTransceiver("video", {
            direction: "sendrecv"
        });
    }
    getAudioTransceiver() {
        // Transceivers always appear in creation-order for both peers
        // Look at addInitialTransceivers
        return this.pc.getTransceivers()[$b31644dc78dca54a$var$AUDIO_TRANSCEIVER_INDEX];
    }
    getVideoTransceiver() {
        // Transceivers always appear in creation-order for both peers
        // Look at addInitialTransceivers
        return this.pc.getTransceivers()[$b31644dc78dca54a$var$VIDEO_TRANSCEIVER_INDEX];
    }
    async startNewPeerConnection(recreatePeerConnection = false) {
        this.pc = this.createPeerConnection();
        this.addInitialTransceivers();
        this.dc = this.createDataChannel("chat", {
            ordered: true
        });
        await this.addUserMedia();
        await this.negotiate(recreatePeerConnection);
    }
    async addUserMedia() {
        (0, $99wTV$logger).debug(`addUserMedia this.tracks(): ${this.tracks()}`);
        let audioTrack = this.tracks().local.audio;
        (0, $99wTV$logger).debug(`addUserMedia audioTrack: ${audioTrack}`);
        if (audioTrack) await this.getAudioTransceiver().sender.replaceTrack(audioTrack);
        let videoTrack = this.tracks().local.video;
        (0, $99wTV$logger).debug(`addUserMedia videoTrack: ${videoTrack}`);
        if (videoTrack) await this.getVideoTransceiver().sender.replaceTrack(videoTrack);
    }
    // Method to handle a general message (this can be expanded for other types of messages)
    handleMessage(message) {
        try {
            const messageObj = JSON.parse(message); // Type is `any` initially
            (0, $99wTV$logger).debug("received message:", messageObj);
            // Check if it's a signalling message
            if (messageObj.type === $b31644dc78dca54a$var$SIGNALLING_TYPE) this.handleSignallingMessage(messageObj); // Delegate to handleSignallingMessage
            else // Bubble any messages with rtvi-ai label
            if (messageObj.label === "rtvi-ai") this._onMessage({
                id: messageObj.id,
                type: messageObj.type,
                data: messageObj.data
            });
        } catch (error) {
            console.error("Failed to parse JSON message:", error);
        }
    }
    // Method to handle signalling messages specifically
    async handleSignallingMessage(messageObj) {
        // Cast the object to the correct type after verification
        const signallingMessage = messageObj;
        // Handle different signalling message types
        switch(signallingMessage.message.type){
            case $b31644dc78dca54a$var$RENEGOTIATE_TYPE:
                this.attemptReconnection(false);
                break;
            case $b31644dc78dca54a$var$PEER_LEFT_TYPE:
                this.disconnect();
                break;
            default:
                console.warn("Unknown signalling message:", signallingMessage.message);
        }
    }
    createDataChannel(label, options) {
        const dc = this.pc.createDataChannel(label, options);
        dc.addEventListener("close", ()=>{
            (0, $99wTV$logger).debug("datachannel closed");
            if (this.keepAliveInterval) {
                clearInterval(this.keepAliveInterval);
                this.keepAliveInterval = null;
            }
        });
        dc.addEventListener("open", ()=>{
            (0, $99wTV$logger).debug("datachannel opened");
            if (this._connectResolved) {
                this.syncTrackStatus();
                this._connectResolved();
                this._connectResolved = null;
                this._connectFailed = null;
            }
            // @ts-ignore
            this.keepAliveInterval = setInterval(()=>{
                const message = "ping: " + new Date().getTime();
                dc.send(message);
            }, 1000);
        });
        dc.addEventListener("message", (evt)=>{
            let message = evt.data;
            this.handleMessage(message);
        });
        return dc;
    }
    closePeerConnection(pc) {
        pc.getTransceivers().forEach((transceiver)=>{
            if (transceiver.stop) transceiver.stop();
        });
        pc.getSenders().forEach((sender)=>{
            sender.track?.stop();
        });
        pc.close();
    }
    async stop() {
        this._stopped = true;
        if (!this.pc) {
            await this.mediaManager.disconnect();
            (0, $99wTV$logger).debug("Peer connection is already closed or null.");
            return;
        }
        if (this.dc) this.dc.close();
        this.closePeerConnection(this.pc);
        this.pc = null;
        await this.mediaManager.disconnect();
        // For some reason after we close the peer connection, it is not triggering the listeners
        this.pc_id = null;
        this.reconnectionAttempts = 0;
        this.isReconnecting = false;
        this._callbacks.onDisconnected?.();
        if (this._connectFailed) this._connectFailed();
        this._connectFailed = null;
        this._connectResolved = null;
    }
    getAllMics() {
        return this.mediaManager.getAllMics();
    }
    getAllCams() {
        return this.mediaManager.getAllCams();
    }
    getAllSpeakers() {
        return this.mediaManager.getAllSpeakers();
    }
    async updateMic(micId) {
        return this.mediaManager.updateMic(micId);
    }
    updateCam(camId) {
        return this.mediaManager.updateCam(camId);
    }
    updateSpeaker(speakerId) {
        return this.mediaManager.updateSpeaker(speakerId);
    }
    get selectedMic() {
        return this.mediaManager.selectedMic;
    }
    get selectedCam() {
        return this.mediaManager.selectedCam;
    }
    get selectedSpeaker() {
        return this.mediaManager.selectedSpeaker;
    }
    set iceServers(iceServers) {
        this._iceServers = iceServers;
    }
    get iceServers() {
        return this._iceServers;
    }
    enableMic(enable) {
        this.mediaManager.enableMic(enable);
        this.sendSignallingMessage(new $b31644dc78dca54a$var$TrackStatusMessage($b31644dc78dca54a$var$AUDIO_TRANSCEIVER_INDEX, enable));
    }
    enableCam(enable) {
        this.mediaManager.enableCam(enable);
        this.sendSignallingMessage(new $b31644dc78dca54a$var$TrackStatusMessage($b31644dc78dca54a$var$VIDEO_TRANSCEIVER_INDEX, enable));
    }
    get isCamEnabled() {
        return this.mediaManager.isCamEnabled;
    }
    get isMicEnabled() {
        return this.mediaManager.isMicEnabled;
    }
    get state() {
        return this._state;
    }
    set state(state) {
        if (this._state === state) return;
        this._state = state;
        this._callbacks.onTransportStateChanged?.(state);
    }
    get expiry() {
        return this._expiry;
    }
    tracks() {
        return this.mediaManager.tracks();
    }
    // Not implemented
    enableScreenShare(enable) {
        (0, $99wTV$logger).error("startScreenShare not implemented for SmallWebRTCTransport");
        throw new Error("Not implemented");
    }
    get isSharingScreen() {
        (0, $99wTV$logger).error("isSharingScreen not implemented for SmallWebRTCTransport");
        return false;
    }
    sdpFilterCodec(kind, codec, realSdp) {
        const allowed = [];
        const rtxRegex = new RegExp("a=fmtp:(\\d+) apt=(\\d+)\\r$");
        const codecRegex = new RegExp("a=rtpmap:([0-9]+) " + this.escapeRegExp(codec));
        const videoRegex = new RegExp("(m=" + kind + " .*?)( ([0-9]+))*\\s*$");
        const lines = realSdp.split("\n");
        let isKind = false;
        for(let i = 0; i < lines.length; i++){
            if (lines[i].startsWith("m=" + kind + " ")) isKind = true;
            else if (lines[i].startsWith("m=")) isKind = false;
            if (isKind) {
                const match = lines[i].match(codecRegex);
                if (match) allowed.push(parseInt(match[1]));
                const matchRtx = lines[i].match(rtxRegex);
                if (matchRtx && allowed.includes(parseInt(matchRtx[2]))) allowed.push(parseInt(matchRtx[1]));
            }
        }
        const skipRegex = "a=(fmtp|rtcp-fb|rtpmap):([0-9]+)";
        let sdp = "";
        isKind = false;
        for(let i = 0; i < lines.length; i++){
            if (lines[i].startsWith("m=" + kind + " ")) isKind = true;
            else if (lines[i].startsWith("m=")) isKind = false;
            if (isKind) {
                const skipMatch = lines[i].match(skipRegex);
                if (skipMatch && !allowed.includes(parseInt(skipMatch[2]))) continue;
                else if (lines[i].match(videoRegex)) sdp += lines[i].replace(videoRegex, "$1 " + allowed.join(" ")) + "\n";
                else sdp += lines[i] + "\n";
            } else sdp += lines[i] + "\n";
        }
        return sdp;
    }
    escapeRegExp(string) {
        return string.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }
}
$b31644dc78dca54a$export$62043589d053a879.SERVICE_NAME = "small-webrtc-transport";




export {$b31644dc78dca54a$export$62043589d053a879 as SmallWebRTCTransport};

