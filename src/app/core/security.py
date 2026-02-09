"""
安全认证模块
提供 Token 验证依赖
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from src.app.core.config import settings

# 定义请求头中的 token 字段
api_key_header = APIKeyHeader(name="token", auto_error=False)


async def verify_token(token: str | None = Depends(api_key_header)) -> str:
    """
    验证请求头中的 token
    
    Args:
        token: 请求头中的 token 值
        
    Returns:
        验证通过返回 token
        
    Raises:
        HTTPException: token 缺失或无效时抛出 401 错误
    """
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 token 请求头",
        )
    if token != settings.api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 token",
        )
    return token
