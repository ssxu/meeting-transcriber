<script setup>
import { onMounted, ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { formatDateTime } from '../utils/format'

const message = ElMessage
const types = ref([])
const loading = ref(false)
const sceneTypes = ref({ categories: [], sub_types: [] })

// 编辑弹窗
const editModal = ref(false)
const editMode = ref('create')
const editForm = ref({
  name: '', description: '', category: 'meeting', sub_type: 'regular',
  map_prompt: '', reduce_prompt: '', single_extract_prompt: '',
  is_default: false,
})
const editId = ref(null)

// 当前编辑的 prompt tab
const promptTab = ref('map')

// 按大类分组的细分类型
const subTypesByCategory = computed(() => {
  const result = { meeting: [], learning: [] }
  for (const st of sceneTypes.value.sub_types) {
    if (result[st.category]) result[st.category].push(st)
  }
  return result
})

const selectedSubTypeInfo = computed(() => {
  return sceneTypes.value.sub_types.find(s => s.key === editForm.value.sub_type) || null
})

// 大类切换时自动重置细分类型
watch(() => editForm.value.category, (newCat) => {
  const subs = subTypesByCategory.value[newCat] || []
  if (subs.length && !subs.find(s => s.key === editForm.value.sub_type)) {
    editForm.value.sub_type = subs[0].key
  }
})

// 细分类型变化时，如果是内置类型且 prompt 为空，提示可以从场景模板生成
watch(() => editForm.value.sub_type, () => {
  // 仅在创建模式或用户主动切换时提示
})

async function load() {
  loading.value = true
  try {
    const [typeList, scenes] = await Promise.all([
      api.meetingTypes(),
      api.sceneTypes(),
    ])
    types.value = typeList
    sceneTypes.value = scenes
  } catch (e) {
    message.error(e._msg || '加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editMode.value = 'create'
  editForm.value = {
    name: '', description: '', category: 'meeting', sub_type: 'regular',
    map_prompt: '', reduce_prompt: '', single_extract_prompt: '',
    is_default: false,
  }
  editId.value = null
  promptTab.value = 'map'
  editModal.value = true
}

function openEdit(t) {
  editMode.value = 'edit'
  editForm.value = {
    name: t.name,
    description: t.description || '',
    category: t.category || 'meeting',
    sub_type: t.sub_type || 'regular',
    map_prompt: t.map_prompt || '',
    reduce_prompt: t.reduce_prompt || '',
    single_extract_prompt: t.single_extract_prompt || '',
    is_default: t.is_default,
  }
  editId.value = t.id
  promptTab.value = 'map'
  editModal.value = true
}

async function confirmEdit() {
  if (!editForm.value.name.trim()) {
    message.warning('请输入类型名称')
    return
  }
  try {
    if (editMode.value === 'create') {
      await api.createMeetingType(editForm.value)
      message.success('内容类型已创建')
    } else {
      await api.updateMeetingType(editId.value, editForm.value)
      message.success('内容类型已更新')
    }
    editModal.value = false
    await load()
  } catch (e) {
    message.error(e._msg || '操作失败')
  }
}

async function deleteType(id) {
  try {
    await ElMessageBox.confirm('确认删除此内容类型？', '删除', { type: 'warning' })
    await api.deleteMeetingType(id)
    message.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') message.error(e._msg || '删除失败')
  }
}

async function resetPrompts(id) {
  try {
    await ElMessageBox.confirm('确定要将此类型的所有提示词重置为场景默认值吗？', '确认重置', { type: 'warning' })
    const updated = await api.resetMeetingTypePrompts(id)
    // 更新列表中的数据
    const idx = types.value.findIndex(t => t.id === id)
    if (idx >= 0) types.value[idx] = updated
    message.success('已重置为场景默认值')
  } catch (e) {
    if (e !== 'cancel') message.error(e._msg || '重置失败')
  }
}

function getCategoryLabel(key) {
  const cat = sceneTypes.value.categories.find(c => c.key === key)
  return cat ? cat.label : key
}

function getSubTypeLabel(key) {
  const st = sceneTypes.value.sub_types.find(s => s.key === key)
  return st ? st.label : key
}

function hasCustomPrompts(t) {
  return t.map_prompt || t.reduce_prompt || t.single_extract_prompt
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-header">
      <span class="page-title">🏷 内容类型管理</span>
      <el-button type="primary" @click="openCreate">＋ 新建类型</el-button>
    </div>

    <el-alert type="info" :closable="false" style="margin-bottom: 16px">
      每个内容类型包含 Map / Reduce / Single-Extract 三个 LLM 提示词模板，控制不同阶段的内容生成方式。
      内置类型已根据场景预设模板，可直接编辑或重置。
    </el-alert>

    <div v-loading="loading">
      <el-empty v-if="!types.length" description="暂无内容类型，点击右上角创建" />
      <el-row v-else :gutter="16">
        <el-col v-for="t in types" :key="t.id" :span="12" :xs="24" :sm="12" :md="12">
          <el-card shadow="hover">
            <template #header>
              <div class="card-header">
                <div class="card-header-left">
                  <span>{{ t.name }}</span>
                  <el-tag size="small" :type="t.category === 'learning' ? 'warning' : 'primary'">
                    {{ getCategoryLabel(t.category) }}
                  </el-tag>
                  <el-tag v-if="t.is_builtin" size="small" type="info">内置</el-tag>
                  <el-tag v-if="t.is_default" size="small" type="success">默认</el-tag>
                </div>
                <div class="card-header-right">
                  <el-button size="small" text @click="openEdit(t)">✏️ 编辑</el-button>
                  <el-button v-if="t.is_builtin" size="small" text @click="resetPrompts(t.id)">↻ 重置</el-button>
                  <el-button v-if="!t.is_builtin" size="small" text type="danger" @click="deleteType(t.id)">🗑 删除</el-button>
                </div>
              </div>
            </template>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="细分类型">
                {{ getSubTypeLabel(t.sub_type) }}
              </el-descriptions-item>
              <el-descriptions-item v-if="t.description" label="描述">
                {{ t.description }}
              </el-descriptions-item>
              <el-descriptions-item label="提示词模板">
                <div class="prompt-status">
                  <el-tag :type="t.map_prompt ? 'success' : 'info'" size="small">Map</el-tag>
                  <el-tag :type="t.reduce_prompt ? 'success' : 'info'" size="small">Reduce</el-tag>
                  <el-tag :type="t.single_extract_prompt ? 'success' : 'info'" size="small">Extract</el-tag>
                </div>
              </el-descriptions-item>
              <el-descriptions-item label="创建时间">
                {{ formatDateTime(t.created_at) }}
              </el-descriptions-item>
            </el-descriptions>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 编辑弹窗 -->
    <el-dialog
      v-model="editModal"
      :title="editMode === 'create' ? '新建内容类型' : '编辑内容类型'"
      width="900px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="类型名称" required>
              <el-input v-model="editForm.name" placeholder="如：周会、技术评审、课程讲座" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="描述">
              <el-input v-model="editForm.description" placeholder="类型描述（可选）" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="内容大类">
              <el-radio-group v-model="editForm.category">
                <el-radio-button v-for="cat in sceneTypes.categories" :key="cat.key" :value="cat.key">
                  {{ cat.label }}
                </el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="细分类型">
              <el-select v-model="editForm.sub_type" placeholder="选择细分类型" style="width: 100%">
                <el-option
                  v-for="st in subTypesByCategory[editForm.category] || []"
                  :key="st.key"
                  :label="st.label"
                  :value="st.key"
                >
                  <span>{{ st.label }}</span>
                  <span style="float: right; color: var(--el-text-color-secondary); font-size: 12px">
                    {{ st.map_focus }}
                  </span>
                </el-option>
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <div v-if="selectedSubTypeInfo" style="margin-bottom: 16px; padding: 10px 14px; background: var(--el-fill-color-light); border-radius: 6px; font-size: 13px; color: var(--el-text-color-secondary)">
          <div><strong>Map 侧重：</strong>{{ selectedSubTypeInfo.map_focus }}</div>
          <div style="margin-top: 4px"><strong>Reduce 输出：</strong>{{ selectedSubTypeInfo.reduce_output }}</div>
        </div>

        <!-- Prompt 模板编辑器 -->
        <el-divider>LLM 提示词模板</el-divider>
        <el-tabs v-model="promptTab">
          <el-tab-pane label="Map 阶段" name="map">
            <div style="font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 8px">
              逐块提取阶段：将转写文本分块后，逐块提取结构化信息（议题、决议、待办、摘要）。占位符 {time_range} 会被替换为片段时间范围。
            </div>
            <el-input
              v-model="editForm.map_prompt"
              type="textarea"
              :rows="14"
              placeholder="Map 阶段提示词模板..."
              class="prompt-editor"
            />
          </el-tab-pane>
          <el-tab-pane label="Reduce 阶段" name="reduce">
            <div style="font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 8px">
              全局汇总阶段：将各块的结构化摘要整合为完整的纪要文档。输出需包含 summary 和 action_items 的 JSON。
            </div>
            <el-input
              v-model="editForm.reduce_prompt"
              type="textarea"
              :rows="14"
              placeholder="Reduce 阶段提示词模板..."
              class="prompt-editor"
            />
          </el-tab-pane>
          <el-tab-pane label="单次提取" name="single_extract">
            <div style="font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 8px">
              短文本直接提取：当转写文本较短无需分片时，一次性提取全部信息。输出需包含 summary 和 action_items 的 JSON。
            </div>
            <el-input
              v-model="editForm.single_extract_prompt"
              type="textarea"
              :rows="14"
              placeholder="单次提取提示词模板..."
              class="prompt-editor"
            />
          </el-tab-pane>
        </el-tabs>

        <el-form-item label="设为默认" style="margin-top: 16px">
          <div style="display: flex; align-items: center; gap: 8px">
            <el-switch v-model="editForm.is_default" />
            <span style="font-size: 12px; color: var(--el-text-color-secondary)">设为默认后，上传录音时自动选中</span>
          </div>
        </el-form-item>
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

.prompt-status {
  display: flex;
  gap: 6px;
}

.prompt-editor :deep(.el-textarea__inner) {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.6;
}
</style>
