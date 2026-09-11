<script setup lang="ts">
/**
 * 物理表（F2.3，原 static/js/ontology.js 类浏览）
 *
 * 搜索（表名/中文注释）+ 类型筛选（维度/事实/其他）+ 卡片网格；
 * 点击卡片在下方展开类详情（字段清单 / 出入关系 / 落列码值）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElInput, ElOption, ElSelect } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { DsAsyncSection } from '@/components'
import {
  fetchOntologyClass,
  fetchOntologyClasses,
  type OntologyClass,
  type OntologyClassDetailResponse,
} from '@/api/ontology'

const KIND_BADGE: Record<string, string> = {
  dimension: '维度',
  fact: '事实',
  other: '其他',
}

const classes = ref<OntologyClass[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const kind = ref('')
const kw = ref('')
const selected = ref<OntologyClassDetailResponse | null>(null)
const detailLoading = ref(false)
const detailError = ref<unknown>(null)

const filtered = computed(() => {
  const q = kw.value.trim().toLowerCase()
  let out = classes.value
  if (kind.value) out = out.filter((c) => c.kind === kind.value)
  if (q) {
    out = out.filter(
      (c) => c.name.toLowerCase().includes(q) || (c.label || '').toLowerCase().includes(q),
    )
  }
  return out
})

function kindText(k: string): string {
  return KIND_BADGE[k] ?? k
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchOntologyClasses()
    classes.value = res.items ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

async function openDetail(name: string) {
  selected.value = null
  detailLoading.value = true
  detailError.value = null
  try {
    selected.value = await fetchOntologyClass(name)
  } catch (e) {
    detailError.value = e
  } finally {
    detailLoading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="classes">
    <div class="classes__toolbar">
      <ElInput v-model="kw" class="classes__search" placeholder="搜索表名或中文注释" clearable :prefix-icon="Search" />
      <ElSelect v-model="kind" class="classes__kind" placeholder="全部类型">
        <ElOption label="全部类型" value="" />
        <ElOption label="维度 Dimension" value="dimension" />
        <ElOption label="事实 Fact" value="fact" />
        <ElOption label="其他" value="other" />
      </ElSelect>
      <span class="classes__count">共 {{ filtered.length }} 个类</span>
    </div>

    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="filtered.length === 0"
      skeleton="cards"
      empty-type="no-result"
      empty-title="没有匹配的物理表"
      empty-desc="尝试调整搜索关键词或类型筛选"
      @retry="load"
    >
      <div class="classes__grid">
        <button v-for="c in filtered" :key="c.name" type="button" class="class-card" @click="openDetail(c.name)">
          <div class="class-card__title">{{ c.label || c.name }}</div>
          <div class="class-card__name">{{ c.name }} · {{ kindText(c.kind) }} · {{ c.property_count ?? 0 }} 属性</div>
        </button>
      </div>
    </DsAsyncSection>

    <div v-if="selected || detailLoading || detailError" class="class-detail">
      <div class="class-detail__bar">
        <h3 v-if="selected" class="class-detail__title">
          {{ selected.class.label || selected.class.name }}
          <span class="class-detail__sub">{{ selected.class.name }}</span>
        </h3>
        <button v-else class="class-detail__close" type="button" @click="selected = null; detailError = null">关闭</button>
      </div>

      <div v-if="detailLoading" class="class-detail__state">加载类详情…</div>
      <div v-else-if="detailError" class="class-detail__error">
        {{ detailError instanceof Error ? detailError.message : String(detailError) }}
      </div>
      <template v-else-if="selected">
        <table class="class-detail__table">
          <thead><tr><th>列名</th><th>中文名</th><th>类型</th><th>主键</th></tr></thead>
          <tbody>
            <tr v-for="p in selected.properties" :key="p.name">
              <td><code>{{ p.name }}</code></td>
              <td>{{ p.label }}</td>
              <td><code>{{ p.data_type }}</code></td>
              <td>{{ p.is_pk ? '✔' : '' }}</td>
            </tr>
          </tbody>
        </table>

        <section v-if="selected.relations.length" class="class-detail__section">
          <h4>关系</h4>
          <ul class="class-detail__list">
            <li v-for="(r, i) in selected.relations" :key="i">
              {{ r.from_class }} ↔ {{ r.to_class }}（{{ (r.join_conditions || []).join(' AND ') }}）
            </li>
          </ul>
        </section>

        <section v-if="selected.enumerations.length" class="class-detail__section">
          <h4>落列码值</h4>
          <ul class="class-detail__list">
            <li v-for="(e, i) in selected.enumerations" :key="i">
              {{ e.column }} → {{ e.cn_name || e.code_name }}<template v-if="e.form">（存{{ e.form }}）</template>
            </li>
          </ul>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.classes {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.classes__toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.classes__search {
  width: 264px;
}
.classes__kind {
  width: 180px;
}
.classes__count {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.classes__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(262px, 1fr));
  gap: var(--sp-3);
}
.class-card {
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
.class-card:hover {
  border-color: var(--accent-line);
  box-shadow: var(--e2);
  transform: translateY(-2px);
}
.class-card__title {
  font-size: var(--fs-body);
  font-weight: 600;
  color: var(--text-1);
}
.class-card__name {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-3);
  margin-top: 2px;
}

.class-detail {
  margin-top: var(--sp-3);
  padding: var(--sp-4);
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
}
.class-detail__bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-3);
}
.class-detail__title {
  margin: 0;
  font-size: var(--fs-h3);
  font-weight: var(--fw-h3);
  color: var(--text-strong);
}
.class-detail__sub {
  font-size: var(--fs-caption);
  font-weight: 400;
  color: var(--text-3);
  margin-left: var(--sp-2);
}
.class-detail__close {
  border: none;
  background: none;
  color: var(--accent);
  font-size: var(--fs-body-sm);
  cursor: pointer;
}
.class-detail__state,
.class-detail__error {
  padding: var(--sp-4) 0;
  color: var(--text-2);
}
.class-detail__error {
  color: var(--danger);
}
.class-detail__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-body-sm);
  margin-top: var(--sp-3);
}
.class-detail__table th,
.class-detail__table td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--line-subtle);
  text-align: left;
}
.class-detail__table th {
  color: var(--text-3);
  font-weight: 500;
  border-bottom-color: var(--line);
}
.class-detail__table code {
  font-family: var(--font-mono);
  font-size: 12px;
}
.class-detail__section {
  margin-top: var(--sp-4);
}
.class-detail__section h4 {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-body);
  color: var(--text-2);
}
.class-detail__list {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  font-size: var(--fs-body-sm);
  color: var(--text-1);
}
</style>
