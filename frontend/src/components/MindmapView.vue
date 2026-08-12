<script setup>
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import MindMap from 'simple-mind-map'
import 'simple-mind-map/dist/simpleMindMap.esm.css'

const props = defineProps({
  markdown: { type: String, default: '' }
})

const containerRef = ref(null)
let mindMapInstance = null

/**
 * Parse markdown list text into a nested tree structure for simple-mind-map.
 * - Lines starting with "- " are list items
 * - Nesting is determined by leading spaces (2 spaces per level)
 * - Returns { data: { text }, children: [...] }
 */
function parseMarkdownToTree(markdown) {
  if (!markdown || !markdown.trim()) return null

  const lines = markdown.split('\n').filter(line => line.trim().startsWith('-'))

  if (lines.length === 0) return null

  // Parse each line into { level, text }
  const items = lines.map(line => {
    // Count leading spaces
    const match = line.match(/^(\s*)-\s+(.*)$/)
    if (!match) return null
    const indent = match[1].length
    const level = Math.floor(indent / 2) // 2 spaces per level
    const text = match[2].trim()
    return { level, text }
  }).filter(Boolean)

  if (items.length === 0) return null

  // Build nested tree using a stack
  const root = { data: { text: items[0].text }, children: [] }
  const stack = [{ node: root, level: items[0].level }]

  for (let i = 1; i < items.length; i++) {
    const { level, text } = items[i]
    const node = { data: { text }, children: [] }

    // Pop stack until we find a parent with lower level
    while (stack.length > 1 && stack[stack.length - 1].level >= level) {
      stack.pop()
    }

    // Add as child of the current stack top
    stack[stack.length - 1].node.children.push(node)
    stack.push({ node, level })
  }

  return root
}

function renderMap() {
  if (!containerRef.value) return

  // Destroy existing instance if any
  if (mindMapInstance) {
    mindMapInstance.destroy()
    mindMapInstance = null
  }

  if (!props.markdown || !props.markdown.trim()) return

  const treeData = parseMarkdownToTree(props.markdown)
  if (!treeData) return

  mindMapInstance = new MindMap({
    el: containerRef.value,
    data: treeData,
    layout: 'logicalStructure',
    theme: 'classic',
    nodeTextEditZIndex: 1000,
    expandBtnSize: 20,
    // Visual tweaks
    readonly: true,
    expand: true,
    scale: true,
    // Theme customization for green primary color
    themeConfig: {
      // Override the classic theme's primary color to green
      node: {
        fillColor: '#e8f5e9',
        borderColor: '#4caf50',
        activeBorderColor: '#66bb6a',
        color: '#2e7d32',
        fontSize: 14,
        activeFillColor: '#c8e6c9',
      },
      line: {
        color: '#81c784',
        width: 2,
      },
      generalization: {
        borderColor: '#4caf50',
        fillColor: '#e8f5e9',
        color: '#2e7d32',
      },
    },
  })
}

watch(() => props.markdown, () => {
  nextTick(renderMap)
})

onMounted(() => {
  nextTick(renderMap)
})

onBeforeUnmount(() => {
  if (mindMapInstance) {
    mindMapInstance.destroy()
    mindMapInstance = null
  }
})
</script>

<template>
  <div class="mindmap-container">
    <div ref="containerRef" class="mindmap-canvas"></div>
    <div v-if="!markdown" class="mindmap-empty">
      暂无思维导图
    </div>
  </div>
</template>

<style scoped>
.mindmap-container {
  width: 100%;
  height: 500px;
  overflow: hidden;
  position: relative;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background: #fff;
}

.mindmap-canvas {
  width: 100%;
  height: 100%;
}

.mindmap-canvas :deep(.smm-node) {
  /* Ensure nodes render crisply */
  user-select: none;
}

.mindmap-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #aaa;
  font-size: 14px;
}
</style>
