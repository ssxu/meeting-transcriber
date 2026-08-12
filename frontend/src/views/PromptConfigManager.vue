<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const message = ElMessage
const loading = ref(false)
const configs = ref([])
const activeType = ref(null)
const editingContent = ref('')
const editingDescription = ref('')
const defaultContent = ref('')
const showDefault = ref(false)
const saving = ref(false)

const typeLabels = {
  map: 'Map 阶段',
  reduce: 'Reduce 阶段',
  single_extract: '单次提取',
}

const typeDescriptions = {
  map: '逐块提取会议逐字稿片段的结构化信息（议题、决议、待办、摘要、关键词）',
  reduce: '将多个片段摘要整合为完整的会议纪要文档',
  single_extract: '会议逐字稿较短时直接提取全部信息',
}

const activeConfig = computed(() => {
  return configs.value.find(c => c.prompt_type === activeType.value) || null
})

async function loadConfigs() {
  loading.value = true
  try {
    configs.value = await api.promptConfigs()
    if (configs.value.length > 0 && !activeType.value) {
      activeType.value = configs.value[0].prompt_type
      await selectType(activeType.value)
    }
  } catch (e) {
    message.error(e._msg || '加载失败')
  } finally {
    loading.value = false
  }
}

async function selectType(type) {
  activeType.value = type
  const config = configs.value.find(c => c.prompt_type === type)
  if (config) {
    editingContent.value = config.content
    editingDescription.value = config.description || ''
  }
  // 加载默认值备用
  try {
    defaultContent.value = (await api.defaultPromptConfig(type)).content
  } catch {
    defaultContent.value = ''
  }
  showDefault.value = false
}

async function save() {
  if (!activeType.value) return
  saving.value = true
  try {
    const updated = await api.updatePromptConfig(activeType.value, {
      content: editingContent.value,
      description: editingDescription.value,
    })
    const idx = configs.value.findIndex(c => c.prompt_type === activeType.value)
    if (idx >= 0) configs.value[idx] = updated
    message.success('保存成功')
  } catch (e) {
    message.error(e._msg || '保存失败')
  } finally {
    saving.value = false
  }
}

async function reset() {
  if (!activeType.value) return
  try {
    await ElMessageBox.confirm('确定要重置为默认值吗？当前修改将丢失。', '确认重置', {
      type: 'warning',
    })
  } catch {
    return
  }
  loading.value = true
  try {
    const updated = await api.resetPromptConfig(activeType.value)
    const idx = configs.value.findIndex(c => c.prompt_type === activeType.value)
    if (idx >= 0) configs.value[idx] = updated
    editingContent.value = updated.content
    editingDescription.value = updated.description || ''
    message.success('已重置为默认值')
  } catch (e) {
    message.error(e._msg || '重置失败')
  } finally {
    loading.value = false
  }
}

async function toggleEnabled(config) {
  try {
    const updated = await api.updatePromptConfig(config.prompt_type, {
      is_enabled: !config.is_enabled,
    })
    const idx = configs.value.findIndex(c => c.prompt_type === config.prompt_type)
    if (idx >= 0) configs.value[idx] = updated
    message.success(updated.is_enabled ? '已启用' : '已禁用')
  } catch (e) {
    message.error(e._msg || '操作失败')
  }
}

onMounted(loadConfigs)
</script>

<template>
  <div class="prompt-config-page">
    <h2>提示词配置</h2>
    <p class="page-desc">管理会议纪要生成各阶段的提示词模板。未启用时使用系统默认值。</p>

    <div class="config-layout" v-loading="loading">
      <!-- 左侧类型列表 -->
      <div class="type-list">
        <div
          v-for="config in configs"
          :key="config.prompt_type"
          :class="['type-item', { active: activeType === config.prompt_type }]"
          @click="selectType(config.prompt_type)"
        >
          <div class="type-item-header">
            <span class="type-label">{{ typeLabels[config.prompt_type] || config.prompt_type }}</span>
            <el-tag
              :type="config.is_enabled ? 'success' : 'info'"
              size="small"
            >{{ config.is_enabled ? '启用' : '禁用' }}</el-tag>
          </div>
          <div class="type-item-desc">{{ typeDescriptions[config.prompt_type] || '' }}</div>
        </div>
      </div>

      <!-- 右侧编辑区 -->
      <div class="editor-area" v-if="activeConfig">
        <div class="editor-header">
          <div>
            <h3>{{ typeLabels[activeType] || activeType }}</h3>
            <p class="editor-desc">{{ typeDescriptions[activeType] || '' }}</p>
          </div>
          <div class="editor-actions">
            <el-button
              :type="activeConfig.is_enabled ? 'warning' : 'success'"
              size="small"
              @click="toggleEnabled(activeConfig)"
            >{{ activeConfig.is_enabled ? '禁用' : '启用' }}</el-button>
            <el-button size="small" @click="showDefault = !showDefault">
              {{ showDefault ? '隐藏默认值' : '查看默认值' }}
            </el-button>
            <el-button type="danger" size="small" @click="reset">重置为默认</el-button>
          </div>
        </div>

        <el-input
          v-model="editingDescription"
          placeholder="描述（可选）"
          size="small"
          style="margin-bottom: 12px;"
        />

        <el-input
          v-model="editingContent"
          type="textarea"
          :rows="20"
          placeholder="请输入提示词内容..."
          class="prompt-editor"
        />

        <div class="editor-footer">
          <el-button type="primary" @click="save" :loading="saving">保存</el-button>
        </div>

        <!-- 默认值对比区 -->
        <div v-if="showDefault" class="default-preview">
          <h4>系统默认值</h4>
          <pre class="default-content">{{ defaultContent }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.prompt-config-page {
  max-width: 1200px;
  margin: 0 auto;
}

.page-desc {
  color: var(--el-text-color-secondary, #909399);
  font-size: 14px;
  margin-bottom: 20px;
}

.config-layout {
  display: flex;
  gap: 20px;
  min-height: 500px;
}

.type-list {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.type-item {
  padding: 12px 16px;
  border: 1px solid var(--el-border-color, #dcdfe6);
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.type-item:hover {
  border-color: var(--el-color-primary, #409eff);
}

.type-item.active {
  border-color: var(--el-color-primary, #409eff);
  background: var(--el-color-primary-light-9, #ecf5ff);
}

.type-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.type-label {
  font-weight: 600;
  font-size: 14px;
}

.type-item-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  line-height: 1.4;
}

.editor-area {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.editor-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.editor-header h3 {
  margin: 0 0 4px 0;
  font-size: 16px;
}

.editor-desc {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary, #909399);
}

.editor-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.prompt-editor :deep(.el-textarea__inner) {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.6;
}

.editor-footer {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

.default-preview {
  margin-top: 20px;
  padding: 16px;
  border: 1px dashed var(--el-border-color, #dcdfe6);
  border-radius: 8px;
  background: var(--el-fill-color-light, #fafafa);
}

.default-preview h4 {
  margin: 0 0 8px 0;
  font-size: 14px;
}

.default-content {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 400px;
  overflow-y: auto;
  margin: 0;
}
</style>
