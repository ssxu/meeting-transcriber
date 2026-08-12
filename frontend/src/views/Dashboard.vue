<script setup>
import { onMounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { formatDateShort } from '../utils/format'

const router = useRouter()

// ── State ──
const stats = ref(null)
const loading = ref(false)

const dateRange = ref([])          // [Date, Date] | null
const dateFrom = ref('')
const dateTo = ref('')

const recentMeetings = ref([])      // recent completed recordings
const meetingsAnalysis = ref({})    // { recId: { speakers: [{name, duration, percentage, segments}], total_duration } }
const analysisLoading = ref({})     // { recId: true/false }

const speakerSummary = ref([])
const speakerLoading = ref(false)

// ── Helpers ──

function formatDuration(s) {
  if (!s || s <= 0) return '0 分钟'
  const hours = Math.floor(s / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  const seconds = Math.floor(s % 60)
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`
  if (minutes > 0) return `${minutes} 分 ${seconds} 秒`
  return `${seconds} 秒`
}

function formatDurationShort(s) {
  if (!s || s <= 0) return '0 分'
  const hours = Math.floor(s / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

function formatDate(d) {
  if (!d) return '-'
  return formatDateShort(d)
}

// Speaker bar colors palette
const speakerColors = [
  '#409EFF', '#67C23A', '#E6A23C', '#F56C6C', '#909399',
  '#9B59B6', '#1ABC9C', '#3498DB', '#E74C3C', '#2ECC71',
  '#F39C12', '#8E44AD', '#16A085', '#D35400', '#7F8C8D'
]

function speakerColor(index) {
  return speakerColors[index % speakerColors.length]
}

const statusColors = {
  pending: 'info',
  transcribing: 'warning',
  transcribed: 'primary',
  summarizing: 'warning',
  done: 'success',
  error: 'danger'
}

const statusLabels = {
  pending: '等待中',
  transcribing: '转录中',
  transcribed: '已转录',
  summarizing: '总结中',
  done: '已完成',
  error: '失败'
}

// ── Data loading ──

async function loadStats() {
  try {
    stats.value = await api.statsWithFilter({ date_from: dateFrom.value, date_to: dateTo.value })
  } catch (e) {
    ElMessage.error(e._msg || '加载统计数据失败')
  }
}

async function loadRecentMeetings() {
  try {
    const params = { page: 1, page_size: 20, q: '' }
    const data = await api.list(params)
    // filter only completed meetings (status === 'done')
    const done = (data.items || data.records || []).filter(r => r.status === 'done')
    recentMeetings.value = done.slice(0, 5)
    // auto-load analysis for each meeting
    for (const m of recentMeetings.value) {
      loadMeetingAnalysis(m.id)
    }
  } catch (e) {
    // silent fail — meetings list is secondary
    console.error('loadRecentMeetings', e)
  }
}

async function loadMeetingAnalysis(recId) {
  analysisLoading.value[recId] = true
  try {
    const data = await api.meetingAnalysis(recId)
    meetingsAnalysis.value[recId] = data
  } catch (e) {
    // silent — analysis endpoint might not be available yet
    meetingsAnalysis.value[recId] = null
  } finally {
    analysisLoading.value[recId] = false
  }
}

async function loadSpeakerSummary() {
  speakerLoading.value = true
  try {
    const data = await api.speakerSummary({ date_from: dateFrom.value, date_to: dateTo.value })
    speakerSummary.value = data.speakers || data || []
  } catch (e) {
    speakerSummary.value = []
  } finally {
    speakerLoading.value = false
  }
}

async function loadAll() {
  loading.value = true
  await Promise.allSettled([
    loadStats(),
    loadRecentMeetings(),
    loadSpeakerSummary()
  ])
  loading.value = false
}

function applyFilter() {
  if (dateRange.value && dateRange.value.length === 2) {
    const [start, end] = dateRange.value
    dateFrom.value = start instanceof Date ? start.toISOString().slice(0, 10) : String(start)
    dateTo.value = end instanceof Date ? end.toISOString().slice(0, 10) : String(end)
  } else {
    dateFrom.value = ''
    dateTo.value = ''
  }
  loadAll()
}

function resetFilter() {
  dateRange.value = []
  dateFrom.value = ''
  dateTo.value = ''
  loadAll()
}

// ── Computed ──

const overviewCards = computed(() => {
  if (!stats.value) return []
  const s = stats.value
  return [
    { title: '录音总数', value: s.total ?? 0, icon: '📁' },
    { title: '总时长', value: formatDuration(s.total_duration), icon: '⏱️' },
    { title: '近7天新增', value: s.recent_7d_count ?? 0, icon: '📅' },
    { title: '近7天时长', value: formatDuration(s.recent_7d_duration), icon: '📊' },
    { title: '发言人数量', value: s.speaker_count ?? s.unique_speakers ?? 0, icon: '👥' },
    { title: '平均时长', value: formatDuration(s.avg_duration ?? (s.total > 0 ? s.total_duration / s.total : 0)), icon: '📈' },
  ]
})

const statusBreakdownEntries = computed(() => {
  if (!stats.value?.status_breakdown) return []
  return Object.entries(stats.value.status_breakdown)
})

// ── Init ──

onMounted(() => {
  loadAll()
})
</script>

<template>
  <div v-loading="loading" class="dashboard-page">
    <div class="dashboard-header">
      <span class="dashboard-title">📊 仪表盘</span>
      <el-button text @click="loadAll">🔄 刷新</el-button>
    </div>
    <el-divider />

    <!-- ════ Section 1: Date Range Filter ════ -->
    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <span class="filter-label">时间范围：</span>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          format="YYYY-MM-DD"
          value-format="YYYY-MM-DD"
          :clearable="true"
          style="width: 320px"
        />
        <el-button type="primary" @click="applyFilter">查询</el-button>
        <el-button @click="resetFilter">重置</el-button>
        <span v-if="dateFrom || dateTo" class="filter-active-hint">
          当前筛选：{{ dateFrom || '不限' }} ~ {{ dateTo || '不限' }}
        </span>
      </div>
    </el-card>

    <!-- ════ Section 2: Overview Cards ════ -->
    <el-row :gutter="16" class="section-row">
      <el-col
        v-for="(card, idx) in overviewCards"
        :key="idx"
        :xs="12"
        :sm="12"
        :md="8"
        :lg="4"
      >
        <el-card shadow="hover" class="overview-card">
          <div class="overview-card-inner">
            <span class="overview-icon">{{ card.icon }}</span>
            <div class="overview-info">
              <div class="overview-label">{{ card.title }}</div>
              <div class="overview-value">{{ card.value }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ════ Section 3: Status Distribution ════ -->
    <el-card class="section-card">
      <template #header>
        <span class="section-header">🏷️ 处理状态分布</span>
      </template>
      <div class="status-tags">
        <el-tag
          v-for="([status, count]) in statusBreakdownEntries"
          :key="status"
          :type="statusColors[status] || 'info'"
          size="large"
        >
          {{ statusLabels[status] || status }}: {{ count }}
        </el-tag>
      </div>
      <el-empty v-if="!statusBreakdownEntries.length" description="暂无数据" :image-size="60" />
    </el-card>

    <!-- ════ Section 4: Meeting Efficiency Analysis ════ -->
    <el-card class="section-card">
      <template #header>
        <span class="section-header">🎙️ 会议效率分析</span>
        <span class="section-sub-header">最近 {{ recentMeetings.length }} 场已完成会议</span>
      </template>

      <el-empty v-if="!recentMeetings.length" description="暂无已完成的会议" :image-size="80" />

      <div v-else class="meeting-analysis-list">
        <div
          v-for="meeting in recentMeetings"
          :key="meeting.id"
          class="meeting-analysis-item"
          @click="router.push(`/recordings/${meeting.id}`)"
        >
          <div class="meeting-item-header">
            <div class="meeting-item-title">
              <el-icon><Microphone /></el-icon>
              <span>{{ meeting.title || '未命名会议' }}</span>
            </div>
            <div class="meeting-item-meta">
              <el-tag size="small" type="info">{{ formatDate(meeting.created_at) }}</el-tag>
              <el-tag size="small">⏱ {{ formatDurationShort(meeting.duration) }}</el-tag>
              <el-tag size="small" type="success">👥 {{ meetingsAnalysis[meeting.id]?.speakers?.length || meeting.speaker_count || '-' }} 人</el-tag>
            </div>
          </div>

          <!-- Speaker speaking distribution bars -->
          <div class="meeting-speakers">
            <template v-if="analysisLoading[meeting.id]">
              <el-skeleton :rows="2" animated />
            </template>
            <template v-else-if="meetingsAnalysis[meeting.id] && meetingsAnalysis[meeting.id].speakers">
              <div
                v-for="(sp, i) in meetingsAnalysis[meeting.id].speakers"
                :key="sp.name"
                class="speaker-bar-row"
              >
                <div class="speaker-bar-label">
                  <span
                    class="speaker-dot"
                    :style="{ backgroundColor: speakerColor(i) }"
                  ></span>
                  <span class="speaker-name">{{ sp.name || '未知' }}</span>
                  <span class="speaker-pct">{{ sp.percentage != null ? sp.percentage.toFixed(1) + '%' : '' }}</span>
                </div>
                <el-progress
                  :percentage="Math.round(sp.percentage || 0)"
                  :color="speakerColor(i)"
                  :stroke-width="14"
                  :show-text="false"
                  class="speaker-progress"
                />
                <span class="speaker-duration">{{ formatDurationShort(sp.duration) }}</span>
              </div>
            </template>
            <template v-else>
              <span class="analysis-unavailable">暂无分析数据</span>
            </template>
          </div>

          <div class="meeting-item-footer">
            <el-button text type="primary" size="small" @click.stop="router.push(`/recordings/${meeting.id}`)">
              查看详情 →
            </el-button>
          </div>
        </div>
      </div>
    </el-card>

    <!-- ════ Section 5: Speaker Summary ════ -->
    <el-card class="section-card">
      <template #header>
        <span class="section-header">👥 发言人统计</span>
      </template>

      <el-table
        v-loading="speakerLoading"
        :data="speakerSummary"
        stripe
        style="width: 100%"
        empty-text="暂无发言人数据"
      >
        <el-table-column type="index" label="#" width="50" />

        <el-table-column prop="name" label="发言人" min-width="120">
          <template #default="{ row, $index }">
            <div class="speaker-cell">
              <span
                class="speaker-dot"
                :style="{ backgroundColor: speakerColor($index) }"
              ></span>
              <span>{{ row.name || row.speaker || '未知' }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="参与会议数" min-width="110" align="center">
          <template #default="{ row }">
            {{ row.meeting_count ?? row.meetings ?? 0 }}
          </template>
        </el-table-column>

        <el-table-column label="总发言时长" min-width="140">
          <template #default="{ row }">
            {{ formatDuration(row.total_duration ?? row.total_speaking_time ?? 0) }}
          </template>
        </el-table-column>

        <el-table-column label="场均发言时长" min-width="140">
          <template #default="{ row }">
            {{ formatDuration(row.avg_duration ?? (row.meeting_count ? (row.total_duration ?? 0) / row.meeting_count : 0)) }}
          </template>
        </el-table-column>

        <el-table-column label="总发言段数" min-width="110" align="center">
          <template #default="{ row }">
            {{ row.segment_count ?? row.segments ?? 0 }}
          </template>
        </el-table-column>

        <!-- Speaking time bar -->
        <el-table-column label="发言占比" min-width="200">
          <template #default="{ row, $index }">
            <el-progress
              :percentage="Math.min(100, Math.round(((row.total_duration ?? row.total_speaking_time ?? 0) / (speakerSummary.length ? Math.max(...speakerSummary.map(s => s.total_duration ?? s.total_speaking_time ?? 0)) : 1)) * 100))"
              :color="speakerColor($index)"
              :stroke-width="12"
              :text-inside="true"
            />
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!speakerLoading && !speakerSummary.length"
        description="暂无发言人统计数据"
        :image-size="80"
      />
    </el-card>

    <!-- Quick Actions -->
    <el-card class="section-card">
      <template #header>
        <span class="section-header">⚡ 快捷操作</span>
      </template>
      <div class="quick-actions">
        <el-button type="primary" @click="router.push('/')">查看录音列表</el-button>
        <el-button @click="loadAll">刷新数据</el-button>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.dashboard-page {
  max-width: 1200px;
  margin: 0 auto;
}

.dashboard-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.dashboard-title {
  font-size: 20px;
  font-weight: bold;
}

/* ── Filter ── */
.filter-card {
  margin-bottom: 16px;
}
.filter-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.filter-label {
  font-weight: 500;
  white-space: nowrap;
}
.filter-active-hint {
  color: var(--el-color-primary);
  font-size: 13px;
}

/* ── Overview cards ── */
.section-row {
  margin-bottom: 16px;
}
.overview-card {
  margin-bottom: 8px;
}
.overview-card-inner {
  display: flex;
  align-items: center;
  gap: 12px;
}
.overview-icon {
  font-size: 28px;
  line-height: 1;
}
.overview-info {
  flex: 1;
  min-width: 0;
}
.overview-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.overview-value {
  font-size: 18px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Section cards ── */
.section-card {
  margin-top: 16px;
}
.section-header {
  font-size: 16px;
  font-weight: 600;
}
.section-sub-header {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: 12px;
}

/* ── Status tags ── */
.status-tags {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

/* ── Meeting analysis ── */
.meeting-analysis-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.meeting-analysis-item {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 16px;
  cursor: pointer;
  transition: box-shadow 0.2s, border-color 0.2s;
}
.meeting-analysis-item:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  border-color: var(--el-color-primary-light-5);
}
.meeting-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.meeting-item-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 600;
}
.meeting-item-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.meeting-speakers {
  margin: 8px 0;
}
.speaker-bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.speaker-bar-label {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 140px;
}
.speaker-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.speaker-name {
  font-size: 13px;
  font-weight: 500;
}
.speaker-pct {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-left: auto;
}
.speaker-progress {
  flex: 1;
  min-width: 100px;
}
.speaker-duration {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  min-width: 50px;
  text-align: right;
}
.analysis-unavailable {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.meeting-item-footer {
  text-align: right;
  margin-top: 4px;
}

/* ── Speaker table ── */
.speaker-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ── Quick actions ── */
.quick-actions {
  display: flex;
  gap: 12px;
}

/* ── Responsive ── */
@media (max-width: 768px) {
  .meeting-item-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .speaker-bar-label {
    min-width: 100px;
  }
}
</style>
