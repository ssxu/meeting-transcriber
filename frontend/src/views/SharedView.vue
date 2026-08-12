<script setup>
import { onMounted, ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import api from '../api'
import AudioPlayer from '../components/AudioPlayer.vue'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const rec = ref(null)
const activeTab = ref('summary')

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

const summaryHtml = computed(() => {
  if (!rec.value?.summary_text) return ''
  return marked.parse(rec.value.summary_text)
})

const segments = computed(() => {
  if (!rec.value?.transcript_segments) return []
  const segs = rec.value.transcript_segments
  return Array.isArray(segs) ? segs : []
})

function formatTimestamp(seconds) {
  if (seconds === undefined || seconds === null) return ''
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatDuration(s) {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}分${sec}秒`
}

async function load() {
  try {
    rec.value = await api.sharedDetail(route.params.token)
  } catch (e) {
    ElMessage.error(e._msg || '加载失败')
  }
}

function goBack() {
  if (window.history.length > 1) {
    window.history.back()
  } else {
    window.location.href = '/'
  }
}

onMounted(load)
</script>

<template>
  <div v-if="rec">
    <div class="share-header">
      <div class="header-left">
        <el-tag :type="tagOf(rec.status).type">{{ tagOf(rec.status).label }}</el-tag>
        <span style="font-size: 13px; color: var(--el-text-color-secondary)">只读分享</span>
      </div>
      <el-button size="small" text @click="goBack">返回</el-button>
    </div>

    <!-- 音频播放器 -->
    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <span>{{ rec.title || rec.original_filename }}</span>
      </template>
      <div style="display: flex; flex-direction: column; gap: 12px">
        <audio-player
          :src="api.audioShareUrl(route.params.token)"
          :duration="rec.duration"
        />
        <div class="meta-row">
          <span v-if="rec.duration">时长：{{ formatDuration(rec.duration) }}</span>
          <span v-if="rec.language">语言：{{ rec.language }}</span>
          <span>大小：{{ (rec.file_size / 1024 / 1024).toFixed(2) }} MB</span>
          <span>{{ formatDateTime(rec.created_at) }}</span>
        </div>
      </div>
    </el-card>

    <el-divider />

    <!-- Tab 切换 -->
    <el-tabs v-model="activeTab">
      <!-- 摘要 -->
      <el-tab-pane label="📋 会议摘要" name="summary">
        <el-card shadow="never">
          <div
            v-if="rec.summary_text"
            v-html="summaryHtml"
            class="markdown-body"
            style="line-height: 1.8"
          ></div>
          <span v-else style="color: #aaa">暂无摘要</span>
        </el-card>
      </el-tab-pane>

      <!-- 逐字稿 -->
      <el-tab-pane label="📝 逐字稿" name="transcript">
        <el-card shadow="never">
          <div v-if="segments.length" style="max-height: 600px; overflow-y: auto; line-height: 1.8">
            <div
              v-for="(seg, i) in segments"
              :key="i"
              class="segment-row"
            >
              <span
                v-if="seg.speaker"
                style="font-weight: 600; color: var(--el-color-primary); margin-right: 8px"
              >[{{ seg.speaker }}]</span>
              <span
                v-if="seg.start !== undefined"
                style="color: var(--el-color-success); font-size: 12px; margin-right: 8px"
              >{{ formatTimestamp(seg.start) }}</span>
              <span>{{ seg.text }}</span>
            </div>
          </div>
          <pre
            v-else-if="rec.transcript_text"
            style="white-space: pre-wrap; line-height: 1.8; margin: 0; font-family: inherit"
          >{{ rec.transcript_text }}</pre>
          <span v-else style="color: #aaa">暂无逐字稿</span>
        </el-card>
      </el-tab-pane>

      <!-- 待办事项 -->
      <el-tab-pane label="✅ 待办事项" name="action_items">
        <el-card shadow="never">
          <div v-if="rec.action_items && rec.action_items.length">
            <div
              v-for="(item, i) in rec.action_items"
              :key="i"
              class="action-item"
            >
              <span style="margin-right: 8px; color: var(--el-color-success)">☐</span>
              <span>{{ item }}</span>
            </div>
          </div>
          <el-empty v-else description="暂无待办事项" />
        </el-card>
      </el-tab-pane>

      <!-- 关键词 -->
      <el-tab-pane label="🏷 关键词" name="keywords">
        <el-card shadow="never">
          <div v-if="rec.keywords && rec.keywords.length" style="display: flex; gap: 8px; flex-wrap: wrap">
            <el-tag
              v-for="kw in rec.keywords"
              :key="kw"
              type="primary"
              size="large"
              round
            >{{ kw }}</el-tag>
          </div>
          <el-empty v-else description="暂无关键词" />
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
  <div v-else v-loading="true" style="margin-top: 80px; min-height: 200px"></div>
</template>

<style scoped>
.share-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.meta-row {
  display: flex;
  gap: 20px;
  font-size: 13px;
  color: #888;
  flex-wrap: wrap;
}

.segment-row {
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.02);
}

.action-item {
  padding: 10px 12px;
  margin-bottom: 8px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.02);
}

.markdown-body :deep(h1) { font-size: 1.4em; margin: 0.6em 0 0.4em; }
.markdown-body :deep(h2) { font-size: 1.2em; margin: 0.6em 0 0.4em; }
.markdown-body :deep(h3) { font-size: 1.1em; margin: 0.4em 0 0.3em; }
.markdown-body :deep(ul) { padding-left: 1.5em; }
.markdown-body :deep(ol) { padding-left: 1.5em; }
.markdown-body :deep(li) { margin: 0.2em 0; }
.markdown-body :deep(p) { margin: 0.5em 0; }
.markdown-body :deep(code) { background: rgba(0,0,0,0.06); padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }
.markdown-body :deep(blockquote) { border-left: 3px solid #ddd; padding-left: 12px; color: #666; margin: 0.5em 0; }
</style>
