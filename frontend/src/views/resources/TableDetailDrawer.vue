<script setup lang="ts">
/**
 * 表详情抽屉（F2.2，目录 / 图谱共用；原 static/js/resource.js showTableDetail）
 *
 * 展示：单表字段（名称 / 类型 / PK / 注释）+ 关联关系（可点击跳转到相邻表）。
 * 数据：GET /api/table-columns?table=。
 */
import { ref, watch } from 'vue'
import { ElDrawer } from 'element-plus'
import { fetchTableColumns, type TableColumnsResponse } from '@/api/resources'
import DsAsyncSection from '@/components/status/DsAsyncSection.vue'

const props = defineProps<{ table: string | null }>()
const emit = defineEmits<{ close: []; navigate: [table: string] }>()

const loading = ref(false)
const error = ref<unknown>(null)
const data = ref<TableColumnsResponse | null>(null)

async function load(t: string) {
  loading.value = true
  error.value = null
  try {
    data.value = await fetchTableColumns(t)
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

watch(
  () => props.table,
  (t) => {
    if (!t) {
      data.value = null
      return
    }
    void load(t)
  },
  { immediate: true },
)
</script>

<template>
  <ElDrawer
    :model-value="props.table !== null"
    size="440px"
    direction="rtl"
    :show-close="true"
    @close="emit('close')"
  >
    <template #header>
      <div class="drawer-head">
        <div class="drawer-title">{{ data?.comment || props.table || '加载中…' }}</div>
        <div class="drawer-sub">{{ props.table }}</div>
      </div>
    </template>

    <DsAsyncSection
      :loading="loading"
      :error="error"
      skeleton="table"
      class="drawer-body"
      @retry="props.table && load(props.table)"
    >
      <template v-if="data">
        <div class="cp-block">
          <div class="cp-label">关联关系（{{ data.relationships.length }}）</div>
          <div v-if="!data.relationships.length" class="cp-empty">无</div>
          <button
            v-for="rel in data.relationships"
            :key="rel.table"
            type="button"
            class="drawer-rel"
            @click="emit('navigate', rel.table)"
          >
            <div class="drawer-rel-table">
              {{ rel.comment || rel.table }} <span class="drawer-rel-name">{{ rel.table }}</span>
            </div>
            <div class="drawer-rel-cond">
              <span v-for="(c, i) in rel.join_conditions" :key="i">{{ c }}<br v-if="i < rel.join_conditions.length - 1" /></span>
            </div>
          </button>
        </div>

        <div class="cp-block">
          <div class="cp-label">字段（{{ data.columns.length }}）</div>
          <table class="col-table">
            <thead>
              <tr>
                <th style="width: 34%">字段</th>
                <th style="width: 22%">类型</th>
                <th>注释</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in data.columns" :key="c.name">
                <td class="mono">
                  {{ c.name }}<span v-if="c.pk" class="pk-chip">PK</span>
                </td>
                <td class="mono type">{{ c.type || '' }}</td>
                <td>{{ c.comment || '' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </DsAsyncSection>
  </ElDrawer>
</template>

<style scoped>
.drawer-head {
  min-width: 0;
}
.drawer-title {
  font-size: var(--fs-h3);
  font-weight: 600;
  color: var(--text-strong);
  line-height: 1.4;
}
.drawer-sub {
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  color: var(--text-3);
  margin-top: 3px;
}
.drawer-body {
  min-height: 200px;
}
.cp-block {
  margin-bottom: var(--sp-5);
}
.cp-label {
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-3);
  margin-bottom: var(--sp-2);
}
.cp-empty {
  font-size: var(--fs-body-sm);
  color: var(--text-3);
  padding: var(--sp-2) 0;
}

.drawer-rel {
  display: block;
  width: 100%;
  text-align: left;
  padding: 11px 13px;
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
  margin-bottom: var(--sp-2);
  cursor: pointer;
  background: var(--surface-2);
  color: inherit;
  transition:
    border-color var(--dur-fast) var(--ease-standard),
    background var(--dur-fast) var(--ease-standard),
    transform var(--dur-fast) var(--ease-standard);
}
.drawer-rel:hover {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  transform: translateX(2px);
}
.drawer-rel-table {
  font-size: var(--fs-body-sm);
  font-weight: 600;
  color: var(--text-1);
}
.drawer-rel-name {
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  color: var(--text-3);
  font-weight: 400;
  margin-left: 6px;
}
.drawer-rel-cond {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-2);
  margin-top: 5px;
  line-height: 1.6;
}

.col-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-body-sm);
}
.col-table th {
  text-align: left;
  padding: 8px 10px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-3);
  background: var(--sunken);
  border-bottom: 1px solid var(--line);
}
.col-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--line-subtle);
  color: var(--text-1);
  vertical-align: top;
}
.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
.type {
  color: var(--text-3);
}
.pk-chip {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: var(--r-xs);
  background: var(--ember-soft);
  color: var(--ember);
  font-size: 10px;
  font-weight: 600;
}
</style>
