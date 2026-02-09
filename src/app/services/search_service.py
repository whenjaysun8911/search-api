"""
多源搜索服务
整合 Brave、Tavily、Serper、DuckDuckGo 和 Wikipedia 搜索
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import wikipediaapi
from ddgs import DDGS
from tavily import TavilyClient

from src.app.core.config import settings
from src.app.schemas.search import SearchResultItem, WikipediaResult

logger = logging.getLogger(__name__)


class SearchService:
    """多源搜索服务类"""

    # 支持的搜索源
    AVAILABLE_SOURCES = ["brave", "tavily", "serper", "duckduckgo", "wikipedia"]

    @staticmethod
    def search_brave(query: str, count: int = 5, freshness: str = "pd") -> list[SearchResultItem]:
        """
        使用 Brave Search API 搜索
        
        Args:
            query: 搜索关键词
            count: 返回结果数量
            freshness: 新鲜度过滤 (pd=24小时, pw=一周, pm=一月)
        """
        if not settings.brave_api_key:
            logger.warning("Brave API key 未配置")
            return []

        url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={count}&freshness={freshness}"
        headers = {"Accept": "application/json", "X-Subscription-Token": settings.brave_api_key}

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    results = data.get("web", {}).get("results", [])
                    return [
                        SearchResultItem(
                            title=r.get("title"),
                            url=r.get("url"),
                            description=r.get("description"),
                            source="brave",
                        )
                        for r in results
                    ]
        except Exception as e:
            logger.error(f"Brave Search 失败: {e}")
        return []

    @staticmethod
    def search_tavily(query: str, count: int = 5) -> list[SearchResultItem]:
        """
        使用 Tavily API 搜索（AI 上下文深度搜索）
        """
        if not settings.tavily_api_key:
            logger.warning("Tavily API key 未配置")
            return []

        try:
            tavily = TavilyClient(api_key=settings.tavily_api_key)
            response = tavily.search(query=query, max_results=count, search_depth="advanced")
            results = response.get("results", [])
            return [
                SearchResultItem(
                    title=r.get("title"),
                    url=r.get("url"),
                    description=r.get("content"),
                    score=r.get("score"),
                    source="tavily",
                )
                for r in results
            ]
        except Exception as e:
            logger.error(f"Tavily Search 失败: {e}")
        return []

    @staticmethod
    def search_serper(query: str, count: int = 5) -> list[SearchResultItem]:
        """
        使用 Serper API 搜索（Google 搜索结果）
        """
        if not settings.serper_api_key:
            logger.warning("Serper API key 未配置")
            return []

        try:
            url = "https://google.serper.dev/search"
            payload = json.dumps({"q": query, "num": count})
            headers = {"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"}

            response = requests.post(url, headers=headers, data=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                results = data.get("organic", [])
                return [
                    SearchResultItem(
                        title=r.get("title"),
                        url=r.get("link"),
                        description=r.get("snippet"),
                        source="serper (google)",
                    )
                    for r in results
                ]
        except Exception as e:
            logger.error(f"Serper Search 失败: {e}")
        return []

    @staticmethod
    def search_duckduckgo(query: str, count: int = 5) -> list[SearchResultItem]:
        """
        使用 DuckDuckGo 搜索（备用搜索引擎，无需 API key）
        """
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=count))
                return [
                    SearchResultItem(
                        title=r.get("title"),
                        url=r.get("href"),
                        description=r.get("body"),
                        source="duckduckgo",
                    )
                    for r in results
                ]
        except Exception as e:
            logger.error(f"DuckDuckGo Search 失败: {e}")
        return []

    @staticmethod
    def get_wikipedia_summary(query: str) -> WikipediaResult | None:
        """
        获取 Wikipedia 摘要信息
        优先中文，回退英文
        """
        user_agent = "SearchAPI/1.0"

        try:
            # 优先尝试中文 Wikipedia
            wiki_zh = wikipediaapi.Wikipedia(user_agent=user_agent, language="zh")
            page = wiki_zh.page(query)
            if page.exists():
                return WikipediaResult(
                    title=page.title,
                    summary=page.summary[:500] + "..." if len(page.summary) > 500 else page.summary,
                    url=page.fullurl,
                    source="wikipedia (zh)",
                )

            # 回退到英文 Wikipedia
            wiki_en = wikipediaapi.Wikipedia(user_agent=user_agent, language="en")
            page_en = wiki_en.page(query)
            if page_en.exists():
                return WikipediaResult(
                    title=page_en.title,
                    summary=page_en.summary[:500] + "..." if len(page_en.summary) > 500 else page_en.summary,
                    url=page_en.fullurl,
                    source="wikipedia (en)",
                )
        except Exception as e:
            logger.error(f"Wikipedia 获取失败: {e}")
        return None

    @classmethod
    def multi_search(
        cls,
        query: str,
        count: int = 5,
        freshness: str = "pd",
        sources: list[str] | None = None,
    ) -> dict:
        """
        执行多源搜索
        
        Args:
            query: 搜索关键词
            count: 每个搜索引擎返回的结果数量
            freshness: 内容新鲜度过滤（仅 Brave 支持）
            sources: 指定的搜索源列表，为空则使用全部
        
        Returns:
            包含各搜索源结果的字典
        """
        # 如果没有指定 sources，则使用全部可用源
        if sources is None:
            sources = cls.AVAILABLE_SOURCES
        else:
            # 过滤无效的 source
            sources = [s.lower() for s in sources if s.lower() in cls.AVAILABLE_SOURCES]

        output = {
            "query": query,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sources": {},
        }

        # 根据配置的 sources 并发执行搜索
        # 定义搜索任务映射，key 为搜索源名称，value 为搜索函数及其参数
        search_tasks = {}
        if "brave" in sources:
            search_tasks["brave"] = (cls.search_brave, (query, count, freshness))
        if "tavily" in sources:
            search_tasks["tavily"] = (cls.search_tavily, (query, count))
        if "serper" in sources:
            search_tasks["serper"] = (cls.search_serper, (query, count))
        if "duckduckgo" in sources:
            search_tasks["duckduckgo"] = (cls.search_duckduckgo, (query, count))
        if "wikipedia" in sources:
            search_tasks["wikipedia"] = (cls.get_wikipedia_summary, (query,))

        # 使用线程池并发执行搜索任务
        with ThreadPoolExecutor(max_workers=len(search_tasks) or 1) as executor:
            # 提交所有任务并记录 future 与搜索源的映射
            future_to_source = {
                executor.submit(func, *args): source_name
                for source_name, (func, args) in search_tasks.items()
            }

            # 收集完成的任务结果
            for future in as_completed(future_to_source):
                source_name = future_to_source[future]
                try:
                    result = future.result()
                    # Wikipedia 返回 None 时不添加到结果中
                    if result is not None:
                        output["sources"][source_name] = result
                except Exception as e:
                    logger.error(f"{source_name} 搜索任务异常: {e}")

        return output
