"""FastAPI 入口。健康检查按技术文档 14.3：演示/答辩前 10 分钟必须打一次。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import Settings, get_settings

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

    app = FastAPI(title="Suibian API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # P0 无账号体系；上生产前收敛
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/v1")

    @app.get("/v1/health")
    async def health() -> dict:
        """依次探测：schema 资源 → 配置完整性 →（非 mock 时）Redis。
        大模型/高德/和风的真实探测在 W1 接网关时补齐（技术文档 14.3），演示脚本会调用本接口自检。"""
        checks: dict[str, str] = {}
        checks["schema"] = "ok" if not check_core_resources(settings) else "missing"
        if settings.mock:
            checks["mode"] = "mock"
        else:
            missing_keys = [
                name
                for name, val in [
                    ("qwen_key", settings.qwen_key),
                    ("amap_web_key", settings.amap_web_key),
                    ("qw_private_key", settings.qw_private_key),
                ]
                if not val
            ]
            checks["config"] = "ok" if not missing_keys else f"missing:{','.join(missing_keys)}"
            # TODO(W1): Redis ping；大模型主备各发 1 token 请求；高德地理编码固定地址；
            # 和风实况一次。返回每项状态和耗时。
        return {"status": "ok", "version": app.version, "checks": checks}

    return app


app = create_app()
