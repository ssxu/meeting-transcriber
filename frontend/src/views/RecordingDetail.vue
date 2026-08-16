<script setup>
import { onMounted, onBeforeUnmount, ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { marked } from 'marked'
import api from '../api'
import AudioPlayer from '../components/AudioPlayer.vue'
import { formatDateTime } from '../utils/format'
import MindmapView from '../components/MindmapView.vue'
import ChatPanel from '../components/ChatPanel.vue'

const route = useRoute()
const router = useRouter()
const rec = ref(null)
const loading = ref(false)
const loadError = ref('')

const activeTab = ref('summary')
const speakerFilter = ref(null)
const playerRef = ref(null)
const mindmapLoading = ref(false)
const mindmapKey = ref(0)  // 用于强制重新挂载思维导图组件

// ===== 备注状态 =====
const notesText = ref('')
const notesMode = ref('preview')  // 'preview' | 'edit'
const notesSaving = ref(false)
const notesHtml = computed(() => {
  if (!notesText.value) return ''
  return marked.parse(notesText.value)
})

// 重新生成相关状态
const meetingTypes = ref([])
const enabledModels = ref([])
const sceneTypes = ref({ categories: [], sub_types: [] })
const regenerateModalShow = ref(false)
const regenerateTarget = ref('')
const regenerateMeetingTypeId = ref(null)
const regenerateModelId = ref(null)
const regenerateSubType = ref(null)
const keywordsLoading = ref(false)

// ===== 转录稿编辑状态 =====
const editMode = ref(false)
const editingSegments = ref([])  // 编辑时的副本
const editLoading = ref(false)

// 段落合并/拆分状态
const mergeSelection = ref([])  // 选中的段落索引
const splitDialogShow = ref(false)
const splitIndex = ref(null)
const splitPosition = ref(0)
const splitText = ref('')

// 说话人重命名状态
const renameDialogShow = ref(false)
const renameMap = ref({})  // {old_name: new_name}

// ===== 文内搜索状态 =====
const searchText = ref('')
const searchMatches = ref([])  // 匹配的段落索引列表
const currentMatchIdx = ref(-1)  // 当前高亮匹配索引
const transcriptContainer = ref(null)

// ===== 导出菜单 =====
const exportDropdownVisible = ref(false)

// ===== SRT 上传（需求8） =====
const srtUploadModal = ref(false)
const srtFile = ref(null)
const srtUploading = ref(false)

// ===== 转录按钮（需求9 + 需求3：选择引擎） =====
const transcribeLoading = ref(false)
const transcribeEngineModal = ref(false)
const transcribeEngineChoice = ref('qwen_asr')
const transcribeAsrProvider = ref(null)
const asrProviders = ref([])

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

const isProcessing = computed(() =>
  rec.value && ['pending', 'transcribing', 'summarizing'].includes(rec.value.status)
)

const summaryHtml = computed(() => {
  if (!rec.value?.summary_text) return ''
  return marked.parse(rec.value.summary_text)
})

const allSegments = computed(() => {
  if (!rec.value?.transcript_segments) return []
  const segs = rec.value.transcript_segments
  return Array.isArray(segs) ? segs : []
})

// 在编辑模式下使用编辑副本，否则使用原始数据
const displaySegments = computed(() => {
  if (editMode.value) return editingSegments.value
  return allSegments.value
})

const filteredSegments = computed(() => {
  if (!displaySegments.value.length) return []
  if (!speakerFilter.value) return displaySegments.value
  return displaySegments.value.filter(s => s.speaker === speakerFilter.value)
})

const speakers = computed(() => {
  const set = new Set()
  displaySegments.value.forEach(s => {
    if (s.speaker) set.add(s.speaker)
  })
  return [...set]
})

const speakerOptions = computed(() => [
  { label: '全部说话人', value: null },
  ...speakers.value.map(s => ({ label: s, value: s }))
])

const meetingTypeOptions = computed(() => {
  const opts = [{ label: '默认模板', value: null }]
  meetingTypes.value.forEach(t => {
    opts.push({ label: t.name, value: t.id })
  })
  return opts
})

const modelOptions = computed(() => {
  const opts = [{ label: '默认模型', value: null }]
  enabledModels.value.forEach(m => {
    opts.push({ label: m.name, value: m.id })
  })
  return opts
})

// ===== 时间格式化 =====
function formatTimestamp(seconds) {
  if (seconds === undefined || seconds === null) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.round(seconds % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatDuration(s) {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}分${sec}秒`
}

// ===== 播放器交互 =====
function seekToTime(seconds) {
  if (playerRef.value && seconds !== undefined) {
    playerRef.value.seekTo(seconds)
    playerRef.value.play()
  }
}

// 播放器时间更新 → 自动滚动到当前段落
function onPlayerTimeUpdate(currentTime) {
  if (editMode.value) return
  if (!allSegments.value.length) return

  // 找到当前播放位置对应的段落
  const idx = allSegments.value.findIndex(seg => {
    const segEnd = seg.end ?? (seg.start ?? 0) + 10
    return currentTime >= (seg.start ?? 0) && currentTime < segEnd
  })

  if (idx >= 0) {
    // 滚动到对应段落
    const container = transcriptContainer.value
    if (container) {
      const rows = container.querySelectorAll('.segment-row')
      if (rows[idx]) {
        const rowTop = rows[idx].offsetTop
        const rowHeight = rows[idx].offsetHeight
        const containerHeight = container.clientHeight
        // 只在段落不在可视区域内时滚动
        if (rowTop < container.scrollTop || rowTop + rowHeight > container.scrollTop + containerHeight) {
          container.scrollTo({
            top: rowTop - containerHeight / 2 + rowHeight / 2,
            behavior: 'smooth'
          })
        }
      }
    }
  }
}

function onPlayerSeek(time) {
  onPlayerTimeUpdate(time)
}

// ===== 文内搜索 =====
function doSearch() {
  const q = searchText.value.trim().toLowerCase()
  if (!q) {
    searchMatches.value = []
    currentMatchIdx.value = -1
    return
  }

  searchMatches.value = []
  allSegments.value.forEach((seg, i) => {
    if (seg.text && seg.text.toLowerCase().includes(q)) {
      searchMatches.value.push(i)
    }
  })

  if (searchMatches.value.length > 0) {
    currentMatchIdx.value = 0
    scrollToMatch(0)
    ElMessage.success(`找到 ${searchMatches.value.length} 处匹配`)
  } else {
    currentMatchIdx.value = -1
    ElMessage.info('未找到匹配内容')
  }
}

function nextMatch() {
  if (!searchMatches.value.length) return
  currentMatchIdx.value = (currentMatchIdx.value + 1) % searchMatches.value.length
  scrollToMatch(currentMatchIdx.value)
}

function prevMatch() {
  if (!searchMatches.value.length) return
  currentMatchIdx.value = (currentMatchIdx.value - 1 + searchMatches.value.length) % searchMatches.value.length
  scrollToMatch(currentMatchIdx.value)
}

function scrollToMatch(matchIdx) {
  if (matchIdx < 0 || !searchMatches.value.length) return
  const segIdx = searchMatches.value[matchIdx]
  const container = transcriptContainer.value
  if (container) {
    const rows = container.querySelectorAll('.segment-row')
    if (rows[segIdx]) {
      rows[segIdx].scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }
}

function clearSearch() {
  searchText.value = ''
  searchMatches.value = []
  currentMatchIdx.value = -1
}

// 检查段落是否是当前匹配
function isMatched(idx) {
  return searchMatches.value.includes(idx)
}

function isCurrentMatch(idx) {
  return searchMatches.value.length > 0 &&
         currentMatchIdx.value >= 0 &&
         searchMatches.value[currentMatchIdx.value] === idx
}

// 高亮搜索文本
function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

function highlightSearch(text) {
  if (!searchText.value.trim() || !text) return escapeHtml(text)
  const q = searchText.value.trim()
  const escapedText = escapeHtml(text)
  const regex = new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
  return escapedText.replace(regex, '<mark class="search-highlight">$1</mark>')
}

// ===== 转录稿编辑 =====
function enterEditMode() {
  // 深拷贝当前段落
  editingSegments.value = JSON.parse(JSON.stringify(allSegments.value))
  editMode.value = true
  mergeSelection.value = []
  ElMessage.info('已进入编辑模式')
}

function exitEditMode() {
  editMode.value = false
  editingSegments.value = []
  mergeSelection.value = []
}

async function saveTranscript() {
  editLoading.value = true
  try {
    rec.value = await api.editTranscript(route.params.id, {
      segments: editingSegments.value
    })
    editMode.value = false
    mergeSelection.value = []
    ElMessage.success('逐字稿已保存')
  } catch (e) {
    ElMessage.error('保存失败：' + (e._msg || e.message))
  } finally {
    editLoading.value = false
  }
}

// 编辑单段文本
function editSegmentText(idx) {
  const seg = editingSegments.value[idx]
  // 使用 prompt 简化交互
  ElMessageBox.prompt('编辑段落文本', `段落 ${idx + 1}`, {
    inputValue: seg.text || '',
    inputType: 'textarea',
    confirmButtonText: '保存',
    cancelButtonText: '取消',
  }).then(({ value }) => {
    editingSegments.value[idx].text = value
  }).catch(() => {})
}

// 编辑段落说话人
function editSegmentSpeaker(idx) {
  const seg = editingSegments.value[idx]
  ElMessageBox.prompt('编辑说话人', `段落 ${idx + 1}`, {
    inputValue: seg.speaker || '',
    confirmButtonText: '保存',
    cancelButtonText: '取消',
  }).then(({ value }) => {
    editingSegments.value[idx].speaker = value || null
  }).catch(() => {})
}

// 合并选中的段落
async function mergeSelected() {
  if (mergeSelection.value.length < 2) {
    ElMessage.warning('请至少选择两个段落')
    return
  }

  // 按索引排序
  const indices = [...mergeSelection.value].sort((a, b) => a - b)
  const firstSeg = editingSegments.value[indices[0]]
  const lastSeg = editingSegments.value[indices[indices.length - 1]]

  // 合并文本
  const mergedText = indices.map(i => editingSegments.value[i].text).join(' ')
  const mergedSpeaker = firstSeg.speaker

  try {
    // 调用后端合并 API
    rec.value = await api.mergeSegments(route.params.id, {
      indices: indices,
      speaker: mergedSpeaker
    })
    // 更新编辑副本
    editingSegments.value = JSON.parse(JSON.stringify(rec.value.transcript_segments || []))
    mergeSelection.value = []
    ElMessage.success('段落已合并')
  } catch (e) {
    ElMessage.error('合并失败：' + (e._msg || e.message))
  }
}

// 打开拆分弹窗
function openSplitDialog(idx) {
  splitIndex.value = idx
  splitText.value = editingSegments.value[idx].text || ''
  splitPosition.value = Math.floor(splitText.value.length / 2)
  splitDialogShow.value = true
}

// 执行拆分
async function confirmSplit() {
  if (splitIndex.value === null) return
  try {
    rec.value = await api.splitSegment(route.params.id, {
      index: splitIndex.value,
      position: splitPosition.value,
    })
    editingSegments.value = JSON.parse(JSON.stringify(rec.value.transcript_segments || []))
    splitDialogShow.value = false
    ElMessage.success('段落已拆分')
  } catch (e) {
    ElMessage.error('拆分失败：' + (e._msg || e.message))
  }
}

// 切换段落选择（用于合并）
function toggleMergeSelection(idx) {
  const pos = mergeSelection.value.indexOf(idx)
  if (pos >= 0) {
    mergeSelection.value.splice(pos, 1)
  } else {
    mergeSelection.value.push(idx)
  }
}

// 打开说话人重命名弹窗
function openRenameDialog() {
  renameMap.value = {}
  speakers.value.forEach(s => {
    renameMap.value[s] = s
  })
  renameDialogShow.value = true
}

// 执行说话人重命名
async function confirmRename() {
  // 检查是否有变化
  const hasChange = Object.entries(renameMap.value).some(([old, newV]) => old !== newV)
  if (!hasChange) {
    renameDialogShow.value = false
    return
  }

  editLoading.value = true
  try {
    rec.value = await api.editTranscript(route.params.id, {
      speaker_names: renameMap.value
    })
    editingSegments.value = JSON.parse(JSON.stringify(rec.value.transcript_segments || []))
    renameDialogShow.value = false
    ElMessage.success('说话人已重命名')
  } catch (e) {
    ElMessage.error('重命名失败：' + (e._msg || e.message))
  } finally {
    editLoading.value = false
  }
}

// 删除段落
async function deleteSegment(idx) {
  try {
    await ElMessageBox.confirm('确认删除此段落？', '删除段落', { type: 'warning' })
    editingSegments.value.splice(idx, 1)
    ElMessage.success('段落已删除（保存后生效）')
  } catch (e) {
    // cancelled
  }
}

// 从编辑后的逐字稿重新生成摘要
async function regenerateSummaryFromTranscript() {
  try {
    await ElMessageBox.confirm('将基于当前编辑后的逐字稿重新生成摘要，确认？', '重新生成摘要', { type: 'info' })
    rec.value = await api.regenerateSummaryFromTranscript(route.params.id)
    ElMessage.info('正在重新生成摘要…')
    await load()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('操作失败：' + (e._msg || e.message))
    }
  }
}

// ===== 导出功能 =====
function exportTxt() {
  window.open(api.exportTxtUrl(route.params.id, true, true), '_blank')
}

function exportTxtPlain() {
  window.open(api.exportTxtUrl(route.params.id, false, false), '_blank')
}

function exportSrt() {
  window.open(api.exportSrtUrl(route.params.id), '_blank')
}

function exportVtt() {
  window.open(api.exportVttUrl(route.params.id), '_blank')
}

function exportPdf() {
  window.open(api.exportPdfUrl(route.params.id), '_blank')
}

function exportDocx() {
  window.open(api.exportDocxUrl(route.params.id), '_blank')
}

// ===== SRT 上传（需求8） =====
function openSrtUpload() {
  srtFile.value = null
  srtUploadModal.value = true
}

function onSrtSelected(file) {
  srtFile.value = file.raw || file
}

async function confirmSrtUpload() {
  if (!srtFile.value) {
    ElMessage.warning('请先选择文件')
    return
  }
  srtUploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', srtFile.value)
    const token = localStorage.getItem('mt_token')
    const resp = await fetch(`/api/recordings/${route.params.id}/upload-srt${token ? '?token=' + encodeURIComponent(token) : ''}`, {
      method: 'POST',
      body: fd,
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: '上传失败' }))
      throw new Error(err.detail || '上传失败')
    }
    const data = await resp.json()
    rec.value = data
    ElMessage.success('文件上传成功，已自动触发摘要生成')
    srtUploadModal.value = false
    srtFile.value = null
  } catch (e) {
    ElMessage.error('上传失败：' + (e.message || e._msg || '未知错误'))
  } finally {
    srtUploading.value = false
  }
}

// ===== 备注保存 =====
async function saveNotes() {
  notesSaving.value = true
  try {
    const updated = await api.saveNotes(route.params.id, notesText.value)
    rec.value = { ...rec.value, ...updated }
    notesMode.value = 'preview'
    ElMessage.success('备注已保存')
  } catch (e) {
    ElMessage.error(e._msg || '保存失败')
  } finally {
    notesSaving.value = false
  }
}

// ===== 转录按钮（需求9 + 需求3：选择引擎） =====
async function addToTranscribeQueue() {
  transcribeEngineModal.value = true
  transcribeEngineChoice.value = 'qwen_asr'
  transcribeAsrProvider.value = null
  // 加载 ASR 提供商列表
  if (!asrProviders.value.length) {
    await loadAsrProviders()
  }
  // 设置默认提供商
  if (asrProviders.value.length) {
    const defaultProvider = asrProviders.value.find(p => p.is_default)
    transcribeAsrProvider.value = defaultProvider ? defaultProvider.id : asrProviders.value[0].id
  }
}

async function confirmTranscribe() {
  transcribeLoading.value = true
  try {
    rec.value = await api.transcribe(route.params.id, transcribeEngineChoice.value, transcribeAsrProvider.value)
    const providerName = asrProviders.value.find(p => p.id === transcribeAsrProvider.value)?.name || 'Qwen ASR'
    ElMessage.success(`已开始转录（引擎: ${providerName}）`)
    transcribeEngineModal.value = false
  } catch (e) {
    ElMessage.error('操作失败：' + (e.message || e._msg || '未知错误'))
  } finally {
    transcribeLoading.value = false
  }
}

// 下载原始录音文件（需求2）
function downloadAudio() {
  window.open(api.downloadUrl(route.params.id), '_blank')
}

// ===== 通用功能 =====
async function copyText(text, label) {
  if (!text) {
    ElMessage.warning(`暂无${label}可复制`)
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(`${label}已复制到剪贴板`)
  } catch (e) {
    ElMessage.error('复制失败')
  }
}

// ===== 重新生成弹窗 =====
function openRegenerateModal(target) {
  regenerateTarget.value = target
  regenerateMeetingTypeId.value = target === 'summary' ? (rec.value?.meeting_type_id || null) : null
  regenerateModelId.value = null
  regenerateSubType.value = target === 'summary' ? (rec.value?.content_sub_type || null) : null
  regenerateModalShow.value = true
}

const regenerateTitle = computed(() => {
  const map = {
    summary: '重新生成内容摘要',
    keywords: '重新生成关键词与标签',
    mindmap: '重新生成思维导图',
  }
  return map[regenerateTarget.value] || '重新生成'
})

let _pollTimer = null

function pollForUpdate(target, attempts = 10, interval = 3000) {
  if (_pollTimer) clearTimeout(_pollTimer)
  const oldKeywords = JSON.stringify(rec.value?.keywords || [])
  const oldTags = JSON.stringify(rec.value?.tags || [])
  const oldMindmap = rec.value?.mindmap_text || ''
  let count = 0
  const poll = async () => {
    count++
    await load()
    if (target === 'keywords') {
      const newKw = JSON.stringify(rec.value?.keywords || [])
      const newTags = JSON.stringify(rec.value?.tags || [])
      if (newKw !== oldKeywords || newTags !== oldTags) {
        keywordsLoading.value = false
        ElMessage.success('关键词与标签已更新')
        return
      }
    } else if (target === 'mindmap') {
      if ((rec.value?.mindmap_text || '') !== oldMindmap) {
        mindmapLoading.value = false
        ElMessage.success('思维导图已更新')
        return
      }
    }
    if (count < attempts) {
      _pollTimer = setTimeout(poll, interval)
    } else {
      keywordsLoading.value = false
      mindmapLoading.value = false
      ElMessage.warning('生成可能仍在进行中，请稍后刷新查看')
    }
  }
  _pollTimer = setTimeout(poll, interval)
}

async function confirmRegenerate() {
  regenerateModalShow.value = false
  const mtId = regenerateMeetingTypeId.value
  const mId = regenerateModelId.value
  const st = regenerateSubType.value

  try {
    if (regenerateTarget.value === 'summary') {
      rec.value = await api.summarize(route.params.id, mtId, mId, st)
      ElMessage.info('正在重新生成摘要…')
      await load()
    } else if (regenerateTarget.value === 'keywords') {
      keywordsLoading.value = true
      await api.regenerateKeywords(route.params.id, mId)
      ElMessage.info('正在重新生成关键词与标签…')
      pollForUpdate('keywords')
    } else if (regenerateTarget.value === 'mindmap') {
      mindmapLoading.value = true
      await api.generateMindmap(route.params.id, mId)
      ElMessage.info('正在重新生成思维导图…')
      pollForUpdate('mindmap')
    }
  } catch (e) {
    ElMessage.error('操作失败：' + (e._msg || e.message))
    keywordsLoading.value = false
    mindmapLoading.value = false
  }
}

async function reSummarize() {
  openRegenerateModal('summary')
}

async function generateMindmap() {
  openRegenerateModal('mindmap')
}

async function regenerateKeywords() {
  openRegenerateModal('keywords')
}

async function loadMeetingTypes() {
  try {
    const [types, scenes] = await Promise.all([
      api.meetingTypes(),
      api.sceneTypes(),
    ])
    meetingTypes.value = types
    sceneTypes.value = scenes
  } catch (e) {}
}

async function loadEnabledModels() {
  try {
    enabledModels.value = await api.enabledModels()
  } catch (e) {}
}

async function loadAsrProviders() {
  try {
    asrProviders.value = await api.enabledAsrProviders()
  } catch (e) {}
}

async function load() {
  const id = route.params.id
  if (!id || id === 'undefined') {
    loadError.value = '无效的录音 ID'
    ElMessage.error('无效的录音 ID')
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    rec.value = await api.detail(id)
    // 同步备注到编辑状态
    notesText.value = rec.value?.notes || ''
    notesMode.value = 'preview'
  } catch (e) {
    loadError.value = e._msg || '加载失败'
    ElMessage.error(e._msg || '加载失败')
  } finally {
    loading.value = false
  }
}

async function deleteRecording() {
  try {
    await ElMessageBox.confirm('确认删除此录音？此操作不可撤销。', '删除', { type: 'warning' })
    await api.remove(route.params.id)
    ElMessage.success('已删除')
    router.push('/')
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error(e._msg || '删除失败')
    }
  }
}

async function shareRecording() {
  try {
    const res = await api.createShare(route.params.id)
    const url = `${window.location.origin}/share/${res.share_token}`
    await navigator.clipboard.writeText(url)
    ElMessage.success('分享链接已复制到剪贴板')
  } catch (e) {
    ElMessage.error(e._msg || '生成分享链接失败')
  }
}

// 切换 tab 时重置搜索，思维导图重新挂载
watch(activeTab, (newTab) => {
  if (newTab !== 'transcript') {
    clearSearch()
  }
  if (newTab === 'mindmap') {
    mindmapKey.value++
  }
})

// 监听路由参数变化，处理组件复用（不同 ID 间切换）的情况
watch(() => route.params.id, (newId, oldId) => {
  if (newId && newId !== oldId) {
    rec.value = null
    load()
  }
})

onMounted(() => {
  load()
  loadMeetingTypes()
  loadEnabledModels()
  loadAsrProviders()
})

onBeforeUnmount(() => {
  if (_pollTimer) clearTimeout(_pollTimer)
})
</script>

<template>
  <!-- 加载中 -->
  <div v-if="loading" class="detail-loading">
    <el-icon class="is-loading" :size="32"><Loading /></el-icon>
    <span>加载中...</span>
  </div>

  <!-- 加载失败 -->
  <div v-else-if="loadError" class="detail-error">
    <el-result icon="error" :title="loadError">
      <template #extra>
        <el-button type="primary" @click="router.push('/')">返回列表</el-button>
      </template>
    </el-result>
  </div>

  <!-- 正常内容 -->
  <div v-else-if="rec">
    <div class="detail-header">
      <div class="header-left">
        <el-button text @click="router.push('/')">← 返回</el-button>
        <el-tag :type="tagOf(rec.status).type">{{ tagOf(rec.status).label }}</el-tag>
      </div>
      <div class="header-right">
        <!-- 转录按钮（需求9 + 需求3：选择引擎）- 当录音未转录时显示 -->
        <el-button
          v-if="rec.status === 'pending'"
          size="small"
          type="primary"
          plain
          :loading="transcribeLoading"
          @click="addToTranscribeQueue"
        >开始转录</el-button>
        <!-- 下载原始录音文件（需求2） -->
        <el-button
          size="small"
          type="success"
          plain
          @click="downloadAudio"
        >⬇ 下载录音</el-button>
        <!-- 字幕/转录文件上传按钮（需求8） -->
        <el-button
          size="small"
          type="info"
          plain
          @click="openSrtUpload"
        >上传字幕/转录</el-button>
        <!-- 导出下拉菜单 -->
        <el-dropdown trigger="click" @command="(cmd) => {
          if (cmd === 'txt') exportTxt()
          else if (cmd === 'txt_plain') exportTxtPlain()
          else if (cmd === 'srt') exportSrt()
          else if (cmd === 'vtt') exportVtt()
          else if (cmd === 'pdf') exportPdf()
          else if (cmd === 'docx') exportDocx()
        }">
          <el-button size="small" :disabled="!rec.transcript_text">📄 导出 ▾</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="txt">TXT（含时间戳和说话人）</el-dropdown-item>
              <el-dropdown-item command="txt_plain">TXT（纯文本）</el-dropdown-item>
              <el-dropdown-item command="srt">SRT 字幕</el-dropdown-item>
              <el-dropdown-item command="vtt">WebVTT</el-dropdown-item>
              <el-dropdown-item divided command="pdf">PDF 文档</el-dropdown-item>
              <el-dropdown-item command="docx">Word 文档</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button size="small" type="info" text @click="shareRecording" :disabled="!rec.transcript_text">🔗 分享</el-button>
        <el-button
          size="small"
          type="warning"
          plain
          :disabled="!rec.transcript_text || isProcessing"
          @click="reSummarize"
        >重新生成摘要</el-button>
        <el-button size="small" type="danger" plain @click="deleteRecording">删除</el-button>
      </div>
    </div>

    <el-divider />

    <!-- 音频播放器 -->
    <el-card shadow="never">
      <template #header>
        <span>{{ rec.title || rec.original_filename }}</span>
      </template>
      <div style="display: flex; flex-direction: column; gap: 12px">
        <audio-player
          ref="playerRef"
          :src="api.audioUrl(rec.id)"
          :duration="rec.duration"
          @timeupdate="onPlayerTimeUpdate"
          @seek="onPlayerSeek"
        />
        <div class="meta-row">
          <span v-if="rec.duration">时长：{{ formatDuration(rec.duration) }}</span>
          <span v-if="rec.language">语言：{{ rec.language }}</span>
          <span>大小：{{ (rec.file_size / 1024 / 1024).toFixed(2) }} MB</span>
          <span>{{ formatDateTime(rec.created_at) }}</span>
        </div>
      </div>
    </el-card>

    <!-- Tab 切换 -->
    <el-tabs v-model="activeTab" style="margin-top: 16px">
      <!-- 摘要 -->
      <el-tab-pane label="📋 会议摘要" name="summary">
        <el-card shadow="never">
          <template #header>
            <div class="tab-header">
              <div class="tab-header-right">
                <el-button size="small" text @click="copyText(rec.summary_text, '摘要')">📋 复制</el-button>
                <el-button
                  size="small"
                  type="warning"
                  plain
                  :disabled="!rec.transcript_text || isProcessing"
                  @click="reSummarize"
                >🔄 重新生成</el-button>
              </div>
            </div>
          </template>
          <div v-loading="rec.status === 'summarizing'">
            <div
              v-if="rec.summary_text"
              v-html="summaryHtml"
              class="markdown-body"
              style="line-height: 1.8"
            ></div>
            <span v-else style="color: #aaa">摘要生成中…</span>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- 逐字稿 -->
      <el-tab-pane name="transcript">
        <template #label>
          📝 逐字稿
          <el-badge v-if="searchMatches.length" :value="searchMatches.length" type="primary" style="margin-left: 4px" />
        </template>
        <el-card shadow="never">
          <template #header>
            <div class="tab-header">
              <!-- 文内搜索栏 -->
              <div class="search-bar">
                <el-input
                  v-model="searchText"
                  placeholder="在逐字稿中搜索…"
                  clearable
                  size="small"
                  style="width: 240px"
                  @keyup.enter="doSearch"
                  @clear="clearSearch"
                >
                  <template #append>
                    <el-button @click="doSearch" size="small">搜索</el-button>
                  </template>
                </el-input>
                <template v-if="searchMatches.length > 0">
                  <el-button-group size="small">
                    <el-button @click="prevMatch">↑</el-button>
                    <el-button disabled>{{ currentMatchIdx + 1 }}/{{ searchMatches.length }}</el-button>
                    <el-button @click="nextMatch">↓</el-button>
                  </el-button-group>
                  <el-button size="small" text @click="clearSearch">清除</el-button>
                </template>
              </div>
              <div class="tab-header-right">
                <!-- 编辑模式切换 -->
                <template v-if="!editMode">
                  <el-select
                    v-if="speakers.length > 1"
                    v-model="speakerFilter"
                    size="small"
                    style="width: 140px"
                    placeholder="全部说话人"
                  >
                    <el-option
                      v-for="opt in speakerOptions"
                      :key="opt.value"
                      :label="opt.label"
                      :value="opt.value"
                    />
                  </el-select>
                  <el-button size="small" text @click="copyText(rec.transcript_text, '逐字稿')">📋 复制</el-button>
                  <el-button
                    size="small"
                    type="primary"
                    plain
                    :disabled="!rec.transcript_text || isProcessing"
                    @click="enterEditMode"
                  >✏️ 编辑</el-button>
                </template>
                <template v-else>
                  <el-button size="small" text @click="openRenameDialog">说话人重命名</el-button>
                  <el-button
                    size="small"
                    type="warning"
                    plain
                    :disabled="mergeSelection.length < 2"
                    @click="mergeSelected"
                  >合并选中 ({{ mergeSelection.length }})</el-button>
                  <el-button size="small" @click="regenerateSummaryFromTranscript" :disabled="!editingSegments.length">重新生成摘要</el-button>
                  <el-button size="small" type="primary" @click="saveTranscript" :loading="editLoading">💾 保存</el-button>
                  <el-button size="small" text @click="exitEditMode">退出编辑</el-button>
                </template>
              </div>
            </div>
          </template>
          <div v-loading="rec.status === 'transcribing' || rec.status === 'pending' || editLoading">
            <div
              v-if="filteredSegments && filteredSegments.length"
              ref="transcriptContainer"
              class="transcript-list"
            >
              <div
                v-for="(seg, i) in filteredSegments"
                :key="i"
                :class="[
                  'segment-row',
                  {
                    'segment-matched': isMatched(i),
                    'segment-current-match': isCurrentMatch(i),
                    'segment-selected': editMode && mergeSelection.includes(i),
                  }
                ]"
              >
                <!-- 编辑模式下的选择框 -->
                <el-checkbox
                  v-if="editMode"
                  :model-value="mergeSelection.includes(i)"
                  @change="toggleMergeSelection(i)"
                  style="margin-right: 8px"
                />
                <span
                  v-if="seg.speaker"
                  class="speaker-label"
                  :content="editMode ? '点击编辑说话人' : ''"
                  @click="editMode && editSegmentSpeaker(i)"
                >[{{ seg.speaker }}]</span>
                <span
                  v-if="seg.start !== undefined"
                  class="timestamp-link"
                  @click="seekToTime(seg.start)"
                >{{ formatTimestamp(seg.start) }}</span>
                <span
                  class="segment-text"
                  :content="editMode ? '点击编辑文本' : ''"
                  @click="editMode && editSegmentText(i)"
                  v-html="editMode ? seg.text : highlightSearch(seg.text)"
                ></span>
                <!-- 编辑模式操作按钮 -->
                <span v-if="editMode" class="segment-actions">
                  <el-button text size="small" @click="openSplitDialog(i)">拆分</el-button>
                  <el-button text size="small" type="danger" @click="deleteSegment(i)">删除</el-button>
                </span>
              </div>
            </div>
            <pre
              v-else-if="rec.transcript_text"
              style="white-space: pre-wrap; line-height: 1.8; margin: 0; font-family: inherit"
            >{{ rec.transcript_text }}</pre>
            <span v-else style="color: #aaa">转录进行中，请稍候…</span>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- 关键词与标签 -->
      <el-tab-pane label="🏷 关键词与标签" name="keywords">
        <el-card shadow="never">
          <template #header>
            <div class="tab-header">
              <div class="tab-header-right">
                <el-button
                  size="small"
                  type="warning"
                  plain
                  :loading="keywordsLoading"
                  :disabled="!rec.summary_text || isProcessing"
                  @click="regenerateKeywords"
                >🔄 重新生成</el-button>
              </div>
            </div>
          </template>
          <div v-if="rec.tags && rec.tags.length" style="margin-bottom: 16px">
            <span style="font-size: 13px; color: var(--el-text-color-secondary); display: block; margin-bottom: 8px">标签</span>
            <div style="display: flex; gap: 8px; flex-wrap: wrap">
              <el-tag
                v-for="t in rec.tags"
                :key="t"
                type="warning"
                size="large"
                effect="plain"
                round
              >{{ t }}</el-tag>
            </div>
          </div>
          <el-divider v-if="rec.tags && rec.tags.length && rec.keywords && rec.keywords.length" />
          <div v-if="rec.keywords && rec.keywords.length">
            <span style="font-size: 13px; color: var(--el-text-color-secondary); display: block; margin-bottom: 8px">关键词</span>
            <div style="display: flex; gap: 8px; flex-wrap: wrap">
              <el-tag
                v-for="kw in rec.keywords"
                :key="kw"
                type="primary"
                size="large"
                round
              >{{ kw }}</el-tag>
            </div>
          </div>
          <el-empty v-if="(!rec.keywords || !rec.keywords.length) && (!rec.tags || !rec.tags.length)" description="暂无关键词与标签" />
        </el-card>
      </el-tab-pane>

      <!-- 思维导图 -->
      <el-tab-pane label="🧠 思维导图" name="mindmap">
        <el-card shadow="never">
          <template #header>
            <div class="tab-header">
              <div class="tab-header-right">
                <el-button
                  size="small"
                  type="primary"
                  plain
                  :loading="mindmapLoading"
                  :disabled="!rec.transcript_text || isProcessing"
                  @click="generateMindmap"
                >{{ rec.mindmap_text ? '🔄 重新生成' : '生成思维导图' }}</el-button>
              </div>
            </div>
          </template>
          <div v-loading="!rec.mindmap_text && isProcessing">
            <mindmap-view v-if="rec.mindmap_text" :key="mindmapKey" :markdown="rec.mindmap_text" />
            <el-empty v-else description="点击右上角按钮生成思维导图" style="padding: 40px" />
          </div>
        </el-card>
      </el-tab-pane>

      <!-- 备注Tab -->
      <el-tab-pane label="📝 备注" name="notes">
        <el-card shadow="never">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
            <span style="color: #909399; font-size: 13px">支持 Markdown 格式</span>
            <div>
              <el-button-group v-if="notesMode === 'edit'">
                <el-button size="small" @click="notesMode = 'preview'">预览</el-button>
                <el-button size="small" type="primary" @click="notesMode = 'edit'">编辑</el-button>
              </el-button-group>
              <el-button-group v-else>
                <el-button size="small" type="primary" @click="notesMode = 'edit'">编辑</el-button>
                <el-button size="small" @click="notesMode = 'preview'">预览</el-button>
              </el-button-group>
            </div>
          </div>
          <!-- 编辑模式 -->
          <el-input
            v-if="notesMode === 'edit'"
            v-model="notesText"
            type="textarea"
            :rows="20"
            placeholder="在此输入备注内容..."
            style="font-family: monospace"
          />
          <!-- 预览模式 -->
          <div v-else>
            <div v-if="notesHtml" v-html="notesHtml" class="markdown-body" style="min-height: 200px" />
            <el-empty v-else description="暂无备注，点击编辑添加" style="padding: 40px" />
          </div>
          <!-- 保存按钮 -->
          <div v-if="notesMode === 'edit'" style="margin-top: 12px; text-align: right">
            <el-button @click="notesText = rec?.notes || ''; notesMode = 'preview'">取消</el-button>
            <el-button type="primary" :loading="notesSaving" @click="saveNotes">保存</el-button>
          </div>
        </el-card>
      </el-tab-pane>

      <!-- AI 对话 -->
      <el-tab-pane label="🤖 AI 对话" name="chat">
        <ChatPanel
          :recording-id="rec.id"
          :summary-text="rec.summary_text"
          :segments="allSegments"
          :model-options="enabledModels"
          :has-transcript="!!rec.transcript_text"
          @seek-to-time="seekToTime"
        />
      </el-tab-pane>
    </el-tabs>

    <el-result
      v-if="rec.status === 'error'"
      icon="error"
      title="处理失败"
      :sub-title="rec.error_message || '未知错误'"
      style="margin-top: 24px"
    >
      <template #extra>
        <el-button @click="load">刷新重试</el-button>
      </template>
    </el-result>

    <!-- 重新生成弹窗 -->
    <el-dialog v-model="regenerateModalShow" :title="regenerateTitle" width="520px">
      <el-form label-position="top" style="margin-top: 12px">
        <el-form-item v-if="regenerateTarget === 'summary'" label="内容类型（总结模板）">
          <el-select
            v-model="regenerateMeetingTypeId"
            placeholder="自动识别场景"
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
        <el-form-item v-if="regenerateTarget === 'summary'" label="细分场景">
          <el-select
            v-model="regenerateSubType"
            placeholder="自动识别"
            style="width: 100%"
            clearable
          >
            <el-option-group
              v-for="cat in sceneTypes.categories"
              :key="cat.key"
              :label="cat.label"
            >
              <el-option
                v-for="st in (sceneTypes.sub_types || []).filter(s => s.category === cat.key)"
                :key="st.key"
                :label="st.label"
                :value="st.key"
              />
            </el-option-group>
          </el-select>
          <div style="font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px">
            不选择则自动识别内容场景
          </div>
        </el-form-item>
        <el-form-item label="使用模型">
          <el-select
            v-model="regenerateModelId"
            placeholder="默认模型"
            style="width: 100%"
          >
            <el-option
              v-for="opt in modelOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="regenerateModalShow = false">取消</el-button>
        <el-button type="primary" @click="confirmRegenerate">确认生成</el-button>
      </template>
    </el-dialog>

    <!-- 段落拆分弹窗 -->
    <el-dialog v-model="splitDialogShow" title="拆分段落" width="600px">
      <div v-if="splitText" style="margin-bottom: 12px">
        <div style="font-size: 13px; color: var(--el-text-color-secondary); margin-bottom: 8px">
          在第 <strong>{{ splitPosition }}</strong> 个字符处拆分：
        </div>
        <div class="split-preview">
          <div class="split-part">
            <span class="split-label">前半段：</span>
            <span>{{ splitText.substring(0, splitPosition) }}</span>
          </div>
          <div class="split-part">
            <span class="split-label">后半段：</span>
            <span>{{ splitText.substring(splitPosition) }}</span>
          </div>
        </div>
      </div>
      <el-slider
        v-model="splitPosition"
        :min="1"
        :max="splitText.length - 1"
        :step="1"
        show-input
      />
      <template #footer>
        <el-button @click="splitDialogShow = false">取消</el-button>
        <el-button type="primary" @click="confirmSplit">确认拆分</el-button>
      </template>
    </el-dialog>

    <!-- 说话人重命名弹窗 -->
    <el-dialog v-model="renameDialogShow" title="说话人重命名" width="500px">
      <el-form label-position="left" label-width="120px">
        <el-form-item
          v-for="oldName in Object.keys(renameMap)"
          :key="oldName"
          :label="oldName"
        >
          <el-input v-model="renameMap[oldName]" placeholder="新名称" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renameDialogShow = false">取消</el-button>
        <el-button type="primary" @click="confirmRename" :loading="editLoading">确认重命名</el-button>
      </template>
    </el-dialog>

    <!-- 字幕/转录文件上传弹窗（支持 SRT 和 TXT） -->
    <el-dialog v-model="srtUploadModal" title="上传字幕/转录文件" width="520px">
      <el-alert type="info" :closable="false" style="margin-bottom: 16px">
        上传 SRT 字幕或 TXT 转录文件后将自动解析并生成总结、关键词、标签等。
      </el-alert>
      <el-form label-position="top">
        <el-form-item label="选择文件（SRT 或 TXT）">
          <el-upload
            :on-change="onSrtSelected"
            :auto-upload="false"
            :limit="1"
            accept=".srt,.txt"
          >
            <el-button>选择文件</el-button>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="srtUploadModal = false">取消</el-button>
        <el-button type="primary" @click="confirmSrtUpload" :disabled="!srtFile" :loading="srtUploading">确认上传</el-button>
      </template>
    </el-dialog>

    <!-- 转录引擎选择弹窗（需求3） -->
    <el-dialog v-model="transcribeEngineModal" title="选择转录引擎" width="560px">
      <el-alert type="info" :closable="false" style="margin-bottom: 16px">
        选择转录引擎后将立即开始转录，转录完成后自动生成摘要、关键词等。
      </el-alert>
      <el-form label-position="top">
        <el-form-item label="转录引擎">
          <el-radio-group v-model="transcribeEngineChoice">
            <el-radio value="qwen_asr">Qwen3-ASR 兼容</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="ASR 提供商">
          <el-select
            v-model="transcribeAsrProvider"
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
      </el-form>
      <template #footer>
        <el-button @click="transcribeEngineModal = false">取消</el-button>
        <el-button type="primary" @click="confirmTranscribe" :loading="transcribeLoading">开始转录</el-button>
      </template>
    </el-dialog>
  </div>
  <div v-else v-loading="true" style="margin-top: 80px; min-height: 200px"></div>
</template>

<style scoped>
.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-right {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.meta-row {
  display: flex;
  gap: 20px;
  font-size: 13px;
  color: #888;
  flex-wrap: wrap;
}

.tab-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.tab-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.search-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.transcript-list {
  max-height: 600px;
  overflow-y: auto;
  line-height: 1.8;
}

.segment-row {
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.02);
  display: flex;
  align-items: flex-start;
  transition: background 0.2s, border-color 0.2s;
  border: 2px solid transparent;
}

.segment-row:hover {
  background: rgba(0, 0, 0, 0.04);
}

.segment-matched {
  background: #fff8e1;
}

.segment-current-match {
  background: #fff3cd;
  border-color: #ffc107;
}

.segment-selected {
  background: #e3f2fd;
  border-color: #2196f3;
}

.speaker-label {
  font-weight: 600;
  color: var(--el-color-primary);
  margin-right: 8px;
  cursor: pointer;
}

.timestamp-link {
  color: var(--el-color-success);
  font-size: 12px;
  margin-right: 8px;
  cursor: pointer;
  text-decoration: underline;
  flex-shrink: 0;
}

.segment-text {
  flex: 1;
  cursor: text;
}

.segment-actions {
  margin-left: 8px;
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.action-item {
  padding: 10px 12px;
  margin-bottom: 8px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.02);
  display: flex;
  align-items: flex-start;
}

.split-preview {
  background: rgba(0, 0, 0, 0.03);
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 12px;
}

.split-part {
  margin-bottom: 8px;
  word-break: break-all;
}

.split-part:last-child {
  margin-bottom: 0;
}

.split-label {
  font-weight: 600;
  color: var(--el-color-primary);
  margin-right: 4px;
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

:deep(.search-highlight) {
  background: #fff3cd;
  padding: 0 2px;
  border-radius: 2px;
  font-weight: 600;
}

.detail-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 80px 0;
  color: var(--el-text-color-secondary, #909399);
}

.detail-error {
  padding: 40px 0;
}
</style>
