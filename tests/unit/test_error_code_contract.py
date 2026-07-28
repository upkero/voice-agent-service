"""The half of the ops-core-api contract that no schema covers.

`_ERROR_CODES` maps ops-core-api's `error_code` strings onto this service's typed
exceptions. Nothing else checks those strings: rename one upstream and the
adapter quietly falls through to "unmapped 4xx", so Мила says "something went
wrong with the booking" instead of "that table was just taken" — with every
test in both repositories still green.

`tests/fixtures/error-codes.json` is a copy of ops-core-api's generated
catalogue, which its own drift test keeps honest against its exception classes.
Refreshing this copy is manual; noticing that it needs refreshing is not.

The catalogue deliberately omits envelope-level codes (`http_error`,
`request_validation_error`, `internal_server_error`) — they are produced by the
exception handlers rather than by the hierarchy, and this adapter never maps them.
"""

import json
from pathlib import Path

from src.app.gateways.core_api_booking import _ERROR_CODES

_CATALOGUE = json.loads((Path(__file__).parents[1] / "fixtures" / "error-codes.json").read_text(encoding="utf-8"))


def test_every_mapped_code_exists_upstream() -> None:
    unknown = sorted(set(_ERROR_CODES) - set(_CATALOGUE))

    assert unknown == [], f"not in ops-core-api's catalogue: {unknown}"


def test_the_status_codes_agree() -> None:
    """A 409 mapped onto a 404 exception would phrase the wrong apology."""
    mismatched = {
        code: (_CATALOGUE[code]["status_code"], exception.status_code)
        for code, exception in _ERROR_CODES.items()
        if _CATALOGUE[code]["status_code"] != exception.status_code
    }

    assert mismatched == {}
