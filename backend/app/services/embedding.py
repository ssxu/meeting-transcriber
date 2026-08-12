"""Embedding 服务模块 - 调用 OpenAI 兼容的 /v1/embeddings 接口生成向量。

支持长文本分片+错位切片：当文本超过 embedding 模型上下文限制时，
自动拆分为多个重叠切片，分别 embedding 后取平均值，保证上下文完整性。
"""
import logging
import httpx
from dataclasses import dataclass

logger = logging.getLogger("meeting-transcriber.embedding")


@dataclass
class EmbeddingModelConfig:
    """Embedding 模型配置。"""
    base_url: str
    api_key: str
    model: str
    max_context_length: int = 8000  # embedding 模型的最大输入 token 数


def _estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。"""
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 2 + other_chars * 0.5)


def _split_with_overlap(text: str, max_tokens: int, overlap_ratio: float = 0.15) -> list[str]:
    """将文本按 token 限制分片，支持错位重叠。
    
    Args:
        max_tokens: 每个切片的最大 token 数
        overlap_ratio: 相邻切片的重叠比例（0~0.5），默认 15%
    
    Returns:
        切片列表，相邻切片有重叠部分以保证上下文完整性
    """
    if _estimate_tokens(text) <= max_tokens:
        return [text]
    
    # 按段落切分
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = ""
    current_tokens = 0
    
    overlap_tokens = int(max_tokens * overlap_ratio)
    
    for para in paragraphs:
        para_tokens = _estimate_tokens(para)
        
        # 如果单个段落超过限制，按句子切分
        if para_tokens > max_tokens:
            sentences = para.replace('\n', ' ').split('。')
            for sent in sentences:
                if sent:
                    sent = sent + '。'
                sent_tokens = _estimate_tokens(sent)
                if current_tokens + sent_tokens > max_tokens and current_chunk:
                    chunks.append(current_chunk)
                    # 错位：取上一块末尾部分作为下一块开头
                    tail = current_chunk
                    tail_tokens = 0
                    for tp in reversed(tail.split('\n\n')):
                        tp_tokens = _estimate_tokens(tp)
                        if tail_tokens + tp_tokens > overlap_tokens:
                            break
                        tail_tokens += tp_tokens
                    if tail_tokens > 0:
                        # 找到已加入的段落，构建重叠前缀
                        tail_paragraphs = []
                        acc = 0
                        for tp in reversed(tail.split('\n\n')):
                            tp_tokens = _estimate_tokens(tp)
                            if acc + tp_tokens > overlap_tokens:
                                break
                            acc += tp_tokens
                            tail_paragraphs.insert(0, tp)
                        current_chunk = '\n\n'.join(tail_paragraphs) if tail_paragraphs else ""
                        current_tokens = acc
                    else:
                        current_chunk = ""
                        current_tokens = 0
                current_chunk = current_chunk + '\n\n' + sent if current_chunk else sent
                current_tokens += sent_tokens
        elif current_tokens + para_tokens > max_tokens and current_chunk:
            chunks.append(current_chunk)
            # 错位：取上一块末尾段落作为下一块开头
            tail_paragraphs = []
            acc = 0
            for tp in reversed(current_chunk.split('\n\n')):
                tp_tokens = _estimate_tokens(tp)
                if acc + tp_tokens > overlap_tokens:
                    break
                acc += tp_tokens
                tail_paragraphs.insert(0, tp)
            current_chunk = '\n\n'.join(tail_paragraphs) if tail_paragraphs else ""
            current_tokens = acc
            current_chunk = current_chunk + '\n\n' + para if current_chunk else para
            current_tokens += para_tokens
        else:
            current_chunk = current_chunk + '\n\n' + para if current_chunk else para
            current_tokens += para_tokens
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks


def _average_vectors(vectors: list[list[float]]) -> list[float]:
    """对多个向量取平均值。"""
    if not vectors:
        return []
    if len(vectors) == 1:
        return vectors[0]
    dim = len(vectors[0])
    result = [0.0] * dim
    for vec in vectors:
        for i in range(dim):
            result[i] += vec[i]
    return [v / len(vectors) for v in result]


async def get_embedding(text: str, config: EmbeddingModelConfig, timeout: int = 60) -> list[float]:
    """调用 embedding API 将文本转为向量。
    
    当文本超过模型上下文限制时，自动分片+错位切片，
    分别 embedding 后取平均向量。
    """
    max_tokens = int(config.max_context_length * 0.8)  # 留 20% 余量
    if max_tokens < 500:
        max_tokens = 500
    
    estimated = _estimate_tokens(text)
    if estimated <= max_tokens:
        # 不需要分片
        url = f"{config.base_url}/v1/embeddings"
        headers = {"Authorization": f"Bearer {config.api_key}"}
        logger.info(f"[embedding] POST {url} headers=Authorization: Bearer {config.api_key[:6]}... model={config.model}")
        async with httpx.AsyncClient(timeout=timeout, http1=True, trust_env=False) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={"model": config.model, "input": text},
            )
            logger.info(f"[embedding] status={resp.status_code} body={resp.text[:200]}")
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
    
    # 需要分片
    chunks = _split_with_overlap(text, max_tokens, overlap_ratio=0.15)
    logger.info(f"Embedding 文本过长(tokens≈{estimated})，分片为 {len(chunks)} 片")
    
    vectors = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Embedding 分片 {i+1}/{len(chunks)}, tokens≈{_estimate_tokens(chunk)}")
        url = f"{config.base_url}/v1/embeddings"
        headers = {"Authorization": f"Bearer {config.api_key}"}
        async with httpx.AsyncClient(timeout=timeout, http1=True, trust_env=False) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={"model": config.model, "input": chunk},
            )
            logger.info(f"[embedding] status={resp.status_code} body={resp.text[:200]}")
            resp.raise_for_status()
            data = resp.json()
            vectors.append(data["data"][0]["embedding"])
    
    return _average_vectors(vectors)


async def get_embeddings_batch(texts: list[str], config: EmbeddingModelConfig, timeout: int = 120) -> list[list[float]]:
    """批量生成向量。"""
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=timeout, http1=True, trust_env=False) as client:
        resp = await client.post(
            f"{config.base_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {config.api_key}"},
            json={
                "model": config.model,
                "input": [t[:8000] for t in texts],
            },
        )
        logger.info(f"[embedding batch] status={resp.status_code} body={resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
        # 按 index 排序确保顺序
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]
