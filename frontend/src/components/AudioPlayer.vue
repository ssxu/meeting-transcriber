<script setup>
import { ref, onMounted, onUnmounted, watch, computed } from 'vue'
import { VideoPlay, VideoPause, Mute, Headset } from '@element-plus/icons-vue'

const props = defineProps({
  src: { type: String, required: true },
  duration: { type: Number, default: 0 },
  mediaType: { type: String, default: 'audio' }
})

const emit = defineEmits(['ready', 'seek', 'timeupdate'])

const audioRef = ref(null)
const progressRef = ref(null)
const isPlaying = ref(false)
const currentTime = ref(0)
const durationVal = ref(0)
const volume = ref(0.8)
const isMuted = ref(false)
const playbackRate = ref(1)
const showRateMenu = ref(false)

const isDragging = ref(false)

const rateOptions = [0.75, 1, 1.25, 1.5, 2]

const progressPercent = computed(() => {
  const d = durationVal.value || props.duration || 0
  if (!d) return 0
  return Math.min((currentTime.value / d) * 100, 100)
})

const bufferedPercent = computed(() => {
  const d = durationVal.value || props.duration || 0
  const audio = audioRef.value
  if (!d || !audio || !audio.buffered.length) return 0
  return Math.min((audio.buffered.end(audio.buffered.length - 1) / d) * 100, 100)
})

function formatTime(s) {
  if (!s || isNaN(s)) return '0:00'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.round(s % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

function togglePlay() {
  const audio = audioRef.value
  if (!audio) return
  if (isPlaying.value) {
    audio.pause()
  } else {
    audio.play()
  }
}

function onLoadedMetadata() {
  const audio = audioRef.value
  if (audio) {
    durationVal.value = audio.duration || props.duration || 0
    audio.volume = volume.value
  }
  emit('ready', audio)
}

function onTimeUpdate() {
  const audio = audioRef.value
  if (!audio) return
  if (!isDragging.value) {
    currentTime.value = audio.currentTime
  }
  emit('timeupdate', audio.currentTime)
}

function onDurationChange() {
  const audio = audioRef.value
  if (audio) durationVal.value = audio.duration
}

function onPlay() {
  isPlaying.value = true
}

function onPause() {
  isPlaying.value = false
}

function onEnded() {
  isPlaying.value = false
  currentTime.value = 0
}

function onSeeked() {
  const audio = audioRef.value
  if (audio) emit('seek', audio.currentTime)
}

function getProgressPercent(clientX) {
  const bar = progressRef.value
  if (!bar) return 0
  const rect = bar.getBoundingClientRect()
  return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
}

function onProgressMouseDown(e) {
  const audio = audioRef.value
  if (!audio) return
  const d = durationVal.value || props.duration || 0
  if (d <= 0) return
  isDragging.value = true
  const percent = getProgressPercent(e.clientX)
  currentTime.value = percent * d
  // register global listeners
  document.addEventListener('mousemove', onProgressMouseMove)
  document.addEventListener('mouseup', onProgressMouseUp)
  e.preventDefault()
}

function onProgressMouseMove(e) {
  if (!isDragging.value) return
  const d = durationVal.value || props.duration || 0
  if (d <= 0) return
  const percent = getProgressPercent(e.clientX)
  currentTime.value = percent * d
}

function onProgressMouseUp(e) {
  if (!isDragging.value) return
  isDragging.value = false
  document.removeEventListener('mousemove', onProgressMouseMove)
  document.removeEventListener('mouseup', onProgressMouseUp)
  const audio = audioRef.value
  if (!audio) return
  const d = durationVal.value || props.duration || 0
  if (d <= 0) return
  const percent = getProgressPercent(e.clientX)
  audio.currentTime = percent * d
  currentTime.value = audio.currentTime
}

function onVolumeChange() {
  const audio = audioRef.value
  if (!audio) return
  volume.value = audio.volume
  isMuted.value = audio.muted
}

function setVolume(val) {
  const audio = audioRef.value
  if (!audio) return
  audio.volume = val
  if (val > 0 && audio.muted) audio.muted = false
}

function toggleMute() {
  const audio = audioRef.value
  if (!audio) return
  audio.muted = !audio.muted
}

function setPlaybackRate(rate) {
  const audio = audioRef.value
  if (!audio) return
  audio.playbackRate = rate
  playbackRate.value = rate
  showRateMenu.value = false
}

function seekTo(seconds) {
  const audio = audioRef.value
  if (audio && seconds !== undefined && seconds !== null) {
    audio.currentTime = seconds
  }
}

function play() {
  const audio = audioRef.value
  if (audio) audio.play()
}

function pause() {
  const audio = audioRef.value
  if (audio) audio.pause()
}

defineExpose({ seekTo, play, pause })

function closeRateMenu(e) {
  if (showRateMenu.value && !e.target.closest('.rate-control')) {
    showRateMenu.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', closeRateMenu)
})

onUnmounted(() => {
  document.removeEventListener('click', closeRateMenu)
  document.removeEventListener('mousemove', onProgressMouseMove)
  document.removeEventListener('mouseup', onProgressMouseUp)
})

watch(() => props.src, () => {
  const audio = audioRef.value
  if (audio) {
    audio.load()
    currentTime.value = 0
    isPlaying.value = false
  }
})
</script>

<template>
  <div class="audio-player">
    <audio
      ref="audioRef"
      :src="src"
      preload="metadata"
      @loadedmetadata="onLoadedMetadata"
      @timeupdate="onTimeUpdate"
      @durationchange="onDurationChange"
      @play="onPlay"
      @pause="onPause"
      @ended="onEnded"
      @seeked="onSeeked"
      @volumechange="onVolumeChange"
    />

    <div class="player-controls">
      <!-- Play/Pause button -->
      <el-button
        type="primary"
        circle
        size="small"
        class="play-btn"
        @click="togglePlay"
      >
        <el-icon :size="18">
          <VideoPause v-if="isPlaying" />
          <VideoPlay v-else />
        </el-icon>
      </el-button>

      <!-- Current time -->
      <span class="time-label">{{ formatTime(currentTime) }}</span>

      <!-- Progress bar -->
      <div
        ref="progressRef"
        class="progress-bar"
        @mousedown="onProgressMouseDown"
      >
        <div class="progress-track">
          <div class="progress-buffered" :style="{ width: bufferedPercent + '%' }"></div>
          <div class="progress-played" :style="{ width: (isDragging ? (currentTime / (durationVal || props.duration || 1)) * 100 : progressPercent) + '%' }">
            <div class="progress-thumb"></div>
          </div>
        </div>
      </div>

      <!-- Duration -->
      <span class="time-label">{{ formatTime(durationVal || duration) }}</span>

      <!-- Volume control -->
      <div class="volume-control">
        <el-icon class="volume-icon" :size="16" @click="toggleMute">
          <Mute v-if="isMuted || volume === 0" />
          <Headset v-else />
        </el-icon>
        <el-slider
          v-model="volume"
          :min="0"
          :max="1"
          :step="0.05"
          :show-tooltip="false"
          class="volume-slider"
          @input="setVolume"
        />
      </div>

      <!-- Playback rate -->
      <div class="rate-control">
        <el-button
          size="small"
          text
          class="rate-btn"
          @click="showRateMenu = !showRateMenu"
        >
          {{ playbackRate }}x
        </el-button>
        <transition name="el-zoom-in-top">
          <div v-show="showRateMenu" class="rate-menu">
            <div
              v-for="rate in rateOptions"
              :key="rate"
              :class="['rate-item', { active: rate === playbackRate }]"
              @click="setPlaybackRate(rate)"
            >
              {{ rate }}x
            </div>
          </div>
        </transition>
      </div>
    </div>
  </div>
</template>

<style scoped>
.audio-player {
  width: 100%;
  background: var(--el-fill-color-light, #f5f7fa);
  border-radius: 8px;
  padding: 8px 12px;
}

.player-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
}

.play-btn {
  flex-shrink: 0;
}

.time-label {
  font-size: 12px;
  color: var(--el-text-color-regular, #606266);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
  min-width: 38px;
  text-align: center;
}

.progress-bar {
  flex: 1;
  min-width: 80px;
  height: 100%;
  display: flex;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.progress-track {
  position: relative;
  width: 100%;
  height: 4px;
  background: var(--el-border-color, #dcdfe6);
  border-radius: 2px;
  transition: height 0.2s;
}

.progress-bar:hover .progress-track {
  height: 6px;
}

.progress-buffered {
  position: absolute;
  height: 100%;
  background: var(--el-border-color-darker, #c0c4cc);
  border-radius: 2px;
  opacity: 0.5;
}

.progress-played {
  position: absolute;
  height: 100%;
  background: var(--el-color-primary, #409eff);
  border-radius: 2px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

.progress-thumb {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--el-color-primary, #409eff);
  border: 2px solid #fff;
  box-shadow: 0 0 4px rgba(0, 0, 0, 0.15);
  opacity: 0;
  transition: opacity 0.2s;
  margin-right: -6px;
}

.progress-bar:hover .progress-thumb,
.progress-bar:active .progress-thumb {
  opacity: 1;
}

.volume-control {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.volume-icon {
  cursor: pointer;
  color: var(--el-text-color-regular, #606266);
}

.volume-slider {
  width: 70px;
}

.rate-control {
  position: relative;
  flex-shrink: 0;
}

.rate-btn {
  font-size: 12px;
  font-weight: 600;
  padding: 4px 6px;
  min-height: auto;
}

.rate-menu {
  position: absolute;
  bottom: 100%;
  right: 0;
  margin-bottom: 4px;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-light, #e4e7ed);
  border-radius: 4px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
  z-index: 10;
  padding: 4px 0;
  min-width: 56px;
}

.rate-item {
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
  text-align: center;
  color: var(--el-text-color-regular, #606266);
  transition: background 0.2s;
}

.rate-item:hover {
  background: var(--el-fill-color, #f0f2f5);
}

.rate-item.active {
  color: var(--el-color-primary, #409eff);
  font-weight: 600;
}

/* Override el-slider to make it compact */
.volume-slider :deep(.el-slider__runway) {
  margin: 0;
  height: 4px;
}

.volume-slider :deep(.el-slider__bar) {
  height: 4px;
}

.volume-slider :deep(.el-slider__button) {
  width: 12px;
  height: 12px;
  border: 2px solid var(--el-color-primary, #409eff);
}

.volume-slider :deep(.el-slider__button-wrapper) {
  top: -5px;
}
</style>
