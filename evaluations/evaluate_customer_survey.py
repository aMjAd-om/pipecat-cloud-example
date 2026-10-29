"""Opt-in synthetic Azure evaluation of the actual customer-satisfaction survey."""
import argparse
import asyncio
import json
import math
import os
import statistics
from pathlib import Path

from openai import AsyncOpenAI

from voice_agent.config import load_settings
from voice_agent.conversation.language import speech_matches_language
from voice_agent.conversation import Decision
from voice_agent.providers.foundry_llm import FoundryDecisionClient

from evaluations.cases.customer_survey_cases import CASES, JOURNEY, prepared


ENGLISH_JOURNEY = [
    ("Yes, please begin", "next_question", "نعم"),
    ("Four", "next_question", 4),
    ("Because the staff were helpful", "next_question", "TEXT"),
    ("Three", "next_question", 3),
    ("Good", "next_question", "جيدة"),
    ("Yes", "stay", None),
    ("The application website kept crashing", "next_question", "TEXT"),
    ("Shorter waiting times", "next_question", "TEXT"),
    ("Maybe", "next_question", "ربما"),
    ("No further comments", "next_question", "TEXT"),
]


async def evaluate(output, interval=3.0):
    load_settings(service="llm")
    rows = []
    semaphore = asyncio.Semaphore(1)
    async with AsyncOpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=os.environ["AZURE_OPENAI_BASE_URL"], timeout=30, max_retries=0) as client:
        model = FoundryDecisionClient(client, os.environ["AZURE_OPENAI_DEPLOYMENT"])

        async def turn(m, name, text, action, value):
            proposal, error_type = None, None
            before, completed = m.question.id, m.completed
            async def capture(messages):
                nonlocal proposal, error_type
                try:
                    raw, usage = await model(messages)
                    proposal = Decision.model_validate_json(raw).model_dump()
                    return raw, usage
                except Exception as exc:
                    error_type = type(exc).__name__
                    raise
            async with semaphore:
                result = await m.turn(name, text, capture)
                # Pace the evaluator, not production calls, to respect Azure quotas.
                await asyncio.sleep(interval)
            expects_commit = value is not None
            actual = proposal["answer_value"] if proposal else None
            answer_ok = (isinstance(actual, str) and bool(actual.strip())) if value == "TEXT" else actual == value
            false_commit = result.committed and (not expects_commit or not answer_ok)
            advanced = m.question.id != before or (m.completed and not completed)
            row = dict(case_id=name, question_id=before, utterance=text,
                expected_action=action, expected_value=value, proposed=proposal,
                accepted=result.accepted, committed=result.committed, reason=result.reason,
                spoken_response=result.spoken_response,
                decision_correct=bool(proposal and proposal["action"] == action and
                    result.accepted and result.committed == expects_commit and
                    (not expects_commit or answer_ok)),
                false_commit=false_commit,
                false_advance=advanced and action not in ("next_question", "skip"),
                delivered_arabic=speech_matches_language(result.spoken_response, "ar"),
                model_arabic=bool(proposal and speech_matches_language(proposal["spoken_response"], "ar")),
                error_type=error_type, metrics=dict(m.metrics[-1]) if m.metrics else {})
            rows.append(row)
            if len(rows) % 10 == 0:
                print(f"Evaluated {len(rows)} synthetic turns", flush=True)
            return row

        async def single(case):
            name, index, text, action, value = case
            await turn(prepared(index), name, text, action, value)

        async def journey(name, turns):
            m = prepared()
            for i, (text, action, value) in enumerate(turns):
                row = await turn(m, f"{name}_{i:02d}", text, action, value)
                if not row["decision_correct"]:
                    # Do not feed later questions' inputs to an earlier active
                    # question and misreport legitimate commits as false commits.
                    break

        await asyncio.gather(*(single(c) for c in CASES), journey("arabic_journey", JOURNEY),
                             journey("english_input_journey", ENGLISH_JOURNEY))
    rows.sort(key=lambda r: r["case_id"])
    times = sorted(r["metrics"]["latency_ms"] for r in rows if r["metrics"])
    summary = dict(planned_cases=len(CASES) + len(JOURNEY) + len(ENGLISH_JOURNEY),
        cases=len(rows), correct=sum(r["decision_correct"] for r in rows),
        false_commits=sum(r["false_commit"] for r in rows),
        false_advances=sum(r["false_advance"] for r in rows),
        delivered_arabic=sum(r["delivered_arabic"] for r in rows),
        model_arabic=sum(r["model_arabic"] for r in rows),
        model_errors=sum(r["error_type"] is not None for r in rows),
        latency_p50_ms=statistics.median(times),
        latency_p95_ms=times[math.ceil(.95 * len(times)) - 1],
        prompt_tokens=sum(r["metrics"].get("prompt_tokens", 0) for r in rows),
        completion_tokens=sum(r["metrics"].get("completion_tokens", 0) for r in rows))
    Path(output).write_text(json.dumps(dict(summary=summary, cases=rows), ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(json.dumps(summary))
    return int(summary["cases"] != summary["planned_cases"] or summary["correct"] != len(rows) or summary["delivered_arabic"] != len(rows)
               or summary["false_commits"] != 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default="evaluations/results/customer_survey_evaluation.json")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between evaluation requests")
    args = parser.parse_args()
    if not args.live:
        parser.error("Specify --live to send only the synthetic cases to Azure.")
    raise SystemExit(asyncio.run(evaluate(args.output, max(0, args.interval))))
