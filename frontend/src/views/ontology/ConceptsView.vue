<script setup lang="ts">
/**
 * 业务概念（F2.3，原 static/js/ontology.js 业务概念）
 *
 * 概念词搜索 + 概念→表映射列表 + 同义词组。
 */
import { computed, onMounted, ref } from 'vue'
import { ElInput } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { DsAsyncSection } from '@/components'
import { fetchOntologyConcepts, type OntologyConcept } from '@/api/ontology'

const concepts = ref<OntologyConcept[]>([])
const synonyms = ref<Record<string, string[]>>({})
const loading = ref(false)
const error = ref<unknown>(null)
const kw = ref('')

const filtered = computed(() => {
  const q = kw.value.trim().toLowerCase()
  if (!q) return concepts.value
  return concepts.value.filter(
    (c) =>
      c.concept.toLowerCase().includes(q) ||
      (c.maps_to || []).some((t) => t.toLowerCase().includes(q)),
  )
})

const synonymKeys = computed(() => Object.keys(synonyms.value))

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchOntologyConcepts()
    concepts.value = res.items ?? []
    synonyms.value = res.synonym_groups ?? {}
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="concepts">
    <div class="concepts__toolbar">
      <ElInput v-model="kw" class="concepts__search" placeholder="搜索概念词或表名" clearable :prefix-icon="Search" />
      <span class="concepts__count">共 {{ filtered.length }} 个概念</span>
    </div>

    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="filtered.length === 0"
      skeleton="text"
      empty-type="no-result"
      empty-title="没有匹配的概念"
      empty-desc="尝试调整搜索关键词"
      @retry="load"
    >
      <div class="concepts__list">
        <div v-for="c in filtered" :key="c.concept" class="concept-row">
          <strong>{{ c.concept }}</strong>
          <span class="concept-row__arrow">→</span>
          <code v-for="t in c.maps_to" :key="t" class="concept-row__table">{{ t }}</code>
          <template v-if="c.alt_labels && c.alt_labels.length">
            <span class="concept-row__alt">（{{ c.alt_labels.join(' / ') }}）</span>
          </template>
        </div>
      </div>
    </DsAsyncSection>

    <section v-if="synonymKeys.length" class="synonyms">
      <h4 class="synonyms__title">同义词组（{{ synonymKeys.length }}）</h4>
      <div v-for="k in synonymKeys" :key="k" class="synonyms__row">
        {{ k }} = {{ (synonyms[k] || []).join(' / ') }}
      </div>
    </section>
  </div>
</template>

<style scoped>
.concepts {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.concepts__toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.concepts__search {
  width: 280px;
}
.concepts__count {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.concepts__list {
  display: flex;
  flex-direction: column;
}
.concept-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  padding: 7px 0;
  border-bottom: 1px solid var(--line-subtle);
  font-size: var(--fs-body-sm);
  color: var(--text-1);
}
.concept-row__arrow {
  color: var(--text-4);
}
.concept-row__table {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--accent);
}
.concept-row__alt {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.synonyms {
  margin-top: var(--sp-2);
}
.synonyms__title {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-body);
  color: var(--text-2);
}
.synonyms__row {
  padding: 2px 0;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
</style>
