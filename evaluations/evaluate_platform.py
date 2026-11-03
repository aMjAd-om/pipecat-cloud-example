"""Opt-in live synthetic evaluation; default prints the scenario catalog without network I/O."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import time

from evaluations.cases.platform_cases import CASES, JOURNEY, CALLER_QUESTIONS
from voice_agent.agents.models import SessionSelection
from voice_agent.agents.runtime import create_runtime, AgentRuntime
from voice_agent.conversation import Decision, ConversationManager, Questionnaire
from voice_agent.conversation.language import speech_matches_language


async def evaluate(output, interval):
    from openai import AsyncOpenAI
    from voice_agent.config import load_settings
    load_settings(service="llm")
    rows = []
    async with AsyncOpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=os.environ["AZURE_OPENAI_BASE_URL"], timeout=30, max_retries=0) as client:
        async def run(runtime, name, text, expected, decide=None):
            proposal = None
            diagnostic = None
            raw_response = None
            bound = decide or runtime.decision_client(client, os.environ["AZURE_OPENAI_DEPLOYMENT"])
            async def capture(messages):
                nonlocal proposal, diagnostic, raw_response
                try:
                    raw, usage = await bound(messages)
                    raw_response = raw
                    proposal = Decision.model_validate_json(raw).model_dump()
                    return raw, usage
                except Exception as exc:
                    diagnostic = type(exc).__name__
                    # Static provider finish codes are safe; do not log arbitrary API exceptions.
                    if isinstance(exc, ValueError) and str(exc).startswith("decision_finish_"):
                        diagnostic = str(exc)
                    raise
            before = dict(runtime.manager.answers)
            started = time.monotonic()
            result = await runtime.manager.turn(name, text, capture)
            elapsed = round((time.monotonic() - started) * 1000, 2)
            metrics = runtime.manager.metrics[-1]
            raw_no_extra_question = None
            checks = dict(accepted=result.accepted,
                action=bool(proposal and proposal["action"] == expected["action"]),
                intent=(not expected.get("intent") or bool(proposal and proposal["intent"] == expected["intent"])),
                tool_selection=metrics.get("tool_calls", 0) == expected.get("tools", 0),
                context_facts=all(s in result.spoken_response for s in expected.get("contains", [])),
                relevant_meaning=all(any(s in result.spoken_response for s in group)
                                    for group in expected.get("contains_any", [])),
                no_unrelated_facts=all(s not in result.spoken_response for s in expected.get("excludes", [])),
                arabic=speech_matches_language(result.spoken_response, "ar"))
            if expected.get("intent") in ("company", "identity"):
                raw_no_extra_question = bool(proposal and
                    not any(mark in proposal["spoken_response"] for mark in ("?", "؟")))
                checks["no_extra_question"] = not any(mark in result.spoken_response for mark in ("?", "؟"))
                checks["no_automatic_reminder"] = not result.follow_up
            if "value" in expected:
                checks["value"] = bool(proposal and (isinstance(proposal["answer_value"], str)
                    if expected["value"] == "TEXT" else proposal["answer_value"] == expected["value"]))
            if expected["action"] == "stay":
                checks["no_false_commit"] = runtime.manager.answers == before
            rows.append(dict(name=name, customer=text, response=result.spoken_response,
                proposal=proposal, diagnostic=diagnostic, raw_response=raw_response,
                raw_no_extra_question=raw_no_extra_question,
                checks=checks, passed=all(checks.values()), elapsed_ms=elapsed,
                words=len(result.spoken_response.split()), metrics=metrics))
            print(f"{name}: {'PASS' if all(checks.values()) else 'FAIL'} ({elapsed:.0f} ms)", flush=True)
            await asyncio.sleep(interval)

        for case in CASES:
            runtime = create_runtime(SessionSelection(agent_id=case["agent"], mock_customer_id=case["customer"]))
            index = case.get("index", 0)
            if index:
                runtime.manager.answers["consent"] = "نعم"
                runtime.manager.index = index
            if index > 1:
                runtime.manager.answers["overall_rating"] = 3
            await run(runtime, case["name"], case["text"], case)
        for mode in ("registered", "legacy"):
            runtime = (create_runtime(SessionSelection(agent_id="bank_feedback")) if mode == "registered"
                       else AgentRuntime(ConversationManager(Questionnaire.load("questionnaires/bank-nizwa.ar.json"))))
            bound = runtime.decision_client(client, os.environ["AZURE_OPENAI_DEPLOYMENT"])
            for i, case in enumerate(CALLER_QUESTIONS):
                await run(runtime, f"{mode}_caller_sequence_{i}", case["text"], case, bound)
        runtime = create_runtime(SessionSelection(agent_id="bank_feedback", mock_customer_id="demo-ahmed"))
        bound = runtime.decision_client(client, os.environ["AZURE_OPENAI_DEPLOYMENT"])
        for i, (text, value) in enumerate(JOURNEY):
            await run(runtime, f"journey_{i}", text,
                      dict(action="next_question", intent="answer", value=value, tools=0), bound)
            if not rows[-1]["passed"]:
                break  # Never feed later answers to the wrong active question.
        journey_completed = runtime.manager.completed
    times = [r["elapsed_ms"] for r in rows]
    report = dict(mode="live_synthetic", model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        total=len(rows), passed=sum(r["passed"] for r in rows), journey_completed=journey_completed,
        latency_p50_ms=statistics.median(times), latency_max_ms=max(times), rows=rows,
        human_review_required=["Natural Omani Arabic", "Conciseness appropriate to the question",
                               "Semantic relevance and context accuracy beyond keyword checks"],
        limits="Text/model path only; excludes real STT, TTS, telephone and WebRTC network latency.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Results: {report['passed']}/{report['total']}; full journey: {journey_completed}")
    return report["passed"] == report["total"] and journey_completed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--interval", type=float, default=3)
    parser.add_argument("--output", type=Path, default=Path("evaluations/results/platform_evaluation.json"))
    args = parser.parse_args()
    if not args.live:
        print(json.dumps(CASES, ensure_ascii=True, indent=2))
        return
    if args.interval < 0:
        parser.error("interval must be nonnegative")
    raise SystemExit(0 if asyncio.run(evaluate(args.output, args.interval)) else 1)


if __name__ == "__main__":
    main()
