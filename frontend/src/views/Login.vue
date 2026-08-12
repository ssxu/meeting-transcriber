<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'

const router = useRouter()
const password = ref('')
const loading = ref(false)

async function handleLogin() {
  if (!password.value) {
    ElMessage.warning('请输入密码')
    return
  }
  loading.value = true
  try {
    const res = await api.login(password.value)
    localStorage.setItem('mt_token', res.token)
    ElMessage.success('登录成功')
    const redirect = router.currentRoute.value.query.redirect || '/'
    router.push(redirect)
  } catch (e) {
    ElMessage.error(e._msg || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrapper">
    <el-card style="width: 380px">
      <template #header>
        <span style="font-size: 18px; font-weight: bold">🎙️ 会议转录平台</span>
      </template>
      <div style="display: flex; flex-direction: column; gap: 16px">
        <span style="font-size: 13px; color: var(--el-text-color-secondary); text-align: center">
          请输入密码登录
        </span>
        <el-input
          v-model="password"
          type="password"
          placeholder="密码"
          show-password
          @keyup.enter="handleLogin"
        />
        <el-button
          type="primary"
          :loading="loading"
          @click="handleLogin"
        >登录</el-button>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrapper {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 80vh;
}
</style>
