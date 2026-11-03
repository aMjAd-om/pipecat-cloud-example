"""Preload trusted agent files; client selections never resolve filesystem paths."""
from dataclasses import dataclass
from pathlib import Path
from voice_agent.agents.models import AgentConfig
from voice_agent.conversation.models import Questionnaire
from voice_agent.paths import QUESTIONNAIRES


@dataclass(frozen=True)
class RegisteredAgent:
    config: AgentConfig
    prompt: str
    surveys: dict


class AgentRegistry:
    def __init__(self, root=None, *, tools=None, surveys_root=None):
        if tools is None:
            from voice_agent.tools.registry import default_tools
            tools = default_tools().names
        root = Path(root or Path(__file__).parent).resolve()
        surveys_root = Path(surveys_root or QUESTIONNAIRES).resolve()
        self._agents = {}
        for path in sorted(root.glob("*/agent.json")):
            config = AgentConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
            if config.agent_id != path.parent.name or config.agent_id in self._agents:
                raise ValueError("agent ID must match its unique directory")
            if not set(config.tools) <= set(tools):
                raise ValueError("agent references an unknown tool")
            prompt_path = (path.parent / config.prompt_file).resolve()
            if not prompt_path.is_relative_to(path.parent.resolve()):
                raise ValueError("agent prompt must stay inside its directory")
            prompt = prompt_path.read_text(encoding="utf-8-sig").strip()
            if not prompt or len(prompt) > 12000:
                raise ValueError("agent prompt is empty or too large")
            surveys = {}
            for survey_id in config.allowed_surveys:
                survey_path = (surveys_root / f"{survey_id}.json").resolve()
                if not survey_path.is_relative_to(surveys_root):
                    raise ValueError("invalid survey ID")
                surveys[survey_id] = Questionnaire.load(survey_path)
            self._agents[config.agent_id] = RegisteredAgent(config, prompt, surveys)

    def get(self, agent_id):
        try:
            return self._agents[agent_id]
        except KeyError:
            raise ValueError("unknown agent ID") from None

    def list(self):
        return tuple(self._agents.values())
