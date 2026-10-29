# Audio-only adaptation of Pipecat SmallWebRTC 0.4.0

Source: published `@pipecat-ai/small-webrtc-transport@0.4.0`,
`dist/index.module.js` from
https://registry.npmjs.org/@pipecat-ai/small-webrtc-transport/-/small-webrtc-transport-0.4.0.tgz

The upstream package depends on `@daily-co/daily-js` for its default device manager.
Even current upstream releases retain that dependency. To meet the requirement of
removing that SDK entirely without upgrading Pipecat 0.0.72, this repository uses an
explicit local package adaptation; it is **not** an unmodified npm release.

`index.js` retains the upstream SmallWebRTC transport and signaling classes, with
the unused bundled media managers omitted. The constructor instantiates the small
native audio manager in `native-audio.js`, which uses getUserMedia and supports the
required microphone mute/unmute lifecycle. Capture requests echo cancellation,
noise suppression, and automatic gain control. Camera/screen/device switching UI is
not included. Device manager placeholders satisfy the old transport interface only.

Additional lifecycle fixes stop reconnect attempts after Stop, stop microphone tracks
when closing before a peer connection exists, and check cancellation after capture.
Client application code bypasses the old SDK's separate auth handshake because our
server already created an owned session; its SDP request remains upstream protocol.

The backend uses Pipecat's unmodified SmallWebRTCConnection/SmallWebRTCTransport.
Offers and answers contain gathered candidates (non-trickle ICE). There are no
external JavaScript requests, cloud room requests, tokens, or third-party SDKs for
transport at runtime. All browser assets are bundled and served by the backend.

The matching `@pipecat-ai/client-js@0.4.0` exports `RTVIClient` (imported under the
local name PipecatClient). Its uuid dependency is overridden to 11.1.1; it uses the
compatible v4 export. Keep this adaptation explicit when upgrading the SDKs.
