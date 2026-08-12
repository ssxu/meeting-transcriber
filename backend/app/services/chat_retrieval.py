"""AI 对话 — 段级混合检索（语义 + 关键词融合）。

两路检索同时执行，取并集后按融合分数排序：
  fused_score = sem_score * 0.7 + kw_score * 0.3
"""
import re
import logging
from app.services.embedding import get_embedding, EmbeddingModelConfig
from app.services.vector_store import vector_store

logger = logging.getLogger("meeting-transcriber.chat_retrieval")

# 中英文分词/短语拆分：按常见分隔符切分，过滤空串和单字
_QUERY_SPLIT_RE = re.compile(r'[\s，,。.！!？?；;：:、\-—/\\]+')


def _split_query(question: str) -> list[str]:
    """将问题切分为检索短语列表。"""
    parts = _QUERY_SPLIT_RE.split(question.strip())
    # 过滤：长度 >= 2 的有意义短语
    return [p for p in parts if len(p) >= 2]


def _keyword_score(segment_text: str, phrases: list[str]) -> float:
    """关键词打分：统计 query 短语在 segment 中的命中率。

    命中率 = 命中的短语数 / 总短语数
    """
    if not phrases:
        return 0.0
    text_lower = segment_text.lower()
    hits = sum(1 for p in phrases if p.lower() in text_lower)
    return hits / len(phrases)


async def retrieve_relevant_segments(
    question: str,
    rec_id: int,
    segments: list[dict],
    emb_config: EmbeddingModelConfig | None,
    top_k: int = 5,
) -> list[dict]:
    """检索与问题最相关的 transcript 段落。

    混合检索策略：**语义 + 关键词 同时执行，结果融合**。
    - 语义检索：问题向量化 → Zvec 段级 collection 搜索（按 recording_id 过滤）
    - 关键词检索：问题分词/短语拆解 → 对 segments 逐段做子串匹配 + 词频打分
    - 融合策略：两路结果取并集，按 fused_score = sem_score * 0.7 + kw_score * 0.3 排序取 top-K
    - 若 embedding 模型未配置，则仅用关键词检索

    Args:
        question: 用户问题
        rec_id: 录音 ID
        segments: 原始 transcript_segments（用于取原文文本）
        emb_config: embedding 模型配置（None 表示仅关键词检索）
        top_k: 返回 top-K 段

    Returns:
        [{segment_index, text, start, end, speaker, score}, ...]  # 按分数降序
    """
    phrases = _split_query(question)
    # 构建 segment 文本索引（key = list 位置，即 segment_index）
    seg_texts: dict[int, str] = {}
    for i, seg in enumerate(segments):
        seg_texts[i] = seg.get("text", "")

    # ===== 语义检索 =====
    sem_results: dict[int, float] = {}  # segment_index -> sem_score
    if emb_config and vector_store.is_ready:
        try:
            query_vec = await get_embedding(question, emb_config)
            if query_vec:
                raw = vector_store.search_segments(query_vec, rec_id, top_k=top_k * 2)
                for r in raw:
                    sidx = r.get("segment_index", 0)
                    sem_results[sidx] = r.get("score", 0.0)
        except Exception as e:
            logger.warning(f"语义检索失败，降级为纯关键词: {e}")

    # ===== 关键词检索 =====
    kw_results: dict[int, float] = {}  # segment_index -> kw_score
    if phrases:
        for idx, text in seg_texts.items():
            score = _keyword_score(text, phrases)
            if score > 0:
                kw_results[idx] = score

    # ===== 融合 =====
    all_indices = set(sem_results.keys()) | set(kw_results.keys())
    fused: list[dict] = []
    for sidx in all_indices:
        sem_s = sem_results.get(sidx, 0.0)
        kw_s = kw_results.get(sidx, 0.0)
        fused_score = sem_s * 0.7 + kw_s * 0.3
        if fused_score <= 0:
            continue
        seg = segments[sidx] if sidx < len(segments) else None
        if not seg:
            continue
        fused.append({
            "segment_index": sidx,
            "text": seg.get("text", ""),
            "start": seg.get("start"),
            "end": seg.get("end"),
            "speaker": seg.get("speaker"),
            "score": round(fused_score, 4),
        })

    fused.sort(key=lambda x: x["score"], reverse=True)
    return fused[:top_k]
