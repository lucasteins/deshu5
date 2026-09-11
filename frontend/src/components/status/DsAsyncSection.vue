<script setup lang="ts">
/**
 * 异步分区容器（设计稿 §4.4.3 加载阶梯）
 *   < 200ms      不显示任何加载态（保持现有内容，避免闪烁）
 *   200ms – 1s   14px spinner（主色，800ms linear）
 *   > 1s         骨架屏（与真实布局同构，形态由 skeleton 决定）
 * 收束优先级：错误 → 空态 → 内容；各段均可通过同名插槽整体替换。
 *
 * 用法：
 *   <DsAsyncSection :loading="loading" :error="error" :is-empty="items.length === 0"
 *     skeleton="table" @retry="load">
 *     <MyTable :items="items" />
 *   </DsAsyncSection>
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import DsSkeleton from './DsSkeleton.vue'
import DsEmpty from './DsEmpty.vue'
import DsError from './DsError.vue'
import '../feedback/feedback.css' // .ds-spinner 共享样式

const props = withDefaults(
  defineProps<{
    loading: boolean
    /** 任意错误对象（Error / ApiError / string / null） */
    error?: unknown
    isEmpty?: boolean
    skeleton?: 'text' | 'table' | 'cards' | 'chart' | 'custom'
    /* 空态透传 */
    emptyType?: 'no-data' | 'no-result' | 'no-permission'
    emptyTitle?: string
    emptyDesc?: string
    emptyActionText?: string
    emptyFilters?: string[]
  }>(),
  { skeleton: 'text' },
)

const emit = defineEmits<{ retry: []; 'empty-action': []; 'empty-clear': [] }>()

type Phase = 'none' | 'spinner' | 'skeleton'
const phase = ref<Phase>('none')
let timerSpinner: ReturnType<typeof setTimeout> | undefined
let timerSkeleton: ReturnType<typeof setTimeout> | undefined

function clearTimers() {
  if (timerSpinner !== undefined) clearTimeout(timerSpinner)
  if (timerSkeleton !== undefined) clearTimeout(timerSkeleton)
  timerSpinner = timerSkeleton = undefined
}

watch(
  () => props.loading,
  (v) => {
    clearTimers()
    phase.value = 'none'
    if (!v) return
    timerSpinner = setTimeout(() => (phase.value = 'spinner'), 200)
    timerSkeleton = setTimeout(() => (phase.value = 'skeleton'), 1000)
  },
  { immediate: true },
)

onBeforeUnmount(clearTimers)

const errorText = computed(() => {
  const e = props.error
  if (e === undefined || e === null || e === false || e === '') return ''
  return e instanceof Error ? e.message : String(e)
})
const hasError = computed(() => errorText.value !== '')
</script>

<template>
  <div class="ds-async">
    <template v-if="loading">
      <!-- < 200ms：保持现有内容（stale），不闪加载态 -->
      <slot v-if="phase === 'none'" />
      <div v-else-if="phase === 'spinner'" class="ds-async__spinner" role="status" aria-label="加载中">
        <span class="ds-spinner" />
      </div>
      <template v-else>
        <slot name="skeleton">
          <DsSkeleton :variant="skeleton === 'custom' ? 'text' : skeleton" />
        </slot>
      </template>
    </template>

    <slot v-else-if="hasError" name="error">
      <DsError variant="module" :reason="errorText" @retry="emit('retry')" />
    </slot>

    <slot v-else-if="isEmpty" name="empty">
      <DsEmpty
        :type="emptyType"
        :title="emptyTitle"
        :desc="emptyDesc"
        :action-text="emptyActionText"
        :filters="emptyFilters"
        @action="emit('empty-action')"
        @clear="emit('empty-clear')"
      />
    </slot>

    <slot v-else />
  </div>
</template>

<style scoped>
.ds-async__spinner {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--sp-10) 0;
}
</style>
