"""FastAPI 入口。健康检查按技术文档 14.3：演示/答辩前 10 分钟必须打一次。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.routes import router
from app.auth.routes import Credentials, protect_business
from app.auth.routes import router as auth_router
from app.auth.store import AuthError, AuthStore
from app.config import Settings, get_settings
from app.providers.routes import router as provider_router
from app.providers.runtime import ModelRuntime
from app.providers.store import ProviderConfigurationError, ProviderStore

CORE_SCHEMA_FILES = [
    "plan_graph.schema.json",
    "intent.schema.json",
    "skeleton.schema.json",
    "examples/parse.json",
]


def check_core_resources(settings: Settings) -> list[str]:
    """启动即校验核心资源；缺失直接拒启（审计 P1-4：镜像漏打 schema 必须第一时间暴露）。"""
    schema_dir = Path(settings.schema_dir)
    return [f for f in CORE_SCHEMA_FILES if not (schema_dir / f).exists()]


def create_app() -> FastAPI:
    settings = get_settings()
    missing = check_core_resources(settings)
    if missing:
        raise RuntimeError(
            f"核心资源缺失: {missing}（schema 目录={settings.schema_dir}）。"
            "检查镜像是否打包了 schema/（见 server/Dockerfile）"
        )

    model_runtime = ModelRuntime()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await model_runtime.aclose()

    app = FastAPI(title="Suibian API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.auth_store = AuthStore(settings.state_dir / "auth.sqlite3")
    if settings.auth_admin_username or settings.auth_admin_password:
        try:
            credentials = Credentials(username=settings.auth_admin_username, password=settings.auth_admin_password)
        except ValidationError:
            raise RuntimeError("请同时配置有效的管理员用户名和至少 12 位密码") from None
        app.state.auth_store.bootstrap_admin(credentials.username, credentials.password.get_secret_value())
    app.state.provider_store = ProviderStore(settings)
    app.state.model_runtime = model_runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/v1", dependencies=[Depends(protect_business)])
    app.include_router(auth_router, prefix="/v1")
    app.include_router(provider_router, prefix="/v1")

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, error: AuthError):
        headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else {}
        if error.status == 429:
            headers["Retry-After"] = "900"
        return JSONResponse(status_code=error.status, content={"detail": str(error)}, headers=headers)

    @app.exception_handler(ProviderConfigurationError)
    async def provider_error(request: Request, error: ProviderConfigurationError):
        return JSONResponse(status_code=503, content={"detail": str(error)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Pydantic 的默认错误包含原始 input，凭据入口不能回显密码或 Key。
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]})

    @app.get("/v1/health")
    async def health() -> dict:
        """依次探测：schema 资源 → 配置完整性 →（非 mock 时）Redis。
        大模型/高德/和风的真实探测在 W1 接网关时补齐（技术文档 14.3），演示脚本会调用本接口自检。"""
        checks: dict[str, str] = {}
        checks["schema"] = "ok" if not check_core_resources(settings) else "missing"
        if settings.mock:
            checks["mode"] = "mock"
        else:
            effective = app.state.provider_store.effective_settings()
            missing_keys = [
                name
                for name, val in [
                    ("llm_key", effective.qwen_key or effective.ds_key),
                    ("amap_web_key", effective.amap_web_key),
                    ("qw_private_key", effective.qw_private_key),
                ]
                if not val
            ]
            checks["config"] = "ok" if not missing_keys else f"missing:{','.join(missing_keys)}"
            # TODO(W1): Redis ping；大模型主备各发 1 token 请求；高德地理编码固定地址；
            # 和风实况一次。返回每项状态和耗时。
        return {"status": "ok", "version": app.version, "checks": checks}

    return app


app = create_app()
