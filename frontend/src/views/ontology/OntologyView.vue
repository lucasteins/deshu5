<script setup lang="ts">
/**
 * 本体模型（F2.3，原 static/index.html #mode-ontology + static/js/ontology.js）
 *
 * 七页签：实体视图 / 实体图谱 / 物理表 / 关系 / 码值枚举 / 业务概念 / 变更提案。
 * 页签状态由 AppShell 的 TabBar 驱动（URL #tab=<key>），本视图读取 hash 同步。
 *
 * 模块级头部（跨页签常驻）：版本 + 导出 + 检测变更 + 手动重建 + 6 项计数 + 漂移横幅。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElButton, ElOption, ElSelect } from 'element-plus'
import { confirm, toast } from '@/components'
import {
  approveProposal,
  checkDrift,
  fetchOntologySummary,
  fetchProposal,
  ontologyExportUrl,
  rebuildOntology,
  rejectProposal,
  type OntologyDiff,
  type OntologySummaryInfo,
  type OntologySummaryResponse,
} from '@/api/ontology'
import OntologyDiffView from './OntologyDiff.vue'
import EntitiesView from './EntitiesView.vue'
import OntologyGraphView from './OntologyGraphView.vue'
import ClassesView from './ClassesView.vue'
import RelationsView from './RelationsView.vue'
import EnumsView from './EnumsView.vue'
import ConceptsView from './ConceptsView.vue'
import ProposalsView from './ProposalsView.vue'

const TABS = ['entities', 'egraph', 'classes', 'relations', 'enums', 'concepts', 'proposals'] as const
type Otab = (typeof TABS)[number]

const activeTab = ref<Otab>('entities')
const summary = ref<OntologySummaryResponse | null>(null)
const summaryLoading = ref(false)
const summaryError = ref<unknown>(null)

const exportFmt = ref('ttl')
const checkingDrift = ref(false)
const rebuilding = ref(false)
const deciding = ref(false)

const bannerDiff = ref<OntologyDiff | null>(null)
const bannerOpen = ref(false)
const bannerLoading = ref(false)

/** 批准/驳回/重建后自增，强制当前页签重挂载以刷新其数据。 */
const reloadToken = ref(0)

const info = computed<OntologySummaryInfo | undefined>(() => summary.value?.summary)
const meta = computed(() => summary.value?.meta)
const pendingId = computed<number | null>(() => summary.value?.pending_proposal ?? null)
const showBanner = computed(() => !!(summary.value?.drift && pendingId.value))

const versionText = computed(() => {
  if (!summary.value?.available || !meta.value) return ''
  const layers = info.value?.entities_by_layer ?? {}
  const layerPart = Object.keys(layers).length
    ? ` · 主数据 ${layers.master ?? 0} / 业务数据 ${layers.business ?? 0} / 报表 ${layers.report ?? 0} 实体`
    : ''
  return `当前版本 v${meta.value.version} · 构建于 ${meta.value.built_at || '-'}${layerPart}`
})

const statCards = computed(() => {
  const s = info.value
  return [
    [s?.entities ?? 0, '业务实体'],
    [s?.classes ?? 0, '物理表（类）'],
    [s?.properties ?? 0, '属性（列）'],
    [s?.relations ?? 0, '关系'],
    [s?.enumerations ?? 0, '码值域'],
    [s?.concepts ?? 0, '业务概念'],
  ] as const
})

function readHashTab(): string {
  const m = /(?:^#|&)tab=([^&]*)/.exec(window.location.hash)
  return m ? decodeURIComponent(m[1]) : ''
}

function syncTab() {
  const fromHash = readHashTab()
  activeTab.value = (TABS as readonly string[]).includes(fromHash)
    ? (fromHash as Otab)
    : 'entities'
}

async function loadSummary() {
  summaryLoading.value = true
  summaryError.value = null
  try {
    summary.value = await fetchOntologySummary()
    bannerDiff.value = null
    bannerOpen.value = false
  } catch (e) {
    summaryError.value = e
  } finally {
    summaryLoading.value = false
  }
}

function exportOntology() {
  window.open(ontologyExportUrl(exportFmt.value), '_blank')
}

async function runDriftCheck() {
  checkingDrift.value = true
  try {
    const res = await checkDrift()
    if (res.drift && res.proposal_id) {
      bannerDiff.value = res.diff ?? null
      bannerOpen.value = true
      await loadSummary()
    } else if (res.drift) {
      toast.info(res.note || '检测到结构变化，但已存在待审批提案')
      await loadSummary()
    } else {
      toast.success('底座结构与当前本体一致，无变更')
    }
  } catch (e) {
    toast.danger({ title: '漂移检测失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    checkingDrift.value = false
  }
}

async function toggleBannerDetail() {
  if (!pendingId.value) return
  if (bannerOpen.value) {
    bannerOpen.value = false
    return
  }
  bannerLoading.value = true
  try {
    const res = await fetchProposal(pendingId.value)
    bannerDiff.value = res.diff
    bannerOpen.value = true
  } catch (e) {
    toast.danger({ title: '加载提案失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    bannerLoading.value = false
  }
}

async function decide(action: 'approve' | 'reject') {
  if (!pendingId.value) return
  const verb = action === 'approve' ? '批准更新' : '驳回'
  const ok = await confirm.l1({
    title: action === 'approve' ? '批准本体变更提案' : '驳回本体变更提案',
    message: `确认${verb}该本体变更提案？`,
    confirmText: verb,
  })
  if (!ok) return

  deciding.value = true
  try {
    if (action === 'approve') {
      const res = await approveProposal(pendingId.value)
      toast.success(`已批准，生效版本 v${res.version}`)
    } else {
      await rejectProposal(pendingId.value)
      toast.success('已驳回，本体保持当前版本')
    }
    await loadSummary()
    reloadToken.value++
  } catch (e) {
    toast.danger({ title: `${verb}失败`, desc: e instanceof Error ? e.message : String(e) })
  } finally {
    deciding.value = false
  }
}

async function rebuild() {
  const ok = await confirm.l1({
    title: '手动重建本体',
    message: '手动重建将基于当前底座生成全量刷新提案（需审批后生效），继续？',
    confirmText: '手动重建',
  })
  if (!ok) return

  rebuilding.value = true
  try {
    const res = await rebuildOntology()
    bannerDiff.value = res.diff ?? null
    bannerOpen.value = true
    await loadSummary()
  } catch (e) {
    toast.danger({ title: '重建失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    rebuilding.value = false
  }
}

function onProposalsRefresh() {
  void loadSummary()
  reloadToken.value++
}

onMounted(() => {
  syncTab()
  window.addEventListener('hashchange', syncTab)
  void loadSummary()
})
onBeforeUnmount(() => window.removeEventListener('hashchange', syncTab))
</script>

<template>
  <div class="ontology">
    <!-- 变更审批横幅 -->
    <div v-if="showBanner" class="drift-banner">
      <div class="drift-banner__row">
        <strong class="drift-banner__warn">⚠ 基础表结构已变化</strong>
        <ElButton size="small" :loading="bannerLoading" @click="toggleBannerDetail">
          {{ bannerOpen ? '收起变更明细' : '查看变更明细' }}
        </ElButton>
        <ElButton type="primary" size="small" :loading="deciding" @click="decide('approve')">批准更新</ElButton>
        <ElButton size="small" :loading="deciding" @click="decide('reject')">驳回</ElButton>
      </div>
      <div v-if="bannerOpen" class="drift-banner__detail">
        <OntologyDiffView :diff="bannerDiff" />
      </div>
    </div>

    <!-- 工具行 -->
    <div class="ontology__toolbar">
      <span class="ontology__version">{{ versionText }}</span>
      <ElSelect v-model="exportFmt" class="ontology__fmt" size="small">
        <ElOption label="OWL (RDF/XML)" value="owl" />
        <ElOption label="Turtle (.ttl)" value="ttl" />
        <ElOption label="N-Triples (.nt)" value="nt" />
        <ElOption label="JSON-LD (.jsonld)" value="jsonld" />
      </ElSelect>
      <ElButton size="small" type="primary" @click="exportOntology">导出本体</ElButton>
      <ElButton size="small" :loading="checkingDrift" @click="runDriftCheck">检测变更</ElButton>
      <ElButton size="small" :loading="rebuilding" @click="rebuild">手动重建</ElButton>
    </div>

    <!-- 总览计数 -->
    <div v-if="summaryLoading" class="ontology__hint">加载本体总览…</div>
    <div v-else-if="summaryError" class="ontology__hint ontology__hint--error">
      {{ summaryError instanceof Error ? summaryError.message : String(summaryError) }}
      <ElButton size="small" @click="loadSummary">重试</ElButton>
    </div>
    <div v-else-if="summary && !summary.available" class="ontology__hint">
      本体尚未构建（启动时自动引导，或检查本体库连通性）
    </div>
    <div v-else class="ontology__stats">
      <div v-for="[n, label] in statCards" :key="label" class="stat-box">
        <div class="stat-box__number">{{ n }}</div>
        <div class="stat-box__label">{{ label }}</div>
      </div>
    </div>

    <!-- 子页签内容 -->
    <EntitiesView v-if="activeTab === 'entities'" :key="`entities:${reloadToken}`" />
    <OntologyGraphView v-else-if="activeTab === 'egraph'" :key="`egraph:${reloadToken}`" />
    <ClassesView v-else-if="activeTab === 'classes'" :key="`classes:${reloadToken}`" />
    <RelationsView v-else-if="activeTab === 'relations'" :key="`relations:${reloadToken}`" />
    <EnumsView v-else-if="activeTab === 'enums'" :key="`enums:${reloadToken}`" />
    <ConceptsView v-else-if="activeTab === 'concepts'" :key="`concepts:${reloadToken}`" />
    <ProposalsView v-else :key="`proposals:${reloadToken}`" @refresh="onProposalsRefresh" />
  </div>
</template>

<style scoped>
.ontology {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.drift-banner {
  padding: 10px 14px;
  border: 1px solid var(--warning);
  border-radius: var(--r-md);
  background: var(--warning-soft);
}
.drift-banner__row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}
.drift-banner__warn {
  color: var(--warning);
}
.drift-banner__detail {
  margin-top: var(--sp-3);
  max-height: 320px;
  overflow-y: auto;
  font-size: var(--fs-caption);
}

.ontology__toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}
.ontology__version {
  font-size: var(--fs-caption);
  color: var(--text-2);
  margin-right: var(--sp-1);
}
.ontology__fmt {
  width: 170px;
}

.ontology__hint {
  font-size: var(--fs-body-sm);
  color: var(--text-2);
}
.ontology__hint--error {
  color: var(--danger);
}

.ontology__stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--sp-3);
}
.stat-box {
  padding: var(--sp-4);
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
}
.stat-box__number {
  font-size: var(--fs-num-xl);
  line-height: var(--lh-num-xl);
  font-weight: var(--fw-num-xl);
  font-variant-numeric: tabular-nums;
  color: var(--text-strong);
}
.stat-box__label {
  margin-top: var(--sp-1);
  font-size: var(--fs-caption);
  color: var(--text-3);
}
</style>
