<script setup lang="ts">
/**
 * 问答对编辑弹窗（F1.1）
 *
 * - 打开时按 id 拉取最新详情（GET /api/qa-detail，与旧实现一致以详情为准）
 * - 表单：业务问题 / 标准 SQL / 难度；必填校验走 DsFieldError（§4.4.1，写「问题 + 怎么改」）
 * - 保存中锁定全部字段（§4.4.8）；有未保存修改时关闭 → L1 确认（放弃修改？）
 */
import { computed, ref, watch } from 'vue'
import { ElButton, ElDialog, ElInput, ElOption, ElSelect } from 'element-plus'
import { confirm, DsFieldError, toast } from '@/components'
import {
  fetchQaDetail,
  QA_DIFFICULTIES,
  updateQaPair,
  type QaDifficulty,
  type QaPairDetail,
} from '@/api/qa'

const props = defineProps<{
  modelValue: boolean
  pairId: number | null
}>()

const emit = defineEmits<{
  'update:modelValue': [visible: boolean]
  saved: []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const original = ref<QaPairDetail | null>(null)
const form = ref<{ question: string; standard_sql: string; difficulty: QaDifficulty }>({
  question: '',
  standard_sql: '',
  difficulty: '进阶题',
})
const errors = ref<{ question?: string; sql?: string }>({})

watch(
  () => [props.modelValue, props.pairId] as const,
  ([open, id]) => {
    if (open && id != null) void loadDetail(id)
  },
)

async function loadDetail(id: number) {
  loading.value = true
  loadError.value = ''
  try {
    const res = await fetchQaDetail(id)
    if (!res.success) throw new Error('详情加载失败')
    original.value = res.item
    form.value = {
      question: res.item.question ?? '',
      standard_sql: res.item.standard_sql ?? '',
      difficulty: (res.item.difficulty || '进阶题') as QaDifficulty,
    }
    errors.value = {}
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function reload() {
  if (props.pairId != null) void loadDetail(props.pairId)
}

const dirty = computed(() => {
  const o = original.value
  if (!o) return false
  return (
    form.value.question !== (o.question ?? '') ||
    form.value.standard_sql !== (o.standard_sql ?? '') ||
    form.value.difficulty !== (o.difficulty || '进阶题')
  )
})

function validate(): boolean {
  errors.value = {}
  if (!form.value.question.trim()) errors.value.question = '业务问题不能为空，请补全后再保存'
  if (!form.value.standard_sql.trim()) errors.value.sql = '标准 SQL 不能为空，请补全后再保存'
  return !errors.value.question && !errors.value.sql
}

async function save() {
  if (props.pairId == null || !validate()) return
  saving.value = true
  try {
    await updateQaPair({
      id: props.pairId,
      question: form.value.question.trim(),
      standard_sql: form.value.standard_sql.trim(),
      difficulty: form.value.difficulty,
    })
    toast.success('修改已保存')
    emit('saved')
    visible.value = false
  } catch (e) {
    toast.danger({ title: '保存失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    saving.value = false
  }
}

/** 关闭请求（取消按钮 / 右上 ✕ / Esc / 遮罩）：保存中忽略；有未保存修改 → L1 确认 */
async function requestClose(done?: () => void) {
  if (saving.value) return
  const close = done ?? (() => (visible.value = false))
  if (!dirty.value) {
    close()
    return
  }
  const ok = await confirm.l1({
    title: '放弃修改？',
    message: '表单有未保存的修改，关闭后将丢失。',
    confirmText: '放弃修改',
    cancelText: '继续编辑',
    danger: true,
  })
  if (ok) close()
}
</script>

<template>
  <ElDialog
    v-model="visible"
    title="编辑问答对"
    width="640px"
    :close-on-click-modal="false"
    :before-close="requestClose"
  >
    <div v-if="loading" class="qa-edit__state">
      <span class="ds-spinner" />
    </div>
    <div v-else-if="loadError" class="qa-edit__state qa-edit__state--error">
      <p>详情加载失败：{{ loadError }}</p>
      <ElButton size="small" @click="reload">重试</ElButton>
    </div>
    <form v-else class="qa-edit__form" @submit.prevent="save">
      <div class="qa-edit__field">
        <label class="qa-edit__label" for="qa-edit-question">
          业务问题 <i class="qa-edit__req">*</i>
        </label>
        <ElInput
          id="qa-edit-question"
          v-model="form.question"
          type="textarea"
          :rows="3"
          :disabled="saving"
          placeholder="请输入业务问题"
        />
        <DsFieldError :message="errors.question" />
      </div>
      <div class="qa-edit__field">
        <label class="qa-edit__label" for="qa-edit-sql">
          标准 SQL <i class="qa-edit__req">*</i>
        </label>
        <ElInput
          id="qa-edit-sql"
          v-model="form.standard_sql"
          type="textarea"
          :rows="7"
          :disabled="saving"
          class="qa-edit__sql"
          placeholder="SELECT ..."
        />
        <DsFieldError :message="errors.sql" />
      </div>
      <div class="qa-edit__field">
        <label class="qa-edit__label">难度</label>
        <ElSelect v-model="form.difficulty" :disabled="saving" style="width: 160px">
          <ElOption v-for="d in QA_DIFFICULTIES" :key="d" :value="d" :label="d" />
        </ElSelect>
      </div>
    </form>
    <template #footer>
      <ElButton :disabled="saving" @click="requestClose()">取消</ElButton>
      <ElButton type="primary" :loading="saving" @click="save">保存</ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.qa-edit__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-8) 0;
  color: var(--text-2);
  font-size: var(--fs-body-sm);
}
.qa-edit__form {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}
.qa-edit__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.qa-edit__label {
  font-size: var(--fs-body-sm);
  font-weight: 500;
  color: var(--text-2);
}
.qa-edit__req {
  color: var(--danger);
  font-style: normal;
}
.qa-edit__sql :deep(textarea) {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
}
</style>
