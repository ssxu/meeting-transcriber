<script setup>
import { ref, watch, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useConfigStore } from './stores/config'

const dark = ref(false)
const router = useRouter()
const route = useRoute()

// 分享页不显示导航栏
const showNav = computed(() => !route.path.startsWith('/share/') && !route.path.startsWith('/login'))

watch(dark, (isDark) => {
  document.documentElement.classList.toggle('dark', isDark)
  document.body.style.background = isDark ? '#141414' : '#f5f6f8'
}, { immediate: true })

const menuOptions = [
  { index: 'list', label: '录音列表' },
  { index: 'search', label: '🔍 搜索' },
  { index: 'dashboard', label: '仪表盘' },
]

const manageItems = [
  { key: 'transcription-queue', label: '📋 待转录队列' },
  { key: 'voiceprints', label: '🎤 声纹管理' },
  { key: 'hotwords', label: '📝 热词库' },
  { key: 'meeting-types', label: '🏷 内容类型配置' },
  { key: 'models', label: '🤖 模型管理' },
  { key: 'export', label: '📦 导出' },
]

function handleMenuSelect(index) {
  router.push({ name: index })
}

function handleManageSelect(key) {
  router.push({ name: key })
}

// 应用启动时加载系统配置（含时区）
const configStore = useConfigStore()
onMounted(() => {
  configStore.load()
})

function logout() {
  localStorage.removeItem('mt_token')
  router.push('/login')
}
</script>

<template>
  <div :class="['app-container', { dark }]">
    <el-config-provider>
      <div class="layout-wrapper">
        <!-- 顶部导航栏 -->
        <header
          v-if="showNav"
          class="app-header"
        >
          <div class="header-left">
            <span class="app-title">🎙️ 会议转录平台</span>
            <el-menu
              mode="horizontal"
              :default-active="route.name"
              :ellipsis="false"
              @select="handleMenuSelect"
              class="header-menu"
            >
              <el-menu-item
                v-for="item in menuOptions"
                :key="item.index"
                :index="item.index"
              >{{ item.label }}</el-menu-item>
            </el-menu>
            <el-dropdown trigger="hover" @command="handleManageSelect">
              <el-button text>管理 ▾</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    v-for="item in manageItems"
                    :key="item.key"
                    :command="item.key"
                  >{{ item.label }}</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <div class="header-right">
            <span class="dark-label">深色</span>
            <el-switch v-model="dark" size="small" />
            <el-button text @click="logout">退出</el-button>
          </div>
        </header>

        <!-- 内容区 -->
        <main :class="['app-content', { 'no-padding': !showNav }]">
          <router-view />
        </main>
      </div>
    </el-config-provider>
  </div>
</template>

<style scoped>
.app-container {
  height: 100vh;
}

.layout-wrapper {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 28px;
  border-bottom: 1px solid var(--el-border-color-light, #e4e7ed);
  background: var(--el-bg-color, #fff);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 24px;
}

.app-title {
  font-size: 18px;
  font-weight: bold;
}

.header-menu {
  border-bottom: none !important;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.dark-label {
  font-size: 13px;
  color: var(--el-text-color-secondary, #909399);
}

.app-content {
  flex: 1;
  overflow-y: auto;
  padding: 28px;
}

.app-content.no-padding {
  padding: 0;
}
</style>
