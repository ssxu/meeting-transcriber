/**
 * 应用配置 Pinia Store - 集中管理后端配置（时区等）
 */
import { defineStore } from 'pinia'
import api from '../api'

export const useConfigStore = defineStore('config', {
  state: () => ({
    timezone: '',
    loaded: false,
    loading: false,
  }),

  getters: {
    /**
     * 获取有效的时区名称。
     * 如果已从后端加载则用后端配置，否则用浏览器本地时区兜底。
     */
    effectiveTimezone(state) {
      return state.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
    },

    isReady(state) {
      return state.loaded
    },
  },

  actions: {
    async load() {
      if (this.loading) return
      this.loading = true
      try {
        const config = await api.config()
        this.timezone = config.timezone
        this.loaded = true
      } catch {
        // 加载失败，使用浏览器本地时区兜底
        this.loaded = true
      } finally {
        this.loading = false
      }
    },
  },
})
