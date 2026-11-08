# Trace gaps in telephone conversations

The voice pipeline writes `latency` events and durations at `LOG_LEVEL=INFO`.
Every trace has a random `call` ID, a VAD `turn` number, and a `response` number.
Response numbers also cover pickup greetings and timed reminders. No audio,
transcripts, phone numbers, credentials, or model request bodies are logged.

The original tracing adds measurements without changing speech behavior. The
current [endpointing experiment](ENDPOINT_LATENCY.md) also lowers the silence
windows and coordinates recognition results with VAD. Deploy the updated code
to apply it.

The subsequent [decision-latency experiment](DECISION_LATENCY.md) compacts model
output and removes repeated instructions. Its logs identify `protocol=compact-v1`;
that document includes before/after measurements and remaining semantic failures.

## Capture a test

On the existing Ubuntu deployment, after installing the updated code, rebuild
the agent during an idle period (restarting it ends active calls):

```bash
docker compose -f docker-compose.ubuntu.yml up -d --build agent
docker compose -f docker-compose.ubuntu.yml logs --follow --no-color agent
```

Make a normal test call from the call desk. Try several short answers and at
least one longer answer. Note the approximate time of a noticeable gap and
whether it followed the customer or the agent. Then save the recent logs:

```bash
docker compose -f docker-compose.ubuntu.yml logs --since 10m --no-color agent > latency.log
rg 'latency ' latency.log
```

Find `event=session_start` at the test-call time, then filter its `call` ID:

```bash
rg 'latency call=REPLACE_WITH_CALL_ID' latency.log
```

Timing is already enabled at INFO; DEBUG is unnecessary. Optional
`TELEPHONY_AUDIO_DIAGNOSTICS=true` also reports input queue depth and audio level
counters every five seconds. It requires restarting the agent and does not record
audio. Use it when speech detection itself appears late or unreliable.

## Read the trace

`at_ms` is elapsed monotonic time from that call's pipeline initialization, not
wall-clock time. Compare events within one call. Metric values are milliseconds.
For example, a line shaped like this measures the model/tool decision stage:

```text
latency call=abc123 turn=2 response=4 metric=decision_model_and_tools ms=1250.0
```

### Customer finishes, agent replies

| Metric / event | What it measures or tells you |
| --- | --- |
| `event=vad_config` | Configured speech-start and speech-stop windows. The experiment defaults to a 500 ms stop window (previously 800 ms). This is configuration, not a measured acoustic endpoint. |
| `event=stt_endpoint_config` | Azure segmentation silence setting, now 400 ms by default. |
| `event=speech_end` | VAD has declared the customer finished. The silence window has already elapsed. |
| `stt_final_after_vad_end` | Azure's final recognition callback relative to VAD stop. A negative value means recognition finished first. |
| `stt_callback_to_dispatch` | Time from the SDK callback to forwarding its final transcript. A large value suggests callback/event-loop backlog. |
| `transcript_wait_for_turn_end` | Final dispatch to release after VAD stops, pending recognition finalizes, and a 100 ms settling interval passes. |
| `event=utterance_endpoint_check` | Numeric VAD-minus-final endpoint difference and pending-recognition/reconnect-gap flags. Different acoustic endpoints are diagnostic, not a reason to reject a completed transcript. |
| `event=utterance_incomplete` / `event=recognition_retry` | Recognition timed out or exceeded bounds; buffered text is discarded and a retry is spoken without a model call or survey advancement. |
| `transcript_to_response_start` | Assembled utterance release to response processing, including processor queues and the context aggregator. |
| `decision_preparation` | Survey response processing to submitting the decision request. |
| `decision_model_and_tools` | Complete survey decision request, including any retrieval rounds. `event=decision_usage` supplies model/tool call counts, aggregate tool time, and token counts. |
| `llm_total` | Response processing to the complete spoken response being emitted. For surveys this includes decision generation, validation, and rendering. |
| `response_buffer_hold` | First emitted response text to text released by the language guard. The legacy assistant buffers its complete streamed response here. |
| `text_to_tts_request` | Guarded text to the first synthesis request. Includes TTS sentence aggregation or queued synthesis work. |
| `tts_first_audio` | First synthesis request to its first PCM audio. |
| `transport_queue_wait` | First audio entering output to the first successful paced WebSocket write to Asterisk. |
| `speech_end_to_transport_audio` | VAD stop to the first successful audio write. This is the main server-side response-latency measure. |
| `llm_end_to_transport_audio` | Complete spoken response text to the first audio write. Useful for isolating the delay after generation. |
| `speech_end_to_nonsilent_audio` | VAD stop to the first successfully sent PCM chunk above the configured energy threshold. |
| `llm_end_to_nonsilent_audio` | Complete response text to the first above-threshold outgoing PCM chunk. |
| `transport_leading_silence` | Duration of below-threshold PCM before the response's first above-threshold chunk. Excludes time waiting for chunks. |

Start with a slow turn's `speech_end_to_transport_audio`, then examine the stage
durations to locate the delay. Durations overlap and must not all be added
together. Azure recognition and VAD run concurrently. A transcript can arrive
before VAD stop; negative offsets are retained rather than clamped.

Survey decisions currently use `stream=False` and are validated before speech.
For this path, `llm_first` / `llm_ttft` mean first **emitted spoken response**,
not the provider's first generated token. Use `decision_model_and_tools` to
measure the actual decision wait. For the legacy streaming assistant,
`llm_first` now sits before the language guard, so the guard's buffering no
longer hides the first-token timing.

### Agent finishes, customer starts

Model generation ending does not mean the customer has heard the complete
question: synthesis and paced playback can continue for seconds afterward.

| Metric / event | What it measures or tells you |
| --- | --- |
| `event=tts_end` | A synthesis segment has finished producing audio. It may still be queued. |
| `event=transport_tail` | Estimated end of the last PCM chunk of a synthesis segment at the local paced transport. There can be several segments per response. |
| `llm_end_to_transport_tail` | Generation completion to that segment's local audio tail. It includes ordinary speech playback time. |
| `transport_tail_to_user_start` | Last locally sent audio tail to the next VAD speech-start detection. Includes human reaction time, telephone transit, and VAD start detection; negative values indicate overlap. |
| `nonsilent_tail_to_user_start` | Last above-threshold outgoing chunk's tail to the next VAD speech start. Useful when the stream contains trailing silence. |
| `transport_trailing_silence` | Duration of below-threshold PCM after the most recent above-threshold chunk, reported at each synthesis-segment end or flush. |
| `event=transport_last_nonsilent` | Estimated end of the most recent above-threshold outgoing chunk. |
| `event=output_audio_activity` | PCM duration, energy threshold and whether any chunk exceeded it. Counters cover the response through the reported synthesis segment. |
| `transport_audio_gap` | More than 100 ms without the next audio chunk while the same response is playing. May indicate synthesis starvation or output/network stalls; can also include intentional pauses between synthesis segments. |
| `event=media_xoff` / `event=media_xon` | Asterisk flow control paused/resumed audio sending. |
| `event=transport_flush` | An interruption flushed queued audio. A flushed tail is not treated as a completed question. |
| `event=pbx_queue_drained` | Asterisk acknowledged a queue-drain report, when normal call teardown requests one. |

Playback traces keep their original call/turn/response identity even when a
new customer turn begins while older audio is queued. `transport_tail` estimates
the end of the last sent chunk using its PCM duration; it does not confirm that
the handset played it. `transport_audio_gap` measures missing chunks, not silent
samples encoded inside a valid TTS stream.

## Limits and next step

The current VAD experiment waits for about 500 ms of silence by configuration,
and the survey waits for a complete structured decision before TTS. Compare
several calls with the previous trace; shorter configured windows alone do not
prove lower perceived latency.

Server tracing cannot directly measure the last audible word at either handset.
The actual customer speech endpoint precedes `speech_end`; the full perceived
delay therefore includes VAD detection time beyond the displayed main latency.
Similarly, the agent-to-customer gap is not purely an application delay.

If server timings are short while the audible gap remains long, the next
measurement is a controlled test with synchronized audio at the PBX/handset to
check telephone buffering and transit. PCM energy timing now helps identify
leading/trailing silence without recording. It works at 20 ms chunk resolution;
noise can exceed the threshold and quiet speech can fall below it. Audio
recording and silence trimming are not enabled.

Offline verification (no calls to providers):

```bash
uv run --frozen python -m unittest discover -s tests -q
```
