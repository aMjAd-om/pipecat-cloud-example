"""Compact model output expanded into the unchanged authoritative Decision."""
import json

from voice_agent.conversation.models import Decision


DECISION_PROTOCOL = "compact-v1"
DECISION_FIELDS = {
    "intent": "intent",
    "status": "answer_status",
    "value": "answer_value",
    "action": "action",
    "reply": "spoken_response",
}
_DESCRIPTIONS = {
    "intent": "Classify the latest concern. skip includes offering to skip; company includes "
              "unfamiliarity with the organization; identity identifies the automated speaker.",
    "status": "valid exclusively for answer/next_question. Skip offers need needs_clarification; "
              "confirmed skips, declined skips and other controls need not_answered.",
    "value": "Explicit current answer; use the final corrected number, never infer a score from "
             "sentiment. correct_previous uses an answered question ID. Otherwise null, "
             "including agreement to or refusal of a skip.",
    "action": "Must match intent: skip offer=stay, agreed skip=skip, declined skip=stay; "
              "pause=pause, resume=resume, cancel=finish. Python validates state.",
    "reply": "Brief natural spoken reply without receipt/storage announcements or automatic "
             "gratitude. Only a short bridge when Python supplies the next question.",
}


def decision_schema():
    source = Decision.model_json_schema()["properties"]
    properties = {}
    for short, full in DECISION_FIELDS.items():
        properties[short] = {k: v for k, v in source[full].items()
                             if k not in ("title", "description")}
        properties[short]["description"] = _DESCRIPTIONS[short]
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(DECISION_FIELDS)}


def expand_decision(raw, question_id):
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.keys() != DECISION_FIELDS.keys():
        raise ValueError("invalid_compact_decision_fields")
    # The model cannot choose which question to answer. Bind to the request's
    # snapshot; the manager still checks its version before applying a decision.
    return Decision.model_validate({
        **{full: payload[short] for short, full in DECISION_FIELDS.items()},
        "current_question_id": question_id,
    }).model_dump_json()
