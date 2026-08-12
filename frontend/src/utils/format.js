/**
 * 日期格式化工具 - 所有用户可见的时间格式化集中在这里。
 *
 * 使用方法：
 *   import { formatDateTime, formatDateShort } from '../utils/format'
 *   formatDateTime(item.created_at)
 *   formatDateShort(item.created_at)
 *
 * 时区来源：优先使用后端配置的时区（在 docker-compose.yml 中通过 TZ 设置），
 * 未加载前用浏览器本地时区兜底。
 */
import { useConfigStore } from '../stores/config'

/**
 * 格式化日期时间为完整字符串，如 "2024-01-01 14:30"。
 * 用于列表、详情等需要完整日期时间的场景。
 */
export function formatDateTime(dateStr) {
  if (!dateStr) return '-'
  const store = useConfigStore()
  const tz = store.effectiveTimezone
  const dt = new Date(dateStr)
  return dt.toLocaleString('zh-CN', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/**
 * 格式化日期为简短格式，如 "1/7 14:30" 或 "1/7"。
 * 用于仪表盘、卡片等紧凑场景。
 */
export function formatDateShort(dateStr) {
  if (!dateStr) return '-'
  const store = useConfigStore()
  const tz = store.effectiveTimezone
  const dt = new Date(dateStr)
  return dt.toLocaleString('zh-CN', {
    timeZone: tz,
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/**
 * 格式化时间戳为文件名友好格式（不含时区转换，仅用于文件名）。
 * 返回 "yyyy-MM-ddTHH-mm-ss" 格式（ISO 的变体）。
 */
export function formatTimestampForFilename(date) {
  const dt = date || new Date()
  return dt.toISOString().replace(/[:.]/g, '-').slice(0, 19)
}
