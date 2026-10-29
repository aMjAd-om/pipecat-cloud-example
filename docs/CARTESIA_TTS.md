# Switch between Azure and Cartesia speech

Azure remains the default. In the server's private `.env`, configure:

```dotenv
TTS_PROVIDER=cartesia
CARTESIA_API_KEY=your_actual_cartesia_key
CARTESIA_MODEL=sonic-3.6
CARTESIA_VOICE_ID=47c38ca4-5f35-497b-b1a3-415245fb35e1
CARTESIA_SPEED=1.3
CARTESIA_VOLUME=1
DEFAULT_LANGUAGE=ar
```

Keep the Azure Speech credentials: recognition still uses Azure STT.
Cartesia receives the agent's generated spoken text for synthesis, which may
include survey-derived information. Its key stays in the backend.

After updating the code, rebuild on Ubuntu:

```sh
docker compose -f docker-compose.ubuntu.yml up -d --build agent
docker compose -f docker-compose.ubuntu.yml exec agent python -m scripts.verify_services tts
docker compose -f docker-compose.ubuntu.yml logs -f agent
```

The verification sends a short synthetic greeting to the selected provider and
reports audio bytes and time to first audio. It does not play audio. Then test a
browser session and Linphone call, including speaking over the agent.

To switch back, set `TTS_PROVIDER=azure`, then recreate the container:

```sh
docker compose -f docker-compose.ubuntu.yml up -d --force-recreate agent
```

Provider settings are captured per session; reconnect after restarting. The text
chat lab does not synthesize audio and cannot compare voice quality.

The adapter uses Cartesia's streaming HTTP bytes endpoint and API version
`2026-08-14`, matching the current request format in the
[official API reference](https://docs.cartesia.ai/api-reference/tts/bytes).
It requests raw signed 16-bit mono PCM at 24 kHz to match the existing Pipecat
output path, rather than a 44.1 kHz WAV file. Existing telephone resampling is
unchanged. Speed must be 0.6–1.5; volume must be 0.5–2.0.

Interrupting synthesis cancels the request coroutine and closes the stream;
queued playback uses Pipecat's existing interruption handling. Errors do not
retry or fall back to another provider mid-response, avoiding duplicated speech.
No audio is saved, and provider error logs omit bodies, keys and spoken text.

Offline tests cover configuration, provider selection, PCM chunk alignment,
stream cancellation and recovery, empty/malformed audio, and safe HTTP failures.
Live voice quality, latency and phone interruption behavior require a real key
and a call; they have not been verified by these offline tests.

Implementation verification: the full regression run completed 88 tests with
87 passing and one Linux-only signal test skipped on Windows. After adding the
network-failure/content-type test, all eight focused Cartesia tests passed.
The dependency lock check and whitespace check passed. No deployment or live
Cartesia synthesis was performed.
