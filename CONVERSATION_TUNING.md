# Conversational response tuning

This change addresses the caller examples supplied from the text lab: repeated introductions/consent requests, failure to answer who is calling or why, unsupported service attribution, demanding the numeric scale after clear verbal feedback, and repeating questions when the caller expresses frustration.

## Behavior changes

- Identity, company, purpose, call authorization, number source, and service-identity questions are separate concerns. The LLM answers the current concern briefly and waits, rather than appending the consent question or a rating request after every reply.
- The reference information now explicitly distinguishes the survey executor, شركة تنقيب البيانات, from the unidentified service provider. It does not supply an authorization or number source, so the agent must not invent one.
- Arabic replies are guided toward everyday polite Omani/Gulf speech, without administrative confirmations, repeated disclaimers, or a fixed “شكراً” on every turn. Contextual transition bridges are allowed; Python still supplies the exact questionnaire question.
- Relevant verbal rating descriptions are handled as clarification, not unrelated speech. “الخدمة جيدة” prompts a short request for the customer's chosen number, without guessing a rating.
- An exact scale-endpoint description can prompt confirmation of its one associated number. “بطيئة جداً” on the speed question may elicit “واضح إن السرعة ما ناسبتك، تقصد نعتمد واحد؟”. Nothing is saved until the customer supplies a number or explicitly confirms that specific pending number. Vague praise is not mapped to a guessed score.
- Frustration is acknowledged using the actual history; the model is instructed to stop restarting the full scale explanation.
- If a model proposes an unspoken/guessed rating anyway, the existing validator rejects the answer. Its numeric fallback now asks concisely for the customer's chosen rating instead of replaying the full question.
- A model-written question in a transition bridge is replaced before Python appends the next question. This prevents a valid resume/advance from asking two questions in the same reply.

The seven-field decision schema, action/intent enums, one model request per finalized turn, authoritative state validation, consent requirements, language lock, and exact questionnaire text are preserved. This is prompt/context and rendering work; the model itself is not retrained. Browser/telephone transports and Azure STT/TTS implementations are unchanged.

## Validation and reproducibility

Offline checks:

```bash
uv run --frozen python -m unittest discover -s tests -v
```

`tests/test_conversation_style.py` adds regressions for rejecting guessed numeric answers, accepting a specifically confirmed endpoint, preventing a vague yes from inventing a rating, preserving state while answering concerns, and removing duplicate transition questions. The full suite includes the existing survey benchmark/state-contract cases.

Live synthetic evaluation:

```bash
uv run --frozen python evaluate_conversation_style.py --live
```

This explicitly makes billable calls to the configured Foundry GPT-4.1-mini deployment. It exercises 27 turns: caller concerns, rating clarifications, repeated frustration, slow/fast endpoint confirmation, irrelevant numbers, ambiguous numbers, prompt injection, pause/resume, explicit correction, and normal consent/answer progression. Requests are paced by default. No customer audio or phone numbers are sent by this evaluator.

Results, actual spoken text, proposed JSON, validation outcomes, latency and token counts are in [conversation_style_evaluation.json](conversation_style_evaluation.json). The `--journey` option can run a selected scenario without repeating the whole evaluation.

Final results: 76 offline tests ran (75 passed, one platform-specific skip). All 27 live synthetic turns matched their expected actions/commit outcomes, with zero false commits, zero unexpected answer changes, and zero failures of the targeted wording checks. Median model-decision latency was 1,187 ms; usage was 125,342 prompt tokens and 1,425 completion tokens for the final run. Earlier iterative trials consumed additional tokens and are not included in these final-run totals. Latency excludes STT/TTS and is not an end-to-end voice latency measurement.

Quality checks cover specific defects: pushing consent/current question during concern handling, failing to confirm a single endpoint, administrative acknowledgement phrases, and duplicate transition questions. These checks are limited heuristics, not an objective measure of natural conversation; human review of the recorded replies is still needed. Earlier tuning passes exposed continued consent pushing, formal acknowledgements, and one repeated-scale response. Those findings drove the final prompt and rendering changes.

## Try it in the chat lab

After updating the files, stop and restart the standalone lab:

```bash
uv run --frozen python survey_chat.py
```

Open http://127.0.0.1:7862 and click **New conversation**. Restarting reloads the Python prompt; a fresh conversation removes the previous robotic replies from recent history.

For a Docker lab on Ubuntu, stop the old lab process/container, rebuild the image, and launch it again from the application checkout:

```bash
docker compose -f docker-compose.ubuntu.yml build agent
docker compose -f docker-compose.ubuntu.yml run --rm --no-deps agent python survey_chat.py
```

This does not recreate the running phone agent. Once the wording is satisfactory, rebuild/recreate that agent to apply the same changes to voice calls:

```bash
docker compose -f docker-compose.ubuntu.yml up -d --build agent
```

No automatic deployment or lab restart was performed by this change.

## Remaining limits

The actual service name and provider are still unspecified. The agent can acknowledge that honestly but cannot identify the service until the questionnaire's authorized information is populated with that fact. It also cannot establish who authorized contact or where a number originated without verified information supplied by the operator.

The model may reuse examples or occasionally produce awkward wording. Naturalness is not guaranteed by a finite test suite, and these results do not measure phone pronunciation, STT errors, VAD, or audible interruption quality. The next iteration should use exported chat traces showing actual/desired replies. Existing answer guards remain necessary because semantic relevance still depends on the LLM.
