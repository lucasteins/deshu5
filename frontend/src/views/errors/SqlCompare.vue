<script setup lang="ts">
/**
 * 错题集「对比视图」—— 生成 SQL vs 修正 SQL 左右并排差异高亮（设计稿 §4.5「4. 错题集」）
 *
 * 差异行高亮：删除/差异行用 --danger-soft 底（左），新增/修正行用 --success-soft 底（右）；
 * 行号 + 新增/删除标记（+ / −）辅助培训与演示定位。
 */
import { computed } from 'vue'
import { diffSqlLines, diffStats } from './sqlDiff'

const props = defineProps<{
  generated: string
  correct: string | null
}>()

const rows = computed(() => diffSqlLines(props.generated, props.correct ?? ''))
const stats = computed(() => diffStats(rows.value))

/** 是否两侧实质相同（无任何差异行） */
const identical = computed(() => stats.value.removed === 0 && stats.value.added === 0)

function cellClass(kind: 'same' | 'del' | 'add'): string {
  return `sql-cmp__line--${kind}`
}
</script>

<template>
  <div class="sql-cmp">
    <div class="sql-cmp__legend">
      <span class="sql-cmp__legend-item sql-cmp__legend-item--del">生成 SQL（差异行）</span>
      <span class="sql-cmp__legend-item sql-cmp__legend-item--add">修正 SQL（差异行）</span>
      <span class="sql-cmp__stat">
        −{{ stats.removed }} +{{ stats.added }} · {{ identical ? '两侧一致' : '存在差异' }}
      </span>
    </div>

    <div v-if="identical" class="sql-cmp__identical">
      生成 SQL 与修正 SQL 一致，无差异可标注。
    </div>

    <div v-else class="sql-cmp__grid">
      <!-- 左列：生成的 SQL -->
      <div class="sql-cmp__col">
        <div class="sql-cmp__head">生成的 SQL</div>
        <div
          v-for="(row, idx) in rows"
          :key="`l-${idx}`"
          class="sql-cmp__line"
          :class="cellClass(row.left.kind)"
        >
          <span class="sql-cmp__no">{{ row.left.no ?? '' }}</span>
          <span class="sql-cmp__marker">{{ row.left.kind === 'del' ? '−' : '' }}</span>
          <code class="sql-cmp__code">{{ row.left.text ?? '' }}</code>
        </div>
      </div>

      <!-- 右列：修正后的 SQL -->
      <div class="sql-cmp__col">
        <div class="sql-cmp__head">修正后的 SQL</div>
        <div
          v-for="(row, idx) in rows"
          :key="`r-${idx}`"
          class="sql-cmp__line"
          :class="cellClass(row.right.kind)"
        >
          <span class="sql-cmp__no">{{ row.right.no ?? '' }}</span>
          <span class="sql-cmp__marker">{{ row.right.kind === 'add' ? '+' : '' }}</span>
          <code class="sql-cmp__code">{{ row.right.text ?? '' }}</code>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sql-cmp {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

/* 图例 */
.sql-cmp__legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-3);
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.sql-cmp__legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.sql-cmp__legend-item::before {
  content: '';
  width: 10px;
  height: 10px;
  border-radius: 2px;
}
.sql-cmp__legend-item--del::before {
  background: var(--danger-soft);
  border: 1px solid var(--danger);
}
.sql-cmp__legend-item--add::before {
  background: var(--success-soft);
  border: 1px solid var(--success);
}
.sql-cmp__stat {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.sql-cmp__identical {
  padding: var(--sp-6);
  text-align: center;
  font-size: var(--fs-body-sm);
  color: var(--text-3);
  background: var(--surface-2);
  border: 1px dashed var(--line);
  border-radius: var(--r-md);
}

/* 左右并排 */
.sql-cmp__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
  align-items: start;
}
.sql-cmp__col {
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
  overflow: hidden;
}
.sql-cmp__head {
  padding: 6px var(--sp-3);
  font-size: var(--fs-caption);
  font-weight: var(--fw-h3);
  color: var(--text-2);
  background: var(--surface-2);
  border-bottom: 1px solid var(--line-subtle);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sql-cmp__line {
  display: flex;
  align-items: stretch;
  min-height: 22px;
  border-bottom: 1px solid var(--line-subtle);
}
.sql-cmp__line:last-child {
  border-bottom: none;
}
.sql-cmp__no {
  flex: none;
  width: 34px;
  padding: 2px 8px 2px 0;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-4);
  user-select: none;
  border-right: 1px solid var(--line-subtle);
  box-sizing: border-box;
}
.sql-cmp__marker {
  flex: none;
  width: 14px;
  text-align: center;
  font-family: var(--font-mono);
  font-size: 11px;
  user-select: none;
}
.sql-cmp__code {
  flex: 1;
  min-width: 0;
  padding: 2px 8px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  white-space: pre;
  overflow-x: auto;
  color: var(--text-1);
}

/* 差异行高亮（设计稿 §4.5「4. 错题集」：--danger-soft / --success-soft 底） */
.sql-cmp__line--del {
  background: var(--danger-soft);
}
.sql-cmp__line--del .sql-cmp__marker {
  color: var(--danger);
}
.sql-cmp__line--del .sql-cmp__code {
  color: var(--danger);
}
.sql-cmp__line--add {
  background: var(--success-soft);
}
.sql-cmp__line--add .sql-cmp__marker {
  color: var(--success);
}
.sql-cmp__line--add .sql-cmp__code {
  color: var(--success);
}
</style>
