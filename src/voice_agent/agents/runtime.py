"""Build one isolated runtime before allocating voice services or speaking."""
import os
from dataclasses import dataclass
from functools import lru_cache
from uuid import uuid4
from voice_agent.agents.models import SessionSelection
from voice_agent.agents.registry import AgentRegistry
from voice_agent.context.loader import ContextLoader
from voice_agent.conversation import ConversationManager, Questionnaire
from voice_agent.paths import QUESTIONNAIRES


@lru_cache(maxsize=1)
def registry():
    return AgentRegistry()


@dataclass
class AgentRuntime:
    manager: ConversationManager
    agent: object = None
    context: object = None
    repository: object = None

    @property
    def config(self):
        return self.agent.config if self.agent else None

    def public(self):
        return dict(agent_id=self.manager.agent_id,
                    agent_name=self.config.name if self.config else "Configured survey",
                    survey_id=self.manager.survey_id, language=self.manager.language,
                    workflow_type="survey", demo=bool(self.context and self.context.demo))

    def decision_client(self, client, model):
        from voice_agent.providers.llm import create_decision_client
        if self.config is None:
            return create_decision_client(client, model)
        from voice_agent.tools.registry import default_tools
        from voice_agent.tools.executor import ToolExecutor
        executor = None
        if self.config and self.config.tools:
            executor = ToolExecutor(default_tools(self.repository), agent_id=self.config.agent_id,
                allowed=self.config.tools, context=self.context)
        return create_decision_client(client, model, executor=executor,
                                     is_current=lambda version: self.manager.version == version)


def legacy_surveys():
    return {p.stem: p for p in sorted(QUESTIONNAIRES.glob("*.json"))}


def create_runtime(selection=None, *, session_id=None, questionnaire_path=None,
                   agents=None, loader=None):
    selection = selection or SessionSelection()
    session_id = session_id or str(uuid4())
    agent_id = selection.agent_id or os.getenv("AGENT_ID") or None
    if agent_id is None:
        if selection.mock_customer_id:
            raise ValueError("mock customer selection requires an agent")
        path = questionnaire_path or os.getenv(
            "SURVEY_QUESTIONNAIRE", "questionnaires/customer-satisfaction.ar.json")
        if selection.survey_id:
            try:
                path = legacy_surveys()[selection.survey_id]
            except KeyError:
                raise ValueError("unknown survey ID") from None
        q = Questionnaire.load(path)
        return AgentRuntime(ConversationManager(q, session_id=session_id,
            language=os.getenv("DEFAULT_LANGUAGE", "ar"), survey_id=selection.survey_id))
    registered = (agents or registry()).get(agent_id)
    config = registered.config
    survey_id = selection.survey_id or config.survey_id
    if survey_id not in registered.surveys:
        raise ValueError("survey is not allowed for this agent")
    loader = loader or ContextLoader()
    context = loader.load(config, session_id, selection.mock_customer_id)
    manager = ConversationManager(registered.surveys[survey_id], session_id=session_id,
        language=config.output_language, agent_prompt=registered.prompt, call_context=context,
        agent_id=config.agent_id, survey_id=survey_id, end_call_on_finish=config.end_call_on_finish)
    return AgentRuntime(manager, registered, context, loader.repository)


def catalog():
    loader = ContextLoader()
    return dict(agents=[dict(agent_id=a.config.agent_id, name=a.config.name,
        description=a.config.description, surveys=list(a.config.allowed_surveys),
        customers=loader.repository.choices(a.config.agent_id), language=a.config.language)
        for a in registry().list()], surveys=list(legacy_surveys()),
        default_agent_id=os.getenv("AGENT_ID") or "",
        default_survey_id=None)
