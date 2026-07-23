"""The dialogue skeleton.

Template Method: `system_prompt()` fixes the order the instructions are
assembled in — who she is, what she can do, the rules she works under, the
facts of today, and how to speak — while each step is overridable on its own.

The order is the part worth protecting. Persona before rules and rules before
context is what keeps a later edit from burying "never confirm a table the
guest did not agree to" under three paragraphs of restaurant trivia, where
models reliably stop honouring it. A subclass can rewrite any single step
without being able to reshuffle them.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import date

from src.app.core.settings.agent import AgentSettings


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

    def context(self) -> str:
        """Facts that change between calls.

        Today's date is here because a model cannot know it and a guest will
        say "tomorrow" within the first ten seconds. Giving it once, plainly,
        is what turns relative dates into an arithmetic problem rather than a
        guess.
        """
        return f"Today's date is {self._today().isoformat()}."

    def speech_policy(self) -> str:
        """How the words should come out of a speaker rather than onto a screen."""
        return (
            "You are speaking out loud, not writing. Keep replies to one or two short sentences. "
            "Say numbers, dates and times as a person would say them, never as digits with symbols. "
            "Do not use lists, bullet points, markdown or emoji — none of it survives being spoken. "
            "Never read out reference codes, identifiers or anything resembling a code: they are internal. "
            "Refer to a table by its time and to a booking by the name and date it is under."
        )


class RestaurantBookingFlow(BaseDialogFlow):
    """Мила, taking table reservations for one restaurant."""

    _LANGUAGE_NAMES = {"ru": "Russian", "en": "English"}

    def persona(self) -> str:
        language = self._LANGUAGE_NAMES.get(self._settings.language, "English")
        return (
            f"You are {self._settings.name}, the receptionist taking table reservations "
            f"for a restaurant called {self._settings.venue_name}. "
            f"You speak {language} and reply only in {language}, whatever language the guest tries. "
            "You are warm, brief and practical, the way a busy host on the phone is."
        )

    def capabilities(self) -> str:
        return (
            "You can do exactly four things: check which tables are free, book one, "
            "find an existing reservation, and cancel one. "
            "You cannot change a booking — cancel it and make a new one. "
            "You know nothing about the menu, prices, parking or opening hours; "
            "for anything else, offer to pass the guest to a colleague."
        )

    def tool_policy(self) -> str:
        """The rules that keep tool calls deterministic.

        Written as instructions about *when* to call, not *how*: the schemas
        already constrain the arguments, and repeating that here would give the
        model two sources of truth to disagree with.
        """
        return (
            "Rules you must follow:\n"
            "- Never state or imply that a table is available until check_availability has told you so. "
            "Do not guess times, and do not offer a time that was not in the result.\n"
            "- A booking is held under a name. Before you book, you must know it: once the guest has "
            "picked a time, ask whose name the table should be under, unless they have already said. "
            "Never invent a name and never book without one.\n"
            "- Before booking, read the whole reservation back — the date, the time, the number of "
            "guests and the name — and wait for the guest to agree. Only then call create_booking with "
            "confirmed set to true. If they have not agreed, do not call it at all.\n"
            "- Cancelling works the same way: read the booking back, wait for agreement, "
            "then call cancel_booking with confirmed set to true.\n"
            "- If you booked a table earlier in this same conversation and the guest changes their mind, "
            "cancel it using the reference you already have. Do not look it up again.\n"
            "- Use find_booking only for a reservation made on an earlier call, and only when the guest "
            "has given both the name it is under and the date.\n"
            "- When a tool result contains a 'say' field, tell the guest that, in your own voice.\n"
            "- If a tool fails, say what happened in one plain sentence. Never go quiet, and never "
            "claim a table is booked when the tool did not confirm it."
        )

    def greeting(self) -> str:
        if self._settings.language == "ru":
            return (
                f"Поздоровайся коротко: представься как {self._settings.name} из ресторана "
                f"{self._settings.venue_name} и спроси, на какое число и на сколько человек нужен столик."
            )
        return (
            f"Greet the guest briefly: introduce yourself as {self._settings.name} from "
            f"{self._settings.venue_name} and ask which date they would like and for how many people."
        )
