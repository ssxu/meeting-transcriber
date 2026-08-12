<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import { formatDateTime } from '../utils/format'

const message = ElMessage
const speakers = ref([])
const loading = ref(false)

// 创建弹窗
const createModal = ref(false)
const createForm = ref({ display_name: '', description: '' })
const createFileList = ref([])

// 添加样本弹窗
const sampleModal = ref(false)
const sampleTarget = ref(null)
const sampleFileList = ref([])

async function load() {
  loading.value = true
  try {
    speakers.value = await api.voiceprints()
  } catch (e) {
    message.error(e._msg || '加载失败')
  } finally {
    loading.value = false
  }
}

async function syncFromRemote() {
  loading.value = true
  try {
    const res = await api.syncVoiceprints()
    message.success(res.detail)
    await load()
  } catch (e) {
    message.error(e._msg || '同步失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.value = { display_name: '', description: '' }
  createFileList.value = []
  createModal.value = true
}

async function confirmCreate() {
  if (!createForm.value.display_name.trim()) {
    message.warning('请输入说话人名称')
    return
  }
  if (!createFileList.value.length) {
    message.warning('请至少上传一个音频样本')
    return
  }
  const fd = new FormData()
  fd.append('display_name', createForm.value.display_name.trim())
  if (createForm.value.description) fd.append('description', createForm.value.description)
  createFileList.value.forEach(f => {
    const file = f.raw || f
    fd.append('file', file)
  })
  try {
    await api.createVoiceprint(fd)
    message.success('说话人创建成功')
    createModal.value = false
    await load()
  } catch (e) {
    message.error(e._msg || '创建失败')
  }
}

function openAddSamples(speaker) {
  sampleTarget.value = speaker
  sampleFileList.value = []
  sampleModal.value = true
}

async function confirmAddSamples() {
  if (!sampleFileList.value.length) {
    message.warning('请至少上传一个音频样本')
    return
  }
  const fd = new FormData()
  sampleFileList.value.forEach(f => {
    const file = f.raw || f
    fd.append('files', file)
  })
  try {
    const res = await api.addVoiceprintSamples(sampleTarget.value.id, fd)
    message.success(res.detail)
    sampleModal.value = false
    await load()
  } catch (e) {
    message.error(e._msg || '添加失败')
  }
}

async function deleteSpeaker(id) {
  try {
    await ElMessageBox.confirm('确认删除此说话人？', '删除', { type: 'warning' })
    await api.deleteVoiceprint(id)
    message.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') message.error(e._msg || '删除失败')
  }
}

function onCreateFileChange(file, fileList) {
  createFileList.value = fileList
}

function onSampleFileChange(file, fileList) {
  sampleFileList.value = fileList
}

function handleCreateRemove(file, fileList) {
  createFileList.value = fileList
}

function handleSampleRemove(file, fileList) {
  sampleFileList.value = fileList
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-header">
      <span class="page-title">🎤 声纹管理</span>
      <div style="display: flex; gap: 8px">
        <el-button @click="syncFromRemote" :loading="loading">从ASR后端同步</el-button>
        <el-button type="primary" @click="openCreate">＋ 注册说话人</el-button>
      </div>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!speakers.length" description="暂无声纹说话人，点击右上角注册" />
      <el-row v-else :gutter="16">
        <el-col v-for="sp in speakers" :key="sp.id" :span="8" :xs="24" :sm="12" :md="8">
          <el-card shadow="hover">
            <template #header>
              <div class="card-header">
                <div class="card-header-left">
                  <span>{{ sp.display_name }}</span>
                  <el-tag size="small" type="info">{{ sp.voiceprint_count }} 个样本</el-tag>
                </div>
                <div class="card-header-right">
                  <el-button size="small" text @click="openAddSamples(sp)">＋ 添加样本</el-button>
                  <el-button size="small" text type="danger" @click="deleteSpeaker(sp.id)">🗑</el-button>
                </div>
              </div>
            </template>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="说话人ID">
                <code style="font-size: 12px">{{ sp.speaker_id }}</code>
              </el-descriptions-item>
              <el-descriptions-item v-if="sp.description" label="描述">
                {{ sp.description }}
              </el-descriptions-item>
              <el-descriptions-item label="创建时间">
                {{ formatDateTime(sp.created_at) }}
              </el-descriptions-item>
            </el-descriptions>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 创建说话人弹窗 -->
    <el-dialog v-model="createModal" title="注册声纹说话人" width="520px">
      <el-form label-position="top">
        <el-form-item label="说话人名称" required>
          <el-input v-model="createForm.display_name" placeholder="如：张三" />
        </el-form-item>
        <el-form-item label="描述（可选）">
          <el-input v-model="createForm.description" type="textarea" :rows="2" placeholder="说话人描述信息" />
        </el-form-item>
        <el-form-item label="音频样本（单人说话）" required>
          <el-upload
            :on-change="onCreateFileChange"
            :on-remove="handleCreateRemove"
            accept="audio/*"
            :limit="10"
            multiple
            :auto-upload="false"
          >
            <el-button>选择音频文件</el-button>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createModal = false">取消</el-button>
        <el-button type="primary" @click="confirmCreate">确认注册</el-button>
      </template>
    </el-dialog>

    <!-- 添加样本弹窗 -->
    <el-dialog v-model="sampleModal" :title="`添加样本 - ${sampleTarget?.display_name || ''}`" width="480px">
      <el-upload
        :on-change="onSampleFileChange"
        :on-remove="handleSampleRemove"
        accept="audio/*"
        :limit="10"
        multiple
        :auto-upload="false"
      >
        <el-button>选择音频文件</el-button>
      </el-upload>
      <template #footer>
        <el-button @click="sampleModal = false">取消</el-button>
        <el-button type="primary" @click="confirmAddSamples">确认添加</el-button>
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
