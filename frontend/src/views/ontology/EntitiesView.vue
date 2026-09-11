<script setup lang="ts">
/**
 * 实体视图（F2.3，原 static/js/ontology.js 实体视图）
 *
 * 层级筛选 + 名称/成员表搜索 + 实体卡片网格 + AI 批量补全缺失描述；
 * 点击卡片在下方展开实体详情（成员表 / 实体间关系 / 映射编辑）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElButton, ElInput, ElOption, ElSelect } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { confirm, DsAsyncSection, toast } from '@/components'
import {
  describeEntity,
  fetchOntologyEntities,
  fetchOntologyEntityDefs,
  type OntologyEntity,
} from '@/api/ontology'
import EntityDetail from './EntityDetail.vue'

const LAYER_BADGE: Record<string, string> = {
  master: '主数据',
  business: '业务数据',
  report: '统计报表',
}

const entities = ref<OntologyEntity[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const layer = ref('')
const kw = ref('')
const selectedName = ref<string | null>(null)
const batchLoading = ref(false)

const filtered = computed(() => {
  const q = kw.value.trim().toLowerCase()
  let out = entities.value
  if (layer.value) out = out.filter((e) => e.layer === layer.value)
  if (q) {
    out = out.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        (e.label || '').toLowerCase().includes(q) ||
        (e.member_tables || []).some((t) => t.toLowerCase().includes(q)),
    )
  }
  return out
})

const emptyFilters = computed<string[]>(() => {
  const chips: string[] = []
  if (kw.value.trim()) chips.push(`关键词 ${kw.value.trim()}`)
  if (layer.value) chips.push(LAYER_BADGE[layer.value] ?? layer.value)
  return chips
})

function layerText(l: string): string {
  return LAYER_BADGE[l] ?? l
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchOntologyEntities()
    entities.value = res.items ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

function openDetail(name: string) {
  selectedName.value = name
}

async function batchDescribe() {
  const ok = await confirm.l1({
    title: 'AI 批量补全实体描述',
    message:
      '将调用 LLM 为所有缺失描述的实体生成描述，并直接写入映射定义表（仍需「手动重建 → 提案审批」后进入生效版本）。继续？',
    confirmText: '开始补全',
  })
  if (!ok) return

  batchLoading.value = true
  const key = 'entity-batch-describe'
  toast.progress({ title: '正在读取实体映射定义…', key })
  try {
    const defsRes = await fetchOntologyEntityDefs()
    const missing = (defsRes.items ?? []).filter((d) => !(d.comment || '').trim())
    if (!missing.length) {
      toast.update(key, { level: 'info', title: '所有实体均已有描述，无需补全' })
      return
    }

    const failures: string[] = []
    for (let i = 0; i < missing.length; i++) {
      const d = missing[i]
      toast.update(key, {
        level: 'progress',
        title: `AI 补全中 ${i + 1}/${missing.length}…`,
      })
      try {
        await describeEntity(d.name, true)
      } catch (e) {
        failures.push(`${d.name}: ${e instanceof Error ? e.message : String(e)}`)
      }
    }

    const success = missing.length - failures.length
    toast.update(key, {
      level: failures.length ? 'warning' : 'success',
      title: `补全完成：成功 ${success} / ${missing.length}`,
      desc: '需「手动重建 → 提案审批」后描述才进入生效版本。' + (failures.length ? `失败：${failures.join('；')}` : ''),
    })
  } catch (e) {
    toast.update(key, {
      level: 'danger',
      title: '批量补全失败',
      desc: e instanceof Error ? e.message : String(e),
    })
  } finally {
    batchLoading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="entities">
    <div class="entities__toolbar">
      <ElInput v-model="kw" class="entities__search" placeholder="搜索实体或成员表" clearable :prefix-icon="Search" />
      <ElSelect v-model="layer" class="entities__layer" placeholder="全部层级">
        <ElOption label="全部层级" value="" />
        <ElOption label="主数据" value="master" />
        <ElOption label="业务数据" value="business" />
        <ElOption label="统计报表" value="report" />
      </ElSelect>
      <ElButton size="small" :loading="batchLoading" @click="batchDescribe">AI 补全缺失描述</ElButton>
      <span class="entities__count">共 {{ filtered.length }} 个实体</span>
    </div>

    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="filtered.length === 0"
      skeleton="cards"
      empty-type="no-result"
      empty-title="没有匹配的实体"
      empty-desc="尝试调整搜索关键词或层级筛选"
      :empty-filters="emptyFilters"
      @retry="load"
    >
      <div class="entities__grid">
        <button v-for="e in filtered" :key="e.name" type="button" class="entity-card" @click="openDetail(e.name)">
          <div class="entity-card__title">{{ e.label || e.name }}</div>
          <div class="entity-card__name">{{ e.name }} · {{ layerText(e.layer) }} · {{ e.member_count ?? e.member_tables.length }} 表</div>
          <div v-if="e.comment" class="entity-card__comment">{{ e.comment }}</div>
        </button>
      </div>
    </DsAsyncSection>

    <EntityDetail v-if="selectedName" :key="selectedName" :name="selectedName" @close="selectedName = null" />
  </div>
</template>

<style scoped>
.entities {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.entities__toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.entities__search {
  width: 264px;
}
.entities__layer {
  width: 150px;
}
.entities__count {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.entities__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(262px, 1fr));
  gap: var(--sp-3);
}
.entity-card {
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
.entity-card:hover {
  border-color: var(--accent-line);
  box-shadow: var(--e2);
  transform: translateY(-2px);
}
.entity-card__title {
  font-size: var(--fs-body);
  font-weight: 600;
  color: var(--text-1);
}
.entity-card__name {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-3);
  margin-top: 2px;
}
.entity-card__comment {
  margin-top: 6px;
  font-size: var(--fs-caption);
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
