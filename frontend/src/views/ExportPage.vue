<script setup>
import { onMounted, ref, computed } from 'vue'
import { formatDateTime } from '../utils/format'
import { ElMessage } from 'element-plus'
import api from '../api'

const loading = ref(false)
const recordings = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

// 时间范围
const dateRange = ref([])

// 选中的录音
const selectedIds = ref(new Set())

const statusMap = {
  pending: { type: 'info', label: '等待中' },
  transcribing: { type: 'warning', label: '转录中' },
  transcribed: { type: 'primary', label: '已转录' },
  summarizing: { type: 'warning', label: '总结中' },
  done: { type: 'success', label: '已完成' },
  error: { type: 'danger', label: '失败' }
}

function tagOf(status) {
  return statusMap[status] || { type: 'info', label: status }
}

function formatDuration(s) {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}分${sec}秒`
}

function formatDate(d) {
  if (!d) return '—'
  return formatDateTime(d)
}

const selectedCount = computed(() => selectedIds.value.size)

async function load() {
  loading.value = true
  try {
    let listData
    if (dateRange.value && dateRange.value.length === 2) {
      // 按时间范围筛选 - 使用现有列表接口，前端过滤
      const [startDate, endDate] = dateRange.value
      const startStr = startDate.toISOString().split('T')[0]
      const endStr = endDate.toISOString().split('T')[0]
      
      // 加载所有数据进行时间过滤（简化实现）
      listData = await api.list({ page: 1, page_size: 1000, q: '', tag: '' })
      const filtered = (listData.items || []).filter(r => {
        const recDate = new Date(r.created_at)
        const startDateObj = new Date(startDate)
        startDateObj.setHours(0, 0, 0, 0)
        const endDateObj = new Date(endDate)
        endDateObj.setHours(23, 59, 59, 999)
        return recDate >= startDateObj && recDate <= endDateObj
      })
      
      // 手动分页
      total.value = filtered.length
      const startIdx = (page.value - 1) * pageSize.value
      recordings.value = filtered.slice(startIdx, startIdx + pageSize.value)
    } else {
      listData = await api.list({ page: page.value, page_size: pageSize.value, q: '', tag: '' })
      recordings.value = listData.items || []
      total.value = listData.total || 0
    }
    selectedIds.value.clear()
  } catch (e) {
    ElMessage.error(e._msg || '加载失败')
  } finally {
    loading.value = false
  }
}

function handlePageChange(p) {
  page.value = p
  load()
}

function handlePageSizeChange(ps) {
  pageSize.value = ps
  page.value = 1
  load()
}

function handleDateRangeChange() {
  page.value = 1
  load()
}

function clearDateRange() {
  dateRange.value = []
  page.value = 1
  load()
}

// 表格选择
function handleSelectionChange(rows) {
  selectedIds.value = new Set(rows.map(r => r.id))
}

function selectAll() {
  recordings.value.forEach(r => selectedIds.value.add(r.id))
  selectedIds.value = new Set(selectedIds.value)
}

// 批量导出为 ZIP
async function batchExportZip() {
  if (!selectedIds.value.size) {
    ElMessage.warning('请先选择录音')
    return
  }
  
  const ids = [...selectedIds.value]
  const token = localStorage.getItem('mt_token')
  
  try {
    ElMessage.info('正在生成 ZIP 文件...')
    const resp = await fetch(`/api/recordings/batch-export-zip${token ? '?token=' + encodeURIComponent(token) : ''}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(ids),
    })
    if (!resp.ok) {
      throw new Error('导出失败')
    }
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '批量导出_会议纪要.zip'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${ids.length} 条录音`)
  } catch (e) {
    ElMessage.error('导出失败：' + (e.message || '未知错误'))
  }
}

// 导出全部（当前筛选条件下的）
async function exportAll() {
  if (!total.value) {
    ElMessage.warning('暂无可导出的录音')
    return
  }
  
  const token = localStorage.getItem('mt_token')
  
  try {
    // 获取当前筛选条件下的所有 ID
    let allIds = []
    if (dateRange.value && dateRange.value.length === 2) {
      const listData = await api.list({ page: 1, page_size: 1000, q: '', tag: '' })
      const [startDate, endDate] = dateRange.value
      allIds = (listData.items || []).filter(r => {
        const recDate = new Date(r.created_at)
        const startDateObj = new Date(startDate)
        startDateObj.setHours(0, 0, 0, 0)
        const endDateObj = new Date(endDate)
        endDateObj.setHours(23, 59, 59, 999)
        return recDate >= startDateObj && recDate <= endDateObj
      }).map(r => r.id)
    } else {
      const listData = await api.list({ page: 1, page_size: 1000, q: '', tag: '' })
      allIds = (listData.items || []).map(r => r.id)
    }
    
    if (!allIds.length) {
      ElMessage.warning('暂无可导出的录音')
      return
    }
    
    ElMessage.info(`正在导出 ${allIds.length} 条录音...`)
    const resp = await fetch(`/api/recordings/batch-export-zip${token ? '?token=' + encodeURIComponent(token) : ''}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(allIds),
    })
    if (!resp.ok) {
      throw new Error('导出失败')
    }
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '全部导出_会议纪要.zip'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${allIds.length} 条录音`)
  } catch (e) {
    ElMessage.error('导出失败：' + (e.message || '未知错误'))
  }
}

onMounted(() => {
  load()
})
</script>

<template>
  <div>
    <div class="page-header">
      <span class="page-title">📦 批量导出</span>
      <div style="display: flex; gap: 8px">
        <el-button @click="exportAll" :disabled="!total" type="info" plain>导出全部</el-button>
        <el-button @click="batchExportZip" :disabled="!selectedCount" type="primary">
          导出选中 ({{ selectedCount }})
        </el-button>
      </div>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <el-date-picker
        v-model="dateRange"
        type="daterange"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        value-format="x"
        @change="handleDateRangeChange"
        style="width: 360px"
      />
      <el-button v-if="dateRange && dateRange.length" text size="small" @click="clearDateRange">清除筛选</el-button>
      <span style="font-size: 13px; color: var(--el-text-color-secondary); margin-left: auto">
        共 {{ total }} 条录音
      </span>
    </div>

    <!-- 录音列表 -->
    <el-table
      :data="recordings"
      v-loading="loading"
      style="width: 100%"
      @selection-change="handleSelectionChange"
      stripe
    >
      <el-table-column type="selection" width="50" />
      <el-table-column label="标题" prop="title" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.title || row.original_filename }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="tagOf(row.status).type" size="small">{{ tagOf(row.status).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时长" width="100">
        <template #default="{ row }">
          {{ formatDuration(row.duration) }}
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">
          {{ formatDate(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column label="标签" min-width="150">
        <template #default="{ row }">
          <el-tag
            v-for="t in (row.tags || []).slice(0, 3)"
            :key="t"
            size="small"
            type="warning"
            effect="plain"
            round
            style="margin-right: 4px"
          >{{ t }}</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页 -->
    <div class="pagination-wrapper" v-if="total > 0">
      <el-pagination
        v-model:current-page="page"
        :total="total"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="handlePageChange"
        @size-change="handlePageSizeChange"
      />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
}

.page-title {
  font-size: 18px;
  font-weight: bold;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.pagination-wrapper {
  margin-top: 24px;
  display: flex;
  justify-content: center;
}
</style>
