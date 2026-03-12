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

    # SearXNG 候选实例列表
    _searxng_candidates: list[str] = []
    # 轮换索引，用于 round-robin 选择实例
    _searxng_rotate_index: int = 0
    # 每个实例的冷却时间记录 {instance_url: 可再次使用的时间戳}
    _searxng_cooldowns: dict[str, float] = {}
    # 缓存的 rnet Client 实例，避免重复创建
    _rnet_client = None
    # 常规请求间隔（秒），避免同一实例被频繁调用
    _SEARXNG_MIN_INTERVAL = 3.0
    # 被限速后的惩罚冷却时间（秒）
    _SEARXNG_PENALTY_COOLDOWN = 300.0
    # 候选列表的过期时间（秒），定期刷新
    _searxng_candidates_expire: float = 0.0
    _SEARXNG_CANDIDATES_TTL = 3600.0  # 1 小时

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
    def _ensure_searxng_candidates(cls):
        """
        确保候选实例列表可用且未过期
        过期后自动重新拉取，同时清理冷却记录中已不存在的实例
        """
        now = time.time()
        if not cls._searxng_candidates or now >= cls._searxng_candidates_expire:
            cls._searxng_candidates = cls._fetch_searxng_candidates()
            cls._searxng_candidates_expire = now + cls._SEARXNG_CANDIDATES_TTL
            cls._searxng_rotate_index = 0
            # 清理不再存在于候选列表中的冷却记录
            candidate_set = set(cls._searxng_candidates)
            cls._searxng_cooldowns = {
                k: v for k, v in cls._searxng_cooldowns.items() if k in candidate_set
            }

    @classmethod
    def _pick_searxng_instance(cls) -> str | None:
        """
        以 round-robin 方式从候选池中选取一个当前不在冷却期的实例
        遍历一圈后仍无可用实例则返回 None
        """
        cls._ensure_searxng_candidates()
        pool_size = len(cls._searxng_candidates)
        if pool_size == 0:
            return None

        now = time.time()
        for _ in range(pool_size):
            idx = cls._searxng_rotate_index % pool_size
            cls._searxng_rotate_index = idx + 1
            candidate = cls._searxng_candidates[idx]
            cooldown_until = cls._searxng_cooldowns.get(candidate, 0)
            if now >= cooldown_until:
                # 设置常规冷却，防止下次立即再选到同一实例
                cls._searxng_cooldowns[candidate] = now + cls._SEARXNG_MIN_INTERVAL
                return candidate

        # 所有实例都在冷却中，返回冷却最早结束的实例
        earliest = min(cls._searxng_cooldowns, key=cls._searxng_cooldowns.get)
        logger.warning(f"所有 SearXNG 实例均在冷却中，强制选择 {earliest}")
        return earliest

    @classmethod
    def _penalize_searxng_instance(cls, instance_url: str):
        """
        对被限速 (429/403) 的实例施加较长的惩罚冷却期
        """
        cls._searxng_cooldowns[instance_url] = time.time() + cls._SEARXNG_PENALTY_COOLDOWN
        logger.info(f"SearXNG 实例 {instance_url} 被限速，冷却 {cls._SEARXNG_PENALTY_COOLDOWN}s")

    @classmethod
    def _remove_searxng_instance(cls, instance_url: str):
        """
        将完全不可用的实例从候选池中永久移除（本轮生命周期内）
        """
        if instance_url in cls._searxng_candidates:
            cls._searxng_candidates.remove(instance_url)
            cls._searxng_cooldowns.pop(instance_url, None)
            logger.info(f"移除不可用实例: {instance_url}（剩余候选 {len(cls._searxng_candidates)} 个）")

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
    def _get_rnet_client(cls):
        """
        获取或创建缓存的 rnet Client 实例
        """
        if cls._rnet_client is None:
            from rnet import Client, Impersonate
            cls._rnet_client = Client(impersonate=Impersonate.Chrome137, verify=False, timeout=12)
        return cls._rnet_client

    @classmethod
    def search_searxng(cls, query: str, count: int = 5) -> list[SearchResultItem]:
        """
        使用 SearXNG 公共实例搜索
        
        优化策略：
        - 每次请求通过 round-robin 轮换不同实例，避免单实例被频繁调用
        - 对 429/403 限速响应施加惩罚冷却期，短期内不再选中该实例
        - 完全不可用的实例从候选池中移除
        - 复用 rnet Client 减少连接开销
        """
        import asyncio

        async def _do_search():
            max_retries = 10
            client = cls._get_rnet_client()

            for attempt in range(max_retries):
                instance_url = cls._pick_searxng_instance()
                if not instance_url:
                    logger.error("SearXNG 无可用候选实例")
                    return []

                base_url = instance_url.rstrip("/")
                rate_limited = False

                # 情况 A: 尝试 JSON API
                json_url = f"{base_url}/search?q={urllib.parse.quote(query)}&format=json"
                try:
                    response = await client.get(url=json_url)
                    status = response.status_code.as_int()
                    if status == 200:
                        try:
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
                    elif status in (429, 403):
                        rate_limited = True
                        logger.debug(f"SearXNG 实例 {instance_url} JSON 请求被限速: {status}")
                except Exception as e:
                    logger.debug(f"SearXNG 实例 {instance_url} JSON 请求异常: {e}")

                # 如果已被限速，直接惩罚并切换，跳过 HTML 尝试
                if rate_limited:
                    cls._penalize_searxng_instance(instance_url)
                    continue

                # 情况 B: JSON 失败或无结果，尝试 HTML 解析
                html_url = f"{base_url}/search?q={urllib.parse.quote(query)}&categories=general"
                try:
                    headers = {
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": f"{base_url}/",
                    }
                    response = await client.get(url=html_url, headers=headers)
                    status = response.status_code.as_int()
                    if status == 200:
                        html_content = await response.text()
                        results = cls._parse_searxng_html(html_content)
                        if results:
                            logger.info(f"SearXNG 实例 {instance_url} HTML 搜索成功")
                            return results[:count]
                    elif status in (429, 403):
                        # HTML 也被限速，施加惩罚冷却
                        cls._penalize_searxng_instance(instance_url)
                        continue
                except Exception as e:
                    logger.warning(f"SearXNG 实例 {instance_url} HTML 请求失败: {e}")

                # JSON + HTML 都失败且非限速，判定为完全不可用，从候选池移除
                logger.info(f"SearXNG 实例 {instance_url} 完全不可用，进行第 {attempt + 1} 次重试")
                cls._remove_searxng_instance(instance_url)

            logger.error(f"SearXNG 搜索失败: 达到最大重试次数 {max_retries}")
            return []

        # 由于在多线程中执行，直接运行 asyncio.run 包装异步请求
        try:
            return asyncio.run(_do_search())
        except Exception as e:
            logger.error(f"SearXNG 异步执行失败: {e}")
            return []

    @staticmethod
    def _wikipedia_search_titles(query: str, lang: str = "en", limit: int = 3) -> list[str]:
        """
        使用 Wikipedia OpenSearch API 根据关键词搜索匹配的页面标题列表
        
        Args:
            query: 搜索关键词
            lang: 语言代码，如 "zh" 或 "en"
            limit: 返回的最大标题数量
        
        Returns:
            匹配的页面标题列表，未找到则返回空列表
        """
        try:
            search_url = (
                f"https://{lang}.wikipedia.org/w/api.php"
                f"?action=opensearch&search={urllib.parse.quote(query)}"
                f"&limit={limit}&namespace=0&format=json"
            )
            req = urllib.request.Request(
                search_url,
                headers={"User-Agent": "SearchAPI/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    # OpenSearch 返回格式: [query, [titles], [descriptions], [urls]]
                    data = json.loads(response.read().decode())
                    if len(data) >= 2 and data[1]:
                        return data[1]
        except Exception as e:
            logger.debug(f"Wikipedia OpenSearch ({lang}) 搜索失败: {e}")
        return []

    @classmethod
    def get_wikipedia_summary(cls, query: str) -> WikipediaResult | None:
        """
        获取 Wikipedia 摘要信息
        先通过 OpenSearch API 搜索匹配的页面标题，再获取摘要
        优先中文，回退英文
        """
        user_agent = "SearchAPI/1.0"

        try:
            # 优先尝试中文 Wikipedia
            zh_titles = cls._wikipedia_search_titles(query, lang="zh")
            if zh_titles:
                wiki_zh = wikipediaapi.Wikipedia(user_agent=user_agent, language="zh")
                for title in zh_titles:
                    page = wiki_zh.page(title)
                    if page.exists():
                        return WikipediaResult(
                            title=page.title,
                            summary=page.summary[:500] + "..." if len(page.summary) > 500 else page.summary,
                            url=page.fullurl,
                            source="wikipedia (zh)",
                        )

            # 回退到英文 Wikipedia
            en_titles = cls._wikipedia_search_titles(query, lang="en")
            if en_titles:
                wiki_en = wikipediaapi.Wikipedia(user_agent=user_agent, language="en")
                for title in en_titles:
                    page = wiki_en.page(title)
                    if page.exists():
                        return WikipediaResult(
                            title=page.title,
                            summary=page.summary[:500] + "..." if len(page.summary) > 500 else page.summary,
                            url=page.fullurl,
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
