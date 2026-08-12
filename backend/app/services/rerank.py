"""Rerank 服务模块 - 调用三方重排序 API 对搜索结果重排序。"""
import logging
import httpx
from dataclasses import dataclass

logger = logging.getLogger("meeting-transcriber.rerank")


@dataclass
class RerankModelConfig:
    """Rerank 模型配置。"""
    base_url: str
    api_key: str
    model: str


async def rerank(
    query: str,
    documents: list[str],
    config: RerankModelConfig,
    top_n: int | None = None,
    timeout: int = 60,
) -> list[tuple[int, float]]:
    """调用 rerank API 对文档重排序。

    返回 [(原始索引, rerank_score), ...] 按分数降序排列。
    """
    if not documents:
        return []
    payload = {
        "model": config.model,
        "query": query,
        "documents": documents,
    }
    if top_n is not None:
        payload["top_n"] = top_n

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{config.base_url}/v1/rerank",
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    # 兼容不同返回格式: results / data
    results = data.get("results") or data.get("data") or []
    out = []
    for item in results:
        idx = item.get("index", 0)
        score = item.get("relevance_score", item.get("score", 0.0))
        out.append((idx, float(score)))
    out.sort(key=lambda x: x[1], reverse=True)
    return out
