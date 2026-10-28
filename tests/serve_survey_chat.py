"""Deterministic local server for the chat browser regression; no Azure calls."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from survey_chat import create_app


async def decide(messages):
    import asyncio
    await asyncio.sleep(.2)
    state = json.loads(messages[1]["content"])
    text = messages[-1]["content"]
    result = dict(intent="off_topic", answer_status="not_answered", answer_value=None,
                  action="stay", spoken_response="خلّنا نرجع للسؤال الحالي.",
                  current_question_id=state["current_question"]["id"])
    if text == "نعم" and (state.get("pending_confirmation") or {}).get("action") == "skip":
        result.update(intent="skip", action="skip", spoken_response="حاضر.")
    elif text == "ما تعاملت مع الموظفين":
        result.update(intent="skip", answer_status="needs_clarification",
                      action="stay", spoken_response="فهمت، نتخطى هالسؤال؟")
    elif text == "كانت الخدمة سريعة":
        result.update(intent="answer", answer_status="valid", answer_value=text,
                      action="next_question", spoken_response="فهمت عليك.")
    elif text == "نعم":
        result.update(intent="answer", answer_status="valid", answer_value="نعم",
                      action="next_question", spoken_response="شكراً لك.")
    elif text == "أربعة":
        result.update(intent="answer", answer_status="valid", answer_value=4,
                      action="next_question", spoken_response="شكراً لك.")
    elif text == "وقف":
        result.update(intent="pause", action="pause", spoken_response="حاضر، خذ راحتك.")
    elif text == "كمل":
        result.update(intent="resume", action="resume", spoken_response="حاضر.")
    return json.dumps(result, ensure_ascii=False), {"total_tokens": 123}


if __name__ == "__main__":
    os.environ["DEFAULT_LANGUAGE"] = "ar"
    port = int(os.getenv("CHAT_TEST_PORT", "17862"))
    uvicorn.run(create_app(decide=decide, port=port), host="127.0.0.1", port=port,
                access_log=False, log_level="warning")
