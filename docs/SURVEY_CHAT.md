# Local survey conversation lab

After a company/identity question or another accepted non-answer, the lab displays
the direct reply, waits one second, then separately returns to the current survey
question. Sending another reply, interrupting, or starting a new conversation
cancels the older reminder. Exported turn records include `follow_up` and
`pause_seconds` separately from the model's `spoken_response`.

Test the Arabic survey by typing what a customer would say. The page uses the same `ConversationManager`, questionnaire, five-section prompt, strict model schema, `FoundryDecisionClient`, and final language check used for phone calls. No STT, TTS, microphone, Asterisk, or WebRTC connection is required.

## Run on your computer

From the application repository, with your existing `.env`:

```bash
uv run --frozen python -m voice_agent.api.survey_chat
```

Open **http://127.0.0.1:7862**. It also accepts `http://localhost:7862`. You need Azure Foundry LLM credentials; Azure Speech credentials are not required by this tool. To change the port, set `SURVEY_CHAT_PORT` in `.env` before starting.

The server always binds `127.0.0.1` regardless of the main application's `HOST` setting. It is a standalone process, never started by the normal app, and has no routes on the public browser service. It rejects remote client IPs, unexpected Host/Origin headers, and requests that lack the page's mutation token. Do not add the lab to public Nginx routes. Stop it with Ctrl+C when done.

## Use it

1. The page starts a fresh conversation and displays the exact survey introduction and consent question.
2. Type a customer reply and press Enter or **Send reply**. Shift+Enter inserts a newline. Example chips fill the input without sending it.
3. Read the final spoken-response text in the conversation. This includes Python's exact next question after a permitted transition.
4. Inspect the current question, accepted answers, model-proposed JSON, Python validation outcome, model token usage, and response time in the sidebar.
5. Try off-topic speech, vague ratings, Arabic/English mixtures, clarifications, previous-answer corrections, pause/resume, skips, and cancellation.
6. Click **Simulate interruption** during an in-flight reply to invalidate that decision. A stale response cannot commit an answer or become a spoken message. This tests conversation state, not microphone detection or audible playback cancellation.
7. Use **New conversation** to clear the session and start again. Reset cancels an outstanding model request. The questionnaire file is reloaded for the new conversation.
8. Use **Export chat** to explicitly download a JSON trace, including typed customer text, final replies, proposed decisions, validation results, and accepted answers. Share the relevant trace when requesting response changes.

You can continue typing after completion to test previous-answer corrections, as allowed by the survey manager. A cancelled survey remains cancelled; start a new conversation to try again.

## Tune and retest

For each behavior you want changed, provide the customer reply, actual agent response, expected response, and preferably the exported trace. For example: “After ‘ممتازة’ on the numeric rating, ask for a number more naturally.”

- Conversational behavior and style: `PROMPT` in `src/voice_agent/conversation/prompts.py`.
- Fixed introduction, exact question wording, closing, authorized company information, and constraints: `questionnaires/customer-satisfaction.ar.json`.
- Legal state transitions and answer constraints: `ConversationManager` in `src/voice_agent/conversation/manager.py`.
- Output-language enforcement: `src/voice_agent/voice/language.py`.

Restart the lab after editing Python prompt/code. Start a new conversation after editing the questionnaire. These are the actual files used by the phone agent too; apply tested changes to Ubuntu and rebuild its agent when ready. The lab does not publish changes or deploy automatically.

## Run against the Ubuntu model configuration if needed

If you prefer to run the lab inside the existing Ubuntu agent image, rebuild the image with these changes, then from the application checkout:

```bash
docker compose -f docker-compose.ubuntu.yml run --rm --no-deps agent python -m voice_agent.api.survey_chat
```

This starts an additional temporary container with the same `.env` and Linux host networking. It does not replace the running voice agent. Access it with a browser on Ubuntu, or create a private SSH local forward from your computer:

```bash
ssh -N -L 7862:127.0.0.1:7862 USER@UBUNTU_SSH_ADDRESS
```

Then open `http://127.0.0.1:7862` on your computer. Use your existing way of reaching Ubuntu over SSH if a jump host is needed. No VPN is required for this private TCP page. Keep the lab on a separate port from the telephone listener (`7861`) and main browser app (`7860`).

## Limits and privacy

The page is local; model inference still goes to your configured Azure Foundry endpoint and incurs LLM usage. Opening/resetting a conversation does not call the LLM. Each new finalized typed reply normally makes one request, without an additional response-writing request. Duplicate events and invalid/stale UI submissions do not create extra accepted answers.

This is a text simulation, not a phone-audio simulator: it cannot assess STT transcription errors, microphone levels, VAD, echo, audio codecs, audible barge-in quality, TTS pronunciation, or end-to-end voice latency. Response times shown include the text/model decision path only. Model outputs may vary between calls even with identical inputs.

Sessions are held in memory, with at most eight open sessions, 30-minute idle expiry, and the most recent 100 trace records per session. Closing/reloading the page attempts to clear its session; reset and server shutdown clear it explicitly. Nothing is automatically written to a transcript file or browser storage. An exported trace contains the text you typed and should be reviewed before sharing.

## Validation

Backend checks run with the existing suite:

```bash
uv run --frozen python -m unittest discover -s tests -t . -v
```

An additional real-browser regression uses a deterministic model stub, so it incurs no Azure usage:

```bash
node tests/browser/survey_chat_browser.mjs
```

This uses the repository's Playwright installation and Edge on Windows (or Playwright Chromium on other platforms). `BROWSER_PATH` can override the browser executable. The fixture uses loopback port `17862`, which must be available.
