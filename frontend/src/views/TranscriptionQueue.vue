<script setup>
import { onMounted, ref, computed } from 'vue'
import { formatDateTime } from '../utils/format'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const router = useRouter()
const loading = ref(false)
const queue = ref([])

// 转录时间设置
const scheduleEnabled = ref(false)
const scheduleStart = ref('00:00')
const scheduleEnd = ref('23:59')
const scheduleLoading = ref(false)

const statusMap = {
  queued: { type: 'info', label: '排队中' },
  processing: { type: 'warning', label: '转录中' },
  done: { type: 'success', label: '已完成' },
  cancelled: { type: 'danger', label: '已取消' },
}

const engineLabel = (engine) => {
  return 'Qwen ASR'
}

function tagOf(status) {
  return statusMap[status] || { type: 'info', label: status }
}

function formatDate(d) {
  if (!d) return '—'
  return formatDateTime(d)
}

function formatDuration(s) {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}分${sec}秒`
}

async function loadQueue() {
  loading.value = true
  try {
    const token = localStorage.getItem('mt_token')
    const resp = await fetch(`/api/transcription-queue${token ? '?token=' + encodeURIComponent(token) : ''}`)
    if (!resp.ok) throw new Error('加载失败')
    queue.value = await resp.json()
  } catch (e) {
    ElMessage.error('加载队列失败：' + (e.message || ''))
  } finally {
    loading.value = false
  }
}

async function loadSchedule() {
  try {
    const token = localStorage.getItem('mt_token')
    const resp = await fetch(`/api/settings/transcription-schedule${token ? '?token=' + encodeURIComponent(token) : ''}`)
    if (!resp.ok) throw new Error('加载失败')
    const data = await resp.json()
    scheduleEnabled.value = data.enabled
    scheduleStart.value = data.start_time
    scheduleEnd.value = data.end_time
  } catch (e) {
    // 静默失败
  }
}

async function saveSchedule() {
  scheduleLoading.value = true
  try {
    const token = localStorage.getItem('mt_token')
    const resp = await fetch(`/api/settings/transcription-schedule${token ? '?token=' + encodeURIComponent(token) : ''}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: scheduleEnabled.value,
        start_time: scheduleStart.value,
        end_time: scheduleEnd.value,
      }),
    })
    if (!resp.ok) throw new Error('保存失败')
    ElMessage.success('转录时间设置已保存')
  } catch (e) {
    ElMessage.error('保存失败：' + (e.message || ''))
  } finally {
    scheduleLoading.value = false
  }
}

async function cancelQueueItem(item) {
  try {
    await ElMessageBox.confirm(`确认取消 "${item.recording_title}" 的转录？`, '取消转录', { type: 'warning' })
    const token = localStorage.getItem('mt_token')
    const resp = await fetch(`/api/transcription-queue/${item.id}${token ? '?token=' + encodeURIComponent(token) : ''}`, {
      method: 'DELETE',
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: '操作失败' }))
      throw new Error(err.detail || '操作失败')
    }
    ElMessage.success('已取消')
    await loadQueue()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('操作失败：' + (e.message || ''))
    }
  }
}

// 切换转录引擎（仅排队中的任务可切换）
async function switchEngine(item) {
  // 当前仅支持 qwen_asr 引擎，无需切换
  ElMessage.info('当前仅支持 Qwen ASR 引擎')
}

function goToDetail(recordingId) {
  router.push({ name: 'detail', params: { id: recordingId } })
}

onMounted(() => {
  loadQueue()
  loadSchedule()
})
</script>

<template>
  <div>
    <div class="page-header">
      <span class="page-title">📋 待转录队列</span>
      <el-button @click="loadQueue" :loading="loading">刷新</el-button>
    </div>

    <!-- 转录时间设置 -->
    <el-card shadow="never" style="margin-bottom: 20px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>🕐 转录时间段设置</span>
          <el-switch v-model="scheduleEnabled" />
        </div>
      </template>
      <div style="display: flex; align-items: center; gap: 12px">
        <span>在以下时间段内自动处理队列中的转录任务：</span>
        <el-time-picker
          v-model="scheduleStart"
          format="HH:mm"
          value-format="HH:mm"
          :disabled="!scheduleEnabled"
          style="width: 120px"
        />
        <span>至</span>
        <el-time-picker
          v-model="scheduleEnd"
          format="HH:mm"
          value-format="HH:mm"
          :disabled="!scheduleEnabled"
          style="width: 120px"
        />
        <el-button type="primary" size="small" @click="saveSchedule" :loading="scheduleLoading" :disabled="!scheduleEnabled">保存设置</el-button>
        <span v-if="!scheduleEnabled" style="color: var(--el-text-color-secondary); font-size: 13px">
          （未启用，上传后立即转录）
        </span>
      </div>
    </el-card>

    <!-- 队列列表 -->
    <el-table :data="queue" v-loading="loading" style="width: 100%" stripe>
      <el-table-column label="录音" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <el-link type="primary" @click="goToDetail(row.recording_id)">{{ row.recording_title }}</el-link>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="tagOf(row.status).type" size="small">{{ tagOf(row.status).label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="转录引擎" width="130">
        <template #default="{ row }">
          <el-tag size="small" type="primary">{{ engineLabel(row.engine) }}</el-tag>
          <el-button
            v-if="row.status === 'queued'"
            size="small"
            text
            style="margin-left: 4px"
            @click="switchEngine(row)"
          >切换</el-button>
        </template>
      </el-table-column>
      <el-table-column label="录音状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" type="info">{{ row.recording_status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时长" width="100">
        <template #default="{ row }">
          {{ formatDuration(row.recording_duration) }}
        </template>
      </el-table-column>
      <el-table-column label="排队时间" width="180">
        <template #default="{ row }">
          {{ formatDate(row.queued_at) }}
        </template>
      </el-table-column>
      <el-table-column label="开始时间" width="180">
        <template #default="{ row }">
          {{ formatDate(row.started_at) }}
        </template>
      </el-table-column>
      <el-table-column label="完成时间" width="180">
        <template #default="{ row }">
          {{ formatDate(row.completed_at) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'queued'"
            size="small"
            type="danger"
            text
            @click="cancelQueueItem(row)"
          >取消</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!queue.length && !loading" description="暂无待转录任务" />
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
</style>
