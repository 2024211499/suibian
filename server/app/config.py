"""全局配置：全部来自 .env（技术文档 14.2），代码里不写死密钥和模型名。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# schema 目录定位：向上找第一个包含 schema/ 的祖先目录。
# 兼容两种布局——开发仓（suibian/server/app/config.py → suibian）和
# 容器镜像（/srv/app/config.py → /srv，schema 与 app 同级打进镜像）。
def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        if (p / "schema").is_dir():
            return p
    # 兜底：开发仓布局的仓库根
    return here.parents[2]


REPO_ROOT = _find_repo_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../deploy/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MOCK=1 时所有 /v1/* 直接返回 schema/examples/ 的示例，不调任何外部服务。
    # 开发和测试默认开；部署时在 .env 里改 0。
    mock: bool = True

    # --- 大模型（技术文档 5.1，模型 ID 以控制台为准）---
    qwen_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_key: str = ""
    qwen_main: str = "qwen-plus"
    qwen_lite: str = "qwen-flash"
    ds_base: str = "https://api.deepseek.com"
    ds_key: str = ""
    ds_main: str = ""

    # --- 外部服务 ---
    amap_web_key: str = ""
    qw_host: str = ""  # 和风控制台分配的 API Host
    qw_project_id: str = ""
    qw_key_id: str = ""
    qw_private_key: str = ""
    redis_url: str = "redis://localhost:6379/0"

    # --- 预算与限流（技术文档 6.4 / 14.3，配额为 2025-05 月配额制）---
    daily_llm_calls_max: int = 3000
    daily_search_max: int = 120
    daily_generate_max: int = 10

    # --- 路径 ---
    schema_dir: Path = REPO_ROOT / "schema"


@lru_cache
def get_settings() -> Settings:
    return Settings()
