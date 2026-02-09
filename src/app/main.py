"""
FastAPI 应用入口
"""

from fastapi import FastAPI

from src.app.api.v1.router import api_router
from src.app.core.config import settings

# 创建 FastAPI 应用实例
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 注册 API 路由
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "healthy"}
