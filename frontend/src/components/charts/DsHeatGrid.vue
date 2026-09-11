<script setup lang="ts">
/**
 * 7×24 热力网格（设计稿 §4.4.7「二维密度」/ §4.5.3 提问活跃度）
 *
 * 单色明度阶梯 --seq-1..5（不引入第二种色相）；CSS Grid 实现（比 ECharts heatmap 更贴合
 * mockup 的表头刻度与单元比例，且不占 canvas）。
 * 单元 hover 显示「周三 14:00 · 128 次」。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 7 行 × 24 列 计数矩阵（行 = 周一..周日） */
    matrix: number[][]
    weekdays?: string[]
    /** 计数单位（tooltip / 图例） */
    unit?: string
    /** 图例右侧说明 */
    legendNote?: string
  }>(),
  {
    weekdays: () => ['周一', '周二', '周三', '周四', '周五', '周六', '周日'],
    unit: '次',
    legendNote: '',
  },
)

const max = computed(() => {
  let m = 0
  for (const row of props.matrix) for (const v of row) if (v > m) m = v
  return m
})

/** 强度 → 5 档明度（阈值对齐 mockup） */
function level(count: number): number {
  if (max.value <= 0) return 1
  const v = count / max.value
  if (v < 0.06) return 1
  if (v < 0.2) return 2
  if (v < 0.42) return 3
  if (v < 0.68) return 4
  return 5
}

const cells = computed(() =>
  props.weekdays.map((day, d) => ({
    day,
    hours: Array.from({ length: 24 }, (_, h) => {
      const count = props.matrix[d]?.[h] ?? 0
      return {
        h,
        count,
        lvl: level(count),
        title: `${day} ${String(h).padStart(2, '0')}:00 · ${count} ${props.unit}`,
      }
    }),
  })),
)

const hourTicks = Array.from({ length: 24 }, (_, h) => (h % 3 === 0 ? String(h).padStart(2, '0') : ''))
</script>

<template>
  <div class="ds-heat" role="img" aria-label="活跃度热力网格（周一至周日 × 0-23 时）">
    <div class="ds-heat__grid">
      <span class="ds-heat__corner" aria-hidden="true" />
      <span v-for="(t, h) in hourTicks" :key="`t${h}`" class="ds-heat__hlabel">{{ t }}</span>

      <template v-for="row in cells" :key="row.day">
        <span class="ds-heat__rlabel">{{ row.day }}</span>
        <span
          v-for="cell in row.hours"
          :key="`${row.day}-${cell.h}`"
          class="ds-heat__cell"
          :style="{ background: `var(--seq-${cell.lvl})` }"
          :title="cell.title"
        />
      </template>
    </div>

    <div class="ds-heat__legend">
      <span>少</span>
      <span v-for="l in [1, 2, 3, 4, 5]" :key="l" class="ds-heat__sw" :style="{ background: `var(--seq-${l})` }" />
      <span>多</span>
      <span class="ds-heat__note">{{ legendNote || `单位：${unit} / 小时` }}</span>
    </div>
  </div>
</template>

<style scoped>
.ds-heat {
  width: 100%;
}
.ds-heat__grid {
  display: grid;
  grid-template-columns: 32px repeat(24, minmax(0, 1fr));
  gap: 3px;
  align-items: center;
  width: 100%;
}
.ds-heat__hlabel {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-3);
  text-align: center;
}
.ds-heat__rlabel {
  font-size: 11px;
  color: var(--text-3);
  text-align: right;
  padding-right: 6px;
  white-space: nowrap;
}
.ds-heat__cell {
  width: 100%;
  aspect-ratio: 1 / 1;
  max-height: 22px;
  border-radius: 3px;
  transition: transform var(--dur-fast) var(--ease-standard);
}
.ds-heat__cell:hover {
  outline: 1px solid var(--accent);
  position: relative;
  z-index: 2;
  transform: scale(1.18);
}
.ds-heat__legend {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
  font-size: var(--fs-micro);
  color: var(--text-3);
}
.ds-heat__sw {
  width: 14px;
  height: 12px;
  border-radius: 3px;
}
.ds-heat__note {
  margin-left: auto;
}
</style>
