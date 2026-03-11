"""
多源搜索服务
整合 Brave、Tavily、Serper、DuckDuckGo 和 Wikipedia 搜索
"""

import json
import logging
import re
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
    AVAILABLE_SOURCES = ["brave", "tavily", "serper", "duckduckgo", "wikipedia", "searxng"]

    # 缓存的 SearXNG 候选实例列表
    _searxng_candidates: list[str] = []
    # 当前正在使用的 SearXNG 实例
    _searxng_instance: str | None = None

    @staticmethod
    def search_brave(query: str, count: int = 5, freshness: str = "") -> list[SearchResultItem]:
        """
        使用 Brave Search API 搜索
        
        Args:
            query: 搜索关键词
            count: 返回结果数量
            freshness: 新鲜度过滤 (pd=24小时, pw=一周, pm=一月, 空=不限制)
        """
        if not settings.brave_api_key:
            logger.warning("Brave API key 未配置")
            return []

        # 构建请求 URL，仅在 freshness 有值时添加该参数
        base_url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={count}"
        url = f"{base_url}&freshness={freshness}" if freshness else base_url
        headers = {"Accept": "application/json", "X-Subscription-Token": settings.brave_api_key}

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    results = data.get("web", {}).get("results", [])
                    logger.debug(f"Brave Search 返回 {len(results)} 条结果")
                    return [
                        SearchResultItem(
                            title=r.get("title"),
                            url=r.get("url"),
                            description=r.get("description"),
                            source="brave",
                        )
                        for r in results
                    ]
                else:
                    logger.warning(f"Brave Search 响应状态码: {response.status}")
        except urllib.error.HTTPError as e:
            logger.error(f"Brave Search HTTP 错误: {e.code} - {e.reason}")
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

    @classmethod
    def _fetch_searxng_candidates(cls) -> list[str]:
        """
        从 searx.space 拉取并筛选高质量 SearXNG 公共实例列表
        筛选条件：网络类型为 normal、HTTP 状态码 200、周可用率 >= 95%
        """
        import random

        try:
            req = urllib.request.Request("https://searx.space/data/instances.json")
            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    instances = data.get("instances", {})
                    # 严格筛选：正常网络 + HTTP 200 + 周可用率 >= 95%
                    valid = [
                        url
                        for url, info in instances.items()
                        if info.get("network_type") == "normal"
                        and info.get("http", {}).get("status_code") == 200
                        and (info.get("uptime", {}).get("uptimeWeek") or 0) >= 95
                    ]
                    if valid:
                        random.shuffle(valid)
                        logger.info(f"获取到 {len(valid)} 个高质量 SearXNG 候选实例")
                        return valid
        except Exception as e:
            logger.error(f"获取 SearXNG 实例列表失败: {e}")

        # 兜底返回几个已知可用的默认实例
        return ["https://searx.ro/", "https://baresearch.org/", "https://paulgo.io/"]

    @classmethod
    def get_searxng_instance(cls) -> str:
        """
        获取一个可用的 SearXNG 公共实例
        优先从缓存的候选列表中取，列表为空时重新拉取
        """
        # 当前实例仍可用，直接返回
        if cls._searxng_instance:
            return cls._searxng_instance

        # 候选列表为空，重新拉取
        if not cls._searxng_candidates:
            cls._searxng_candidates = cls._fetch_searxng_candidates()

        # 从候选列表中取第一个作为当前实例
        if cls._searxng_candidates:
            cls._searxng_instance = cls._searxng_candidates[0]
            logger.info(f"选择 SearXNG 实例: {cls._searxng_instance}（剩余候选 {len(cls._searxng_candidates)} 个）")
            return cls._searxng_instance

        # 极端兜底
        return "https://searx.ro/"

    @classmethod
    def _invalidate_searxng_instance(cls):
        """
        将当前 SearXNG 实例标记为不可用
        从候选列表中移除并清空当前实例，下次调用 get_searxng_instance 会自动切换到下一个
        """
        if cls._searxng_instance and cls._searxng_instance in cls._searxng_candidates:
            cls._searxng_candidates.remove(cls._searxng_instance)
            logger.info(f"移除不可用实例: {cls._searxng_instance}（剩余候选 {len(cls._searxng_candidates)} 个）")
        cls._searxng_instance = None

    @classmethod
    def _parse_searxng_html(cls, html: str) -> list[SearchResultItem]:
        """
        从 SearXNG HTML 响应中提取搜索结果
        """
        results = []
        # 正则提取 <article class="result ...">...</article>
        articles = re.findall(r'<article[^>]+class="[^"]*result[^"]*"[^>]*>(.*?)</article>', html, re.DOTALL)
        for art in articles:
            # 提取 URL 和 title: <h3><a href="...">title</a></h3>
            m_title = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', art, re.DOTALL)
            # 提取摘要: <p class="content">...</p>
            m_snippet = re.search(r'<p[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</p>', art, re.DOTALL)
            if m_title:
                url = m_title.group(1).strip()
                # 过滤 HTML 标签
                title = re.sub(r'<[^>]+>', '', m_title.group(2)).strip()
                description = re.sub(r'<[^>]+>', '', m_snippet.group(1)).strip() if m_snippet else ""
                results.append(
                    SearchResultItem(
                        title=title,
                        url=url,
                        description=description,
                        source="searxng (html)"
                    )
                )
        return results

    @classmethod
    def search_searxng(cls, query: str, count: int = 5) -> list[SearchResultItem]:
        """
        使用 SearXNG 公共实例搜索
        由于 JSON API 常被禁用，逻辑如下：
        1. 优先尝试请求带 format=json 的 API
        2. 如果 JSON 失败或返回空，则请求 HTML 页面并进行正则解析
        """
        import asyncio
        from rnet import Client, Impersonate

        async def _do_search():
            max_retries = 10
            # 使用 rnet Client，增加超时到 12s
            client = Client(impersonate=Impersonate.Chrome137, verify=False, timeout=12)

            for attempt in range(max_retries):
                instance_url = cls.get_searxng_instance()
                base_url = instance_url.rstrip("/")
                
                # 情况 A: 尝试 JSON API
                json_url = f"{base_url}/search?q={urllib.parse.quote(query)}&format=json"
                try:
                    response = await client.get(url=json_url)
                    #修正：rnet 的 status_code 需要调用 as_int() 来比较
                    if response.status_code.as_int() == 200:
                        try:
                            # 修正：rnet 的 json() 是异步的
                            data = await response.json()
                            results = data.get("results", [])
                            if results:
                                logger.info(f"SearXNG 实例 {instance_url} JSON 搜索成功")
                                return [
                                    SearchResultItem(
                                        title=r.get("title"),
                                        url=r.get("url"),
                                        description=r.get("content") or r.get("snippet"),
                                        source=f"searxng ({r.get('engine', 'unknown')})",
                                    )
                                    for r in results[:count]
                                ]
                        except Exception:
                            logger.debug(f"SearXNG 实例 {instance_url} JSON 解析失败，尝试 HTML 方式")
                except Exception as e:
                    logger.debug(f"SearXNG 实例 {instance_url} JSON 请求异常: {e}")

                # 情况 B: JSON 失败或无结果，尝试 HTML 解析
                html_url = f"{base_url}/search?q={urllib.parse.quote(query)}&categories=general"
                try:
                    # 增加常见浏览器 Header 以减少 403
                    headers = {
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": f"{base_url}/",
                    }
                    response = await client.get(url=html_url, headers=headers)
                    if response.status_code.as_int() == 200:
                        # 修正：rnet 的 text() 是异步的
                        html_content = await response.text()
                        results = cls._parse_searxng_html(html_content)
                        if results:
                            logger.info(f"SearXNG 实例 {instance_url} HTML 搜索成功")
                            return results[:count]
                except Exception as e:
                    logger.warning(f"SearXNG 实例 {instance_url} HTML 请求失败: {e}")

                # 请求彻底不可用，淘汰当前实例并切换
                logger.info(f"SearXNG 实例 {instance_url} 完全不可用，进行第 {attempt + 1} 次重试")
                cls._invalidate_searxng_instance()

            logger.error(f"SearXNG 搜索失败: 达到最大重试次数 {max_retries}")
            return []

        # 由于在多线程中执行，直接运行 asyncio.run 包装异步请求
        try:
            return asyncio.run(_do_search())
        except Exception as e:
            logger.error(f"SearXNG 异步执行失败: {e}")
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
        freshness: str = "",
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
        if "searxng" in sources:
            search_tasks["searxng"] = (cls.search_searxng, (query, count))

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
