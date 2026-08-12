<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { formatDateTime } from '../utils/format'

const message = ElMessage
const models = ref([])
const loading = ref(false)

// 编辑弹窗
const editModal = ref(false)
const editMode = ref('create')
const editForm = ref({
  name: '', model_id: '', base_url: '', api_key: '',
  model_type: 'chat',
  is_default: false, is_enabled: true, sort_order: 0, max_context_length: 32000,
  description: ''
})
const editId = ref(null)

async function load() {
  loading.value = true
  try {
    models.value = await api.models()
  } catch (e) {
    message.error(e._msg || '加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editMode.value = 'create'
  editForm.value = {
    name: '', model_id: '', base_url: '', api_key: '',
    model_type: 'chat',
    is_default: false, is_enabled: true, sort_order: 0, max_context_length: 32000,
    description: ''
  }
  editId.value = null
  editModal.value = true
}

function openEdit(m) {
  editMode.value = 'edit'
  editForm.value = {
    name: m.name,
    model_id: m.model_id,
    base_url: m.base_url,
    api_key: m.api_key || '',
    model_type: m.model_type || 'chat',
    is_default: m.is_default,
    is_enabled: m.is_enabled,
    sort_order: m.sort_order || 0,
    max_context_length: m.max_context_length || 32000,
    description: m.description || '',
  }
  editId.value = m.id
  editModal.value = true
}

function duplicateModel(m) {
  editMode.value = 'create'
  editForm.value = {
    name: m.name + ' (副本)',
    model_id: m.model_id,
    base_url: m.base_url,
    api_key: '',
    model_type: m.model_type || 'chat',
    is_default: false,
    is_enabled: m.is_enabled,
    sort_order: m.sort_order || 0,
    max_context_length: m.max_context_length || 32000,
    description: m.description || '',
  }
  editId.value = null
  editModal.value = true
}

async function confirmEdit() {
  if (!editForm.value.name.trim()) {
    message.warning('请输入模型名称')
    return
  }
  if (!editForm.value.model_id.trim()) {
    message.warning('请输入模型标识')
    return
  }
  if (!editForm.value.base_url.trim()) {
    message.warning('请输入 API Base URL')
    return
  }
  try {
    if (editMode.value === 'create') {
      await api.createModel(editForm.value)
      message.success('模型已创建')
    } else {
      await api.updateModel(editId.value, editForm.value)
      message.success('模型已更新')
    }
    editModal.value = false
    await load()
  } catch (e) {
    message.error(e._msg || '操作失败')
  }
}

async function deleteModel(id) {
  try {
    await ElMessageBox.confirm('确认删除此模型配置？', '删除', { type: 'warning' })
    await api.deleteModel(id)
    message.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') message.error(e._msg || '删除失败')
  }
}

async function setDefault(id) {
  try {
    await api.updateModel(id, { is_default: true })
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
      <span class="page-title">🤖 模型管理</span>
      <el-button type="primary" @click="openCreate">＋ 新建模型</el-button>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!models.length" description="暂无模型配置，点击右上角创建" />
      <el-row v-else :gutter="16">
        <el-col v-for="m in models" :key="m.id" :span="12" :xs="24" :sm="12" :md="12">
          <el-card shadow="hover">
            <template #header>
              <div class="card-header">
                <div class="card-header-left">
                  <span>{{ m.name }}</span>
                  <el-tag size="small" :type="m.model_type === 'embedding' ? 'primary' : m.model_type === 'rerank' ? 'warning' : 'info'">{{ { chat: '对话', embedding: '向量', rerank: '重排' }[m.model_type] || m.model_type }}</el-tag>
                  <el-tag v-if="m.is_default" size="small" type="success">默认</el-tag>
                  <el-tag v-if="!m.is_enabled" size="small" type="danger">已禁用</el-tag>
                </div>
                <div class="card-header-right">
                  <el-button v-if="!m.is_default" size="small" text type="success" @click="setDefault(m.id)">设为默认</el-button>
                  <el-button size="small" text @click="duplicateModel(m)" title="复制模型配置">📋</el-button>
                  <el-button size="small" text @click="openEdit(m)">✏️</el-button>
                  <el-button size="small" text type="danger" @click="deleteModel(m.id)">🗑</el-button>
                </div>
              </div>
            </template>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="模型标识">
                <code>{{ m.model_id }}</code>
              </el-descriptions-item>
              <el-descriptions-item label="API 地址">
                <span style="font-size: 12px; color: var(--el-text-color-secondary)">{{ m.base_url }}</span>
              </el-descriptions-item>
              <el-descriptions-item v-if="m.description" label="描述">
                {{ m.description }}
              </el-descriptions-item>
              <el-descriptions-item label="排序权重">
                {{ m.sort_order }}
              </el-descriptions-item>
              <el-descriptions-item label="最大上下文">
                {{ m.max_context_length || 32000 }} tokens
              </el-descriptions-item>
              <el-descriptions-item label="创建时间">
                {{ formatDateTime(m.created_at) }}
              </el-descriptions-item>
            </el-descriptions>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editModal" :title="editMode === 'create' ? '新建模型' : '编辑模型'" width="640px">
      <el-form label-position="top">
        <el-form-item label="模型名称" required>
          <el-input v-model="editForm.name" placeholder="如：GPT-4o、Qwen3-32B" />
        </el-form-item>
        <el-form-item label="模型标识 (model_id)" required>
          <el-input v-model="editForm.model_id" placeholder="如：gpt-4o、qwen3:32b" />
        </el-form-item>
        <el-form-item label="API Base URL" required>
          <el-input v-model="editForm.base_url" placeholder="如：http://host.docker.internal:11434" />
        </el-form-item>
        <el-form-item label="模型类型" required>
          <el-radio-group v-model="editForm.model_type">
            <el-radio value="chat">对话 (Chat)</el-radio>
            <el-radio value="embedding">向量 (Embedding)</el-radio>
            <el-radio value="rerank">重排序 (Rerank)</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="editForm.api_key" type="password" show-password placeholder="留空则使用空字符串" />
        </el-form-item>
        <el-form-item label="最大上下文长度 (tokens)">
          <el-input-number v-model="editForm.max_context_length" :min="1000" :max="1000000" :step="1000" style="width: 100%" />
          <span style="font-size: 12px; color: var(--el-text-color-secondary)">逐字稿超过此长度的 80% 时将自动分片处理</span>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" placeholder="模型描述（可选）" />
        </el-form-item>
        <div style="display: flex; gap: 24px">
          <el-form-item label="设为默认模型">
            <el-switch v-model="editForm.is_default" />
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="editForm.is_enabled" />
          </el-form-item>
          <el-form-item label="排序权重">
            <el-input-number v-model="editForm.sort_order" :min="0" style="width: 120px" />
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
