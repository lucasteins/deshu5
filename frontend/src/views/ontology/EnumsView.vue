<script setup lang="ts">
/**
 * 码值枚举（F2.3，原 static/js/ontology.js 码值枚举）
 *
 * 搜索（中文名/英文名/业务域）+ 码值域卡片；点击在下方展开单域详情（业务域 / 落列 / 码值明细）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElInput } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { DsAsyncSection } from '@/components'
import {
  fetchOntologyEnumeration,
  fetchOntologyEnumerations,
  type OntologyEnumeration,
  type OntologyEnumerationSummary,
} from '@/api/ontology'

const enums = ref<OntologyEnumerationSummary[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const kw = ref('')
const selected = ref<OntologyEnumeration | null>(null)
const detailLoading = ref(false)
const detailError = ref<unknown>(null)

const filtered = computed(() => {
  const q = kw.value.trim().toLowerCase()
  if (!q) return enums.value
  return enums.value.filter(
    (e) =>
      e.code_name.toLowerCase().includes(q) ||
      (e.cn_name || '').toLowerCase().includes(q) ||
      (e.domain || '').toLowerCase().includes(q),
  )
})

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchOntologyEnumerations()
    enums.value = res.items ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

async function openDetail(codeName: string) {
  selected.value = null
  detailLoading.value = true
  detailError.value = null
  try {
    const res = await fetchOntologyEnumeration(codeName)
    selected.value = res.item
  } catch (e) {
    detailError.value = e
  } finally {
    detailLoading.value = false
  }
}

function domainText(e: OntologyEnumeration): string {
  return [e.domain_l1, e.domain_l2, e.domain_l3].filter(Boolean).join(' / ') || '-'
}

onMounted(load)
</script>

<template>
  <div class="enums">
    <div class="enums__toolbar">
      <ElInput v-model="kw" class="enums__search" placeholder="搜索码值域（中文名/英文名/业务域）" clearable :prefix-icon="Search" />
      <span class="enums__count">共 {{ filtered.length }} 个码值域</span>
    </div>

    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="filtered.length === 0"
      skeleton="cards"
      empty-type="no-result"
      empty-title="没有匹配的码值域"
      empty-desc="尝试调整搜索关键词"
      @retry="load"
    >
      <div class="enums__grid">
        <button v-for="e in filtered" :key="e.code_name" type="button" class="enum-card" @click="openDetail(e.code_name)">
          <div class="enum-card__title">{{ e.cn_name || e.code_name }}</div>
          <div class="enum-card__name">
            {{ e.code_name }} · {{ e.domain || '-' }} · {{ e.item_count }} 项 · 落 {{ e.column_refs.length }} 列
          </div>
        </button>
      </div>
    </DsAsyncSection>

    <div v-if="selected || detailLoading || detailError" class="enum-detail">
      <div class="enum-detail__bar">
        <h3 v-if="selected" class="enum-detail__title">
          {{ selected.cn_name || selected.code_name }}
          <span class="enum-detail__sub">{{ selected.code_name }}</span>
        </h3>
        <button v-else class="enum-detail__close" type="button" @click="selected = null; detailError = null">关闭</button>
      </div>

      <div v-if="detailLoading" class="enum-detail__state">加载码值域…</div>
      <div v-else-if="detailError" class="enum-detail__error">
        {{ detailError instanceof Error ? detailError.message : String(detailError) }}
      </div>
      <template v-else-if="selected">
        <div class="enum-detail__domain">业务域：{{ domainText(selected) }} · 共 {{ selected.items.length }} 项（最多显示 100）</div>

        <section v-if="selected.column_refs.length" class="enum-detail__section">
          <h4>落列</h4>
          <ul class="enum-detail__list">
            <li v-for="(r, i) in selected.column_refs" :key="i">
              {{ r.table }}.{{ r.column }}<template v-if="r.form">（存{{ r.form }}）</template>
            </li>
          </ul>
        </section>

        <table class="enum-detail__table">
          <thead><tr><th>编码</th><th>名称</th></tr></thead>
          <tbody>
            <tr v-for="it in selected.items.slice(0, 100)" :key="it.code">
              <td><code>{{ it.code }}</code></td>
              <td>{{ it.name }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>
  </div>
</template>

<style scoped>
.enums {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.enums__toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.enums__search {
  width: 320px;
}
.enums__count {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.enums__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(262px, 1fr));
  gap: var(--sp-3);
}
.enum-card {
  position: relative;
  text-align: left;
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
  padding: 15px 17px;
  cursor: pointer;
  transition:
    border-color var(--dur-fast) var(--ease-standard),
    box-shadow var(--dur-fast) var(--ease-standard),
    transform var(--dur-fast) var(--ease-standard);
}
.enum-card:hover {
  border-color: var(--accent-line);
  box-shadow: var(--e2);
  transform: translateY(-2px);
}
.enum-card__title {
  font-size: var(--fs-body);
  font-weight: 600;
  color: var(--text-1);
}
.enum-card__name {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-3);
  margin-top: 2px;
}

.enum-detail {
  margin-top: var(--sp-3);
  padding: var(--sp-4);
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
}
.enum-detail__bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-3);
}
.enum-detail__title {
  margin: 0;
  font-size: var(--fs-h3);
  font-weight: var(--fw-h3);
  color: var(--text-strong);
}
.enum-detail__sub {
  font-size: var(--fs-caption);
  font-weight: 400;
  color: var(--text-3);
  margin-left: var(--sp-2);
}
.enum-detail__close {
  border: none;
  background: none;
  color: var(--accent);
  font-size: var(--fs-body-sm);
  cursor: pointer;
}
.enum-detail__state,
.enum-detail__error {
  padding: var(--sp-4) 0;
  color: var(--text-2);
}
.enum-detail__error {
  color: var(--danger);
}
.enum-detail__domain {
  margin-top: var(--sp-2);
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.enum-detail__section {
  margin-top: var(--sp-3);
}
.enum-detail__section h4 {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-body);
  color: var(--text-2);
}
.enum-detail__list {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  font-size: var(--fs-body-sm);
  color: var(--text-1);
}
.enum-detail__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-body-sm);
  margin-top: var(--sp-3);
}
.enum-detail__table th,
.enum-detail__table td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--line-subtle);
  text-align: left;
}
.enum-detail__table th {
  color: var(--text-3);
  font-weight: 500;
  border-bottom-color: var(--line);
}
.enum-detail__table code {
  font-family: var(--font-mono);
  font-size: 12px;
}
</style>
