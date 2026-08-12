# Task: Replace MindmapView with simple-mind-map

**Date:** 2026-07-11
**Requirement:** #7 — Replace markmap-lib/markmap-view with simple-mind-map

## Objective
Replace the static markmap-lib/markmap-view SVG rendering in MindmapView.vue with the interactive simple-mind-map library.

## Changes Made

### 1. `frontend/package.json`
- **Removed:** `markmap-lib: ^0.18.0`, `markmap-view: ^0.18.0`
- **Added:** `simple-mind-map: ^0.6.0`

### 2. `frontend/src/components/MindmapView.vue` — Full rewrite

**Before:** Used `Transformer` from markmap-lib and `Markmap` from markmap-view to render markdown as a static SVG.

**After:** Uses `MindMap` from simple-mind-map to render an interactive mind map in a div container.

#### Key implementation details:

- **Markdown-to-tree parser** (`parseMarkdownToTree` function):
  - Parses `- ` list items from the markdown string
  - Determines nesting level by leading spaces (2 spaces = 1 level)
  - Builds nested `{ data: { text }, children: [] }` structure using a stack-based algorithm
  - Returns `null` for empty/invalid input

- **Rendering:**
  - Container div with `ref="containerRef"` (100% width, 500px height)
  - `logicalStructure` layout (left-to-right tree)
  - `classic` theme with green primary color customization (fillColor: #e8f5e9, borderColor: #4caf50, line color: #81c784)
  - `readonly: true` to prevent accidental editing
  - Expand buttons enabled (20px size)

- **Lifecycle:**
  - Initialize on `onMounted` via `nextTick(renderMap)`
  - Destroy and re-create on `markdown` prop change via `watch`
  - Cleanup on `onBeforeUnmount` by calling `mindMapInstance.destroy()`

- **Empty state:** Shows "暂无思维导图" placeholder when no markdown is provided

- **Public API unchanged:** Component still accepts a `markdown` string prop, so the parent `RecordingDetail.vue` requires no changes.

## Verification
- The parent component (`RecordingDetail.vue`) passes `rec.mindmap_text` as the `markdown` prop — interface is unchanged.
- The `simple-mind-map/dist/style.css` is imported for proper styling.
- Z-index for text editing set to 1000 to avoid overlap issues.

## To Run
After pulling these changes, run `npm install` in the `frontend/` directory to install `simple-mind-map` and remove the old markmap packages.
