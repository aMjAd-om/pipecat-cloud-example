"""Opt-in Azure evaluation using ONLY the included synthetic customer utterances.

Run: python evaluate_survey.py --live --output survey_evaluation.json
The artifact contains synthetic responses for review, never production dialogue.
"""
import argparse
import asyncio
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path

from openai import AsyncOpenAI

from config import load_settings
from survey import ConversationManager, Decision, Questionnaire
from survey_voice import FoundryDecisionClient

sys.path.insert(0, str(Path(__file__).parent / "tests"))
from survey_cases import CASES


# Each sequence shares real state and history, without model-created setup.
# Tuple: utterance, expected action, expected committed value (None = no commit).
SCENARIOS = {
    "return_after_off_topic": [
        ("How are you?", "stay", None), ("My rating is four", "next_question", 4)],
    "no_carryover": [
        ("4", "next_question", 4), ("Yes", "stay", None)],
    "pause_resume": [
        ("Pause the survey", "pause", None), ("4", "stay", None),
        ("Resume the survey", "resume", None), ("three", "next_question", 3)],
    "clarification": [
        ("Three or four, I'm not sure", "stay", None),
        ("Four is my final rating", "next_question", 4)],
    "correction": [
        ("4", "next_question", 4), ("Change my previous rating", "repeat", None),
        ("three", "next_question", 3),
        ("The staff need more training", "next_question", "text")],
}


def new_manager():
    return ConversationManager(Questionnaire.load("questionnaire.example.json"))


async def evaluate(output):
    load_settings(service="llm")
    semaphore = asyncio.Semaphore(4)
    rows = []
    async with AsyncOpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=os.environ["AZURE_OPENAI_BASE_URL"], timeout=30, max_retries=0) as client:
        real_model = FoundryDecisionClient(client, os.environ["AZURE_OPENAI_DEPLOYMENT"])

        async def run_turn(m, case_id, utterance, expected, expected_value, expected_decision=None):
            proposed = None
            error_type = None
            previous_question, previously_completed = m.question.id, m.completed
            async def capture(messages):
                nonlocal proposed, error_type
                try:
                    raw, usage = await real_model(messages)
                    proposed = Decision.model_validate_json(raw).model_dump()
                    return raw, usage
                except Exception as exc:
                    error_type = type(exc).__name__  # No endpoint/credential errors in output.
                    raise
            async with semaphore:
                result = await m.turn(case_id, utterance, capture)
            wants_commit = expected_value is not None
            value_ok = not wants_commit or (
                result.committed and (isinstance(proposed["answer_value"], str)
                if expected_value == "text" else proposed["answer_value"] == expected_value))
            proposal_ok = proposed is not None and proposed["action"] == expected
            if expected_decision and proposed:
                proposal_ok = proposal_ok and all(proposed[k] == v for k, v in expected_decision.items())
            advanced = ((m.question.id != previous_question and expected != "repeat") or
                        (m.completed and not previously_completed))
            advance_allowed = ((expected == "next_question" and wants_commit and value_ok) or
                               expected == "skip")
            row = dict(case_id=case_id, utterance=utterance, expected_action=expected,
                       expected_value=expected_value, proposed=proposed,
                       accepted=result.accepted, committed=result.committed,
                       reason=result.reason, spoken_response=result.spoken_response,
                       decision_correct=proposal_ok and value_ok and result.committed == wants_commit,
                       false_commit=result.committed and (not wants_commit or not value_ok),
                       false_advance=advanced and not advance_allowed,
                       concise=bool(proposed) and len(proposed["spoken_response"]) <= 300,
                       english_response=(not re.search(r"[\u0600-\u06ff]", proposed["spoken_response"]))
                           if proposed and re.search(r"[A-Za-z]", utterance) and not re.search(r"[\u0600-\u06ff]", utterance)
                           else None,
                       duplicated_question=any(result.spoken_response.count(q.text) > 1
                                               for q in m.questionnaire.questions),
                       transition_question=bool(proposed and proposed["action"] in
                            ("next_question", "repeat", "resume", "skip") and
                            re.search(r"[?؟]", proposed["spoken_response"])),
                       error_type=error_type, metrics=dict(m.metrics[-1]) if m.metrics else {})
            rows.append(row)

        async def single(i, case):
            category, text, intent, status, action, value = case
            m = new_manager()
            if category.startswith("text_"):
                m.index = 1
                m.answers["service_rating"] = 4
            expected_value = ("text" if isinstance(value, str) else value) if status == "valid" else None
            await run_turn(m, f"{i:02d}_{category}", text, action, expected_value,
                           dict(intent=intent, answer_status=status))

        async def sequence(name, turns):
            m = new_manager()
            for i, (text, action, value) in enumerate(turns):
                await run_turn(m, f"{name}_{i}", text, action, value)

        await asyncio.gather(*(single(i, c) for i, c in enumerate(CASES)),
                             *(sequence(n, s) for n, s in SCENARIOS.items()))
    rows.sort(key=lambda r: r["case_id"])
    times = sorted(r["metrics"]["latency_ms"] for r in rows if "latency_ms" in r["metrics"])
    summary = dict(cases=len(rows), correct=sum(r["decision_correct"] for r in rows),
                   false_commits=sum(r["false_commit"] for r in rows),
                   false_advances=sum(r["false_advance"] for r in rows),
                   model_errors=sum(r["error_type"] is not None for r in rows),
                   concise_responses=sum(r["concise"] for r in rows),
                   english_response_correct=sum(r["english_response"] is True for r in rows),
                   english_response_cases=sum(r["english_response"] is not None for r in rows),
                   duplicated_questions=sum(r["duplicated_question"] for r in rows),
                   transition_questions_in_model_reply=sum(r["transition_question"] for r in rows),
                   latency_p50_ms=statistics.median(times),
                   latency_p95_ms=times[math.ceil(.95 * len(times)) - 1],
                   prompt_tokens=sum(r["metrics"].get("prompt_tokens", 0) for r in rows),
                   completion_tokens=sum(r["metrics"].get("completion_tokens", 0) for r in rows),
                   naturalness="Manual review required; concision is only a proxy.")
    Path(output).write_text(json.dumps(dict(summary=summary, cases=rows), ensure_ascii=False,
                                      indent=2), encoding="utf-8")
    print(json.dumps(summary))
    return 0 if summary["correct"] == len(rows) and not summary["false_commits"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send synthetic cases to configured Azure deployment")
    parser.add_argument("--output", default="survey_evaluation.json")
    args = parser.parse_args()
    if not args.live:
        parser.error("Specify --live to opt in to Azure requests; offline checks use unittest.")
    raise SystemExit(asyncio.run(evaluate(args.output)))
