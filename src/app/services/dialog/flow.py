"""The dialogue skeleton.

Template Method: `system_prompt()` fixes the order the instructions are
assembled in — who she is, what she can do, the rules she works under, the
facts of today, and how to speak — while each step is overridable on its own.

The order is the part worth protecting. Persona before rules and rules before
context is what keeps a later edit from burying "never confirm a table the
guest did not agree to" under three paragraphs of restaurant trivia, where
models reliably stop honouring it. A subclass can rewrite any single step
without being able to reshuffle them.

Each step now returns the text of a prompt in `prompts/` rather than an
f-string. The skeleton is the code; the wording is data, and a subclass that
wants different words points a step at a different file.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import date

from src.app.core.settings.agent import AgentSettings
from src.app.messages import greeting_line
from src.app.prompts import get_prompt


class BaseDialogFlow(ABC):
    def __init__(self, settings: AgentSettings, *, today: Callable[[], date] = date.today) -> None:
        self._settings = settings
        self._today = today

    @property
    def settings(self) -> AgentSettings:
        return self._settings

    # --- the skeleton: fixed order, not overridable ---
    def system_prompt(self) -> str:
        sections = (
            self.persona(),
            self.capabilities(),
            self.tool_policy(),
            self.context(),
            self.speech_policy(),
        )
        return "\n\n".join(section.strip() for section in sections if section.strip())

    # --- the steps ---
    @abstractmethod
    def persona(self) -> str: ...

    @abstractmethod
    def capabilities(self) -> str: ...

    @abstractmethod
    def tool_policy(self) -> str: ...

    @abstractmethod
    def greeting(self) -> str: ...

    @abstractmethod
    def greeting_text(self) -> str:
        """The opening line, word for word. Fixed text so its audio can be made ahead of the call."""

    def context(self) -> str:
        """Facts that change between calls.

        Today's date is here because a model cannot know it and a guest will
        say "tomorrow" within the first ten seconds. Giving it once, plainly,
        is what turns relative dates into an arithmetic problem rather than a
        guess.
        """
        return get_prompt("context").render(today=self._today().isoformat())

    def speech_policy(self) -> str:
        """How the words should come out of a speaker rather than onto a screen."""
        return get_prompt("speech_policy").text


class RestaurantBookingFlow(BaseDialogFlow):
    """Мила, taking table reservations for one restaurant."""

    _LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}

    @property
    def _reply_language(self) -> str:
        return self._LANGUAGE_NAMES.get(self._settings.language, "English")

    def persona(self) -> str:
        return get_prompt("persona").render(
            agent_name=self._settings.display_name,
            venue_name=self._settings.venue_name,
            reply_language=self._reply_language,
        )

    def capabilities(self) -> str:
        return get_prompt("capabilities").text

    def tool_policy(self) -> str:
        """The rules that keep tool calls deterministic.

        Written as instructions about *when* to call, not *how*: the schemas
        already constrain the arguments, and repeating that here would give the
        model two sources of truth to disagree with.
        """
        return get_prompt("tool_policy").text

    def greeting(self) -> str:
        """One prompt, in English, with the reply language as a placeholder.

        It used to be an if on `language` with a Russian branch and an English
        one, which is two prompts to keep in step and the wrong shape besides:
        the instruction is to the model, and only the guest's half of the call
        is in Russian. `persona` had this right already.
        """
        return get_prompt("greeting").render(
            agent_name=self._settings.display_name,
            venue_name=self._settings.venue_name,
            reply_language=self._reply_language,
        )

    def greeting_text(self) -> str:
        return greeting_line(self._settings.language, self._settings.display_name, self._settings.venue_name)
