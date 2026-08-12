<script setup>
import { onMounted, ref, computed, h } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import api from '../api'

const message = ElMessage
const libraries = ref([])
const loading = ref(false)

// 创建/编辑弹窗
const editModal = ref(false)
const editMode = ref('create')
const editForm = ref({ name: '', description: '', is_default: false })
const editId = ref(null)

// 热词管理
const activeLib = ref(null)
const libDetail = ref(null)
const libLoading = ref(false)

// 添加热词
const newWord = ref('')
const newWeight = ref(1)

// 批量导入
const importText = ref('')

// 热词表格列
const hotwordColumns = [
  { prop: 'word', label: '热词', width: 200 },
  { prop: 'weight', label: '权重', width: 80 },
  {
    prop: 'actions',
    label: '操作',
    width: 80,
    render: (row) => h(ElButton, {
      size: 'small',
      text: true,
      type: 'danger',
      onClick: () => deleteHotword(row.id)
    }, { default: () => '🗑' })
  }
]

// 使用 ElDataTable 需要通过 template 渲染，改用计算属性
const tableData = computed(() => libDetail.value?.hotwords || [])

async function load() {
  loading.value = true
  try {
    libraries.value = await api.hotwordLibraries()
    if (activeLib.value) {
      await loadLibDetail(activeLib.value)
    }
  } catch (e) {
    message.error(e._msg || '加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editMode.value = 'create'
  editForm.value = { name: '', description: '', is_default: false }
  editId.value = null
  editModal.value = true
}

function openEdit(lib) {
  editMode.value = 'edit'
  editForm.value = { name: lib.name, description: lib.description || '', is_default: lib.is_default }
  editId.value = lib.id
  editModal.value = true
}

async function confirmEdit() {
  if (!editForm.value.name.trim()) {
    message.warning('请输入库名称')
    return
  }
  try {
    if (editMode.value === 'create') {
      await api.createHotwordLibrary(editForm.value)
      message.success('热词库已创建')
    } else {
      await api.updateHotwordLibrary(editId.value, editForm.value)
      message.success('热词库已更新')
    }
    editModal.value = false
    await load()
  } catch (e) {
    message.error(e._msg || '操作失败')
  }
}

async function deleteLibrary(id) {
  try {
    await ElMessageBox.confirm('确认删除此热词库？', '删除', { type: 'warning' })
    await api.deleteHotwordLibrary(id)
    message.success('已删除')
    if (activeLib.value === id) {
      activeLib.value = null
      libDetail.value = null
    }
    await load()
  } catch (e) {
    if (e !== 'cancel') message.error(e._msg || '删除失败')
  }
}

async function loadLibDetail(id) {
  activeLib.value = id
  libLoading.value = true
  try {
    libDetail.value = await api.hotwordLibraryDetail(id)
  } catch (e) {
    message.error(e._msg || '加载详情失败')
  } finally {
    libLoading.value = false
  }
}

async function addWord() {
  if (!newWord.value.trim()) {
    message.warning('请输入热词')
    return
  }
  try {
    await api.addHotwords(activeLib.value, [{ word: newWord.value.trim(), weight: newWeight.value }])
    message.success('热词已添加')
    newWord.value = ''
    newWeight.value = 1
    await loadLibDetail(activeLib.value)
    await load()
  } catch (e) {
    message.error(e._msg || '添加失败')
  }
}

async function importHotwords() {
  if (!importText.value.trim()) {
    message.warning('请输入热词文本')
    return
  }
  try {
    const res = await api.importHotwords(activeLib.value, importText.value)
    message.success(`已导入 ${res.length} 个热词`)
    importText.value = ''
    await loadLibDetail(activeLib.value)
    await load()
  } catch (e) {
    message.error(e._msg || '导入失败')
  }
}

async function deleteHotword(wordId) {
  try {
    await api.deleteHotword(wordId)
    message.success('已删除')
    await loadLibDetail(activeLib.value)
    await load()
  } catch (e) {
    message.error(e._msg || '删除失败')
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-header">
      <span class="page-title">📝 热词库管理</span>
      <el-button type="primary" @click="openCreate">＋ 新建热词库</el-button>
    </div>

    <el-row :gutter="16">
      <!-- 左侧：热词库列表 -->
      <el-col :span="8" :xs="24" :md="8">
        <div v-loading="loading">
          <el-card shadow="never">
            <template #header>热词库列表</template>
            <el-empty v-if="!libraries.length" description="暂无热词库" />
            <div v-else class="lib-list">
              <el-card
                v-for="lib in libraries"
                :key="lib.id"
                shadow="hover"
                :class="['lib-item', { 'lib-active': activeLib === lib.id }]"
                @click="loadLibDetail(lib.id)"
              >
                <div class="lib-row">
                  <div class="lib-info">
                    <span class="lib-name">{{ lib.name }}</span>
                    <el-tag v-if="lib.is_default" size="small" type="success">默认</el-tag>
                    <el-tag size="small">{{ lib.hotword_count }}词</el-tag>
                  </div>
                  <div class="lib-actions">
                    <el-button size="small" text @click.stop="openEdit(lib)">✏️</el-button>
                    <el-button size="small" text type="danger" @click.stop="deleteLibrary(lib.id)">🗑</el-button>
                  </div>
                </div>
                <div v-if="lib.description" class="lib-desc">{{ lib.description }}</div>
              </el-card>
            </div>
          </el-card>
        </div>
      </el-col>

      <!-- 右侧：热词详情和管理 -->
      <el-col :span="16" :xs="24" :md="16">
        <div v-loading="libLoading">
          <el-card v-if="libDetail" shadow="never">
            <template #header>{{ libDetail.name }} - 热词管理</template>
            <el-tabs>
              <!-- 热词列表 -->
              <el-tab-pane label="热词列表" name="list">
                <el-table :data="tableData" border stripe size="small" max-height="400">
                  <el-table-column prop="word" label="热词" width="200" />
                  <el-table-column prop="weight" label="权重" width="80" />
                  <el-table-column label="操作" width="80">
                    <template #default="{ row }">
                      <el-button size="small" text type="danger" @click="deleteHotword(row.id)">🗑</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </el-tab-pane>

              <!-- 添加热词 -->
              <el-tab-pane label="添加热词" name="add">
                <div style="display: flex; align-items: center; gap: 8px">
                  <el-input v-model="newWord" placeholder="输入热词" style="width: 200px" @keyup.enter="addWord" />
                  <el-input-number v-model="newWeight" :min="1" :max="100" style="width: 120px" />
                  <el-button type="primary" @click="addWord">添加</el-button>
                </div>
              </el-tab-pane>

              <!-- 批量导入 -->
              <el-tab-pane label="批量导入" name="import">
                <div style="font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 8px">
                  每行一个热词，格式：词 权重（权重可选，默认1）。如：<br>
                  腾讯 10<br>
                  微信 8 5
                </div>
                <el-input v-model="importText" type="textarea" :rows="8" placeholder="每行一个热词" />
                <el-button type="primary" style="margin-top: 8px" @click="importHotwords">导入</el-button>
              </el-tab-pane>
            </el-tabs>
          </el-card>
          <el-card v-else shadow="never">
            <el-empty description="选择左侧热词库查看详情" />
          </el-card>
        </div>
      </el-col>
    </el-row>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editModal" :title="editMode === 'create' ? '新建热词库' : '编辑热词库'" width="460px">
      <el-form label-position="top">
        <el-form-item label="库名称" required>
          <el-input v-model="editForm.name" placeholder="如：公司专有名词" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="2" placeholder="热词库描述" />
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="editForm.is_default" />
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

.lib-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.lib-item {
  cursor: pointer;
}

.lib-active {
  border-color: var(--el-color-primary) !important;
  background: var(--el-color-primary-light-9, rgba(64, 158, 255, 0.05));
}

.lib-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.lib-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.lib-name {
  font-weight: bold;
}

.lib-actions {
  display: flex;
  gap: 4px;
}

.lib-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
</style>
