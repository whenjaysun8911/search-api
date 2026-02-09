"""
Hello World API 路由
"""

from fastapi import APIRouter

from src.app.schemas.common import MessageResponse, ResponseModel
from src.app.services.hello_service import HelloService

router = APIRouter(prefix="/hello", tags=["Hello"])


@router.get("", response_model=ResponseModel[str])
async def hello_world():
    """
    Hello World 接口
    返回简单的 Hello World 消息
    """
    message = HelloService.get_hello_message()
    return ResponseModel(data=message)


@router.get("/{name}", response_model=ResponseModel[str])
async def hello_name(name: str):
    """
    带名字的问候接口
    根据传入的名字返回个性化问候
    """
    greeting = HelloService.get_greeting(name)
    return ResponseModel(data=greeting)
