"""应用自身账号接口；供应商凭据不参与用户登录。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr

router = APIRouter(prefix="/auth", tags=["账户"])
security = HTTPBearer(auto_error=False)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,32}$", strict=True)
    password: SecretStr = Field(min_length=12, max_length=128)


def require_user(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]):
    if not credentials:
        raise HTTPException(401, "请登录或重新登录", headers={"WWW-Authenticate": "Bearer"})
    user = request.app.state.auth_store.authenticate(credentials.credentials)
    request.state.user = user
    return user


def require_admin(user: Annotated[dict, Depends(require_user)]):
    if user["role"] != "admin":
        raise HTTPException(403, "仅管理员可配置团队凭据")
    return user


def protect_business(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]):
    if request.url.path == "/v1/version" or not request.app.state.settings.auth_required:
        return
    require_user(request, credentials)


@router.post("/register", status_code=201)
def register(payload: Credentials, request: Request):
    store = request.app.state.auth_store
    store.throttle(f"register:{request.client.host if request.client else 'unknown'}", 5)
    return store.register(payload.username, payload.password.get_secret_value())


@router.post("/login")
def login(payload: Credentials, request: Request):
    store = request.app.state.auth_store
    store.throttle(f"login-ip:{request.client.host if request.client else 'unknown'}", 20)
    store.throttle(f"login-name:{payload.username.lower()}", 10)
    return store.login(payload.username, payload.password.get_secret_value())


@router.get("/me")
def me(user: Annotated[dict, Depends(require_user)]):
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
           user: Annotated[dict, Depends(require_user)]):
    request.app.state.auth_store.logout(credentials.credentials)
    return Response(status_code=204)
