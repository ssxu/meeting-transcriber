<script setup>
import { ref, watch, nextTick } from 'vue'
import { marked } from 'marked'
import api from '../api'

const props = defineProps({
  recordingId: { type: Number, required: true },
  summaryText: { type: String, default: '' },
  segments: { type: Array, default: () => [] },
  modelOptions: { type: Array, default: () => [] },
  hasTranscript: { type: Boolean, default: false },
})

const emit = defineEmits(['seek-to-time'])

const messages = ref([])
const inputText = ref('')
const loading = ref(false)
const selectedModelId = ref(null)
const expandedSources = ref(new Set())
const messageListRef = ref(null)
const inputRef = ref(null)

const SUGGESTIONS = [
  '这个会议讨论了什么？',
  '主要决议有哪些？',
  '参会人各自说了什么？',
  '有哪些待办事项？',
]

watch(
  () => props.recordingId,
  () => {
    messages.value = []
    selectedModelId.value = null
  }
)

function scrollToBottom() {
  nextTick(() => {
    if (messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight
    }
  })
}

function formatTime(seconds) {
  if (seconds == null || isNaN(seconds)) return ''
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

async function send() {
  const text = inputText.value.trim()
  if (!text || loading.value) return

  // 禁用输入防止重复提交
  inputText.value = ''
  loading.value = true

  messages.value.push({ role: 'user', content: text })
  scrollToBottom()

  try {
    // 构建 history（仅 role + content）
    const history = messages.value
      .filter(m => m.role !== 'assistant' || m._sources)
      .slice(0, -1)  // 去掉刚加的 user
      .map(m => ({ role: m.role, content: m.content }))

    const resp = await api.chatWithRecording(
      props.recordingId,
      text,
      selectedModelId.value,
      history
    )

    const assistantMsg = {
      role: 'assistant',
      content: resp.reply,
      _sources: resp.sources || [],
    }
    messages.value.push(assistantMsg)
    if (resp.sources && resp.sources.length > 0) {
      expandedSources.value.add(messages.value.length - 1)
    }
  } catch (e) {
    messages.value.push({
      role: 'assistant',
      content: `请求失败：${e.message || e}`,
      _sources: [],
    })
  } finally {
    loading.value = false
    scrollToBottom()
    nextTick(() => inputRef.value?.focus())
  }
}

function seekTo(seg) {
  const time = seg.start != null ? seg.start : 0
  emit('seek-to-time', time)
}

function clearChat() {
  messages.value = []
  expandedSources.value.clear()
}

function getSources(index) {
  const msg = messages.value[index]
  return msg._sources || []
}

// Expose marked for template v-html
function renderMarkdown(text) {
  if (!text) return ''
  try {
    return marked.parse(text)
  } catch {
    return text
  }
}
</script>

<template>
  <div class="chat-panel">
    <!-- 工具栏 -->
    <div class="chat-toolbar" v-if="hasTranscript">
      <el-select
        v-model="selectedModelId"
        placeholder="默认模型"
        clearable
        size="small"
        style="width: 180px"
        :disabled="loading"
      >
        <el-option label="默认模型" :value="null" />
        <el-option
          v-for="m in modelOptions"
          :key="m.id"
          :label="m.name"
          :value="m.id"
        />
      </el-select>
      <el-button size="small" @click="clearChat" :disabled="loading || messages.length === 0">
        清空对话
      </el-button>
    </div>

    <!-- 无转录文本提示 -->
    <el-empty v-if="!hasTranscript" description="请先完成转录后再使用 AI 对话" />

    <!-- 消息列表 -->
    <div v-else class="chat-messages" ref="messageListRef">
      <!-- 空状态 -->
      <div v-if="messages.length === 0" class="chat-empty-state">
        <div class="chat-suggestions">
          <div class="suggestions-title">试试这些问题：</div>
          <el-button
            v-for="s in SUGGESTIONS"
            :key="s"
            size="small"
            @click="inputText = s; send()"
          >
            {{ s }}
          </el-button>
        </div>
      </div>

      <!-- 消息气泡 -->
      <div
        v-for="(msg, idx) in messages"
        :key="idx"
        :class="['chat-msg', msg.role]"
      >
        <div class="msg-bubble" :class="msg.role">
          <div v-if="msg.role === 'user'" class="msg-text">{{ msg.content }}</div>
          <div v-else class="msg-text msg-markdown" v-html="renderMarkdown(msg.content)" />

          <!-- 来源引用 -->
          <div v-if="getSources(idx).length > 0" class="msg-sources">
            <div class="sources-header">
              <span class="sources-label">来源引用 ({{ getSources(idx).length }}段)</span>
              <el-button
                text
                size="small"
                @click="
                  expandedSources.value.has(idx)
                    ? expandedSources.value.delete(idx)
                    : expandedSources.value.add(idx)
                "
              >
                {{ expandedSources.value.has(idx) ? '收起' : '展开' }}
              </el-button>
            </div>
            <el-collapse-transition>
              <div v-show="expandedSources.value.has(idx)" class="sources-list">
                <div
                  v-for="seg in getSources(idx)"
                  :key="seg.segment_index"
                  class="source-item"
                >
                  <span class="source-time" @click="seekTo(seg)" title="跳转到该时间点">
                    {{ formatTime(seg.start) }}
                  </span>
                  <span class="source-speaker">{{ seg.speaker || '未知' }}：</span>
                  <span class="source-text">{{ seg.text }}</span>
                </div>
              </div>
            </el-collapse-transition>
          </div>
        </div>
      </div>

      <!-- 加载中 -->
      <div v-if="loading" class="chat-msg assistant">
        <div class="msg-bubble assistant loading-bubble">
          <span class="loading-dots">思考中...</span>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="chat-input-area" v-if="hasTranscript">
      <el-input
        ref="inputRef"
        v-model="inputText"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 4 }"
        placeholder="输入问题，按 Enter 发送，Shift+Enter 换行"
        :disabled="loading"
        @keydown.enter.exact.prevent="send"
        @keydown.enter.shift="inputText += '\n'"
      />
      <el-button
        type="primary"
        :disabled="loading || !inputText.trim()"
        @click="send"
        class="send-btn"
      >
        发送
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 500px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
}

.chat-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #e4e7ed;
  background: #fafafa;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.chat-empty-state {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
}

.chat-suggestions {
  text-align: center;
}

.suggestions-title {
  color: #909399;
  margin-bottom: 12px;
  font-size: 14px;
}

.chat-msg {
  display: flex;
  margin-bottom: 12px;
}

.chat-msg.user {
  justify-content: flex-end;
}

.chat-msg.assistant {
  justify-content: flex-start;
}

.msg-bubble {
  max-width: 80%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
}

.msg-bubble.user {
  background: #409eff;
  color: #fff;
  border-bottom-right-radius: 4px;
}

.msg-bubble.assistant {
  background: #f4f4f5;
  color: #303133;
  border-bottom-left-radius: 4px;
}

.msg-text {
  word-break: break-word;
  white-space: pre-wrap;
}

.msg-markdown :deep(p) { margin: 0 0 8px; }
.msg-markdown :deep(p:last-child) { margin-bottom: 0; }
.msg-markdown :deep(ul),
.msg-markdown :deep(ol) { margin: 4px 0; padding-left: 20px; }
.msg-markdown :deep(code) {
  background: #e4e7ed;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 13px;
}
.msg-markdown :deep(pre) {
  background: #e4e7ed;
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
}
.msg-markdown :deep(pre code) { padding: 0; background: transparent; }

.msg-sources {
  margin-top: 8px;
  border-top: 1px solid #e4e7ed;
  padding-top: 6px;
}

.sources-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sources-label {
  font-size: 12px;
  color: #909399;
}

.sources-list {
  margin-top: 6px;
}

.source-item {
  font-size: 13px;
  padding: 4px 0;
  border-bottom: 1px solid #f0f0f0;
}

.source-item:last-child {
  border-bottom: none;
}

.source-time {
  color: #409eff;
  cursor: pointer;
  font-weight: 500;
  margin-right: 6px;
  font-variant-numeric: tabular-nums;
}

.source-time:hover {
  text-decoration: underline;
}

.source-speaker {
  color: #606266;
  font-weight: 500;
}

.source-text {
  color: #303133;
}

.loading-bubble {
  min-width: 80px;
  text-align: center;
}

.loading-dots {
  display: inline-flex;
  gap: 4px;
}

.chat-input-area {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid #e4e7ed;
  background: #fafafa;
}

.chat-input-area :deep(.el-textarea__inner) {
  resize: none;
}

.send-btn {
  align-self: flex-end;
}
</style>
