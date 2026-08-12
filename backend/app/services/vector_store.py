"""Zvec 向量存储服务 — 管理录音向量索引与检索。

使用 Zvec 嵌入式向量数据库（阿里巴巴开源），替代纯 Python 余弦相似度计算。
Zvec 提供 HNSW 索引，支持毫秒级搜索，内进程无服务依赖。
"""
import os
import logging
from typing import Optional
from app.config import settings

logger = logging.getLogger("meeting-transcriber.vector_store")

# Zvec collection 名称
COLLECTION_NAME = "recording_embeddings"
SEGMENT_COLLECTION_NAME = "segment_embeddings"

# 默认向量维度（常见 embedding 模型维度）
DEFAULT_DIM = 1024

# 标量字段名
FIELD_RECORDING_ID = "recording_id"
FIELD_TITLE = "title"
FIELD_CREATED_AT = "created_at_ts"
FIELD_STATUS = "status"

# 向量字段名
VECTOR_FIELD = "embedding"


class VectorStore:
    """Zvec 向量存储管理器（单例）。"""

    def __init__(self):
        self._collection = None
        self._segment_collection = None
        self._dim = None
        self._path = None

    async def init(self, dim: int = DEFAULT_DIM):
        """初始化 Zvec collection。若已存在则直接打开。

        Args:
            dim: 向量维度，需与 embedding 模型输出维度一致
        """
        import zvec

        self._path = settings.zvec_data_path
        os.makedirs(self._path, exist_ok=True)
        self._dim = dim

        collection_path = os.path.join(self._path, COLLECTION_NAME)

        # 尝试打开已存在的 collection
        if os.path.exists(collection_path) and os.listdir(collection_path):
            try:
                self._collection = zvec.open(collection_path)
                # 从已有 schema 读取维度
                if hasattr(self._collection.schema, 'vectors') and self._collection.schema.vectors:
                    self._dim = self._collection.schema.vectors[0].dimension
                logger.info(f"Zvec collection 已打开: path={collection_path}, dim={self._dim}")
                return
            except Exception as e:
                logger.warning(f"打开 Zvec collection 失败，将重建: {e}")

        # 创建新 collection
        schema = zvec.CollectionSchema(
            name=COLLECTION_NAME,
            fields=[
                zvec.FieldSchema(
                    name=FIELD_RECORDING_ID,
                    data_type=zvec.DataType.INT64,
                ),
                zvec.FieldSchema(
                    name=FIELD_TITLE,
                    data_type=zvec.DataType.STRING,
                ),
                zvec.FieldSchema(
                    name=FIELD_CREATED_AT,
                    data_type=zvec.DataType.DOUBLE,
                ),
            ],
            vectors=[
                zvec.VectorSchema(
                    name=VECTOR_FIELD,
                    data_type=zvec.DataType.VECTOR_FP32,
                    dimension=dim,
                    index_param=zvec.HnswIndexParam(
                        metric_type=zvec.MetricType.COSINE,
                    ),
                ),
            ],
        )
        self._collection = zvec.create_and_open(
            path=collection_path,
            schema=schema,
        )
        logger.info(f"Zvec collection 已创建: path={collection_path}, dim={dim}")

    async def ensure_dim(self, dim: int):
        """确保 collection 维度匹配，不匹配则重建。"""
        if self._dim == dim:
            return
        logger.warning(f"向量维度不匹配 (当前={self._dim}, 需要={dim})，重建 collection")
        # 销毁旧 collection
        if self._collection:
            self._collection.destroy()
            self._collection = None
        # 用新维度重建
        await self.init(dim)

    @property
    def collection(self):
        return self._collection

    @property
    def dim(self):
        return self._dim

    @property
    def is_ready(self) -> bool:
        return self._collection is not None

    def insert(self, doc_id: str, recording_id: int, embedding: list[float],
               title: str = "", created_at_ts: float = 0.0):
        """插入或更新一条向量记录。"""
        import zvec
        if not self._collection:
            logger.warning("Zvec collection 未初始化，跳过插入")
            return
        # upsert：如果 id 已存在则覆盖
        self._collection.upsert(
            zvec.Doc(
                id=doc_id,
                vectors={VECTOR_FIELD: embedding},
                fields={
                    FIELD_RECORDING_ID: recording_id,
                    FIELD_TITLE: title,
                    FIELD_CREATED_AT: created_at_ts,
                },
            )
        )

    def delete(self, doc_id: str):
        """删除一条向量记录。"""
        if not self._collection:
            return
        try:
            self._collection.delete(ids=doc_id)
        except Exception as e:
            logger.warning(f"删除 Zvec 文档失败: id={doc_id}, error={e}")

    def search(self, query_vector: list[float], topk: int = 50,
               filter: Optional[str] = None) -> list[dict]:
        """向量相似度搜索。

        Returns:
            [{"doc_id": str, "recording_id": int, "score": float, "title": str, "created_at_ts": float}, ...]
        """
        import zvec
        if not self._collection:
            return []

        query = zvec.Query(
            field_name=VECTOR_FIELD,
            vector=query_vector,
        )
        kwargs = {"queries": query, "topk": topk}
        if filter:
            kwargs["filter"] = filter

        results = self._collection.query(**kwargs)

        out = []
        for doc in results:
            rec_id = doc.fields.get(FIELD_RECORDING_ID) if hasattr(doc, 'fields') else None
            title = doc.fields.get(FIELD_TITLE, "") if hasattr(doc, 'fields') else ""
            ts = doc.fields.get(FIELD_CREATED_AT, 0.0) if hasattr(doc, 'fields') else 0.0
            out.append({
                "doc_id": doc.id,
                "recording_id": rec_id,
                "score": doc.score if hasattr(doc, 'score') else 0.0,
                "title": title,
                "created_at_ts": ts,
            })
        return out

    def count(self) -> int:
        """返回 collection 中的文档数量。"""
        if not self._collection:
            return 0
        try:
            stats = self._collection.stats
            if hasattr(stats, 'doc_count'):
                return stats.doc_count
            if isinstance(stats, dict):
                return stats.get('doc_count', 0)
        except Exception:
            pass
        return 0

    def optimize(self):
        """优化索引，提升搜索性能。"""
        if not self._collection:
            return
        try:
            self._collection.optimize()
            logger.info("Zvec 索引优化完成")
        except Exception as e:
            logger.warning(f"Zvec 索引优化失败: {e}")

    def flush(self):
        """刷写数据到磁盘。"""
        if not self._collection:
            return
        try:
            self._collection.flush()
        except Exception as e:
            logger.warning(f"Zvec flush 失败: {e}")

    # ===== 段级 collection 管理 =====

    async def init_segments(self, dim: int = DEFAULT_DIM):
        """初始化段级 Zvec collection。"""
        import zvec

        collection_path = os.path.join(self._path, SEGMENT_COLLECTION_NAME)

        if os.path.exists(collection_path) and os.listdir(collection_path):
            try:
                self._segment_collection = zvec.open(collection_path)
                if hasattr(self._segment_collection.schema, 'vectors') and self._segment_collection.schema.vectors:
                    dim = self._segment_collection.schema.vectors[0].dimension
                logger.info(f"Zvec 段级 collection 已打开: path={collection_path}, dim={dim}")
                return
            except Exception as e:
                logger.warning(f"打开 Zvec 段级 collection 失败，将重建: {e}")

        schema = zvec.CollectionSchema(
            name=SEGMENT_COLLECTION_NAME,
            fields=[
                zvec.FieldSchema(name="recording_id", data_type=zvec.DataType.INT64),
                zvec.FieldSchema(name="segment_index", data_type=zvec.DataType.INT64),
                zvec.FieldSchema(name="start", data_type=zvec.DataType.DOUBLE),
                zvec.FieldSchema(name="end", data_type=zvec.DataType.DOUBLE),
                zvec.FieldSchema(name="speaker", data_type=zvec.DataType.STRING),
            ],
            vectors=[
                zvec.VectorSchema(
                    name=VECTOR_FIELD,
                    data_type=zvec.DataType.VECTOR_FP32,
                    dimension=dim,
                    index_param=zvec.HnswIndexParam(metric_type=zvec.MetricType.COSINE),
                ),
            ],
        )
        self._segment_collection = zvec.create_and_open(path=collection_path, schema=schema)
        logger.info(f"Zvec 段级 collection 已创建: path={collection_path}, dim={dim}")

    def insert_segment(self, doc_id: str, recording_id: int, segment_index: int,
                       start: float, end: float, speaker: str, embedding: list[float]):
        """插入或更新一条段级向量记录。"""
        import zvec
        if not self._segment_collection:
            logger.warning("Zvec 段级 collection 未初始化，跳过插入")
            return
        self._segment_collection.upsert(
            zvec.Doc(
                id=doc_id,
                vectors={VECTOR_FIELD: embedding},
                fields={
                    "recording_id": recording_id,
                    "segment_index": segment_index,
                    "start": start,
                    "end": end,
                    "speaker": speaker or "",
                },
            )
        )

    def search_segments(self, query_vector: list[float], recording_id: int, top_k: int = 5) -> list[dict]:
        """段级向量检索，按 recording_id 过滤。

        Returns:
            [{"doc_id": str, "recording_id": int, "segment_index": int,
              "start": float, "end": float, "speaker": str, "score": float}, ...]
        """
        import zvec
        if not self._segment_collection:
            return []

        query = zvec.Query(field_name=VECTOR_FIELD, vector=query_vector)
        zvec_filter = f"recording_id == {recording_id}"
        results = self._segment_collection.query(queries=query, topk=top_k, filter=zvec_filter)

        out = []
        for doc in results:
            fields = getattr(doc, 'fields', {})
            out.append({
                "doc_id": doc.id,
                "recording_id": fields.get("recording_id"),
                "segment_index": fields.get("segment_index", 0),
                "start": fields.get("start", 0.0),
                "end": fields.get("end", 0.0),
                "speaker": fields.get("speaker", ""),
                "score": getattr(doc, 'score', 0.0),
            })
        return out

    def delete_segments_by_recording(self, recording_id: int):
        """删除一条录音的所有段级向量。"""
        if not self._segment_collection:
            return
        try:
            zvec_filter = f"recording_id == {recording_id}"
            self._segment_collection.delete(filter=zvec_filter)
        except Exception as e:
            logger.warning(f"删除段级向量失败: recording_id={recording_id}, error={e}")

    def segment_count(self, recording_id: int | None = None) -> int:
        """统计段级文档数。"""
        if not self._segment_collection:
            return 0
        try:
            stats = self._segment_collection.stats
            if hasattr(stats, 'doc_count'):
                return stats.doc_count
            if isinstance(stats, dict):
                return stats.get('doc_count', 0)
        except Exception:
            pass
        return 0

    def flush_segments(self):
        """刷写段级数据到磁盘。"""
        if not self._segment_collection:
            return
        try:
            self._segment_collection.flush()
        except Exception as e:
            logger.warning(f"Zvec 段级 flush 失败: {e}")

    def optimize_segments(self):
        """优化段级索引。"""
        if not self._segment_collection:
            return
        try:
            self._segment_collection.optimize()
            logger.info("Zvec 段级索引优化完成")
        except Exception as e:
            logger.warning(f"Zvec 段级索引优化失败: {e}")


# 全局单例
vector_store = VectorStore()
