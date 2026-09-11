<script setup lang="ts">
/**
 * 筛选条（设计稿 §4.4.2）：「已筛选： <chip> ✕ … 清空全部」
 * 每列一个 chip（多值为「a/b」），点击 ✕ 移除该列筛选。
 */
import { Close } from '@element-plus/icons-vue'
import type { DsFilterChip } from './types'

defineProps<{ chips: DsFilterChip[] }>()

const emit = defineEmits<{
  remove: [key: string]
  clear: []
}>()
</script>

<template>
  <div v-if="chips.length" class="ds-chips">
    <span class="ds-chips__label">已筛选：</span>
    <span v-for="chip in chips" :key="chip.key" class="ds-chips__chip">
      {{ chip.label }}
      <button
        type="button"
        class="ds-chips__x"
        :aria-label="`移除筛选：${chip.label}`"
        @click="emit('remove', chip.key)"
      >
        <Close />
      </button>
    </span>
    <button type="button" class="ds-chips__clear" @click="emit('clear')">清空全部</button>
  </div>
</template>

<style scoped>
.ds-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-4);
  border-bottom: 1px solid var(--line-subtle);
  background: var(--surface-2);
}
.ds-chips__label {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.ds-chips__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px 2px 10px;
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-full);
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.ds-chips__x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: none;
  color: var(--text-4);
  font-size: 10px;
  cursor: pointer;
}
.ds-chips__x:hover {
  color: var(--text-1);
  background: var(--line);
}
.ds-chips__clear {
  margin-left: auto;
  padding: 0;
  border: none;
  background: none;
  font-size: var(--fs-caption);
  color: var(--accent);
  cursor: pointer;
}
.ds-chips__clear:hover {
  text-decoration: underline;
}
</style>
