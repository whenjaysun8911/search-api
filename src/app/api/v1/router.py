"""
V1 API 路由聚合
将所有 v1 版本的路由统一注册
"""

from fastapi import APIRouter

from src.app.api.v1 import hello

# 创建 v1 主路由
api_router = APIRouter()

# 注册各模块路由
api_router.include_router(hello.router)
