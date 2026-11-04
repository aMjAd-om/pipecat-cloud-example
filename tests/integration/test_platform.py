"""Session selection through real HTTP/WebSocket boundaries, with no provider calls."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.integration.test_telephony import start_event
from tests.unit.test_survey import decision
from voice_agent.agents.models import SessionSelection
from voice_agent.agents.runtime import create_runtime
from voice_agent.api.survey_chat import create_app as chat_app
from voice_agent.api.telephony import create_telephony_app
from voice_agent.tools.registry import default_tools


class PlatformChatTests(unittest.TestCase):
    def test_two_agents_context_state_and_survey_permissions(self):
        seen = []
        async def decide(messages):
            seen.append(messages)
            return decision('consent', intent='company', action='stay', answer_status='not_answered',
                            answer_value=None, spoken_response='من شركة داتامايننج.'), {}
        with TestClient(chat_app(decide=decide), base_url='http://127.0.0.1:7862',
                        client=('127.0.0.1', 50000)) as client:
            headers = {'X-Survey-Chat-Token': client.get('/api/settings').json()['token']}
            choices = client.get('/api/agents').json()
            self.assertEqual(len(choices['agents']), 2)
            states = []
            for agent_id, customer in [('bank_feedback', 'demo-ahmed'), ('municipality_complaints', 'demo-municipality')]:
                response = client.post('/api/sessions', headers=headers,
                    json=dict(agent_id=agent_id, mock_customer_id=customer))
                self.assertEqual(response.status_code, 200)
                state = response.json()['state']
                states.append(state)
                result = client.post(f"/api/sessions/{state['session_id']}/turn", headers=headers,
                    json=dict(text='من أي شركة؟', turn_id='1', expected_version=state['version'])).json()
                self.assertFalse(result['state']['answers'])
                self.assertEqual(result['state']['selection']['agent_id'], agent_id)
            self.assertNotEqual(states[0]['session_id'], states[1]['session_id'])
            self.assertIn('الخوير', seen[0][3]['content'])
            self.assertNotIn('الخوير', json.dumps(seen[1], ensure_ascii=False))
            for payload in [dict(agent_id='unknown'), dict(agent_id='bank_feedback', survey_id='example'),
                            dict(agent_id='municipality_complaints', mock_customer_id='demo-ahmed')]:
                self.assertEqual(client.post('/api/sessions', headers=headers, json=payload).status_code, 400)

    def test_municipality_has_no_tools_and_bank_has_only_visits(self):
        bank = create_runtime(SessionSelection(agent_id='bank_feedback'))
        municipality = create_runtime(SessionSelection(agent_id='municipality_complaints'))
        self.assertEqual(len(default_tools().definitions(bank.config.tools)), 1)
        self.assertEqual(default_tools().definitions(municipality.config.tools), [])
        self.assertNotIn('بنك نزوى', municipality.manager.agent_prompt)

    def test_telephony_uses_operator_route_and_ignores_wire_customer_selection(self):
        seen = []
        async def pipeline(_connection, *, stop_event, transport, runtime):
            seen.append(runtime)
            await transport.peer.command('GET_STATUS')
        with tempfile.TemporaryDirectory() as directory:
            route = Path(directory) / 'calls.json'
            route.write_text(json.dumps({'trusted-channel': dict(agent_id='bank_feedback',
                              mock_customer_id='demo-ahmed')}), encoding='utf-8')
            with patch.dict(os.environ, {'TELEPHONY_CALL_CONFIG': str(route), 'DEMO_TESTING_ENABLED': 'true'}):
                app = create_telephony_app(pipeline=pipeline)
                with TestClient(app, client=('127.0.0.1', 50000)) as client:
                    with client.websocket_connect('/telephony/media', subprotocols=['media']) as ws:
                        ws.send_json({**start_event('trusted-channel'), 'agent_id': 'municipality_complaints',
                                      'mock_customer_id': 'demo-sara'})
                        self.assertEqual(ws.receive_json(), {'command': 'GET_STATUS'})
                        with self.assertRaises(WebSocketDisconnect):
                            ws.receive_bytes()
                self.assertEqual(seen[0].context.customer.name, 'أحمد')
                self.assertEqual(seen[0].manager.session_id, 'trusted-channel')
