<script setup lang="ts">
/**
 * 实体详情（F2.3，复用给「实体视图」与「实体图谱」）
 *
 * - 展示成员物理表（含在库状态）+ 实体间关系
 * - 映射编辑：中文名 / 层级 / 成员表（每行一个）/ 描述；保存走 PUT /entity-defs/<name>
 * - AI 生成描述：POST /entity-defs/<name>/describe（apply=false，回填编辑框，人工确认后保存）
 */
import { onMounted, ref } from 'vue'
import { ElButton, ElInput, ElOption, ElSelect } from 'element-plus'
import { toast } from '@/components'
import {
  describeEntity,
  fetchOntologyEntity,
  upsertEntityDef,
  type OntologyEntityDef,
  type OntologyLayer,
} from '@/api/ontology'

const props = defineProps<{ name: string }>()
const emit = defineEmits<{ close: [] }>()

const LAYER_BADGE: Record<string, string> = {
  master: '主数据',
  business: '业务数据',
  report: '统计报表',
}
const LAYERS: OntologyLayer[] = ['master', 'business', 'report']

const loading = ref(false)
const error = ref<unknown>(null)
const detail = ref<Awaited<ReturnType<typeof fetchOntologyEntity>> | null>(null)

const label = ref('')
const layer = ref<OntologyLayer>('business')
const membersText = ref('')
const comment = ref('')
const saving = ref(false)
const describing = ref(false)

function fillEditor(def: OntologyEntityDef | null) {
  const e = detail.value?.entity
  const source = def ?? null
  label.value = source?.label ?? e?.label ?? ''
  layer.value = (source?.layer as OntologyLayer) ?? (e?.layer as OntologyLayer) ?? 'business'
  membersText.value = (source?.member_tables ?? e?.member_tables ?? []).join('\n')
  comment.value = source?.comment ?? e?.comment ?? ''
}

async function load() {
  loading.value = true
  error.value = null
  try {
    detail.value = await fetchOntologyEntity(props.name)
    fillEditor(detail.value.def)
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

function layerText(l: string): string {
  return LAYER_BADGE[l] ?? l
}

async function save() {
  saving.value = true
  try {
    const memberTables = membersText.value
      .split(/[\s,，、]+/)
      .map((s) => s.trim())
      .filter(Boolean)
    const res = await upsertEntityDef(props.name, {
      label: label.value.trim(),
      layer: layer.value,
      member_tables: memberTables,
      comment: comment.value.trim(),
    })
    toast.success(res.note || '映射已保存')
  } catch (e) {
    toast.danger({ title: '保存失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    saving.value = false
  }
}

async function aiDescribe() {
  describing.value = true
  try {
    const res = await describeEntity(props.name, false)
    comment.value = res.comment || ''
    toast.success('已生成描述并回填编辑框，请确认后点击「保存映射」')
  } catch (e) {
    toast.danger({ title: 'AI 生成描述失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    describing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="entity-detail">
    <div class="entity-detail__bar">
      <h3 class="entity-detail__title" v-if="detail">
        {{ detail.entity.label || detail.entity.name }}
        <span class="entity-detail__sub">{{ detail.entity.name }} · {{ layerText(detail.entity.layer) }}</span>
      </h3>
      <ElButton size="small" text @click="emit('close')">关闭详情</ElButton>
    </div>

    <div v-if="loading" class="entity-detail__loading">加载实体详情…</div>
    <div v-else-if="error" class="entity-detail__error">
      {{ error instanceof Error ? error.message : String(error) }}
      <ElButton size="small" @click="load">重试</ElButton>
    </div>
    <template v-else-if="detail">
      <p v-if="detail.entity.comment" class="entity-detail__comment">{{ detail.entity.comment }}</p>
      <p v-else class="entity-detail__hint">暂无描述（可在下方映射编辑中补充，或点击「AI 生成描述」自动生成）</p>

      <section class="entity-detail__section">
        <h4>成员物理表</h4>
        <table class="entity-detail__table">
          <thead>
            <tr><th>成员物理表</th><th>中文名</th><th>在库</th></tr>
          </thead>
          <tbody>
            <tr v-for="m in detail.members" :key="m.table">
              <td><code>{{ m.table }}</code></td>
              <td>{{ m.label }}</td>
              <td>{{ m.exists ? '✔' : '✘ 不在库' }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="detail.entity_relations.length" class="entity-detail__section">
        <h4>实体间关系</h4>
        <ul class="entity-detail__rels">
          <li v-for="(r, i) in detail.entity_relations" :key="i">
            {{ r.from_entity }} ↔ {{ r.to_entity }}
            <span class="entity-detail__hint">
              （{{ r.member_relations.length }} 条表级关系<template v-if="r.scenarios.length">；场景：{{ r.scenarios.slice(0, 3).join('、') }}</template>）
            </span>
          </li>
        </ul>
      </section>

      <section class="entity-detail__section">
        <h4>映射编辑 <span class="entity-detail__hint">（保存后需手动重建并审批生效）</span></h4>
        <div class="entity-detail__form-row">
          <ElInput v-model="label" class="entity-detail__label" placeholder="实体中文名" />
          <ElSelect v-model="layer" class="entity-detail__layer">
            <ElOption v-for="l in LAYERS" :key="l" :label="LAYER_BADGE[l]" :value="l" />
          </ElSelect>
        </div>
        <ElInput
          v-model="membersText"
          class="entity-detail__members"
          type="textarea"
          :rows="5"
          placeholder="成员物理表，每行一个"
        />
        <ElInput
          v-model="comment"
          class="entity-detail__comment-input"
          type="textarea"
          :rows="3"
          placeholder="实体描述（业务含义 / 数据范围 / 典型分析场景），可点击「AI 生成描述」自动生成后修改"
        />
        <div class="entity-detail__actions">
          <ElButton type="primary" size="small" :loading="saving" @click="save">保存映射</ElButton>
          <ElButton size="small" :loading="describing" @click="aiDescribe">AI 生成描述</ElButton>
          <span class="entity-detail__hint">保存写入映射定义表，经「手动重建 → 提案审批」后进入生效版本</span>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.entity-detail {
  margin-top: var(--sp-3);
  padding: var(--sp-4);
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
}
.entity-detail__bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-3);
}
.entity-detail__title {
  margin: 0;
  font-size: var(--fs-h3);
  font-weight: var(--fw-h3);
  color: var(--text-strong);
}
.entity-detail__sub {
  font-size: var(--fs-caption);
  font-weight: 400;
  color: var(--text-3);
  margin-left: var(--sp-2);
}
.entity-detail__comment {
  margin: var(--sp-2) 0 0;
  color: var(--text-1);
  line-height: var(--lh-body);
}
.entity-detail__hint {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.entity-detail__loading,
.entity-detail__error {
  padding: var(--sp-4) 0;
  color: var(--text-2);
}
.entity-detail__error {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  color: var(--danger);
}
.entity-detail__section {
  margin-top: var(--sp-4);
}
.entity-detail__section h4 {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-body);
  color: var(--text-2);
}
.entity-detail__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-body-sm);
}
.entity-detail__table th,
.entity-detail__table td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--line-subtle);
  text-align: left;
}
.entity-detail__table th {
  color: var(--text-3);
  font-weight: 500;
  border-bottom-color: var(--line);
}
.entity-detail__table code {
  font-family: var(--font-mono);
  font-size: 12px;
}
.entity-detail__rels {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  font-size: var(--fs-body-sm);
  color: var(--text-1);
}
.entity-detail__form-row {
  display: flex;
  gap: var(--sp-2);
  flex-wrap: wrap;
  margin-bottom: var(--sp-2);
}
.entity-detail__label {
  max-width: 240px;
}
.entity-detail__layer {
  width: 150px;
}
.entity-detail__members,
.entity-detail__comment-input {
  margin-top: var(--sp-2);
}
.entity-detail__members :deep(textarea),
.entity-detail__comment-input :deep(textarea) {
  font-family: var(--font-mono);
}
.entity-detail__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  margin-top: var(--sp-2);
}
</style>
