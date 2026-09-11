<script setup lang="ts">
/**
 * 列头漏斗筛选（设计稿 §4.4.2「筛选：列头漏斗进入 → 枚举勾选列表」）
 * 勾选即生效（同列多值为 OR）；激活态漏斗图标着主色，面板底部提供「清除该列筛选」。
 */
import { computed } from 'vue'
import { ElCheckbox, ElCheckboxGroup, ElPopover } from 'element-plus'
import { Filter } from '@element-plus/icons-vue'
import type { DsFilterOption } from './types'

const props = defineProps<{
  label: string
  options: DsFilterOption[]
  modelValue: string[]
}>()

const emit = defineEmits<{
  'update:modelValue': [values: string[]]
  clear: []
}>()

const active = computed(() => props.modelValue.length > 0)
</script>

<template>
  <span class="ds-col-filter" :class="{ 'is-active': active }">
    <span class="ds-col-filter__label">{{ label }}</span>
    <ElPopover trigger="click" :width="180" placement="bottom-start" popper-class="ds-col-filter__popper">
      <template #reference>
        <!-- 阻止冒泡：列头点击（排序）与漏斗点击分离 -->
        <button
          type="button"
          class="ds-col-filter__btn"
          :aria-label="`筛选：${label}`"
          @click.stop
        >
          <Filter />
        </button>
      </template>
      <div class="ds-col-filter__panel">
        <ElCheckboxGroup
          :model-value="modelValue"
          @update:model-value="(v: unknown) => emit('update:modelValue', (v as string[]) ?? [])"
        >
          <ElCheckbox v-for="opt in options" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </ElCheckbox>
        </ElCheckboxGroup>
        <button v-if="active" type="button" class="ds-col-filter__clear" @click="emit('clear')">
          清除该列筛选
        </button>
      </div>
    </ElPopover>
  </span>
</template>

<style scoped>
.ds-col-filter {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.ds-col-filter__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: var(--r-xs);
  background: none;
  color: var(--text-4);
  font-size: 12px;
  cursor: pointer;
  transition: color var(--dur-fast) var(--ease-out), background var(--dur-fast) var(--ease-out);
}
.ds-col-filter__btn:hover {
  color: var(--text-2);
  background: var(--sunken);
}
.ds-col-filter.is-active .ds-col-filter__btn {
  color: var(--accent);
}
.ds-col-filter__panel {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 260px;
  overflow: auto;
}
.ds-col-filter__panel :deep(.el-checkbox) {
  height: 28px;
  margin-right: 0;
}
.ds-col-filter__clear {
  margin-top: 6px;
  padding: 4px 0 0;
  border: none;
  border-top: 1px solid var(--line-subtle);
  background: none;
  color: var(--text-3);
  font-size: var(--fs-caption);
  cursor: pointer;
  text-align: left;
}
.ds-col-filter__clear:hover {
  color: var(--accent);
}
</style>
