"""
搜索 API 路由
"""

from fastapi import APIRouter

from src.app.schemas.common import ResponseModel
from src.app.schemas.search import SearchRequest, SearchResponse
from src.app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["Search"])


@router.post("", response_model=ResponseModel[SearchResponse])
async def multi_search(request: SearchRequest):
    """
    多源搜索接口
    
    同时查询多个搜索引擎并返回聚合结果，支持以下搜索源：
    - **brave**: Brave Search（需配置 BRAVE_API_KEY）
    - **tavily**: Tavily AI 深度搜索（需配置 TAVILY_API_KEY）
    - **serper**: Google 搜索结果（需配置 SERPER_API_KEY）
    - **duckduckgo**: DuckDuckGo 搜索（无需 API key）
    - **wikipedia**: Wikipedia 摘要（无需 API key）
    """
    result = SearchService.multi_search(
        query=request.query,
        count=request.count,
        freshness=request.freshness,
        sources=request.sources,
    )
    return ResponseModel(data=SearchResponse(**result))


@router.get("", response_model=ResponseModel[SearchResponse])
async def search_get(
    query: str,
    count: int = 5,
    freshness: str = "pd",
    sources: str | None = None,
):
    """
    多源搜索接口（GET 方法）
    
    Args:
        query: 搜索关键词
        count: 每个搜索引擎返回的结果数量（1-20）
        freshness: 内容新鲜度过滤(仅Brave): pd=24小时, pw=一周, pm=一月
        sources: 逗号分隔的搜索源列表，如 "brave,tavily,duckduckgo"
    """
    # 处理 sources 参数
    source_list = None
    if sources:
        source_list = [s.strip() for s in sources.split(",")]

    result = SearchService.multi_search(
        query=query,
        count=min(max(count, 1), 20),  # 限制范围1-20
        freshness=freshness,
        sources=source_list,
    )
    return ResponseModel(data=SearchResponse(**result))
