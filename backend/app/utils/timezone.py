"""时区工具模块 - 集中处理系统时区转换。

所有用户可见的时间输出都应通过此模块转换，确保一致性。
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from app.config import settings


def get_app_tz() -> ZoneInfo:
    """获取应用配置的时区对象。"""
    return ZoneInfo(settings.timezone)


def to_app_tz(dt: Optional[datetime]) -> Optional[datetime]:
    """将 UTC datetime 转换为应用配置的时区。

    如果 dt 没有时区信息（naive），则视为 UTC 处理。
    如果 dt 为 None，返回 None。
    """
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(get_app_tz())
    return dt


def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """确保 datetime 是 UTC 时区感知的。

    如果 dt 没有时区信息（naive），则视为 UTC 处理。
    如果 dt 为 None，返回 None。
    """
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return dt


def format_datetime(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M") -> str:
    """将 datetime 格式化为应用配置时区的字符串。

    用于导出文档、MCP 响应等后端生成的文字中的时间显示。
    如果 dt 为 None，返回空字符串。
    """
    if dt is None:
        return ""
    local_dt = to_app_tz(dt)
    return local_dt.strftime(fmt)
