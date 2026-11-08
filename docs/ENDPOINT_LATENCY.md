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
the buffer waits for a final for any pending recognition phrase, then allows
100 ms without new recognition activity or resumed speech before release.
Internal progress/final events survive cancellation of an older response's data
queue. Progress events contain only numeric metadata; partial text never reaches
the conversation manager.

The previous gate required Azure's word endpoint to reach VAD's active-audio
endpoint within 150 ms. A real call delivered a final result, then waited five
seconds and discarded it because those endpoints disagreed. This strict check
has been removed: Azure word durations exclude leading/trailing silence and are
not the same boundary as local VAD. See [Microsoft's recognition timing guide](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/get-speech-recognition-results).

Pending partial phrases are matched with finals inside the recognizer's own
sample clock, rather than against VAD. A final for an earlier phrase cannot clear
a later pending phrase. Late partials from an already finalized phrase are
ignored. A reconnect during an unfinished utterance discards that incomplete
utterance and requests a repeat instead of saving a fragment across the gap.

For example, if Azure finalizes "three" and then "actually four" before the
same VAD turn ends, the decider receives "three actually four" once. If the
caller resumes before release, the pending segments stay together. Pauses
longer than the VAD window can still create separate turns; this is a setting
tradeoff to assess during calls. Joining preserves words, including repetitions;
it does not fix the model's known correction or pause interpretation errors
documented in [the previous experiment](DECISION_LATENCY.md).

The 100 ms settling delay applies to all transports. This replaces the earlier
zero-delay release on the telephone path and gives late recognition events time
to cancel release. Events delayed beyond that settling interval can still form
a later turn; include corrections and pauses in telephone regression tests.

The buffer allows at most 32 segments / 2000 characters. A five-second watchdog
starts at VAD stop even if no final arrived. On incomplete recognition, it
discards buffered text and emits a nonfatal pipeline error
(`event=utterance_incomplete`) and a spoken request to repeat through the normal
language guard, TTS and output path (`event=recognition_retry`). This does not
call the model, save an answer or advance the survey. Late results from that
failed attempt are ignored until a new speech-start event. Retries are suppressed
during the initial pickup handshake, after the survey ends, while the caller is
speaking, and if a newer turn has superseded the failed attempt.

`event=utterance_released` reports segment and character counts only.
`event=utterance_endpoint_check` reports the numeric VAD-minus-final difference
(`vad_minus_final_ms`), pending recognition and reconnect-gap flags. The endpoint
difference is diagnostic and no longer decides whether a transcript is accepted.
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
