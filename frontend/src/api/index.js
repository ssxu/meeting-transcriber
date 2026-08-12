/**
 * API 请求模块 - 统一 Axios 实例和错误拦截
 */
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// 请求拦截：自动携带 JWT token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('mt_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截：401 自动跳转登录
api.interceptors.response.use(
  (response) => response,
  (error) => {
    let msg = '网络请求失败'
    if (error.response) {
      msg = error.response.data?.detail || `服务器错误 (${error.response.status})`
      // 401 未认证 → 清除 token 并跳转登录页
      if (error.response.status === 401) {
        localStorage.removeItem('mt_token')
        // 避免在登录页重复跳转
        const currentPath = window.location.pathname
        if (!currentPath.startsWith('/login') && !currentPath.startsWith('/share/')) {
          window.location.href = `/login?redirect=${encodeURIComponent(currentPath)}`
        }
      }
    } else if (error.request) {
      msg = '服务器无响应，请检查网络连接'
    }
    error._msg = msg
    return Promise.reject(error)
  }
)

export default {
  // ===== 认证 =====
  login: (password) => api.post('/auth/login', { password }).then((r) => r.data),
  // ===== 录音相关 =====
  list: (params = {}) => {
    const { page = 1, page_size = 12, q = '', tag = '' } = params
    const query = []
    if (page) query.push(`page=${page}`)
    if (page_size) query.push(`page_size=${page_size}`)
    if (q) query.push(`q=${encodeURIComponent(q)}`)
    if (tag) query.push(`tag=${encodeURIComponent(tag)}`)
    const qs = query.length ? '?' + query.join('&') : ''
    return api.get(`/recordings${qs}`).then((r) => r.data)
  },
  tags: () => api.get('/recordings/tags/list').then((r) => r.data),
  detail: (id) => api.get(`/recordings/${id}`).then((r) => r.data),
  upload: (file, hotwordLibraryId = null, meetingTypeId = null, srtFile = null, engine = 'qwen_asr') => {
    const fd = new FormData()
    fd.append('file', file)
    if (srtFile) fd.append('srt_file', srtFile)
    fd.append('engine', engine)
    if (hotwordLibraryId) fd.append('hotword_library_id', hotwordLibraryId)
    if (meetingTypeId) fd.append('meeting_type_id', meetingTypeId)
    return api.post('/recordings/upload', fd).then((r) => r.data)
  },
  audioUrl: (id) => {
    const token = localStorage.getItem('mt_token')
    return `/api/recordings/${id}/audio${token ? '?token=' + encodeURIComponent(token) : ''}`
  },
  summarize: (id, meetingTypeId = null, modelId = null, subType = null) => {
    // meetingTypeId: null 表示使用默认模板（后端清除关联），数字表示指定会议类型
    const body = { meeting_type_id: meetingTypeId }
    if (modelId !== null) body.model_id = modelId
    if (subType !== null) body.sub_type = subType
    return api.post(`/recordings/${id}/summarize`, body).then((r) => r.data)
  },
  regenerateActionItems: (id, modelId = null) => api.post(`/recordings/${id}/action-items`, modelId ? { model_id: modelId } : {}).then((r) => r.data),
  regenerateKeywords: (id, modelId = null) => api.post(`/recordings/${id}/keywords`, modelId ? { model_id: modelId } : {}).then((r) => r.data),
  remove: (id) => api.delete(`/recordings/${id}`).then((r) => r.data),
  // 旧的 recordings/search 端点已被统一搜索 /api/search 替代，不再单独导出
  exportUrl: (id) => {
    const token = localStorage.getItem('mt_token')
    return `/api/recordings/${id}/export${token ? '?token=' + encodeURIComponent(token) : ''}`
  },
  rename: (id, title) => api.patch(`/recordings/${id}/title`, { title }).then((r) => r.data),
  batchDelete: (ids) => api.post('/recordings/batch-delete', { ids }).then((r) => r.data),
  batchExportUrl: (ids) => {
    const token = localStorage.getItem('mt_token')
    const tokenParam = token ? `&token=${encodeURIComponent(token)}` : ''
    return `/api/recordings/batch-export?ids=${ids.join(',')}${tokenParam}`
  },
  createShare: (id) => api.post(`/recordings/${id}/share`).then((r) => r.data),
  revokeShare: (id) => api.delete(`/recordings/${id}/share`).then((r) => r.data),
  audioShareUrl: (shareToken) => `/api/recordings/audio-share/${shareToken}`,
  generateMindmap: (id, modelId = null) => api.post(`/recordings/${id}/mindmap`, modelId ? { model_id: modelId } : {}).then((r) => r.data),
  saveNotes: (id, notes) => {
    const fd = new FormData()
    fd.append('notes', notes || '')
    return api.put(`/recordings/${id}/notes`, fd).then((r) => r.data)
  },

  // 下载原始录音文件
  downloadUrl: (id) => {
    const token = localStorage.getItem('mt_token')
    return `/api/recordings/${id}/download${token ? '?token=' + encodeURIComponent(token) : ''}`
  },

  // 直接发起转录（选择引擎）
  transcribe: (id, engine = 'qwen_asr') => {
    const token = localStorage.getItem('mt_token')
    return api.post(`/recordings/${id}/transcribe`, null, { params: { engine } }).then((r) => r.data)
  },

  // 加入转录队列（选择引擎）
  addToTranscriptionQueue: (id, engine = 'qwen_asr') => {
    const token = localStorage.getItem('mt_token')
    return api.post(`/transcription-queue/${id}`, null, { params: { engine } }).then((r) => r.data)
  },

  // ===== AI 对话 =====
  chatWithRecording: (id, message, modelId = null, history = []) =>
    api.post(`/recordings/${id}/chat`, { message, model_id: modelId, history }).then((r) => r.data),
  reindexSegments: (force = false) => api.post(`/recordings/reindex-segments?force=${force}`).then((r) => r.data),

  // ===== 统计 =====
  stats: () => api.get('/stats').then((r) => r.data),

  // ===== Enhanced Stats =====
  statsWithFilter: (params = {}) => {
    const { date_from = '', date_to = '' } = params
    const query = []
    if (date_from) query.push(`date_from=${date_from}`)
    if (date_to) query.push(`date_to=${date_to}`)
    const qs = query.length ? '?' + query.join('&') : ''
    return api.get(`/stats${qs}`).then((r) => r.data)
  },
  meetingAnalysis: (recId) => api.get(`/stats/meeting-analysis/${recId}`).then((r) => r.data),
  speakerSummary: (params = {}) => {
    const { date_from = '', date_to = '' } = params
    const query = []
    if (date_from) query.push(`date_from=${date_from}`)
    if (date_to) query.push(`date_to=${date_to}`)
    const qs = query.length ? '?' + query.join('&') : ''
    return api.get(`/stats/speaker-summary${qs}`).then((r) => r.data)
  },

  // ===== 转录稿编辑 =====
  editTranscript: (id, data) => api.put(`/recordings/${id}/transcript`, data).then((r) => r.data),
  mergeSegments: (id, data) => api.post(`/recordings/${id}/transcript/merge`, data).then((r) => r.data),
  splitSegment: (id, data) => api.post(`/recordings/${id}/transcript/split`, data).then((r) => r.data),
  regenerateSummaryFromTranscript: (id, meetingTypeId = null, modelId = null) => {
    const body = {}
    if (meetingTypeId !== null) body.meeting_type_id = meetingTypeId
    if (modelId !== null) body.model_id = modelId
    return api.post(`/recordings/${id}/transcript/regenerate-summary`, body).then((r) => r.data)
  },

  // ===== 导出 =====
  exportTxtUrl: (id, withTimestamps = false, withSpeakers = false) => {
    const token = localStorage.getItem('mt_token')
    const params = []
    if (withTimestamps) params.push('with_timestamps=true')
    if (withSpeakers) params.push('with_speakers=true')
    if (token) params.push(`token=${encodeURIComponent(token)}`)
    const qs = params.length ? '?' + params.join('&') : ''
    return `/api/recordings/export/${id}/txt${qs}`
  },
  exportSrtUrl: (id) => {
    const token = localStorage.getItem('mt_token')
    return `/api/recordings/export/${id}/srt${token ? '?token=' + encodeURIComponent(token) : ''}`
  },
  exportVttUrl: (id) => {
    const token = localStorage.getItem('mt_token')
    return `/api/recordings/export/${id}/vtt${token ? '?token=' + encodeURIComponent(token) : ''}`
  },
  exportPdfUrl: (id) => {
    const token = localStorage.getItem('mt_token')
    return `/api/recordings/export/${id}/pdf${token ? '?token=' + encodeURIComponent(token) : ''}`
  },
  exportDocxUrl: (id) => {
    const token = localStorage.getItem('mt_token')
    return `/api/recordings/export/${id}/docx${token ? '?token=' + encodeURIComponent(token) : ''}`
  },

  // ===== 分享访问 =====
  sharedDetail: (token) => api.get(`/share/${token}`).then((r) => r.data),

  // ===== 热词库 =====
  hotwordLibraries: () => api.get('/hotwords/libraries').then((r) => r.data),
  hotwordLibraryDetail: (id) => api.get(`/hotwords/libraries/${id}`).then((r) => r.data),
  createHotwordLibrary: (data) => api.post('/hotwords/libraries', data).then((r) => r.data),
  updateHotwordLibrary: (id, data) => api.patch(`/hotwords/libraries/${id}`, data).then((r) => r.data),
  deleteHotwordLibrary: (id) => api.delete(`/hotwords/libraries/${id}`).then((r) => r.data),
  addHotwords: (libId, hotwords) => api.post(`/hotwords/libraries/${libId}/words`, { hotwords }).then((r) => r.data),
  importHotwords: (libId, text) => api.post(`/hotwords/libraries/${libId}/import`, { text }).then((r) => r.data),
  deleteHotword: (wordId) => api.delete(`/hotwords/words/${wordId}`).then((r) => r.data),

  // ===== 会议类型 =====
  meetingTypes: () => api.get('/meeting-types').then((r) => r.data),
  createMeetingType: (data) => api.post('/meeting-types', data).then((r) => r.data),
  updateMeetingType: (id, data) => api.patch(`/meeting-types/${id}`, data).then((r) => r.data),
  deleteMeetingType: (id) => api.delete(`/meeting-types/${id}`).then((r) => r.data),
  resetMeetingTypePrompts: (id) => api.post(`/meeting-types/reset/${id}`).then((r) => r.data),
  sceneTypes: () => api.get('/meeting-types/scene-types/list').then((r) => r.data),

  // ===== LLM 模型管理 =====
  models: () => api.get('/models').then((r) => r.data),
  enabledModels: () => api.get('/models/enabled').then((r) => r.data),
  createModel: (data) => api.post('/models', data).then((r) => r.data),
  updateModel: (id, data) => api.patch(`/models/${id}`, data).then((r) => r.data),
  deleteModel: (id) => api.delete(`/models/${id}`).then((r) => r.data),

  // ===== 声纹管理 =====
  voiceprints: () => api.get('/voiceprints').then((r) => r.data),
  syncVoiceprints: () => api.get('/voiceprints/sync').then((r) => r.data),
  createVoiceprint: (formData) => api.post('/voiceprints', formData).then((r) => r.data),
  addVoiceprintSamples: (id, formData) => api.post(`/voiceprints/${id}/samples`, formData).then((r) => r.data),
  updateVoiceprint: (id, data) => api.patch(`/voiceprints/${id}`, data).then((r) => r.data),
  deleteVoiceprint: (id) => api.delete(`/voiceprints/${id}`).then((r) => r.data),

  // ===== 搜索 =====
  search: (params = {}) => {
    const { q = '', mode = 'auto', speaker = '', date_from = '', date_to = '', tag = '', sort_by = 'relevance', limit = 20 } = params
    const query = []
    if (q) query.push(`q=${encodeURIComponent(q)}`)
    if (mode) query.push(`mode=${mode}`)
    if (speaker) query.push(`speaker=${encodeURIComponent(speaker)}`)
    if (date_from) query.push(`date_from=${date_from}`)
    if (date_to) query.push(`date_to=${date_to}`)
    if (tag) query.push(`tag=${encodeURIComponent(tag)}`)
    if (sort_by) query.push(`sort_by=${sort_by}`)
    if (limit) query.push(`limit=${limit}`)
    const qs = query.length ? '?' + query.join('&') : ''
    return api.get(`/search${qs}`).then((r) => r.data)
  },
  searchStatus: () => api.get('/search/status').then((r) => r.data),
  reindexAll: () => api.post('/search/reindex?force=false').then((r) => r.data),
  reindexFull: () => api.post('/search/reindex?force=true').then((r) => r.data),
  reindexSingle: (id) => api.post(`/search/reindex/${id}`).then((r) => r.data),

  // ===== 提示词配置 =====
  promptConfigs: () => api.get('/prompt-configs').then((r) => r.data),
  promptConfig: (type) => api.get(`/prompt-configs/${type}`).then((r) => r.data),
  updatePromptConfig: (type, data) => api.patch(`/prompt-configs/${type}`, data).then((r) => r.data),
  resetPromptConfig: (type) => api.post(`/prompt-configs/${type}/reset`).then((r) => r.data),
  defaultPromptConfig: (type) => api.get(`/prompt-configs/defaults/${type}`).then((r) => r.data),

  // ===== 健康检查 =====
  health: () => api.get('/health').then((r) => r.data),

  // ===== 系统配置 =====
  config: () => api.get('/config').then((r) => r.data)
}
