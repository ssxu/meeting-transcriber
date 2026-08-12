// ===== Enhanced Stats — add these methods to the exported object in api/index.js =====
// Insert after the existing `stats:` line.

  // 统计（带日期筛选）
  statsWithFilter: (params = {}) => {
    const { date_from = '', date_to = '' } = params
    const query = []
    if (date_from) query.push(`date_from=${date_from}`)
    if (date_to) query.push(`date_to=${date_to}`)
    const qs = query.length ? '?' + query.join('&') : ''
    return api.get(`/stats${qs}`).then((r) => r.data)
  },

  // 单场会议效率分析
  meetingAnalysis: (recId) => api.get(`/stats/meeting-analysis/${recId}`).then((r) => r.data),

  // 发言人汇总统计（带日期筛选）
  speakerSummary: (params = {}) => {
    const { date_from = '', date_to = '' } = params
    const query = []
    if (date_from) query.push(`date_from=${date_from}`)
    if (date_to) query.push(`date_to=${date_to}`)
    const qs = query.length ? '?' + query.join('&') : ''
    return api.get(`/stats/speaker-summary${qs}`).then((r) => r.data)
  },
