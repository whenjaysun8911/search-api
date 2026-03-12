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


class _RateLimitError(Exception):
    """SearXNG 实例被限速时的内部异常"""
    def __init__(self, instance_url: str):
        self.instance_url = instance_url
        super().__init__(f"Rate limited: {instance_url}")


class _InstanceFailedError(Exception):
    """SearXNG 实例完全不可用时的内部异常"""
    def __init__(self, instance_url: str):
        self.instance_url = instance_url
        super().__init__(f"Instance failed: {instance_url}")

class SearchService:
    """多源搜索服务类"""

    # 支持的搜索源
    AVAILABLE_SOURCES = ["brave", "tavily", "serper", "duckduckgo", "wikipedia", "searxng"]

    # SearXNG 候选实例列表（已按响应速度筛选）
    _searxng_candidates: list[str] = []
    # 轮换索引，用于 round-robin 选择实例
    _searxng_rotate_index: int = 0
    # 每个实例的冷却时间记录 {instance_url: 可再次使用的时间戳}
    _searxng_cooldowns: dict[str, float] = {}
    # 曾成功返回结果的实例列表，优先级最高
    _searxng_successful: list[str] = []
    # 缓存的 rnet Client 实例，避免重复创建
    _rnet_client = None
    # 常规请求间隔（秒），避免同一实例被频繁调用
    _SEARXNG_MIN_INTERVAL = 3.0
    # 被限速后的惩罚冷却时间（秒）
    _SEARXNG_PENALTY_COOLDOWN = 300.0
    # 候选列表的过期时间（秒），定期刷新
    _searxng_candidates_expire: float = 0.0
    _SEARXNG_CANDIDATES_TTL = 3600.0  # 1 小时
    # 并发探测批次大小：同时向多个实例发起请求
    _SEARXNG_CONCURRENCY = 5

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
        筛选条件：
        - 网络类型为 normal
        - HTTP 状态码 200
        - 周可用率 >= 95%
        - 搜索响应时间 < 1 秒（过滤掉慢实例）
        按响应时间升序排列，优先使用快实例
        """
        import random

        try:
            req = urllib.request.Request("https://searx.space/data/instances.json")
            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    instances = data.get("instances", {})
                    scored = []
                    for url, info in instances.items():
                        if info.get("network_type") != "normal":
                            continue
                        if info.get("http", {}).get("status_code") != 200:
                            continue
                        if (info.get("uptime", {}).get("uptimeWeek") or 0) < 95:
                            continue
                        # 获取搜索响应时间，优先 search.time，否则用 http 的 timing
                        timing = (
                            info.get("timing", {}).get("search", {}).get("all", {}).get("median")
                            or info.get("http", {}).get("timing")
                            or 999
                        )
                        # 仅保留响应时间 < 2s 的实例
                        if isinstance(timing, (int, float)) and timing < 2.0:
                            scored.append((url, timing))
                    if scored:
                        # 按响应时间升序排列，最快的在前面
                        scored.sort(key=lambda x: x[1])
                        valid = [url for url, _ in scored]
                        # 在前 30 个快速实例中随机打散，兼顾速度和负载均衡
                        top = valid[:30]
                        rest = valid[30:]
                        random.shuffle(top)
                        valid = top + rest
                        logger.info(f"获取到 {len(valid)} 个 SearXNG 候选实例（响应 < 2s）")
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
    def _pick_searxng_batch(cls, batch_size: int | None = None) -> list[str]:
        """
        从候选池中选取一批不在冷却期的实例，用于并发探测
        优先选用曾成功过的实例
        
        Args:
            batch_size: 需要的实例数量，默认为 _SEARXNG_CONCURRENCY
        
        Returns:
            最多 batch_size 个可用实例 URL 列表
        """
        if batch_size is None:
            batch_size = cls._SEARXNG_CONCURRENCY

        cls._ensure_searxng_candidates()
        pool_size = len(cls._searxng_candidates)
        if pool_size == 0:
            return []

        now = time.time()
        picked: list[str] = []
        picked_set: set[str] = set()

        # 第一优先级：从曾成功的实例中选取（不在冷却期的）
        for inst in cls._searxng_successful:
            if len(picked) >= batch_size:
                break
            if inst in picked_set:
                continue
            if now >= cls._searxng_cooldowns.get(inst, 0):
                picked.append(inst)
                picked_set.add(inst)
                cls._searxng_cooldowns[inst] = now + cls._SEARXNG_MIN_INTERVAL

        # 第二优先级：round-robin 从全部候选中补齐
        scanned = 0
        while len(picked) < batch_size and scanned < pool_size:
            idx = cls._searxng_rotate_index % pool_size
            cls._searxng_rotate_index = idx + 1
            candidate = cls._searxng_candidates[idx]
            scanned += 1
            if candidate in picked_set:
                continue
            if now >= cls._searxng_cooldowns.get(candidate, 0):
                picked.append(candidate)
                picked_set.add(candidate)
                cls._searxng_cooldowns[candidate] = now + cls._SEARXNG_MIN_INTERVAL

        return picked

    @classmethod
    def _penalize_searxng_instance(cls, instance_url: str):
        """
        对被限速 (429/403) 的实例施加较长的惩罚冷却期
        """
        cls._searxng_cooldowns[instance_url] = time.time() + cls._SEARXNG_PENALTY_COOLDOWN
        logger.info(f"SearXNG 实例 {instance_url} 被限速，冷却 {cls._SEARXNG_PENALTY_COOLDOWN}s")

    @classmethod
    def _mark_searxng_success(cls, instance_url: str):
        """
        将实例标记为成功，加入优先队列（最多保留 10 个）
        """
        if instance_url not in cls._searxng_successful:
            cls._searxng_successful.insert(0, instance_url)
            # 只保留最近成功的 10 个
            cls._searxng_successful = cls._searxng_successful[:10]

    @classmethod
    def _remove_searxng_instance(cls, instance_url: str):
        """
        将完全不可用的实例从候选池中永久移除（本轮生命周期内）
        """
        if instance_url in cls._searxng_candidates:
            cls._searxng_candidates.remove(instance_url)
            cls._searxng_cooldowns.pop(instance_url, None)
            logger.info(f"移除不可用实例: {instance_url}（剩余候选 {len(cls._searxng_candidates)} 个）")
        # 同时从成功列表中移除
        if instance_url in cls._searxng_successful:
            cls._searxng_successful.remove(instance_url)

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
        获取或创建缓存的 rnet Client 实例，超时 8 秒
        """
        if cls._rnet_client is None:
            from rnet import Client, Impersonate
            cls._rnet_client = Client(impersonate=Impersonate.Chrome137, verify=False, timeout=8)
        return cls._rnet_client

    @classmethod
    def search_searxng(cls, query: str, count: int = 5) -> list[SearchResultItem]:
        """
        使用 SearXNG 公共实例搜索
        
        核心策略：并发探测 + 快速返回
        - 每轮同时向 N 个实例发起请求
        - 使用 FIRST_COMPLETED 模式，任一成功立即返回并取消剩余任务
        - 最多进行 4 轮（共覆盖 ~20 个实例）
        - 成功实例被记住，下次优先使用
        - 被限速的实例施加 5 分钟冷却，超时/不可用的实例直接移除
        """
        import asyncio

        async def _try_instance(client, instance_url: str, q: str, cnt: int):
            """
            尝试单个实例的 JSON -> HTML 搜索流程
            
            保证只抛出以下三种结果之一：
            - 返回 (instance_url, results) 表示成功
            - 抛出 _RateLimitError 表示被限速
            - 抛出 _InstanceFailedError 表示不可用（含超时）
            """
            try:
                base_url = instance_url.rstrip("/")

                # 尝试 JSON API
                json_url = f"{base_url}/search?q={urllib.parse.quote(q)}&format=json"
                try:
                    response = await client.get(url=json_url)
                    status = response.status_code.as_int()
                    if status == 200:
                        try:
                            data = await response.json()
                            results = data.get("results", [])
                            if results:
                                return (instance_url, [
                                    SearchResultItem(
                                        title=r.get("title"),
                                        url=r.get("url"),
                                        description=r.get("content") or r.get("snippet"),
                                        source=f"searxng ({r.get('engine', 'unknown')})",
                                    )
                                    for r in results[:cnt]
                                ])
                        except Exception:
                            pass  # JSON 解析失败，继续尝试 HTML
                    elif status in (429, 403):
                        raise _RateLimitError(instance_url)
                except _RateLimitError:
                    raise
                except Exception:
                    pass  # JSON 请求失败（含超时），继续尝试 HTML

                # 尝试 HTML 解析
                html_url = f"{base_url}/search?q={urllib.parse.quote(q)}&categories=general"
                headers = {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": f"{base_url}/",
                }
                response = await client.get(url=html_url, headers=headers)
                status = response.status_code.as_int()
                if status in (429, 403):
                    raise _RateLimitError(instance_url)
                if status == 200:
                    html_content = await response.text()
                    results = cls._parse_searxng_html(html_content)
                    if results:
                        return (instance_url, results[:cnt])

                # JSON + HTML 都无有效结果
                raise _InstanceFailedError(instance_url)

            except (_RateLimitError, _InstanceFailedError):
                raise
            except Exception:
                # 任何未预期的异常（超时、连接失败等）统一视为实例不可用
                raise _InstanceFailedError(instance_url)

        async def _do_search():
            client = cls._get_rnet_client()
            max_rounds = 4  # 最多 4 轮并发探测

            for round_idx in range(max_rounds):
                batch = cls._pick_searxng_batch()
                if not batch:
                    logger.error("SearXNG 无可用候选实例")
                    return []

                logger.debug(f"SearXNG 第 {round_idx + 1} 轮并发探测 ({len(batch)} 个实例): {batch}")
                pending = {
                    asyncio.create_task(_try_instance(client, inst, query, count))
                    for inst in batch
                }

                # FIRST_COMPLETED 循环：一有结果就检查，成功则立即返回
                result = None
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        try:
                            inst_url, items = task.result()
                            logger.info(f"SearXNG 实例 {inst_url} 搜索成功")
                            cls._mark_searxng_success(inst_url)
                            result = items
                        except _RateLimitError as e:
                            cls._penalize_searxng_instance(e.instance_url)
                        except _InstanceFailedError as e:
                            cls._remove_searxng_instance(e.instance_url)

                    # 拿到成功结果后取消剩余任务并返回
                    if result is not None:
                        for t in pending:
                            t.cancel()
                        return result

            logger.error(f"SearXNG 搜索失败: {max_rounds} 轮并发探测均未成功")
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
