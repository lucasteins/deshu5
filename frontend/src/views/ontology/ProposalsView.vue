<script setup lang="ts">
/**
 * 变更提案（F2.3，原 static/js/ontology.js 提案历史 + 漂移横幅审批）
 *
 * 列表展示提案状态与计数；点击展开差异明细；pending 提案可在此直接批准 / 驳回。
 * 批准 / 驳回后向上 emit refresh，由 OntologyView 刷新顶部总览与漂移横幅。
 */
import { onMounted, ref } from 'vue'
import { ElButton } from 'element-plus'
import { confirm, DsAsyncSection, toast } from '@/components'
import {
  approveProposal,
  fetchProposal,
  fetchProposals,
  rejectProposal,
  type OntologyDiff,
  type Proposal,
} from '@/api/ontology'
import OntologyDiffView from './OntologyDiff.vue'

const STATUS_BADGE: Record<string, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
}

const proposals = ref<Proposal[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const expandedId = ref<number | null>(null)
const expandedDiff = ref<OntologyDiff | null>(null)
const expandedLoading = ref(false)
const expandedError = ref<unknown>(null)
const actingId = ref<number | null>(null)

const emit = defineEmits<{ refresh: [] }>()

function statusText(s: string): string {
  return STATUS_BADGE[s] ?? s
}

function countsText(p: Proposal): string {
  const c = p.counts ?? {}
  return `+${c.classes_added ?? 0} 表 / -${c.classes_removed ?? 0} 表 / ` +
    `+${c.properties_added ?? 0} 列 / -${c.properties_removed ?? 0} 列 / ` +
    `~${c.properties_changed ?? 0} 列改 / ±${(c.relations_added ?? 0) + (c.relations_removed ?? 0)} 关系`
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchProposals()
    proposals.value = res.items ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

async function toggle(pid: number) {
  if (expandedId.value === pid) {
    expandedId.value = null
    expandedDiff.value = null
    return
  }
  expandedId.value = pid
  expandedDiff.value = null
  expandedLoading.value = true
  expandedError.value = null
  try {
    const res = await fetchProposal(pid)
    expandedDiff.value = res.diff
  } catch (e) {
    expandedError.value = e
  } finally {
    expandedLoading.value = false
  }
}

async function approve(p: Proposal) {
  const ok = await confirm.l1({
    title: '批准本体变更提案',
    message: `确认批准提案 #${p.id} 并生成为新的生效版本？`,
    confirmText: '批准更新',
  })
  if (!ok) return
  actingId.value = p.id
  try {
    const res = await approveProposal(p.id)
    toast.success(`已批准，生效版本 v${res.version}`)
    expandedId.value = null
    expandedDiff.value = null
    await load()
    emit('refresh')
  } catch (e) {
    toast.danger({ title: '批准失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    actingId.value = null
  }
}

async function reject(p: Proposal) {
  const ok = await confirm.l1({
    title: '驳回本体变更提案',
    message: `确认驳回提案 #${p.id} 并保持当前本体版本？`,
    confirmText: '驳回',
  })
  if (!ok) return
  actingId.value = p.id
  try {
    await rejectProposal(p.id)
    toast.success('已驳回，本体保持当前版本')
    expandedId.value = null
    expandedDiff.value = null
    await load()
    emit('refresh')
  } catch (e) {
    toast.danger({ title: '驳回失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    actingId.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="proposals">
    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="proposals.length === 0"
      skeleton="text"
      empty-title="暂无变更提案"
      empty-desc="执行「检测变更」或「手动重建」后会在此生成提案"
      @retry="load"
    >
      <div class="proposals__list">
        <div v-for="p in proposals" :key="p.id" class="proposal">
          <button type="button" class="proposal__head" @click="toggle(p.id)">
            <strong>#{{ p.id }}</strong>
            <span class="proposal__status">{{ statusText(p.status) }}</span>
            <span class="proposal__time">{{ p.created_at }}</span>
            <span class="proposal__counts">{{ countsText(p) }}</span>
          </button>

          <div v-if="expandedId === p.id" class="proposal__body">
            <div v-if="expandedLoading" class="proposal__state">加载差异明细…</div>
            <div v-else-if="expandedError" class="proposal__error">
              {{ expandedError instanceof Error ? expandedError.message : String(expandedError) }}
            </div>
            <template v-else>
              <OntologyDiffView :diff="expandedDiff" />
              <div v-if="p.status === 'pending'" class="proposal__actions">
                <ElButton type="primary" size="small" :loading="actingId === p.id" @click="approve(p)">批准</ElButton>
                <ElButton size="small" :loading="actingId === p.id" @click="reject(p)">驳回</ElButton>
              </div>
            </template>
          </div>
        </div>
      </div>
    </DsAsyncSection>
  </div>
</template>

<style scoped>
.proposals {
  display: flex;
  flex-direction: column;
}
.proposals__list {
  display: flex;
  flex-direction: column;
}
.proposal {
  border-bottom: 1px solid var(--line-subtle);
}
.proposal__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  width: 100%;
  padding: 9px 0;
  text-align: left;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-1);
  font-size: var(--fs-body-sm);
}
.proposal__head:hover {
  color: var(--text-strong);
}
.proposal__status {
  font-size: var(--fs-caption);
  color: var(--accent);
}
.proposal__time {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.proposal__counts {
  margin-left: auto;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.proposal__body {
  padding: var(--sp-2) 0 var(--sp-3);
}
.proposal__state,
.proposal__error {
  color: var(--text-2);
}
.proposal__error {
  color: var(--danger);
}
.proposal__actions {
  display: flex;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}
</style>
