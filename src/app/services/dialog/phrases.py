"""What Мила says when something goes wrong, per language.

Data, not subclasses. The dialogue *skeleton* is a Template Method because its
steps genuinely differ between flows; the wording differs only by language, and
a class per locale would be ceremony around a dictionary.

Every entry is a sentence a person would say. "Error 503" is not one, and
neither is silence — a guest on a phone call cannot see a spinner, so a failure
that produces no words is indistinguishable from the agent having hung up.
"""

from typing import Final

# Keyed by our own error_code values, so a new failure mode without a phrase is
# a visible KeyError in tests rather than a mute agent in production.
ERROR_PHRASES: Final[dict[str, dict[str, str]]] = {
    "ru": {
        "core_unavailable": (
            "Не могу сейчас заглянуть в журнал бронирований — похоже, система недоступна. "
            "Давайте я запишу ваш номер, и мы перезвоним?"
        ),
        "core_rate_limited": "Журнал бронирований сейчас перегружен. Секунду, попробую ещё раз.",
        "slot_unavailable": "Извините, этот столик только что заняли. Посмотрю, что есть рядом по времени.",
        "slot_capacity_exceeded": "За этот столик столько гостей не поместится. Подберу побольше.",
        "entity_not_found": "Не нахожу брони на это имя и число. Может быть, она на другой день?",
        "unknown_reference": "Кажется, я потеряла нить. Давайте уточним: на какое число и на сколько человек?",
        "booking_error": "Что-то пошло не так с бронированием. Давайте попробуем ещё раз.",
        "not_confirmed": "Поняла, ничего не бронирую. Скажите, когда будете готовы подтвердить.",
        "no_slots": "На это число свободных столиков нет. Посмотреть соседние дни?",
        "invalid_arguments": "Я не расслышала. Повторите, пожалуйста, дату, время и количество гостей.",
        "party_too_large": "Такую большую компанию я по телефону не оформлю — соединю вас с администратором.",
        "date_in_past": "Эта дата уже прошла. На какой день оформляем бронь?",
        "date_too_far": "Так далеко вперёд мы пока не бронируем.",
    },
    "en": {
        "core_unavailable": (
            "I can't reach the reservations diary right now — the system seems to be down. "
            "Shall I take your number and call you back?"
        ),
        "core_rate_limited": "The reservations diary is busy at the moment. One second, let me try again.",
        "slot_unavailable": "Sorry, that table has just been taken. Let me see what's free around that time.",
        "slot_capacity_exceeded": "That table won't seat your party. Let me find a bigger one.",
        "entity_not_found": "I can't find a booking under that name for that date. Might it be another day?",
        "unknown_reference": "I've lost the thread there. Which date, and for how many people?",
        "booking_error": "Something went wrong with the booking. Let's try that again.",
        "not_confirmed": "Understood, I won't book anything. Just say the word when you're ready.",
        "no_slots": "There's nothing free that day. Shall I check the days either side?",
        "invalid_arguments": "I didn't catch that. Could you repeat the date, the time and the number of guests?",
        "party_too_large": "A party that size needs a person — let me put you through to the manager.",
        "date_in_past": "That date has already passed. Which day did you have in mind?",
        "date_too_far": "We're not taking bookings that far ahead yet.",
    },
}


def phrase(language: str, code: str) -> str:
    """Look up a sentence, falling back to English rather than to nothing."""
    table = ERROR_PHRASES.get(language) or ERROR_PHRASES["en"]
    return table.get(code) or ERROR_PHRASES["en"].get(code, ERROR_PHRASES["en"]["booking_error"])
