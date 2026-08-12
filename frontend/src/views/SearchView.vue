<script setup>
import { ref, computed, onMounted } from 'vue'
import { formatDateTime } from '../utils/format'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'

const router = useRouter()

// 搜索状态
const searchInput = ref('')
const searchMode = ref('auto')  // auto / keyword / semantic / hybrid
const searchExecuted = ref(false)
const loading = ref(false)

// 筛选
const filterSpeaker = ref('')
const filterDateFrom = ref('')
const filterDateTo = ref('')
const filterTag = ref('')

// 排序
const sortBy = ref('relevance')  // relevance / time

// 结果
const results = ref([])
const totalResults = ref(0)
const actualMode = ref('')

// 搜索系统状态
const sysStatus = ref(null)
const showIndexPanel = ref(false)
const reindexLoadingIncremental = ref(false)
const reindexLoadingFull = ref(false)

// 标签列表
const allTags = ref([])

// 说话人列表（从搜索结果中动态收集）
const speakerOptions = computed(() => {
  const set = new Set()
  results.value.forEach(r => {
    if (r.speakers) r.speakers.forEach(s => set.add(s))
  })
  return [...set].map(s => ({ label: s, value: s }))
})

const modeOptions = [
  { label: '自动', value: 'auto' },
  { label: '全文搜索', value: 'keyword' },
  { label: '语义搜索', value: 'semantic' },
  { label: '混合搜索', value: 'hybrid' },
]

const modeLabel = (m) => {
  const map = { auto: '自动', keyword: '全文', semantic: '语义', hybrid: '混合', empty: '-', semantic_error: '语义错误' }
  return map[m] || m
}

async function doSearch() {
  const q = searchInput.value.trim()
  if (!q) return
  loading.value = true
  searchExecuted.value = true
  try {
    const data = await api.search({
      q,
      mode: searchMode.value,
      speaker: filterSpeaker.value,
      date_from: filterDateFrom.value || '',
      date_to: filterDateTo.value || '',
      tag: filterTag.value || '',
      sort_by: sortBy.value,
      limit: 50,
    })
    results.value = data.items || []
    totalResults.value = data.total || 0
    actualMode.value = data.mode || ''
    if (data.error) {
      ElMessage.warning(`语义搜索出错: ${data.error}`)
    }
  } catch (e) {
    ElMessage.error(e._msg || '搜索失败')
    results.value = []
  } finally {
    loading.value = false
  }
}

function clearFilters() {
  filterSpeaker.value = ''
  filterDateFrom.value = ''
  filterDateTo.value = ''
  filterTag.value = ''
  if (searchExecuted.value) doSearch()
}

function onSortChange() {
  if (searchExecuted.value) doSearch()
}

async function loadStatus() {
  try {
    sysStatus.value = await api.searchStatus()
  } catch (e) {
    // ignore
  }
}

async function loadTags() {
  try {
    allTags.value = await api.tags()
  } catch (e) {
    // ignore
  }
}

async function reindexIncremental() {
  reindexLoadingIncremental.value = true
  try {
    const res = await api.reindexAll()
    ElMessage.success(res.detail || '索引完成')
    loadStatus()
  } catch (e) {
    ElMessage.error(e._msg || '索引失败')
  } finally {
    reindexLoadingIncremental.value = false
  }
}

async function reindexFull() {
  reindexLoadingFull.value = true
  try {
    const res = await api.reindexFull()
    ElMessage.success(res.detail || '索引完成')
    loadStatus()
  } catch (e) {
    ElMessage.error(e._msg || '索引失败')
  } finally {
    reindexLoadingFull.value = false
  }
}

function formatDate(s) {
  if (!s) return ''
  return formatDateTime(s)
}

onMounted(() => {
  loadStatus()
  loadTags()
})
</script>

<template>
  <div class="search-page">
    <!-- 搜索头部 -->
    <div class="search-header">
      <div class="search-bar">
        <el-input
          v-model="searchInput"
          placeholder="搜索会议内容…（支持自然语言提问，如「讨论过预算的会议」）"
          clearable
          size="large"
          style="flex: 1"
          @keyup.enter="doSearch"
        />
        <el-select v-model="searchMode" style="width: 130px" size="large">
          <el-option
            v-for="opt in modeOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-button type="primary" size="large" @click="doSearch" :loading="loading">搜索</el-button>
      </div>

      <!-- 筛选栏 -->
      <div class="filter-bar">
        <el-input
          v-model="filterSpeaker"
          placeholder="说话人"
          clearable
          style="width: 140px"
          v-if="!speakerOptions.length || searchExecuted"
        />
        <el-select
          v-else
          v-model="filterSpeaker"
          placeholder="说话人"
          clearable
          filterable
          style="width: 140px"
        >
          <el-option v-for="opt in speakerOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
        <el-date-picker
          v-model="filterDateFrom"
          type="date"
          placeholder="开始日期"
          value-format="YYYY-MM-DD"
          style="width: 140px"
        />
        <span style="color: #999">~</span>
        <el-date-picker
          v-model="filterDateTo"
          type="date"
          placeholder="截止日期"
          value-format="YYYY-MM-DD"
          style="width: 140px"
        />
        <el-select v-model="filterTag" placeholder="标签" clearable filterable style="width: 150px">
          <el-option v-for="t in allTags" :key="t" :label="t" :value="t" />
        </el-select>
        <el-radio-group v-model="sortBy" size="small" @change="onSortChange">
          <el-radio-button value="relevance">相关度</el-radio-button>
          <el-radio-button value="time">时间</el-radio-button>
        </el-radio-group>
        <el-button text size="small" @click="clearFilters">清除筛选</el-button>
        <el-button text size="small" @click="showIndexPanel = !showIndexPanel">
          🔧 索引管理
        </el-button>
      </div>
    </div>

    <!-- 索引管理面板 -->
    <el-collapse-transition>
      <div v-if="showIndexPanel" class="index-panel">
        <el-card shadow="never">
          <template #header>
            <span style="font-weight: 600">向量索引管理</span>
          </template>
          <div v-if="sysStatus" class="index-status">
            <el-descriptions :column="3" border size="small">
              <el-descriptions-item label="Embedding 模型">
                <el-tag :type="sysStatus.embedding_model ? 'success' : 'danger'" size="small">
                  {{ sysStatus.embedding_model || '未配置' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="Rerank 模型">
                <el-tag :type="sysStatus.rerank_model ? 'success' : 'info'" size="small">
                  {{ sysStatus.rerank_model || '未配置' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="索引覆盖率">
                {{ sysStatus.coverage }}
              </el-descriptions-item>
            </el-descriptions>
            <div style="margin-top: 12px; display: flex; gap: 8px; align-items: center">
              <el-button
                size="small"
                :loading="reindexLoadingIncremental"
                :disabled="!sysStatus?.semantic_ready"
                @click="reindexIncremental"
              >
                增量索引剩余
              </el-button>
              <el-button
                type="primary"
                size="small"
                :loading="reindexLoadingFull"
                :disabled="!sysStatus?.semantic_ready"
                @click="reindexFull"
              >
                重新索引全部
              </el-button>
              <span v-if="!sysStatus.semantic_ready" style="margin-left: 8px; color: #f56c6c; font-size: 13px">
                请先在模型管理中添加 embedding 类型的模型
              </span>
            </div>
          </div>
        </el-card>
      </div>
    </el-collapse-transition>

    <!-- 搜索结果 -->
    <div class="search-results" v-if="searchExecuted">
      <div class="results-meta">
        <span>找到 <strong>{{ totalResults }}</strong> 条结果</span>
        <el-tag size="small" type="info">{{ modeLabel(actualMode) }}</el-tag>
      </div>

      <el-empty v-if="!results.length && !loading" description="未找到匹配的会议" />

      <div v-if="results.length" class="result-list">
        <el-card
          v-for="item in results"
          :key="item.id"
          shadow="hover"
          class="result-card"
          @click="router.push(`/recordings/${item.id}`)"
        >
          <div class="result-card-header">
            <span class="result-title">{{ item.title }}</span>
            <span class="result-score" v-if="item.score">相关度 {{ (item.score * 100).toFixed(1) }}%</span>
          </div>
          <div class="result-snippet" v-html="item.snippet"></div>
          <div class="result-meta">
            <span>{{ formatDate(item.created_at) }}</span>
            <span v-if="item.speakers && item.speakers.length" class="speaker-tags">
              <el-tag v-for="sp in item.speakers.slice(0, 3)" :key="sp" size="small" type="info">{{ sp }}</el-tag>
            </span>
            <span v-if="item.tags && item.tags.length" class="tag-tags">
              <el-tag v-for="t in item.tags.slice(0, 3)" :key="t" size="small" type="warning" effect="plain">{{ t }}</el-tag>
            </span>
          </div>
        </el-card>
      </div>
    </div>

    <!-- 未搜索时的提示 -->
    <div v-if="!searchExecuted" class="search-hint">
      <el-empty description="输入关键词或自然语言开始搜索会议内容" />
    </div>
  </div>
</template>

<style scoped>
.search-page {
  max-width: 900px;
  margin: 0 auto;
}

.search-header {
  margin-bottom: 20px;
}

.search-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.index-panel {
  margin-bottom: 20px;
}

.results-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result-card {
  cursor: pointer;
  transition: border-color 0.2s;
}

.result-card:hover {
  border-color: var(--el-color-primary);
}

.result-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.result-title {
  font-weight: 600;
  font-size: 15px;
}

.result-score {
  font-size: 12px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 2px 8px;
  border-radius: 4px;
}

.result-snippet {
  font-size: 14px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
  margin-bottom: 8px;
}

.result-snippet :deep(mark) {
  background: #fff3cd;
  padding: 0 2px;
  border-radius: 2px;
  font-weight: 600;
}

.result-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  flex-wrap: wrap;
}

.speaker-tags, .tag-tags {
  display: inline-flex;
  gap: 4px;
}

.search-hint {
  margin-top: 80px;
}
</style>
