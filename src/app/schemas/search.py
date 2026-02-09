"""
搜索相关的数据模型
"""

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """搜索请求参数"""

    query: str = Field(..., description="搜索关键词")
    count: int = Field(default=5, ge=1, le=20, description="每个搜索引擎返回的结果数量")
    freshness: str = Field(default="pd", description="内容新鲜度过滤(仅Brave支持): pd=过去24小时, pw=过去一周, pm=过去一月")
    sources: list[str] | None = Field(
        default=None,
        description="指定使用的搜索源，可选: brave, tavily, serper, duckduckgo, wikipedia。为空则使用全部",
    )


class SearchResultItem(BaseModel):
    """单条搜索结果"""

    title: str | None = None
    url: str | None = None
    description: str | None = None
    score: float | None = None
    source: str


class WikipediaResult(BaseModel):
    """Wikipedia搜索结果"""

    title: str
    summary: str
    url: str
    source: str = "wikipedia"


class SearchResponse(BaseModel):
    """搜索响应"""

    query: str
    timestamp: str
    sources: dict[str, list[SearchResultItem] | WikipediaResult | None]
