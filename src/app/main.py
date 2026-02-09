"""
FastAPI 应用入口
"""

from fastapi import Depends, FastAPI

from src.app.api.v1.router import api_router
from src.app.core.config import settings
from src.app.core.security import verify_token

# 创建 FastAPI 应用实例（禁用文档端点以增强安全性）
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)

# 注册 API 路由（全局 token 验证）
app.include_router(api_router, prefix=settings.api_v1_prefix, dependencies=[Depends(verify_token)])


@app.get("/health")
async def health_check():
    """健康检查接口（无需 token）"""
    return {"status": "healthy"}
