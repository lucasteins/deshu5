<script setup lang="ts">
/**
 * 关系（F2.3，原 static/js/ontology.js 关系浏览）
 *
 * 来源筛选（物理外键/治理文档/双源一致）+ 表名搜索 + 关系列表（连接条件 + 业务场景）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElInput, ElOption, ElSelect } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { DsAsyncSection } from '@/components'
import { fetchOntologyRelations, type OntologyRelation } from '@/api/ontology'

const SOURCE_BADGE: Record<string, string> = {
  physical_fk: '物理外键',
  governance_doc: '治理文档',
  both: '双源一致',
}

const relations = ref<OntologyRelation[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const source = ref('')
const kw = ref('')

const filtered = computed(() => {
  const q = kw.value.trim().toLowerCase()
  let out = relations.value
  if (source.value) out = out.filter((r) => r.source === source.value)
  if (q) {
    out = out.filter(
      (r) =>
        r.from_class.toLowerCase().includes(q) || r.to_class.toLowerCase().includes(q),
    )
  }
  return out
})

function sourceText(s: string): string {
  return SOURCE_BADGE[s] ?? s
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchOntologyRelations()
    relations.value = res.items ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="relations">
    <div class="relations__toolbar">
      <ElSelect v-model="source" class="relations__source" placeholder="全部来源">
        <ElOption label="全部来源" value="" />
        <ElOption label="物理外键" value="physical_fk" />
        <ElOption label="治理文档" value="governance_doc" />
        <ElOption label="双源一致" value="both" />
      </ElSelect>
      <ElInput v-model="kw" class="relations__search" placeholder="搜索表名" clearable :prefix-icon="Search" />
      <span class="relations__count">共 {{ filtered.length }} 条</span>
    </div>

    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="filtered.length === 0"
      skeleton="text"
      empty-type="no-result"
      empty-title="没有匹配的关系"
      empty-desc="尝试调整搜索关键词或来源筛选"
      @retry="load"
    >
      <div class="relations__list">
        <div v-for="(r, i) in filtered" :key="i" class="relation-row">
          <strong>{{ r.from_class }} → {{ r.to_class }}</strong>
          <span class="relation-row__source">{{ sourceText(r.source) }}</span>
          <div v-if="(r.join_conditions || []).length" class="relation-row__cond">
            {{ r.join_conditions.join(' AND ') }}
          </div>
          <div v-if="(r.business_scenarios || []).length" class="relation-row__scene">
            场景：{{ r.business_scenarios.join('、') }}
          </div>
        </div>
      </div>
    </DsAsyncSection>
  </div>
</template>

<style scoped>
.relations {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.relations__toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.relations__source {
  width: 160px;
}
.relations__search {
  width: 220px;
}
.relations__count {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.relations__list {
  display: flex;
  flex-direction: column;
}
.relation-row {
  padding: 9px 0;
  border-bottom: 1px solid var(--line-subtle);
  font-size: var(--fs-body-sm);
  color: var(--text-1);
}
.relation-row__source {
  margin-left: var(--sp-2);
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.relation-row__cond {
  margin-top: 2px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-2);
}
.relation-row__scene {
  margin-top: 2px;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
</style>
