# Endpoint timing experiment

The previous compact-decision test reduced model time, but VAD-stop to first
outgoing PCM stayed around 1.66 seconds. This experiment tests earlier speech
end detection and recognition finalization, and measures silence in outgoing
PCM. A real telephone retest is still required to measure its effect.

## Configuration and deployment

These defaults apply when the variables are absent. Existing explicit settings
take precedence. To make the trial settings explicit in the server's `.env`:

```dotenv
TELEPHONY_VAD_STOP_MS=500
AZURE_STT_SEGMENTATION_SILENCE_MS=400
TELEPHONY_NONSILENT_RMS_MIN=200
```

VAD previously used 800 ms. Azure previously used its service/SDK default;
its effective value was not measured. Azure's segmentation setting controls how
much silence closes a recognition phrase. A lower value can split a caller's
answer into more phrases, so the pipeline now joins phrases before deciding.
See [Microsoft's segmentation guidance](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-recognize-speech?pivots=programming-language-python).

During an idle period, pull the branch containing this change, then rebuild:

```bash
git pull
docker compose -f docker-compose.ubuntu.yml up -d --build agent
docker compose -f docker-compose.ubuntu.yml logs --follow --no-color agent
```

Restarting ends active calls. Check `event=vad_config` for `stop_ms=500.0`
and `event=stt_endpoint_config segmentation_ms=400` when making the next call.
Keep the same model, survey, TTS provider and speech speed for comparison.

To restore the earlier silence settings, set these values and recreate the
agent with the same compose command:

```dotenv
TELEPHONY_VAD_STOP_MS=800
AZURE_STT_SEGMENTATION_SILENCE_MS=
```

A blank Azure value omits the SDK property; removing the variable uses the new
400 ms default. Utterance coordination and numeric tracing remain enabled.
Accepted ranges are VAD 200–2000 ms, Azure 100–5000 ms and RMS 1–32767.
Invalid values fail validation at startup.

## Recognition and turn boundaries

Recognition finals are buffered while the caller is speaking. After VAD stops,
the buffer waits until the latest final covers the last VAD-active input audio,
allowing 150 ms for the detectors' different acoustic boundaries. Azure's
sample offsets are mapped onto the input PCM clock, accounting for recognizer
reconnects and audio discarded from queues during interruptions. Internal final
events survive cancellation of an older response's data queue.

For example, if Azure finalizes "three" and then "actually four" before the
same VAD turn ends, the decider receives "three actually four" once. If the
caller resumes before release, the pending segments stay together. Pauses
longer than the VAD window can still create separate turns; this is a setting
tradeoff to assess during calls. Joining preserves words, including repetitions;
it does not fix the model's known correction or pause interpretation errors
documented in [the previous experiment](DECISION_LATENCY.md).

There is no added settling delay when both PCM clocks are available. Custom
transports without this metadata use a 100 ms settling delay after speech ends.
The buffer allows at most 32 segments / 2000 characters. If an incomplete tail
has not finalized within five seconds after VAD stops, it discards the buffered
text and emits a nonfatal pipeline error (`event=utterance_incomplete`) instead
of sending a partial answer. This error does not itself speak a retry prompt.
A new speech-start event permits another utterance.

`event=utterance_released` reports segment and character counts only.
`transcript_wait_for_turn_end` measures final dispatch to assembled utterance
release; `transcript_to_response_start` now starts at that release. Together
they distinguish endpoint waiting from downstream processing.

## Compare telephone calls

Capture several calls using [the trace guide](LATENCY_TRACING.md). Include short
answers, longer answers with pauses, self-corrections, answer-plus-pause requests,
and interruptions. Confirm the full answer reaches one decision and survey
progress remains correct before accepting a speed improvement. Do not treat
passing mocked decision tests as proof that the live model understands controls.

Compare `stt_final_after_vad_end`, `transcript_wait_for_turn_end`,
`decision_model_and_tools`, `tts_first_audio`, `speech_end_to_transport_audio`
and `speech_end_to_nonsilent_audio`. The speech-end metrics start after the VAD
silence window; the user's perceived wait also includes that window.

For gaps after the agent's question, inspect `transport_trailing_silence` and
`nonsilent_tail_to_user_start`. `transport_leading_silence` distinguishes silent
PCM at the start of TTS from waiting for TTS/network data. Silence durations count
PCM samples, excluding gaps between sends. Summaries at synthesis-segment ends
are cumulative within a response; a flush reports the interrupted response.

Energy classification uses 20 ms outgoing PCM16 chunks and the configurable RMS
threshold. It estimates non-silent audio, not words: noise can count and quiet
speech can be missed. It measures successful writes to Asterisk, not confirmed
handset playback. Only numeric counters are retained; no audio is recorded or
trimmed.

## Offline verification

```bash
uv run --frozen python -m unittest discover -s tests -q
```

Tests cover split recognition, delayed final tails, caller resumption,
interruption queue cancellation, timeout/cancel cleanup, clock alignment,
SDK configuration, survey routing with mocked decisions, and PCM energy timing.
They make no provider requests or telephone calls.
