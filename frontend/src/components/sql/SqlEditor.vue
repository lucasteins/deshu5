<script setup lang="ts">
/**
 * SQL 编辑器 SqlEditor（SQL Lab，F2.6）—— Monaco 封装。
 *
 * - language=sql；Ctrl/Cmd+Enter 触发 `run`；Tab=2 空格；自动布局
 * - Schema 感知补全：关键字 + 表名（表注释作说明）+ `表.字段`（元数据由 props.schema 传入）
 * - 主题跟随全局 data-theme（app store）；v-model 双向绑定编辑器文本
 *
 * 用法：
 *   <SqlEditor v-model="sql" :schema="schemaTables" @run="runSql" ref="editorRef" />
 *   editorRef.value?.insertText('table_name')
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/editor/editor.worker?worker'
import { useAppStore } from '@/stores/app'

self.MonacoEnvironment = {
  getWorker: () => new editorWorker(),
}

export interface SqlSchemaColumn {
  name: string
  comment?: string
}

export interface SqlSchemaTable {
  name: string
  comment?: string
  columns?: SqlSchemaColumn[]
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    /** Schema 元数据（表/字段），用于自动补全 */
    schema?: SqlSchemaTable[]
    placeholder?: string
  }>(),
  { schema: () => [], placeholder: '' },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
  run: []
}>()

const app = useAppStore()
const containerRef = ref<HTMLElement>()
let editor: monaco.editor.IStandaloneCodeEditor | null = null
let completionDisposable: monaco.IDisposable | null = null
/** 内部编辑回写 v-model 期间置位，阻断 watch 回灌造成的光标跳动 */
let syncingFromEditor = false

const SQL_KEYWORDS = [
  'SELECT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'LIMIT', 'JOIN', 'LEFT JOIN',
  'INNER JOIN', 'ON', 'AS', 'AND', 'OR', 'NOT', 'IN', 'LIKE', 'BETWEEN', 'IS NULL',
  'IS NOT NULL', 'DISTINCT', 'COUNT', 'SUM', 'AVG', 'MAX', 'MIN', 'HAVING', 'UNION',
  'UNION ALL', 'CASE WHEN', 'THEN', 'ELSE', 'END', 'WITH', 'DESC', 'ASC', 'DATE_FORMAT',
  'IFNULL', 'COALESCE', 'ROUND', 'CONCAT', 'SUBSTRING', 'NOW', 'CURDATE',
]

const isDark = computed(() => app.theme === 'dark')

onMounted(() => {
  if (!containerRef.value) return
  editor = monaco.editor.create(containerRef.value, {
    value: props.modelValue,
    language: 'sql',
    theme: isDark.value ? 'vs-dark' : 'vs',
    automaticLayout: true,
    minimap: { enabled: false },
    fontSize: 13,
    lineNumbers: 'on',
    tabSize: 2,
    insertSpaces: true,
    scrollBeyondLastLine: false,
    wordWrap: 'on',
    renderWhitespace: 'none',
    suggestOnTriggerCharacters: true,
  })

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => emit('run'))

  editor.onDidChangeModelContent(() => {
    if (!editor) return
    syncingFromEditor = true
    emit('update:modelValue', editor.getValue())
    syncingFromEditor = false
  })

  completionDisposable = monaco.languages.registerCompletionItemProvider('sql', {
    triggerCharacters: ['.', ' '],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position)
      const range = new monaco.Range(
        position.lineNumber, word.startColumn, position.lineNumber, word.endColumn,
      )
      const lineBefore = model.getLineContent(position.lineNumber).slice(0, position.column - 1)
      const dotMatch = lineBefore.match(/([A-Za-z_][\w]*)\.$/)
      const suggestions: monaco.languages.CompletionItem[] = []

      if (dotMatch) {
        // `表.` → 该表字段补全
        const table = props.schema.find(
          (t) => t.name.toLowerCase() === dotMatch[1].toLowerCase(),
        )
        for (const col of table?.columns ?? []) {
          suggestions.push({
            label: col.name,
            kind: monaco.languages.CompletionItemKind.Field,
            insertText: col.name,
            documentation: col.comment || undefined,
            range,
          })
        }
        return { suggestions }
      }

      for (const kw of SQL_KEYWORDS) {
        suggestions.push({
          label: kw,
          kind: monaco.languages.CompletionItemKind.Keyword,
          insertText: kw,
          range,
        })
      }
      for (const t of props.schema) {
        suggestions.push({
          label: t.name,
          kind: monaco.languages.CompletionItemKind.Class,
          insertText: t.name,
          detail: t.comment || undefined,
          documentation: t.comment || undefined,
          range,
        })
      }
      return { suggestions }
    },
  })
})

watch(isDark, (dark) => {
  monaco.editor.setTheme(dark ? 'vs-dark' : 'vs')
})

watch(
  () => props.modelValue,
  (value) => {
    if (!editor || syncingFromEditor) return
    if (editor.getValue() !== value) editor.setValue(value)
  },
)

onBeforeUnmount(() => {
  completionDisposable?.dispose()
  editor?.dispose()
})

/** 在光标处插入文本（目录点击表名/字段名时调用），并保持焦点 */
function insertText(text: string) {
  if (!editor) return
  const selection = editor.getSelection()
  if (selection) {
    editor.executeEdits('sqllab-insert', [{ range: selection, text }])
  } else {
    editor.setValue(editor.getValue() + text)
  }
  editor.focus()
}

function focus() {
  editor?.focus()
}

defineExpose({ insertText, focus })
</script>

<template>
  <div ref="containerRef" class="sql-editor" />
</template>

<style scoped>
.sql-editor {
  width: 100%;
  height: 100%;
  min-height: 160px;
  border: 1px solid var(--line, #d8dee5);
  border-radius: 8px;
  overflow: hidden;
}
</style>
