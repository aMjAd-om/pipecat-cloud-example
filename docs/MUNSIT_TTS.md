# Munsit speech output

Select Munsit for browser and Asterisk calls in `.env`:

```dotenv
TTS_PROVIDER=munsit
MUNSIT_API_KEY=your_actual_munsit_key
MUNSIT_VOICE_ID=SyU41Fvh3zAI9ZRf81uuTYQ4
MUNSIT_SPEED=1.0
MUNSIT_STABILITY=0.5
```

The voice ID and speed default to the values above. Speed accepts 0.7–1.2;
stability accepts 0–1. Azure Speech credentials are still needed for STT and
Azure OpenAI credentials for the conversation model.

For native startup, restart `uv run --frozen python -m voice_agent` after editing
`.env`. For Docker, rebuild and recreate using your existing Compose configuration:

```bash
docker compose up -d --build
# Ubuntu host-network installation instead:
docker compose -f docker-compose.ubuntu.yml up -d --build
```

An agent's optional `voice.provider` and `voice.voice_id` override environment
selection. Use `"provider": "munsit"` there if the agent has an explicit voice
configuration. The text conversation lab does not synthesize audio.

To check just TTS with a short synthetic greeting and report time to first audio:

```bash
uv run --frozen python scripts/verify_services.py tts
```

The shared factory uses `StreamingMunsitTTS` in `providers/munsit_tts.py`, posting
the agent's text to
`https://api.munsit.com/api/v1/text-to-speech/faseeh-v1-preview` with the
`x-api-key` header. The request adds `streaming: true`, `sample_rate: 24000`,
and the configured stability. Munsit's default non-streaming response is WAV;
streaming returns mono PCM16, as described in the
[official streaming reference](https://docs.munsit.com/text-to-speech/audio-streaming-output).
Explicitly requesting 24 kHz matches the existing Pipecat audio pipeline.

HTTP chunks become audio frames as they arrive, with incomplete sample bytes
carried into the next chunk. Interruptions close the current response while
keeping the client available for the next turn. Shutdown closes the client.
Unexpected formats, empty audio, API failures and timeouts produce a sanitized
error. Requests are not automatically retried because that could repeat speech
after partial playback. Audio is not saved to disk.

Offline checks (no Munsit account required):

```bash
uv run --frozen python -m unittest tests.integration.test_tts_providers -q
```
