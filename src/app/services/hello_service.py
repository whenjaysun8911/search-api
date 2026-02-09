"""
Hello World 业务逻辑服务
"""


class HelloService:
    """Hello服务类"""

    @staticmethod
    def get_hello_message() -> str:
        """获取Hello消息"""
        return "Hello World"

    @staticmethod
    def get_greeting(name: str) -> str:
        """获取带名字的问候消息"""
        return f"Hello, {name}!"
