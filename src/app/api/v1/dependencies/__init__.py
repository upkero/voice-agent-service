from typing import Annotated

from fastapi import Depends, Request

from src.app.bootstrap.container import ApplicationContainer
from src.app.services.token.access_token_service import AccessTokenService


def get_container(request: Request) -> ApplicationContainer:
    return request.app.state.container  # type: ignore[no-any-return]


def get_access_token_service(request: Request) -> AccessTokenService:
    """Resolved through the container rather than constructed per request.

    Routers depend on this, never on app.state directly, so the tests can
    override one dependency instead of assembling a whole container.
    """
    return get_container(request).access_token_service


ContainerDep = Annotated[ApplicationContainer, Depends(get_container)]
AccessTokenServiceDep = Annotated[AccessTokenService, Depends(get_access_token_service)]
