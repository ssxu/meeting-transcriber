<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { formatDateTime } from '../utils/format'

const message = ElMessage
const providers = ref([])
const loading = ref(false)

// 编辑弹窗
const editModal = ref(false)
const editMode = ref('create')
const editForm = ref({
  name: '',
  base_url: '',
  auth_header: '',
  auth_value: '',
  timeout: 600,
  supports_speaker: true,
  supports_hotwords: true,
  is_default: false,
  is_enabled: true,
  sort_order: 0,
  description: ''
})
const editId = ref(null)

async function load() {
  loading.value = true
  try {
    providers.value = await api.asrProviders()
  } catch (e) {
    message.error(e._msg || '加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editMode.value = 'create'
  editForm.value = {
    name: '', base_url: '', auth_header: '', auth_value: '',
    timeout: 600, supports_speaker: true, supports_hotwords: true,
    is_default: false, is_enabled: true, sort_order: 0, description: ''
  }
  editId.value = null
  editModal.value = true
}

function openEdit(p) {
  editMode.value = 'edit'
  editForm.value = {
    name: p.name,
    base_url: p.base_url,
    auth_header: p.auth_header || '',
    auth_value: p.auth_value || '',
    timeout: p.timeout || 600,
    supports_speaker: p.supports_speaker ?? true,
    supports_hotwords: p.supports_hotwords ?? true,
    is_default: p.is_default,
    is_enabled: p.is_enabled,
    sort_order: p.sort_order || 0,
    description: p.description || '',
  }
  editId.value = p.id
  editModal.value = true
}

async function confirmEdit() {
  if (!editForm.value.name.trim()) {
    message.warning('请输入名称')
    return
  }
  if (!editForm.value.base_url.trim()) {
    message.warning('请输入服务地址')
    return
  }
  try {
    if (editMode.value === 'create') {
      await api.createAsrProvider(editForm.value)
      message.success('ASR 提供商已创建')
    } else {
      await api.updateAsrProvider(editId.value, editForm.value)
      message.success('ASR 提供商已更新')
    }
    editModal.value = false
    await load()
  } catch (e) {
    message.error(e._msg || '操作失败')
  }
}

async function deleteProvider(id) {
  try {
    await ElMessageBox.confirm('确认删除此 ASR 提供商配置？', '删除', { type: 'warning' })
    await api.deleteAsrProvider(id)
    message.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') message.error(e._msg || '删除失败')
  }
}

async function setDefault(id) {
  try {
    await api.updateAsrProvider(id, { is_default: true })
    message.success('已设为默认')
    await load()
  } catch (e) {
    message.error(e._msg || '操作失败')
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-header">
      <span class="page-title">🎙️ ASR 提供商管理</span>
      <el-button type="primary" @click="openCreate">＋ 新建提供商</el-button>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!providers.length" description="暂无 ASR 提供商配置，点击右上角创建" />
      <el-row v-else :gutter="16">
        <el-col v-for="p in providers" :key="p.id" :span="12" :xs="24" :sm="12" :md="12">
          <el-card shadow="hover">
            <template #header>
              <div class="card-header">
                <div class="card-header-left">
                  <span>{{ p.name }}</span>
                  <el-tag v-if="p.is_default" size="small" type="success">默认</el-tag>
                  <el-tag v-if="!p.is_enabled" size="small" type="danger">已禁用</el-tag>
                  <el-tag v-if="p.supports_speaker" size="small">说话人</el-tag>
                  <el-tag v-if="p.supports_hotwords" size="small">热词</el-tag>
                </div>
                <div class="card-header-right">
                  <el-button v-if="!p.is_default" size="small" text type="success" @click="setDefault(p.id)">设为默认</el-button>
                  <el-button size="small" text @click="openEdit(p)">✏️</el-button>
                  <el-button size="small" text type="danger" @click="deleteProvider(p.id)">🗑</el-button>
                </div>
              </div>
            </template>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="服务地址">
                <span style="font-size: 12px; color: var(--el-text-color-secondary)">{{ p.base_url }}</span>
              </el-descriptions-item>
              <el-descriptions-item v-if="p.auth_header" label="认证头">
                {{ p.auth_header }}
              </el-descriptions-item>
              <el-descriptions-item label="超时">
                {{ p.timeout }} 秒
              </el-descriptions-item>
              <el-descriptions-item v-if="p.description" label="描述">
                {{ p.description }}
              </el-descriptions-item>
              <el-descriptions-item label="排序权重">
                {{ p.sort_order }}
              </el-descriptions-item>
              <el-descriptions-item label="创建时间">
                {{ formatDateTime(p.created_at) }}
              </el-descriptions-item>
            </el-descriptions>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editModal" :title="editMode === 'create' ? '新建 ASR 提供商' : '编辑 ASR 提供商'" width="560px">
      <el-form label-position="top">
        <el-form-item label="名称" required>
          <el-input v-model="editForm.name" placeholder="如：Paraformer-CAM++、Qwen3-ASR" />
        </el-form-item>
        <el-form-item label="API Base URL" required>
          <el-input v-model="editForm.base_url" placeholder="如：http://host.docker.internal:8000" />
        </el-form-item>
        <el-form-item label="认证头字段名">
          <el-input v-model="editForm.auth_header" placeholder="如：Authorization、X-NLS-Token" />
        </el-form-item>
        <el-form-item label="认证头值">
          <el-input v-model="editForm.auth_value" placeholder="Bearer token 或任意字符串" />
        </el-form-item>
        <el-form-item label="超时（秒）">
          <el-input-number v-model="editForm.timeout" :min="30" :max="3600" :step="30" style="width: 100%" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" placeholder="提供商描述（可选）" />
        </el-form-item>
        <div style="display: flex; gap: 24px">
          <el-form-item label="设为默认">
            <el-switch v-model="editForm.is_default" />
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="editForm.is_enabled" />
          </el-form-item>
          <el-form-item label="排序权重">
            <el-input-number v-model="editForm.sort_order" :min="0" style="width: 120px" />
          </el-form-item>
        </div>
        <el-divider content-position="left">功能支持</el-divider>
        <div style="display: flex; gap: 24px">
          <el-form-item label="支持说话人识别">
            <el-switch v-model="editForm.supports_speaker" />
          </el-form-item>
          <el-form-item label="支持热词库">
            <el-switch v-model="editForm.supports_hotwords" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="editModal = false">取消</el-button>
        <el-button type="primary" @click="confirmEdit">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
}

.page-title {
  font-size: 18px;
  font-weight: bold;
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
}

.card-header-right {
  display: flex;
  gap: 4px;
}
</style>