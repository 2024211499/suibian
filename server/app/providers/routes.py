"""受管理员权限保护的供应商配置和连通性验证。"""

import asyncio
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.auth.routes import require_admin
from app.config import use_settings
from app.gateway import amap
from app.llm.client import LLMClient

router = APIRouter(prefix="/admin/providers", tags=["团队配置"], dependencies=[Depends(require_admin)])
ProviderName = Literal["qwen", "deepseek", "amap"]
MODEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$"
BASES = {
    "qwen": {
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    },
    "deepseek": {"https://api.deepseek.com", "https://api.deepseek.com/v1"},
}


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: SecretStr = Field(min_length=1, max_length=4096)
    base_url: str | None = Field(default=None, max_length=256)
    main_model: str | None = Field(default=None, pattern=MODEL_PATTERN)
    lite_model: str | None = Field(default=None, pattern=MODEL_PATTERN)

    @field_validator("key")
    @classmethod
    def key_has_no_whitespace(cls, value):
        if not all(33 <= ord(char) <= 126 for char in value.get_secret_value()):
            raise ValueError("Key 只能包含可打印的非空白 ASCII 字符")
        return value


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str | None = Field(default=None, min_length=1, max_length=200)
    city_adcode: str | None = Field(default=None, pattern=r"^[0-9]{6}$")


@router.get("")
def list_providers(request: Request):
    return request.app.state.provider_store.list_public()


@router.put("/{provider}")
def configure(provider: ProviderName, payload: ProviderConfig, request: Request):
    store = request.app.state.provider_store
    data = {"key": payload.key.get_secret_value()}
    if provider == "amap":
        if payload.base_url or payload.main_model or payload.lite_model:
            raise HTTPException(422, "高德只需要 Web 服务 Key")
    else:
        settings = store.effective_settings()
        prefix = "qwen" if provider == "qwen" else "ds"
        base = (payload.base_url or getattr(settings, f"{prefix}_base")).rstrip("/")
        if base not in BASES[provider] or urlsplit(base).username:
            raise HTTPException(422, "请使用该服务商官方 HTTPS API 地址")
        model = payload.main_model or getattr(settings, f"{prefix}_main")
        if not model:
            raise HTTPException(422, "请填写控制台提供的模型 ID")
        data.update(base_url=base, main_model=model)
        if provider == "qwen":
            data["lite_model"] = payload.lite_model or settings.qwen_lite or model
        elif payload.lite_model:
            raise HTTPException(422, "DeepSeek 使用 main_model")
    store.set(provider, data)
    return {"provider": provider, **store.list_public()["providers"][provider]}


@router.post("/{provider}/test")
async def probe(provider: ProviderName, payload: ProbeRequest, request: Request):
    store = request.app.state.provider_store
    await asyncio.to_thread(request.app.state.auth_store.throttle, f"probe:{provider}", 10)
    settings = await asyncio.to_thread(store.effective_settings)
    if provider == "amap":
        if not payload.address or not payload.city_adcode:
            raise HTTPException(422, "测试高德需要提供 address 和 city_adcode")
        with use_settings(settings):
            try:
                result = await amap.geocode(payload.address, payload.city_adcode)
            except amap.GatewayError as error:
                return {"provider": provider, "ok": False, "detail": str(error)}
        return {"provider": provider, "ok": True, "adcode": result["adcode"], "level": result["level"]}
    overrides = {"ds_key" if provider == "qwen" else "qwen_key": ""}
    if provider == "qwen":
        overrides["qwen_lite"] = settings.qwen_main
    settings = settings.model_copy(update=overrides)
    client = LLMClient(settings)
    try:
        result = await client.probe()
        return {"provider": provider, "ok": result.get(provider) == "ok", "detail": result.get(provider, "未配置")}
    finally:
        await client.aclose()
