# utils package
from app.models import Recording


def _get_segments(rec: Recording) -> list[dict]:
    """从录音记录中提取 segments 列表。

    兼容 list / JSON 字符串 / None 三种存储格式。
    """
    if not rec.transcript_segments:
        return []
    segs = rec.transcript_segments
    if isinstance(segs, str):
        try:
            import json
            segs = json.loads(segs)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(segs, list):
        return []
    return segs
