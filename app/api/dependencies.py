import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


bearer = HTTPBearer(auto_error=False)


def authorize(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    supplied = credentials.credentials.encode("utf-8") if credentials else b""
    if not secrets.compare_digest(request.app.state.settings.token, supplied):
        raise HTTPException(401, "Invalid or missing Bearer token.", headers={"WWW-Authenticate": "Bearer"})
