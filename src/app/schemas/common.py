"""
通用响应模型
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ResponseModel(BaseModel, Generic[T]):
    """统一API响应格式"""

    code: int = 0
    message: str = "success"
    data: T | None = None


class MessageResponse(BaseModel):
    """简单消息响应"""

    message: str
