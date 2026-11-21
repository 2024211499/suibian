FROM python:3.12-slim

WORKDIR /srv
# 构建上下文 = 仓库根（见 deploy/docker-compose.yml 的 build.context）。
# schema/ 必须打进镜像：mock 模式的示例响应和启动时的核心资源检查都要读它。
COPY server/pyproject.toml ./pyproject.toml
COPY server/app ./app
COPY schema ./schema
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
