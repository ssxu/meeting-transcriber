import { createRouter, createWebHistory } from 'vue-router'
import RecordingList from '../views/RecordingList.vue'
import RecordingDetail from '../views/RecordingDetail.vue'
import SearchView from '../views/SearchView.vue'
import Dashboard from '../views/Dashboard.vue'
import SharedView from '../views/SharedView.vue'
import VoiceprintManager from '../views/VoiceprintManager.vue'
import HotwordManager from '../views/HotwordManager.vue'
import MeetingTypeManager from '../views/MeetingTypeManager.vue'
import ModelManager from '../views/ModelManager.vue'
import ExportPage from '../views/ExportPage.vue'
import TranscriptionQueue from '../views/TranscriptionQueue.vue'
import Login from '../views/Login.vue'

const routes = [
  { path: '/login', name: 'login', component: Login, meta: { public: true } },
  { path: '/', name: 'list', component: RecordingList },
  { path: '/search', name: 'search', component: SearchView },
  { path: '/dashboard', name: 'dashboard', component: Dashboard },
  { path: '/recordings/:id', name: 'detail', component: RecordingDetail },
  { path: '/share/:token', name: 'share', component: SharedView, meta: { public: true } },
  { path: '/voiceprints', name: 'voiceprints', component: VoiceprintManager },
  { path: '/hotwords', name: 'hotwords', component: HotwordManager },
  { path: '/meeting-types', name: 'meeting-types', component: MeetingTypeManager },
  { path: '/models', name: 'models', component: ModelManager },
  { path: '/export', name: 'export', component: ExportPage },
  { path: '/transcription-queue', name: 'transcription-queue', component: TranscriptionQueue },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫：未登录跳转登录页（分享页除外）
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('mt_token')
  if (to.meta.public || token) {
    next()
  } else {
    next({ name: 'login', query: { redirect: to.fullPath } })
  }
})

export default router
