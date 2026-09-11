<script setup lang="ts">
/**
 * 资源管理 · 新建/编辑弹窗（F2.2，原 static/js/resource.js resAdminNew/resAdminEdit/resAdminSave）
 *
 * 表单按 entry_schema 动态生成：int → 数字输入；list/dict → JSON textarea；
 * 长文本字段 → 多行；其余 → 单行。空值不提交（必填校验交给后端，返回错误列表）。
 */
import { computed, ref, watch } from 'vue'
import { ElButton, ElDialog, ElInput } from 'element-plus'
import { ApiError } from '@/api'
import { toast } from '@/components'
import {
  createResourceItem,
  fetchResourceItem,
  updateResourceItem,
  type EntrySchemaField,
} from '@/api/resources'

const props = defineProps<{
  modelValue: boolean
  rtype: string
  schema: EntrySchemaField[]
  itemId: string | null
}>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean]; saved: [] }>()

/** 长文本字段用 textarea（原 RES_ADMIN_LONG_FIELDS） */
const LONG_FIELDS = [
  'content',
  'standard_sql',
  'generated_sql',
  'correct_sql',
  'error_detail',
  'description',
  'pattern_regex',
  'trigger_words',
  'doc_text',
]

const formValues = ref<Record<string, string>>({})
const loading = ref(false)
const saving = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const title = computed(() =>
  props.itemId === null ? `新建 ${props.rtype}` : `编辑 ${props.rtype} #${props.itemId}`,
)

function initForm(item: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const spec of props.schema) {
    const f = spec.field
    let v = item[f]
    if (v === null || v === undefined) v = f === 'enabled' ? '1' : ''
    if (spec.type === 'list' || spec.type === 'dict') {
      out[f] = v === '' ? '' : JSON.stringify(v, null, 1)
    } else {
      out[f] = String(v)
    }
  }
  return out
}

function isLong(spec: EntrySchemaField): boolean {
  if (spec.type === 'list' || spec.type === 'dict') return true
  if (LONG_FIELDS.includes(spec.field)) return true
  return (formValues.value[spec.field] ?? '').length > 60
}

function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    const errors = e.payload && Array.isArray(e.payload.errors) ? (e.payload.errors as string[]) : null
    if (errors && errors.length) return errors.join('；')
    return e.message
  }
  return e instanceof Error ? e.message : String(e)
}

watch(
  () => [props.modelValue, props.itemId, props.rtype],
  async ([open, itemId, rtype]) => {
    if (!open) return
    const r = rtype as string
    if (itemId === null) {
      formValues.value = initForm({})
      return
    }
    loading.value = true
    try {
      const res = await fetchResourceItem(r, itemId as string)
      formValues.value = initForm(res.item ?? {})
    } catch (e) {
      toast.danger({ title: '加载失败', desc: describeError(e) })
      visible.value = false
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

async function save() {
  const payload: Record<string, unknown> = {}
  for (const spec of props.schema) {
    const f = spec.field
    const raw = (formValues.value[f] ?? '').trim()
    if (raw === '') continue
    if (spec.type === 'int') {
      payload[f] = parseInt(raw, 10)
    } else if (spec.type === 'list' || spec.type === 'dict') {
      try {
        payload[f] = JSON.parse(raw)
      } catch {
        toast.danger({ title: `字段 ${f} 不是合法 JSON` })
        return
      }
    } else {
      payload[f] = raw
    }
  }

  saving.value = true
  try {
    await (props.itemId === null
      ? createResourceItem(props.rtype, payload)
      : updateResourceItem(props.rtype, props.itemId, payload))
    toast.success(props.itemId === null ? '已新建' : '已保存')
    visible.value = false
    emit('saved')
  } catch (e) {
    toast.danger({ title: '保存失败', desc: describeError(e) })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ElDialog v-model="visible" :title="title" width="640px" destroy-on-close>
    <div v-loading="loading" class="edit-form">
      <template v-if="props.schema.length">
        <div v-for="spec in props.schema" :key="spec.field" class="form-group">
          <label class="form-label">
            {{ spec.field }}<span v-if="spec.required" class="req">*</span>
            <span v-if="spec.type === 'list' || spec.type === 'dict'" class="type-hint">
              （JSON {{ spec.type === 'list' ? '数组' : '对象' }}）
            </span>
          </label>
          <ElInput
            v-if="spec.type === 'int'"
            v-model="formValues[spec.field]"
            type="number"
          />
          <ElInput
            v-else-if="isLong(spec)"
            v-model="formValues[spec.field]"
            type="textarea"
            :rows="spec.type === 'list' || spec.type === 'dict' ? 3 : 4"
          />
          <ElInput v-else v-model="formValues[spec.field]" />
        </div>
      </template>
      <p v-else class="empty">该资源未提供录入模板</p>
    </div>
    <template #footer>
      <ElButton @click="visible = false">取消</ElButton>
      <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.edit-form {
  min-height: 120px;
  max-height: 60vh;
  overflow-y: auto;
}
.form-group {
  margin-bottom: var(--sp-3);
}
.form-label {
  display: block;
  margin-bottom: 6px;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.req {
  color: var(--danger);
  margin-left: 2px;
}
.type-hint {
  color: var(--text-3);
  font-weight: 400;
}
.empty {
  color: var(--text-3);
  font-size: var(--fs-body-sm);
}
</style>
