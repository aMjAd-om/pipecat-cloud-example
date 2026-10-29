"""Local text lab exercises the authoritative survey state without Azure audio."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from voice_agent.conversation import ConversationManager, Questionnaire
from voice_agent.api.survey_chat import create_app


def decision(qid="consent", **changes):
    return dict(intent="answer", answer_status="valid", answer_value="نعم",
                action="next_question", spoken_response="شكراً لك.",
                current_question_id=qid, **changes)


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DEFAULT_LANGUAGE": "ar"})
        self.environment.start()
        self.calls = []
        self.proposal = decision()
        async def decide(messages):
            self.calls.append(messages)
            return json.dumps(self.proposal), {"total_tokens": 123}
        self.app = create_app(decide=decide)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:7862", client=("127.0.0.1", 50000))
        self.client.__enter__()
        self.headers = {"X-Survey-Chat-Token": self.client.get("/api/settings").json()["token"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.environment.stop()

    def new(self):
        response = self.client.post("/api/sessions", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def turn(self, state, text, turn_id="turn1"):
        return self.client.post(f"/api/sessions/{state['session_id']}/turn", headers=self.headers,
            json=dict(text=text, turn_id=turn_id, expected_version=state["version"]))

    def test_exact_greeting_and_same_decision_path_as_phone(self):
        opening = self.new()
        self.assertEqual(len(self.calls), 0)
        expected = ConversationManager(Questionnaire.load("questionnaires/customer-satisfaction.ar.json"))
        self.assertEqual(opening["spoken_response"], expected.opening())
        result = self.turn(opening["state"], "نعم").json()
        self.assertEqual(result["state"]["answers"], {"consent": "نعم"})
        self.assertEqual(result["state"]["current_question"]["id"], "overall_rating")
        self.assertEqual(result["spoken_response"], "شكراً لك. " + expected.questionnaire.questions[1].text)
        self.assertTrue(result["validation"]["committed"])
        self.assertEqual(result["proposed_decision"], self.proposal)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["metrics"]["total_tokens"], 123)

    def test_company_reply_returns_separate_delayed_question_without_consent(self):
        state = self.new()["state"]
        self.proposal = {**decision(), "intent": "company", "answer_status": "not_answered",
                         "answer_value": None, "action": "stay",
                         "spoken_response": "من شركة داتامايننج."}
        result = self.turn(state, "من أي شركة قلت لي؟").json()
        self.assertEqual(result["spoken_response"], "من شركة داتامايننج.")
        self.assertIn("هل تسمح لنا نبدأ الاستبيان؟", result["follow_up"])
        self.assertEqual(result["pause_seconds"], 1.0)
        self.assertEqual(result["state"]["answers"], {})
        self.assertEqual(result["state"]["current_question"]["id"], "consent")

    def test_invalid_proposal_does_not_speak_acknowledgement_or_commit(self):
        state = self.new()["state"]
        self.proposal = {**decision(), "current_question_id": "wrong", "spoken_response": "تم تسجيل إجابتك."}
        result = self.turn(state, "نعم").json()
        self.assertEqual(result["state"]["answers"], {})
        self.assertEqual(result["validation"]["reason"], "question_mismatch")
        self.assertNotIn("تم تسجيل", result["spoken_response"])

    def test_two_chats_reset_export_and_duplicate_retries(self):
        a, b = self.new()["state"], self.new()["state"]
        result = self.turn(a, "نعم").json()
        retry = self.turn(result["state"], "نعم").json()
        self.assertEqual(retry["validation"]["reason"], "duplicate")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.app.state.sessions[b["session_id"]].manager.answers, {})
        exported = self.client.get(f"/api/sessions/{a['session_id']}/export").json()
        self.assertEqual(exported["records"][0]["customer"], "نعم")
        self.client.request("DELETE", f"/api/sessions/{a['session_id']}", headers=self.headers, json={})
        self.assertNotIn(a["session_id"], self.app.state.sessions)

    def test_foreign_origin_host_remote_client_and_missing_token_denied(self):
        self.assertEqual(self.client.post("/api/sessions", json={}).status_code, 403)
        self.assertEqual(self.client.get("/api/settings", headers={"Origin": "https://foreign.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/settings", headers={"Host": "foreign.example:7862"}).status_code, 403)
        with TestClient(self.app, base_url="http://127.0.0.1:7862", client=("192.168.1.10", 50000)) as remote:
            self.assertEqual(remote.get("/").status_code, 403)
        self.assertEqual(self.client.get("/").headers["cache-control"], "no-store")

    def test_stale_versions_and_oversized_input_are_rejected_without_model(self):
        state = self.new()["state"]
        self.assertEqual(self.turn({**state, "version": 99}, "نعم").status_code, 409)
        self.assertEqual(self.turn(state, "x" * 2001).status_code, 422)
        self.assertEqual(self.turn(state, " " * 4).status_code, 422)
        self.assertEqual(self.calls, [])

    def test_interruption_invalidates_pending_decision(self):
        state = self.new()["state"]
        entered = asyncio.Event()
        release = asyncio.Event()
        async def slow(messages):
            entered.set()
            await release.wait()
            return json.dumps(decision()), {}
        other = create_app(decide=slow)
        with TestClient(other, base_url="http://127.0.0.1:7862", client=("127.0.0.1", 50000)) as client:
            headers = {"X-Survey-Chat-Token": client.get("/api/settings").json()["token"]}
            state = client.post("/api/sessions", json={}, headers=headers).json()["state"]
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(client.post, f"/api/sessions/{state['session_id']}/turn", headers=headers,
                    json=dict(text="نعم", turn_id="slow", expected_version=0))
                client.portal.call(entered.wait)
                second = client.post(f"/api/sessions/{state['session_id']}/turn", headers=headers,
                    json=dict(text="نعم", turn_id="second", expected_version=0))
                self.assertEqual(second.status_code, 409)
                client.post(f"/api/sessions/{state['session_id']}/interrupt", json={}, headers=headers)
                client.portal.call(release.set)
                result = pending.result(timeout=5).json()
            self.assertEqual(result["validation"]["reason"], "stale")
            self.assertEqual(result["spoken_response"], "")
            self.assertEqual(result["state"]["answers"], {})


if __name__ == "__main__":
    unittest.main()
