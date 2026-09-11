<script lang="ts">
/**
 * 三级确认对话框（设计稿 §4.4.1）
 * L1 420px 常规确认 / L2 520px 影响范围确认（勾选闸门）/ L3 560px 危险二次确认（输入对象名 + 备份入口）
 * 共同约束：默认焦点在「取消」；危险操作不绑定 Enter 直达确认；Esc 关闭（Esc 不构成正向确认）。
 *
 * 由 confirm.ts 命令式挂载，业务侧不直接使用本组件。
 */
export interface ConfirmDialogPayload {
  level: 1 | 2 | 3
  title: string
  message?: string
  confirmText: string
  cancelText: string
  /** 危险语义：确认按钮红色实心（L3 恒真） */
  danger: boolean
  /** L2：具体影响清单 */
  impacts?: string[]
  /** L2：勾选文案 */
  impactCheckText?: string
  /** L3：需手动输入的确认对象名称 */
  objectName?: string
  /** L3：「先导出备份」入口 */
  backupText?: string
  backupHandler?: () => void | Promise<void>
}
</script>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElCheckbox, ElDialog, ElInput } from 'element-plus'

const props = defineProps<{ payload: ConfirmDialogPayload }>()
const emit = defineEmits<{ resolve: [confirmed: boolean] }>()

const open = ref(true)
let settled = false
let result = false

const cancelRef = ref<InstanceType<typeof ElButton> | null>(null)

/* L2 勾选闸门 / L3 名称输入闸门 */
const checked = ref(false)
const typed = ref('')

/** L1 420 / L2 520 / L3 560（§4.4.1） */
const width = computed(() =>
  props.payload.level === 1 ? 420 : props.payload.level === 2 ? 520 : 560,
)

const nameMatched = computed(
  () => props.payload.level !== 3 || typed.value.trim() === props.payload.objectName,
)

const canConfirm = computed(() => {
  if (props.payload.level === 2) return checked.value
  if (props.payload.level === 3) return nameMatched.value
  return true
})

const showTypedError = computed(
  () => props.payload.level === 3 && typed.value.length > 0 && !nameMatched.value,
)

function settle(confirmed: boolean) {
  if (settled) return
  settled = true
  result = confirmed
  open.value = false
}

async function onBackup() {
  await props.payload.backupHandler?.()
}

/** 默认焦点在「取消」（ElDialog 无焦点目标选项，在 opened 时机手动落焦） */
function focusCancel() {
  cancelRef.value?.$el?.focus?.()
}
</script>

<template>
  <ElDialog
    v-model="open"
    class="ds-confirm"
    :width="width"
    align-center
    :show-close="false"
    :close-on-click-modal="false"
    @opened="focusCancel"
    @closed="emit('resolve', result)"
  >
    <template #header>
      <span class="ds-confirm__title">{{ payload.title }}</span>
    </template>

    <p v-if="payload.message" class="ds-confirm__message">{{ payload.message }}</p>

    <!-- L2：具体影响清单 + 勾选闸门 -->
    <template v-if="payload.level === 2">
      <ul v-if="payload.impacts?.length" class="ds-confirm__impacts">
        <li v-for="(impact, i) in payload.impacts" :key="i">{{ impact }}</li>
      </ul>
      <ElCheckbox v-model="checked" class="ds-confirm__check">
        {{ payload.impactCheckText }}
      </ElCheckbox>
    </template>

    <!-- L3：不可撤销声明 + 输入对象名称闸门 + 先导出备份 -->
    <template v-if="payload.level === 3">
      <p class="ds-confirm__irreversible">此操作不可撤销</p>
      <div class="ds-confirm__typed">
        <div class="ds-confirm__typed-row">
          <ElInput
            v-model="typed"
            :placeholder="`请输入「${payload.objectName}」以确认`"
            spellcheck="false"
          />
          <ElButton v-if="payload.backupHandler" text class="ds-confirm__backup" @click="onBackup">
            {{ payload.backupText }}
          </ElButton>
        </div>
        <p v-if="showTypedError" class="ds-confirm__typed-error">
          名称不匹配，请完整输入“{{ payload.objectName }}”
        </p>
      </div>
    </template>

    <template #footer>
      <ElButton ref="cancelRef" @click="settle(false)">{{ payload.cancelText }}</ElButton>
      <ElButton
        :type="payload.danger ? 'danger' : 'primary'"
        :disabled="!canConfirm"
        @click="settle(true)"
      >
        {{ payload.confirmText }}
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.ds-confirm__title {
  font-size: var(--fs-h2); /* 16 / 600 */
  font-weight: var(--fw-h2);
  line-height: var(--lh-h2);
  color: var(--text-strong);
}
.ds-confirm__message {
  margin: 0;
  font-size: var(--fs-body); /* 14 */
  line-height: var(--lh-body);
  color: var(--text-1);
}
.ds-confirm__impacts {
  margin: var(--sp-3) 0 0;
  padding: var(--sp-2) var(--sp-3);
  list-style: none;
  background: var(--surface-2);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  font-size: var(--fs-body-sm); /* 13 */
  line-height: var(--lh-body-sm);
  color: var(--text-2);
}
.ds-confirm__impacts li + li {
  margin-top: var(--sp-1);
}
.ds-confirm__check {
  margin-top: var(--sp-3);
}
.ds-confirm__irreversible {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-body-sm);
  line-height: var(--lh-body-sm);
  color: var(--danger);
}
.ds-confirm__typed {
  margin-top: var(--sp-2);
}
.ds-confirm__typed-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.ds-confirm__typed-error {
  margin: 6px 0 0;
  font-size: var(--fs-caption); /* 12 */
  line-height: var(--lh-caption);
  color: var(--danger);
}
</style>
