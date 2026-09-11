<script setup lang="ts">
/**
 * 收束条 DsClosingBar（设计稿 §4.4.4「收束期」/ §4.5.1）。
 *
 * 生成结束后追加的「收尾动作」条：总耗时 + 摘要数字 + 后续动作按钮，
 * 是「生成过程」变成「可沉淀资产」的接口。动作按钮通过具名插槽传入。
 *
 * 用法：
 *   <DsClosingBar :total-ms="6420" :metrics="[{label:'涉及', value:'6 张表'}]">
 *     <button @click="...">加入问答对库</button>
 *   </DsClosingBar>
 */
import { computed } from 'vue'

const props = defineProps<{
  /** 总耗时（ms） */
  totalMs?: number
  /** 摘要项（label + 等宽数字），如「涉及 6 张表」「LLM 调用 3 次」 */
  metrics?: { label: string; value: string | number }[]
}>()

const totalText = computed(() => {
  if (props.totalMs == null) return ''
  return props.totalMs >= 1000 ? `${(props.totalMs / 1000).toFixed(2)}s` : `${Math.round(props.totalMs)}ms`
})
</script>

<template>
  <div class="closing">
    <span class="closing-text">
      本次生成：<template v-if="totalText">总耗时 <b class="tabular">{{ totalText }}</b></template>
      <template v-for="(m, i) in metrics || []" :key="i">
        <span class="closing-sep">·</span>{{ m.label }} <b class="tabular">{{ m.value }}</b>
      </template>
    </span>
    <span class="closing-actions">
      <slot />
    </span>
  </div>
</template>

<style scoped>
.closing {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px;
  background: var(--accent-soft);
  border-top: 1px solid var(--accent-line);
  border-radius: 0 0 var(--r-md) var(--r-md);
}
.closing-text {
  font-size: 13px;
  color: var(--text-1);
}
.closing-text b {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--text-strong);
}
.closing-sep {
  color: var(--text-4);
  margin: 0 4px;
}
.closing-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
