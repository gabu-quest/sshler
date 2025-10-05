<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { oneDark } from '@codemirror/theme-one-dark'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { json } from '@codemirror/lang-json'
import { markdown } from '@codemirror/lang-markdown'
import { xml } from '@codemirror/lang-xml'

interface Props {
  modelValue: string
  language?: string
  theme?: 'light' | 'dark'
  readonly?: boolean
  lineNumbers?: boolean
  wordWrap?: boolean
  placeholder?: string
}

interface Emits {
  (e: 'update:modelValue', value: string): void
  (e: 'change', value: string): void
}

const props = withDefaults(defineProps<Props>(), {
  language: 'text',
  theme: 'dark',
  readonly: false,
  lineNumbers: true,
  wordWrap: true,
  placeholder: ''
})

const emit = defineEmits<Emits>()

const editorRef = ref<HTMLDivElement>()
let editorView: EditorView | null = null

const getLanguageExtension = (lang: string) => {
  switch (lang.toLowerCase()) {
    case 'javascript':
    case 'js':
    case 'jsx':
    case 'ts':
    case 'tsx':
      return javascript()
    case 'python':
    case 'py':
      return python()
    case 'html':
    case 'htm':
      return html()
    case 'css':
    case 'scss':
    case 'sass':
      return css()
    case 'json':
      return json()
    case 'markdown':
    case 'md':
      return markdown()
    case 'xml':
    case 'svg':
      return xml()
    default:
      return []
  }
}

const createEditor = () => {
  if (!editorRef.value) return

  const extensions = [
    basicSetup,
    getLanguageExtension(props.language),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        const value = update.state.doc.toString()
        emit('update:modelValue', value)
        emit('change', value)
      }
    }),
    EditorView.lineWrapping,
    EditorState.readOnly.of(props.readonly)
  ]

  if (props.theme === 'dark') {
    extensions.push(oneDark)
  }

  const state = EditorState.create({
    doc: props.modelValue,
    extensions
  })

  editorView = new EditorView({
    state,
    parent: editorRef.value
  })
}

const updateContent = (newValue: string) => {
  if (!editorView) return
  
  const currentValue = editorView.state.doc.toString()
  if (currentValue !== newValue) {
    editorView.dispatch({
      changes: {
        from: 0,
        to: editorView.state.doc.length,
        insert: newValue
      }
    })
  }
}

watch(() => props.modelValue, updateContent)
watch(() => props.language, () => {
  if (editorView) {
    editorView.destroy()
    createEditor()
  }
})
watch(() => props.theme, () => {
  if (editorView) {
    editorView.destroy()
    createEditor()
  }
})

onMounted(async () => {
  await nextTick()
  createEditor()
})

onUnmounted(() => {
  if (editorView) {
    editorView.destroy()
  }
})

defineExpose({
  focus: () => editorView?.focus(),
  getSelection: () => editorView?.state.selection,
  insertText: (text: string) => {
    if (!editorView) return
    const selection = editorView.state.selection.main
    editorView.dispatch({
      changes: {
        from: selection.from,
        to: selection.to,
        insert: text
      }
    })
  }
})
</script>

<template>
  <div ref="editorRef" class="code-editor" />
</template>

<style scoped>
.code-editor {
  border: 1px solid var(--stroke);
  border-radius: 8px;
  overflow: hidden;
}

.code-editor :deep(.cm-editor) {
  font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace;
  font-size: 14px;
  line-height: 1.5;
}

.code-editor :deep(.cm-focused) {
  outline: none;
}

.code-editor :deep(.cm-scroller) {
  font-family: inherit;
  overflow: auto !important;
}

.code-editor :deep(.cm-editor) {
  height: 100%;
}

.code-editor :deep(.cm-content) {
  min-height: 100%;
}
</style>
