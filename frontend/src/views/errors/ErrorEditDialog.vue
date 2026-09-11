<script setup lang="ts">
/**
 * 错题编辑弹窗（F2.5，对等旧实现 openEditModal / saveErrorEdit）
 *
 * 编辑范围（与 /api/error-update 核心字段对齐）：业务问题 / 错误 SQL / 修正 SQL / 错误类型。
 * 旧实现的「错误分析 / SQL 技巧」拆解编辑未列入本次验收（见交接日志「遗留」），
 * 详情展开内仍完整展示 error_detail 原文，不丢数据。
 */
import { computed, ref, watch } from 'vue'
import { ElButton, ElDialog, ElInput, ElOption, ElSelect } from 'element-plus'
import { toast } from '@/components'
import { ERROR_TYPES, fetchErrorDetail, updateError } from '@/api/errors'

const props = defineProps<{ visible: boolean; errorId: number | null }>()
const emit = defineEmits<{ 'update:visible': [v: boolean]; saved: [] }>()

const loading = ref(false)
const saving = ref(false)
const form = ref({
  business_question: '',
  generated_sql: '',
  correct_sql: '',
  error_type: '其他',
})

const modelVisible = computed({
  get: () => props.visible,
  set: (v: boolean) => emit('update:visible', v),
})

watch(
  () => [props.visible, props.errorId] as const,
  async ([visible, id]) => {
    if (!visible || id === null) return
    loading.value = true
    form.value = { business_question: '', generated_sql: '', correct_sql: '', error_type: '其他' }
    try {
      const res = await fetchErrorDetail(id)
      form.value = {
        business_question: res.item.business_question ?? '',
        generated_sql: res.item.generated_sql ?? '',
        correct_sql: res.item.correct_sql ?? '',
        error_type: res.item.error_type ?? '其他',
      }
    } catch (e) {
      toast.danger({ title: '加载错题详情失败', desc: e instanceof Error ? e.message : String(e) })
      modelVisible.value = false
    } finally {
      loading.value = false
    }
  },
)

async function onSave() {
  if (props.errorId === null) return
  if (!form.value.business_question.trim() || !form.value.generated_sql.trim()) {
    toast.warning('业务问题和错误 SQL 不能为空')
    return
  }
  saving.value = true
  try {
    await updateError({
      id: props.errorId,
      business_question: form.value.business_question.trim(),
      generated_sql: form.value.generated_sql.trim(),
      correct_sql: form.value.correct_sql.trim() || null,
      error_type: form.value.error_type,
    })
    toast.success('修改已保存')
    modelVisible.value = false
    emit('saved')
  } catch (e) {
    toast.danger({ title: '保存失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ElDialog
    v-model="modelVisible"
    title="编辑错题"
    width="640px"
    class="err-edit"
    :close-on-click-modal="false"
  >
    <div v-loading="loading" class="err-edit__body">
      <div class="err-edit__field">
        <label class="err-edit__label">业务问题</label>
        <ElInput
          v-model="form.business_question"
          type="textarea"
          :rows="2"
          placeholder="如：查询杭州各供电单位的用电量"
        />
      </div>
      <div class="err-edit__field">
        <label class="err-edit__label">错误 SQL</label>
        <ElInput
          v-model="form.generated_sql"
          type="textarea"
          :rows="5"
          class="err-edit__sql"
          placeholder="SELECT ..."
        />
      </div>
      <div class="err-edit__field">
        <label class="err-edit__label">修正 SQL（可为空）</label>
        <ElInput
          v-model="form.correct_sql"
          type="textarea"
          :rows="5"
          class="err-edit__sql"
          placeholder="SELECT ..."
        />
      </div>
      <div class="err-edit__field">
        <label class="err-edit__label">错误类型</label>
        <ElSelect v-model="form.error_type" style="width: 100%">
          <ElOption v-for="t in ERROR_TYPES" :key="t" :value="t" :label="t" />
        </ElSelect>
      </div>
    </div>

    <template #footer>
      <ElButton @click="modelVisible = false">取消</ElButton>
      <ElButton type="primary" :loading="saving" @click="onSave">保存</ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.err-edit__body {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  min-height: 120px;
}
.err-edit__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.err-edit__label {
  font-size: var(--fs-caption);
  font-weight: var(--fw-h3);
  color: var(--text-2);
}
.err-edit__sql :deep(.el-textarea__inner) {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
}
</style>
