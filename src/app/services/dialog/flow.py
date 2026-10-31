"""The system prompt and the greeting for one call.

The wording lives in `prompts/`; this class fills the placeholders and fixes the
order of the sections.
"""

from collections.abc import Callable
from datetime import date

from src.app.core.settings.agent import AgentSettings
from src.app.messages import greeting_line
from src.app.prompts import get_prompt

# Order matters: persona, then rules, then context. A rule buried under trivia
# ("never confirm a table the guest did not agree to") is one models stop honouring.
_SECTIONS = ("persona", "capabilities", "tool_policy", "context", "speech_policy")
_LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}


class DialogFlow:
    """Мила, taking table reservations for one restaurant."""

    def __init__(self, settings: AgentSettings, *, today: Callable[[], date] | None = None) -> None:
        self._settings = settings
        self._today = today or settings.today

    def _values(self) -> dict[str, str]:
        return {
            "agent_name": self._settings.display_name,
            "venue_name": self._settings.venue_name,
            "reply_language": _LANGUAGE_NAMES.get(self._settings.language, "English"),
        }

    def system_prompt(self) -> str:
        # Today's date is stated because a model cannot know it, and a guest says
        # "tomorrow" within the first ten seconds.
        values = self._values() | {"today": self._today().isoformat()}
        return "\n\n".join(get_prompt(name).render(**values) for name in _SECTIONS)

    def greeting(self) -> str:
        """The instruction for a generated greeting, in English with the reply language as a placeholder."""
        return get_prompt("greeting").render(**self._values())

    def greeting_text(self) -> str:
        """The opening line, word for word. Fixed text so its audio can be made ahead of the call."""
        return greeting_line(self._settings.language, self._settings.display_name, self._settings.venue_name)
