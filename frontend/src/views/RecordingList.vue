<script setup>
import { onMounted, onUnmounted, ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { formatDateTime } from '../utils/format'

const router = useRouter()

// 视图切换
const viewMode = ref(localStorage.getItem('mt_list_view') || 'table')
const isWideScreen = ref(window.matchMedia('(min-width: 1200px)').matches)
let mqHandler = null

function switchView(mode) {
  viewMode.value = mode
  localStorage.setItem('mt_list_view', mode)
}

// 列表数据
const recordings = ref([])
const loading = ref(false)
const total = ref(0)
const totalPages = ref(0)

// 分页
const page = ref(1)
const pageSize = ref(12)

// 搜索与过滤
const searchQuery = ref('')
const searchInput = ref('')
const selectedTag = ref(null)
const allTags = ref([])

// 热词库和会议类型选项
const hotwordLibs = ref([])
const meetingTypes = ref([])
const asrProviders = ref([])

// 上传选项
const uploadModal = ref(false)
const uploadFile = ref(null)
const uploadSrtFile = ref(null)
const uploadHotwordLib = ref(null)
const uploadMeetingType = ref(null)
const uploadEngine = ref('qwen_asr')
const uploadAsrProvider = ref(null)

// 批量选择
const checkedIds = ref(new Set())
const batchMode = ref(false)

// 重命名模态框
const renameModal = ref(false)
const renameTarget = ref(null)
const renameTitle = ref('')

// 实时录音
const isRecording = ref(false)
const mediaRecorder = ref(null)
const audioChunks = ref([])
const recordingTime = ref(0)
let recordingTimer = null

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

function formatSize(bytes) {
  return (bytes / 1024 / 1024).toFixed(2) + ' MB'
}

const selectedCount = computed(() => checkedIds.value.size)

const hotwordOptions = computed(() => {
  const opts = [{ label: '不使用热词库', value: null }]
  hotwordLibs.value.forEach(lib => {
    opts.push({ label: `${lib.name} (${lib.hotword_count}词)`, value: lib.id })
  })
  return opts
})

const meetingTypeOptions = computed(() => {
  const opts = [{ label: '默认（通用提示词）', value: null }]
  meetingTypes.value.forEach(t => {
    opts.push({ label: t.name, value: t.id })
  })
  return opts
})

const tagOptions = computed(() => {
  return allTags.value.map(t => ({ label: t, value: t }))
})

async function load() {
  loading.value = true
  try {
    const [listData, libs, types, tags, asrList] = await Promise.all([
      api.list({
        page: page.value,
        page_size: pageSize.value,
        q: searchQuery.value,
        tag: selectedTag.value || '',
      }),
      api.hotwordLibraries(),
      api.meetingTypes(),
      api.tags(),
      api.enabledAsrProviders(),
    ])
    recordings.value = listData.items || []
    total.value = listData.total || 0
    totalPages.value = listData.total_pages || 0
    hotwordLibs.value = libs
    meetingTypes.value = types
    allTags.value = tags
    asrProviders.value = asrList

    // 仅在首次加载时设置默认选项，避免轮询时覆盖用户手动选择
    if (!hotwordLibs.value.length || !uploadHotwordLib.value) {
      const defaultLib = libs.find(l => l.is_default)
      if (defaultLib) uploadHotwordLib.value = defaultLib.id
    }
    if (!meetingTypes.value.length || !uploadMeetingType.value) {
      const defaultType = types.find(t => t.is_default)
      if (defaultType) uploadMeetingType.value = defaultType.id
    }
    if (!asrProviders.value.length || !uploadAsrProvider.value) {
      const defaultProvider = asrList.find(p => p.is_default)
      if (defaultProvider) uploadAsrProvider.value = defaultProvider.id
    }
  } catch (e) {
    ElMessage.error(e._msg || '加载列表失败')
  } finally {
    loading.value = false
  }
}

// 搜索
function handleSearch() {
  searchQuery.value = searchInput.value.trim()
  page.value = 1
  load()
}

function clearSearch() {
  searchInput.value = ''
  searchQuery.value = ''
  selectedTag.value = null
  page.value = 1
  load()
}

// 标签过滤
watch(selectedTag, () => {
  page.value = 1
  load()
})

// 翻页
function handlePageChange(p) {
  page.value = p
  load()
}

function handlePageSizeChange(ps) {
  pageSize.value = ps
  page.value = 1
  load()
}

// 上传弹窗
function openUploadModal() {
  uploadFile.value = null
  uploadSrtFile.value = null
  uploadEngine.value = 'qwen_asr'
  uploadAsrProvider.value = null
  // 重置为默认值
  const defaultLib = hotwordLibs.value.find(l => l.is_default)
  uploadHotwordLib.value = defaultLib ? defaultLib.id : null
  const defaultType = meetingTypes.value.find(t => t.is_default)
  uploadMeetingType.value = defaultType ? defaultType.id : null
  const defaultProvider = asrProviders.value.find(p => p.is_default)
  uploadAsrProvider.value = defaultProvider ? defaultProvider.id : null
  uploadModal.value = true
}

function onFileSelected(file) {
  uploadFile.value = file.raw || file
}

function onSrtFileSelected(file) {
  uploadSrtFile.value = file.raw || file
}

async function confirmUpload() {
  if (!uploadFile.value) {
    ElMessage.warning('请先选择录音文件')
    return
  }
  try {
    await api.upload(uploadFile.value, uploadHotwordLib.value, uploadMeetingType.value, uploadSrtFile.value, uploadEngine.value, uploadAsrProvider.value)
    let msg = '上传成功'
    if (uploadSrtFile.value) {
      msg = '上传成功，已使用字幕/转录文件跳过转录'
    } else {
      const providerName = asrProviders.value.find(p => p.id === uploadAsrProvider.value)?.name || 'Qwen3-ASR'
      msg = `上传成功，已使用 ${providerName} 引擎加入转录队列`
    }
    ElMessage.success(msg)
    uploadModal.value = false
    uploadFile.value = null
    uploadSrtFile.value = null
    load()
  } catch (e) {
    ElMessage.error('上传失败：' + (e._msg || e.message))
  }
}

// 快速上传（不弹窗，使用默认设置）
async function quickUpload(file) {
  const actualFile = file.raw || file
  if (!actualFile) {
    ElMessage.error('文件获取失败')
    return
  }
  try {
    const defaultLib = hotwordLibs.value.find(l => l.is_default)
    const defaultType = meetingTypes.value.find(t => t.is_default)
    await api.upload(actualFile, defaultLib?.id || null, defaultType?.id || null)
    ElMessage.success('上传成功，已开始转录')
    load()
  } catch (e) {
    ElMessage.error('上传失败：' + (e._msg || e.message))
  }
}

// el-upload 的自定义请求
function uploadRequest(options) {
  quickUpload(options.file)
}

async function deleteRecording(id) {
  try {
    await api.remove(id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    ElMessage.error(e._msg || '删除失败')
  }
}

// 批量操作
function toggleBatchMode() {
  batchMode.value = !batchMode.value
  if (!batchMode.value) checkedIds.value.clear()
}

function toggleCheck(id, checked) {
  if (checked) checkedIds.value.add(id)
  else checkedIds.value.delete(id)
  checkedIds.value = new Set(checkedIds.value)
}

function selectAll() {
  recordings.value.forEach(r => checkedIds.value.add(r.id))
  checkedIds.value = new Set(checkedIds.value)
}

function deselectAll() {
  checkedIds.value.clear()
  checkedIds.value = new Set(checkedIds.value)
}

async function batchDelete() {
  if (!checkedIds.value.size) {
    ElMessage.warning('请先选择录音')
    return
  }
  try {
    await ElMessageBox.confirm(`确认删除 ${checkedIds.value.size} 条录音？`, '批量删除', {
      type: 'warning',
    })
    await api.batchDelete([...checkedIds.value])
    ElMessage.success(`已删除 ${checkedIds.value.size} 条录音`)
    checkedIds.value.clear()
    batchMode.value = false
    load()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error(e._msg || '批量删除失败')
    }
  }
}

function batchExport() {
  if (!checkedIds.value.size) {
    ElMessage.warning('请先选择录音')
    return
  }
  // 使用 POST 批量导出 zip 接口（修复 latin-1 编码问题）
  const ids = [...checkedIds.value]
  const token = localStorage.getItem('mt_token')
  // 使用 GET batch-export 接口（已修复为返回 zip）
  window.open(api.batchExportUrl(ids), '_blank')
}

function openRename(rec) {
  renameTarget.value = rec.id
  renameTitle.value = rec.title || rec.original_filename
  renameModal.value = true
}

async function confirmRename() {
  if (!renameTitle.value.trim()) {
    ElMessage.warning('标题不能为空')
    return
  }
  try {
    await api.rename(renameTarget.value, renameTitle.value.trim())
    ElMessage.success('已重命名')
    renameModal.value = false
    load()
  } catch (e) {
    ElMessage.error(e._msg || '重命名失败')
  }
}

async function copyShareLink(id) {
  try {
    const res = await api.createShare(id)
    const url = `${window.location.origin}/share/${res.share_token}`
    await navigator.clipboard.writeText(url)
    ElMessage.success('分享链接已复制到剪贴板')
  } catch (e) {
    ElMessage.error(e._msg || '生成分享链接失败')
  }
}

// 实时录音
async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    mediaRecorder.value = new MediaRecorder(stream)
    audioChunks.value = []
    recordingTime.value = 0

    mediaRecorder.value.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.value.push(e.data)
    }

    mediaRecorder.value.onstop = async () => {
      stream.getTracks().forEach(t => t.stop())
      clearInterval(recordingTimer)

      const blob = new Blob(audioChunks.value, { type: 'audio/webm' })
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
      const file = new File([blob], `录音_${timestamp}.webm`, { type: 'audio/webm' })

      try {
        const defaultLib = hotwordLibs.value.find(l => l.is_default)
        const defaultType = meetingTypes.value.find(t => t.is_default)
        await api.upload(file, defaultLib?.id || null, defaultType?.id || null)
        ElMessage.success('录音上传成功，已开始转录')
        load()
      } catch (e) {
        ElMessage.error('录音上传失败：' + (e._msg || e.message))
      }
    }

    mediaRecorder.value.start()
    isRecording.value = true
    recordingTimer = setInterval(() => recordingTime.value++, 1000)
  } catch (e) {
    ElMessage.error('无法访问麦克风：' + e.message)
  }
}

function stopRecording() {
  if (mediaRecorder.value && mediaRecorder.value.state !== 'inactive') {
    mediaRecorder.value.stop()
  }
  isRecording.value = false
}

function formatTime(s) {
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`
}

function goToDetail(r) {
  if (batchMode.value) return
  if (!r || !r.id) return
  router.push({ name: 'detail', params: { id: r.id } })
}

async function confirmDeleteRecording(id) {
  try {
    await ElMessageBox.confirm('确认删除此录音？', '删除', { type: 'warning' })
    deleteRecording(id)
  } catch (e) {
    // cancel
  }
}

onMounted(() => {
  const mq = window.matchMedia('(min-width: 1200px)')
  mqHandler = (e) => { isWideScreen.value = e.matches }
  mq.addEventListener('change', mqHandler)
  load()
})

onUnmounted(() => {
  const mq = window.matchMedia('(min-width: 1200px)')
  if (mqHandler) mq.removeEventListener('change', mqHandler)
  if (recordingTimer) clearInterval(recordingTimer)
  if (mediaRecorder.value && mediaRecorder.value.state !== 'inactive') {
    mediaRecorder.value.stop()
  }
})
</script>

<template>
  <div>
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <el-button @click="load" :loading="loading">刷新</el-button>
        <el-button-group>
          <el-button :type="viewMode === 'table' ? 'primary' : 'default'" @click="switchView('table')">📊 表格</el-button>
          <el-button :type="viewMode === 'card' ? 'primary' : 'default'" @click="switchView('card')">📦 卡片</el-button>
        </el-button-group>
        <el-button :type="batchMode ? 'primary' : 'default'" @click="toggleBatchMode">
          {{ batchMode ? '退出批量' : '批量操作' }}
        </el-button>
        <template v-if="batchMode">
          <el-button size="small" text @click="selectAll">全选</el-button>
          <el-button size="small" text @click="deselectAll">取消全选</el-button>
          <el-button size="small" type="danger" :disabled="!selectedCount" @click="batchDelete">
            删除 ({{ selectedCount }})
          </el-button>
          <el-button size="small" type="info" :disabled="!selectedCount" @click="batchExport">
            批量导出
          </el-button>
        </template>
        <el-button
          v-if="!isRecording && !batchMode"
          type="info"
          @click="startRecording"
        >
          🎤 开始录音
        </el-button>
        <el-button
          v-if="isRecording"
          type="danger"
          @click="stopRecording"
        >
          ⏹ 停止录音 ({{ formatTime(recordingTime) }})
        </el-button>
      </div>

      <div class="toolbar-center">
        <el-input
          v-model="searchInput"
          placeholder="搜索标题/逐字稿/摘要…"
          clearable
          style="width: 260px"
          @keyup.enter="handleSearch"
          @clear="clearSearch"
        />
        <el-select
          v-model="selectedTag"
          placeholder="标签筛选"
          clearable
          style="width: 150px"
        >
          <el-option
            v-for="opt in tagOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-button type="primary" plain @click="handleSearch">搜索</el-button>
        <el-button v-if="searchQuery || selectedTag" text size="small" @click="clearSearch">清除</el-button>
      </div>

      <div class="toolbar-right" v-if="!batchMode">
        <el-upload
          :http-request="uploadRequest"
          :show-file-list="false"
          accept="audio/*,video/*,.wav,.mp3,.m4a,.flac,.ogg,.webm,.mp4,.mkv,.avi,.mov,.wmv"
        >
          <el-button type="primary">＋ 快速上传</el-button>
        </el-upload>
        <el-button type="primary" @click="openUploadModal">上传（选择配置）</el-button>
      </div>
    </div>

    <!-- 搜索状态提示 -->
    <div v-if="searchQuery || selectedTag" style="margin-bottom: 12px">
      <span style="font-size: 13px; color: var(--el-text-color-secondary)">
        找到 {{ total }} 条结果
        <span v-if="searchQuery">· 关键词: "{{ searchQuery }}"</span>
        <span v-if="selectedTag">· 标签: {{ selectedTag }}</span>
      </span>
    </div>

    <!-- 录音列表 -->
    <el-skeleton :loading="loading" animated>
      <template #default>
        <el-empty v-if="!recordings.length" description="暂无录音，点击右上角上传或开始录音" />

        <!-- 表格视图 -->
        <el-table
          v-if="recordings.length && viewMode === 'table'"
          :data="recordings"
          @row-click="(row) => goToDetail(row)"
          row-class-name="rec-table-row"
          style="width: 100%"
          :header-cell-style="{ padding: '8px 0' }"
          :cell-style="{ padding: '6px 0' }"
        >
          <el-table-column type="selection" v-if="batchMode" width="40" />
          <el-table-column label="标题" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="table-title">{{ row.title || row.original_filename }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="tagOf(row.status).type" size="small">{{ tagOf(row.status).label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时长" width="70" align="center">
            <template #default="{ row }">{{ formatDuration(row.duration) }}</template>
          </el-table-column>
          <el-table-column label="语言" width="60" align="center">
            <template #default="{ row }">{{ row.language || '—' }}</template>
          </el-table-column>
          <el-table-column label="创建时间" width="140">
            <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column v-if="isWideScreen" label="标签" min-width="120">
            <template #default="{ row }">
              <template v-if="row.tags && row.tags.length">
                <el-tag
                  v-for="t in row.tags.slice(0, 3)"
                  :key="t"
                  size="small"
                  type="warning"
                  effect="plain"
                  round
                  style="margin-right: 4px; margin-bottom: 2px"
                >{{ t }}</el-tag>
                <el-tag v-if="row.tags.length > 3" size="small" type="info" round>+{{ row.tags.length - 3 }}</el-tag>
              </template>
              <span v-else style="color: var(--el-text-color-placeholder)">—</span>
            </template>
          </el-table-column>
          <el-table-column v-if="isWideScreen" label="关键词" min-width="120">
            <template #default="{ row }">
              <template v-if="row.keywords && row.keywords.length">
                <el-tag
                  v-for="kw in row.keywords.slice(0, 3)"
                  :key="kw"
                  size="small"
                  type="primary"
                  round
                  style="margin-right: 4px; margin-bottom: 2px"
                >{{ kw }}</el-tag>
                <el-tag v-if="row.keywords.length > 3" size="small" type="info" round>+{{ row.keywords.length - 3 }}</el-tag>
              </template>
              <span v-else style="color: var(--el-text-color-placeholder)">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" v-if="!batchMode">
            <template #default="{ row }">
              <div class="table-actions">
                <el-button size="small" text @click.stop="openRename(row)">✏️</el-button>
                <el-button size="small" text @click.stop="copyShareLink(row.id)">🔗</el-button>
                <el-button size="small" text type="danger" @click.stop="confirmDeleteRecording(row.id)">🗑</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <!-- 卡片视图 -->
        <div v-if="recordings.length && viewMode === 'card'" class="card-grid">
          <el-card
            v-for="r in recordings"
            :key="r.id"
            shadow="hover"
            class="rec-card"
            @click="goToDetail(r)"
          >
            <template #header>
              <div class="card-header">
                <div class="card-header-left">
                  <el-checkbox
                    v-if="batchMode"
                    :model-value="checkedIds.has(r.id)"
                    @change="(v) => toggleCheck(r.id, v)"
                    @click.stop
                  />
                  <span :title="r.title || r.original_filename">{{ r.title || r.original_filename }}</span>
                </div>
                <div class="card-header-right">
                  <el-tag :type="tagOf(r.status).type" size="small">{{ tagOf(r.status).label }}</el-tag>
                  <el-button
                    v-if="!batchMode"
                    size="small"
                    text
                    @click.stop="openRename(r)"
                  >✏️</el-button>
                  <el-button
                    v-if="!batchMode"
                    size="small"
                    text
                    @click.stop="copyShareLink(r.id)"
                  >🔗</el-button>
                  <el-button
                    v-if="!batchMode"
                    size="small"
                    text
                    type="danger"
                    @click.stop="confirmDeleteRecording(r.id)"
                  >🗑</el-button>
                </div>
              </div>
            </template>
            <div class="card-body">
              <div class="card-title-preview">{{ r.title || r.original_filename }}</div>
              <div style="color: #888; font-size: 13px">
                时长：{{ formatDuration(r.duration) }} | 大小：{{ formatSize(r.file_size) }}
              </div>
              <div style="color: #888; font-size: 13px">语言：{{ r.language || '—' }}</div>
              <div style="color: #888; font-size: 13px">
                {{ formatDateTime(r.created_at) }}
              </div>
              <div v-if="r.tags && r.tags.length" class="tag-row">
                <el-tag
                  v-for="t in r.tags.slice(0, 4)"
                  :key="t"
                  size="small"
                  type="warning"
                  effect="plain"
                  round
                >{{ t }}</el-tag>
              </div>
              <div v-if="r.keywords && r.keywords.length" class="tag-row">
                <el-tag
                  v-for="kw in r.keywords.slice(0, 3)"
                  :key="kw"
                  size="small"
                  type="primary"
                  round
                >{{ kw }}</el-tag>
              </div>
            </div>
          </el-card>
        </div>
      </template>
    </el-skeleton>

    <!-- 分页 -->
    <div v-if="total > 0" class="pagination-wrapper">
      <el-pagination
        v-model:current-page="page"
        :total="total"
        :page-size="pageSize"
        :page-sizes="[12, 24, 48, 96]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="handlePageChange"
        @size-change="handlePageSizeChange"
      />
    </div>

    <!-- 上传配置弹窗 -->
    <el-dialog v-model="uploadModal" title="上传录音" width="520px">
      <el-form label-position="top">
        <el-form-item label="选择录音文件">
          <el-upload
            :on-change="onFileSelected"
            :auto-upload="false"
            :limit="1"
            accept="audio/*,video/*,.wav,.mp3,.m4a,.flac,.ogg,.webm,.mp4,.mkv,.avi,.mov,.wmv"
          >
            <el-button>选择文件</el-button>
          </el-upload>
        </el-form-item>
        <el-form-item label="上传字幕/转录文件（选传）">
          <el-upload
            :on-change="onSrtFileSelected"
            :auto-upload="false"
            :limit="1"
            accept=".srt,.txt"
          >
            <el-button>选择文件</el-button>
          </el-upload>
          <div style="font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px">
            上传 SRT 字幕或 TXT 转录文件后将跳过 ASR 转录，直接使用文件内容生成逐字稿并触发后续流程
          </div>
        </el-form-item>
        <el-form-item v-if="!uploadSrtFile" label="转录引擎">
          <el-radio-group v-model="uploadEngine">
            <el-radio value="qwen_asr">Qwen3-ASR 兼容</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="!uploadSrtFile" label="ASR 提供商">
          <el-select
            v-model="uploadAsrProvider"
            placeholder="自动选择默认提供商"
            style="width: 100%"
          >
            <el-option
              v-for="p in asrProviders"
              :key="p.id"
              :label="p.name + (p.is_default ? '（默认）' : '')"
              :value="p.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="热词库">
          <el-select
            v-model="uploadHotwordLib"
            placeholder="不使用热词库"
            style="width: 100%"
          >
            <el-option
              v-for="opt in hotwordOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="会议类型">
          <el-select
            v-model="uploadMeetingType"
            placeholder="默认（通用提示词）"
            style="width: 100%"
          >
            <el-option
              v-for="opt in meetingTypeOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadModal = false">取消</el-button>
        <el-button type="primary" @click="confirmUpload" :disabled="!uploadFile">确认上传</el-button>
      </template>
    </el-dialog>

    <!-- 重命名弹窗 -->
    <el-dialog v-model="renameModal" title="重命名录音" width="420px">
      <el-input v-model="renameTitle" placeholder="输入新标题" />
      <template #footer>
        <el-button @click="renameModal = false">取消</el-button>
        <el-button type="primary" @click="confirmRename">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 18px;
}

.toolbar-left,
.toolbar-center,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.rec-card {
  cursor: pointer;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
}

.card-header-left > span {
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  white-space: normal;
  line-height: 1.4;
}

.card-header-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tag-row {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.pagination-wrapper {
  margin-top: 24px;
  display: flex;
  justify-content: center;
}

/* 表格视图 */
.table-title {
  font-weight: 500;
}

.table-actions {
  display: flex;
  gap: 2px;
  white-space: nowrap;
  justify-content: center;
}

:deep(.rec-table-row) {
  cursor: pointer;
}

:deep(.rec-table-row:hover) {
  background-color: var(--el-fill-color-light) !important;
}

/* 卡片视图标题预览 */
.card-title-preview {
  font-size: 13px;
  color: var(--el-text-color-primary);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin-bottom: 4px;
}
</style>
