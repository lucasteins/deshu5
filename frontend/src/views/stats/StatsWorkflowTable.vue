<script setup lang="ts">
/**
 * 统计看板 · 工作流运行表（F1.2）
 *
 * 数据：GET /api/generation-logs（服务端分页，每页 20）——旧看板「工作流运行 › 运行日志」同一口径。
 * 行展开显示生成 SQL；执行 / 判定用语义色徽标。
 */
import { onMounted, ref } from 'vue'
import { ElPagination, ElTable, ElTableColumn } from 'element-plus'
import { DsAsyncSection } from '@/components'
import { fetchGenerationLogs, type GenerationLog } from '@/api/stats'
import { normalizeQaTime } from '@/api/qa'

const PER_PAGE = 20

const rows = ref<GenerationLog[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const error = ref<unknown>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchGenerationLogs(page.value, PER_PAGE)
    rows.value = res.items ?? []
    total.value = res.total ?? 0
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

onMounted(load)

function onPage(p: number) {
  page.value = p
  void load()
}

function fmtTime(raw: string): string {
  return normalizeQaTime(raw) || '—'
}
function fmtLatency(ms: number): string {
  if (!ms) return '—'
  return `${(ms / 1000).toFixed(1)}s`
}
function judgmentClass(j: string): string {
  if (j === '正确') return 'run-badge--success'
  if (j === '错误') return 'run-badge--danger'
  return 'run-badge--muted'
}
</script>

<template>
  <DsAsyncSection
    :loading="loading"
    :error="error"
    :is-empty="rows.length === 0"
    skeleton="table"
    empty-title="还没有运行日志"
    empty-desc="在智能问答或训练模式中生成一次 SQL，这里就会出现运行记录"
    @retry="load"
  >
    <div class="run-table">
      <ElTable :data="rows" row-key="id" size="default" class="run-table__el">
        <ElTableColumn type="expand">
          <template #default="{ row }">
            <div class="run-detail">
              <div class="run-detail__label">生成 SQL</div>
              <pre class="run-detail__sql">{{ row.generated_sql || '（未生成）' }}</pre>
              <div class="run-detail__meta">
                <span>日志 ID：{{ row.id }}</span>
                <span>返回行数：{{ row.row_count ?? '—' }}</span>
                <span>评审通过：{{ row.review_passed ? '是' : '否' }}</span>
              </div>
            </div>
          </template>
        </ElTableColumn>

        <ElTableColumn label="业务问题" min-width="320">
          <template #default="{ row }">
            <span class="run-q" :title="row.question">{{ row.question || '（空）' }}</span>
          </template>
        </ElTableColumn>

        <ElTableColumn label="执行" width="96" align="center">
          <template #default="{ row }">
            <span
              class="run-badge"
              :class="row.execution_status === 'success' ? 'run-badge--success' : 'run-badge--danger'"
            >
              {{ row.execution_status === 'success' ? '成功' : row.execution_status || '失败' }}
            </span>
          </template>
        </ElTableColumn>

        <ElTableColumn label="判定" width="92" align="center">
          <template #default="{ row }">
            <span v-if="row.user_judgment" class="run-badge" :class="judgmentClass(row.user_judgment)">
              {{ row.user_judgment }}
            </span>
            <span v-else class="run-dash">—</span>
          </template>
        </ElTableColumn>

        <ElTableColumn label="尝试" width="76" align="center">
          <template #default="{ row }">
            <span class="run-num">{{ row.attempts || 1 }}</span>
          </template>
        </ElTableColumn>

        <ElTableColumn label="耗时" width="96" align="right">
          <template #default="{ row }">
            <span class="run-num">{{ fmtLatency(row.latency_ms) }}</span>
          </template>
        </ElTableColumn>

        <ElTableColumn label="行数" width="92" align="right">
          <template #default="{ row }">
            <span class="run-num">{{ row.row_count ?? '—' }}</span>
          </template>
        </ElTableColumn>

        <ElTableColumn label="时间" width="164">
          <template #default="{ row }">
            <span class="run-num run-num--time">{{ fmtTime(row.created_at) }}</span>
          </template>
        </ElTableColumn>
      </ElTable>

      <div v-if="total > PER_PAGE" class="run-table__pager">
        <ElPagination
          layout="prev, pager, next, total"
          :total="total"
          :page-size="PER_PAGE"
          :current-page="page"
          background
          @current-change="onPage"
        />
      </div>
    </div>
  </DsAsyncSection>
</template>

<style scoped>
.run-table {
  display: flex;
  flex-direction: column;
}
.run-table__el {
  --el-table-border-color: var(--line-subtle);
}
.run-q {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-1);
}
.run-num {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}
.run-num--time {
  color: var(--text-3);
}
.run-dash {
  color: var(--text-4);
}

.run-badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--r-full);
  font-size: var(--fs-caption);
  font-weight: 500;
  white-space: nowrap;
}
.run-badge--success {
  background: var(--success-soft);
  color: var(--success);
}
.run-badge--danger {
  background: var(--danger-soft);
  color: var(--danger);
}
.run-badge--muted {
  background: var(--sunken);
  color: var(--text-3);
}

.run-detail {
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border-radius: var(--r-md);
}
.run-detail__label {
  margin-bottom: 4px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-3);
}
.run-detail__sql {
  margin: 0;
  padding: var(--sp-3);
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 220px;
  overflow: auto;
}
.run-detail__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-4);
  margin-top: var(--sp-2);
  font-size: var(--fs-caption);
  color: var(--text-3);
}

.run-table__pager {
  display: flex;
  justify-content: flex-end;
  padding: var(--sp-3) var(--sp-1) 0;
}
</style>
