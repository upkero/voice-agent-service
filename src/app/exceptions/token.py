"""Typed failures from minting a join token.

Here rather than beside AccessTokenService for the same reason as every other
exception in this package: an exception is part of a module's public contract,
and a caller should be able to catch it without importing the service that
raises it.
"""

from src.app.exceptions.base import BaseAppException


class InvalidRoomNameError(BaseAppException):
    status_code = 422
    error_code = "invalid_room_name"
    default_detail = "Room name may contain only letters, digits, dashes and underscores."
