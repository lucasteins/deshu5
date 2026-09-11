<script setup lang="ts">
/**
 * 设置弹窗（F2.5 · 对等旧实现 static/js/settings.js + #settings-modal）
 *
 * 三区：数据库配置（档位切换 / 主机信息 / 连通测试）→ LLM 配置（多 Provider 读写 / 测试 / 保存）→
 *       SQL 生成工作流预设（切换热生效）。
 * 以命令式 API（openSettings）自挂载到 body，SideRail 底部「⚙ 设置」入口调用。
 */
import { computed, onMounted, ref } from 'vue'
import { ElButton, ElDialog, ElInput, ElOption, ElSelect } from 'element-plus'
import { toast } from '@/components'
import {
  getDbSettings,
  getLlmSettings,
  getWorkflows,
  saveLlmSettings,
  switchDbProfile,
  switchWorkflow,
  testDb,
  testLlm,
  type DbSettings,
  type LlmSettings,
  type WorkflowSettings,
} from '@/api/settings'

const emit = defineEmits<{ close: [] }>()

const visible = ref(true)
function close() {
  visible.value = false
  emit('close')
}

const PROVIDER_LABELS: Record<string, string> = {
  kimi: 'Kimi',
  deepseek: 'DeepSeek',
}
function providerLabel(name: string): string {
  return PROVIDER_LABELS[name] ?? name
}

/* ==================== 数据库配置 ==================== */
const dbLoading = ref(false)
const db = ref<DbSettings | null>(null)
const dbForm = ref({ host: '', port: '', user: '', password: '' })
const dbProfile = ref('')
const dbNote = ref<{ text: string; ok: boolean } | null>(null)
const dbSwitching = ref(false)
const dbTestResult = ref<{ text: string; ok: boolean } | null>(null)
const dbTesting = ref(false)

const dbStatusText = computed(() => {
  const d = db.value
  if (!d) return '数据库：加载中…'
  const allOk = (d.databases ?? []).length > 0 && (d.profiles ?? []).find((p) => p.name === d.current_profile)?.connected
  return `数据库（MySQL）：${d.current_label} ${allOk ? '连接正常' : '存在连接异常'} @ ${d.host}:${d.port}`
})
const dbStatusOk = computed(() => {
  const d = db.value
  if (!d) return false
  return (d.profiles ?? []).find((p) => p.name === d.current_profile)?.connected ?? false
})

async function loadDb() {
  dbLoading.value = true
  try {
    const d = await getDbSettings()
    db.value = d
    dbForm.value = { host: String(d.host ?? ''), port: String(d.port ?? ''), user: d.user ?? '', password: '' }
    dbProfile.value = d.current_profile
  } catch (e) {
    dbNote.value = { text: `数据库配置加载失败: ${e instanceof Error ? e.message : String(e)}`, ok: false }
  } finally {
    dbLoading.value = false
  }
}

async function onSwitchProfile() {
  if (!dbProfile.value) return
  dbSwitching.value = true
  dbNote.value = { text: '切换中（缓存重载中）…', ok: false }
  try {
    const res = await switchDbProfile(dbProfile.value)
    dbProfile.value = res.current_profile
    const parts = (res.databases ?? []).map((d) => `${d.name}: ${d.connected ? '✓' : '✗ ' + (d.error || '')}`)
    dbNote.value = { text: `已切换到 ${res.current_label}（缓存已重载）`, ok: true }
    // 回读完整档位状态，刷新档位列表与连通性
    await loadDb()
    void parts
  } catch (e) {
    dbNote.value = { text: `切换失败: ${e instanceof Error ? e.message : String(e)}`, ok: false }
  } finally {
    dbSwitching.value = false
  }
}

async function onTestDb() {
  dbTesting.value = true
  dbTestResult.value = { text: '测试中…', ok: false }
  try {
    const res = await testDb({
      host: dbForm.value.host.trim(),
      port: dbForm.value.port.trim(),
      user: dbForm.value.user.trim(),
      password: dbForm.value.password,
    })
    dbTestResult.value = res.ok
      ? { text: `✓ 连接成功（${res.elapsed_ms}ms）MySQL ${res.server_version ?? ''}`, ok: true }
      : { text: `✗ 连接失败: ${res.error ?? '未知错误'}`, ok: false }
  } catch (e) {
    dbTestResult.value = { text: `✗ 请求失败: ${e instanceof Error ? e.message : String(e)}`, ok: false }
  } finally {
    dbTesting.value = false
  }
}

/* ==================== LLM 配置 ==================== */
const llm = ref<LlmSettings | null>(null)
const provider = ref('')
const llmForm = ref({ api_url: '', model: '', api_key: '', temperature: '' })
const llmTestResult = ref<{ text: string; ok: boolean } | null>(null)
const llmTesting = ref(false)
const llmSaving = ref(false)

const providerOptions = computed(() =>
  Object.keys(llm.value?.providers ?? {}).map((name) => ({ value: name, label: providerLabel(name) })),
)

const activeProvider = computed(() => llm.value?.providers?.[provider.value])

const apiKeyPlaceholder = computed(() => {
  const p = activeProvider.value
  if (!p) return '留空表示不修改'
  return p.has_key ? `当前已配置（${p.api_key_masked}），留空表示不修改` : '未配置，请粘贴 API Key'
})

const llmStatusText = computed(() => {
  if (!llm.value) return '当前生效：-'
  const a = llm.value.active
  const p = llm.value.providers?.[a]
  return `当前生效：${providerLabel(a)} / ${p?.model ?? '-'}${p?.has_key ? '' : '（未配置 Key）'}`
})

async function loadLlm() {
  try {
    const data = await getLlmSettings()
    llm.value = data
    provider.value = data.active
    fillLlmForm(data.active)
  } catch (e) {
    toast.danger({ title: '配置加载失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

function fillLlmForm(name: string) {
  const p = llm.value?.providers?.[name]
  llmForm.value = {
    api_url: p?.api_url ?? '',
    model: p?.model ?? '',
    api_key: '',
    temperature: p?.temperature === null || p?.temperature === undefined ? '' : String(p.temperature),
  }
}

function onProviderChange() {
  fillLlmForm(provider.value)
}

function collectLlmPayload() {
  const t = llmForm.value.temperature.trim()
  return {
    provider: provider.value,
    api_url: llmForm.value.api_url.trim(),
    model: llmForm.value.model.trim(),
    api_key: llmForm.value.api_key.trim(),
    temperature: t === '' ? null : parseFloat(t),
  }
}

async function onTestLlm() {
  llmTesting.value = true
  llmTestResult.value = { text: '测试中…', ok: false }
  try {
    const res = await testLlm(collectLlmPayload())
    llmTestResult.value = res.ok
      ? { text: `✓ 连接成功（${res.elapsed_ms}ms）模型 ${res.model ?? ''}${res.reasoning ? '（推理模型）' : ''}`, ok: true }
      : { text: `✗ 连接失败: ${res.error ?? '未知错误'}`, ok: false }
  } catch (e) {
    llmTestResult.value = { text: `✗ 请求失败: ${e instanceof Error ? e.message : String(e)}`, ok: false }
  } finally {
    llmTesting.value = false
  }
}

async function onSaveLlm() {
  llmSaving.value = true
  try {
    const payload = { ...collectLlmPayload(), set_active: true }
    const data = await saveLlmSettings(payload)
    llm.value = data
    provider.value = data.active
    fillLlmForm(data.active)
    toast.success(`已保存并启用：${providerLabel(data.active)}`)
  } catch (e) {
    toast.danger({ title: '保存失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    llmSaving.value = false
  }
}

/* ==================== 工作流预设 ==================== */
const workflows = ref<WorkflowSettings | null>(null)
const workflowName = ref('')
const workflowNote = ref<{ text: string; ok: boolean } | null>(null)
const workflowSwitching = ref(false)

function describeWorkflow(presets: WorkflowSettings['presets'], current: string): string {
  const p = (presets ?? []).find((x) => x.name === current)
  if (!p) return ''
  const keys = Object.keys(p.overrides ?? {})
  const diff = keys.length ? `差异键: ${keys.join(', ')}` : '与内置默认行为一致'
  return `${p.note ? p.note + '；' : ''}${diff}`
}

async function loadWorkflows() {
  try {
    const data = await getWorkflows()
    workflows.value = data
    workflowName.value = data.current
    workflowNote.value = { text: describeWorkflow(data.presets, data.current), ok: false }
  } catch (e) {
    workflowNote.value = { text: `工作流预设加载失败: ${e instanceof Error ? e.message : String(e)}`, ok: false }
  }
}

async function onWorkflowChange() {
  if (!workflowName.value) return
  workflowSwitching.value = true
  workflowNote.value = { text: '切换中…', ok: false }
  try {
    await switchWorkflow(workflowName.value)
    workflowNote.value = { text: `已切换到 ${workflowName.value}（下次生成起生效）`, ok: true }
  } catch (e) {
    workflowNote.value = { text: `切换失败: ${e instanceof Error ? e.message : String(e)}`, ok: false }
  } finally {
    workflowSwitching.value = false
  }
}

onMounted(() => {
  void loadDb()
  void loadLlm()
  void loadWorkflows()
})
</script>

<template>
  <ElDialog
    v-model="visible"
    title="设置 · 数据库 / LLM 配置"
    width="620px"
    class="settings"
    :close-on-click-modal="false"
    @closed="emit('close')"
  >
    <div class="settings__body">
      <!-- ==================== 数据库配置 ==================== -->
      <div class="settings__status" :class="dbStatusOk ? 'is-ok' : 'is-bad'">{{ dbStatusText }}</div>

      <div class="settings__field">
        <label class="settings__label">数据库档位（切换后缓存自动重载，无需重启）</label>
        <div class="settings__row">
          <ElSelect v-model="dbProfile" :loading="dbLoading" style="flex: 1" @change="onSwitchProfile">
            <ElOption
              v-for="p in db?.profiles ?? []"
              :key="p.name"
              :value="p.name"
              :label="`${p.label}（${p.business} / ${p.governance}）`"
            />
          </ElSelect>
          <ElButton :loading="dbSwitching" @click="onSwitchProfile">切换</ElButton>
        </div>
        <div v-if="dbNote" class="settings__note" :class="dbNote.ok ? 'is-ok' : 'is-bad'">
          {{ dbNote.text }}
        </div>
      </div>

      <div class="settings__field">
        <label class="settings__label">MySQL 主机</label>
        <ElInput v-model="dbForm.host" placeholder="localhost" />
      </div>

      <div class="settings__field">
        <label class="settings__label">端口 / 用户名</label>
        <div class="settings__row">
          <ElInput v-model="dbForm.port" placeholder="3306" style="flex: 1" />
          <ElInput v-model="dbForm.user" placeholder="root" style="flex: 1" />
        </div>
      </div>

      <div class="settings__field">
        <label class="settings__label">密码（留空表示沿用当前配置）</label>
        <ElInput v-model="dbForm.password" type="password" placeholder="留空表示不修改" show-password />
      </div>

      <div class="settings__field">
        <div class="settings__row">
          <ElButton :loading="dbTesting" @click="onTestDb">测试数据库连接</ElButton>
          <span v-if="dbTestResult" class="settings__test" :class="dbTestResult.ok ? 'is-ok' : 'is-bad'">
            {{ dbTestResult.text }}
          </span>
        </div>
        <div v-if="db" class="settings__databases">
          <span v-for="d in db.databases" :key="d.key" class="settings__db-item">
            {{ d.name }}
          </span>
        </div>
      </div>

      <hr class="settings__hr" />

      <!-- ==================== LLM 配置 ==================== -->
      <div class="settings__status">{{ llmStatusText }}</div>

      <div class="settings__field">
        <label class="settings__label">服务商</label>
        <ElSelect v-model="provider" style="width: 100%" @change="onProviderChange">
          <ElOption v-for="o in providerOptions" :key="o.value" :value="o.value" :label="o.label" />
        </ElSelect>
      </div>

      <div class="settings__field">
        <label class="settings__label">API 地址</label>
        <ElInput v-model="llmForm.api_url" placeholder="https://api.deepseek.com" />
      </div>

      <div class="settings__field">
        <label class="settings__label">API Key</label>
        <ElInput
          v-model="llmForm.api_key"
          type="password"
          :placeholder="apiKeyPlaceholder"
          show-password
        />
      </div>

      <div class="settings__field">
        <label class="settings__label">模型</label>
        <ElInput v-model="llmForm.model" placeholder="deepseek-v4-flash" />
      </div>

      <div class="settings__field">
        <label class="settings__label">Temperature（可选，留空用服务端默认）</label>
        <ElInput v-model="llmForm.temperature" placeholder="如 0.2" />
      </div>

      <hr class="settings__hr" />

      <!-- ==================== 工作流预设 ==================== -->
      <div class="settings__field">
        <label class="settings__label">SQL 生成工作流预设（切换即时生效，无需重启）</label>
        <ElSelect
          v-model="workflowName"
          style="width: 100%"
          :loading="workflowSwitching"
          @change="onWorkflowChange"
        >
          <ElOption v-for="p in workflows?.presets ?? []" :key="p.name" :value="p.name" :label="p.name" />
        </ElSelect>
      </div>
      <div v-if="workflowNote" class="settings__note" :class="workflowNote.ok ? 'is-ok' : 'is-muted'">
        {{ workflowNote.text }}
      </div>

      <div v-if="llmTestResult" class="settings__test" :class="llmTestResult.ok ? 'is-ok' : 'is-bad'">
        {{ llmTestResult.text }}
      </div>
    </div>

    <template #footer>
      <ElButton :loading="llmTesting" @click="onTestLlm">测试连接</ElButton>
      <ElButton type="primary" :loading="llmSaving" @click="onSaveLlm">保存并启用</ElButton>
      <ElButton @click="close">取消</ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.settings__body {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  max-height: 62vh;
  overflow-y: auto;
  padding-right: var(--sp-2);
}
.settings__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.settings__label {
  font-size: var(--fs-caption);
  font-weight: var(--fw-h3);
  color: var(--text-2);
}
.settings__row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.settings__status {
  font-size: var(--fs-body-sm);
  color: var(--text-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
}
.settings__status.is-ok {
  color: var(--success);
}
.settings__status.is-bad {
  color: var(--danger);
}
.settings__note {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.settings__note.is-ok {
  color: var(--success);
}
.settings__note.is-bad {
  color: var(--danger);
}
.settings__note.is-muted {
  color: var(--text-3);
}
.settings__test {
  font-size: var(--fs-caption);
}
.settings__test.is-ok {
  color: var(--success);
}
.settings__test.is-bad {
  color: var(--danger);
}
.settings__databases {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.settings__db-item {
  padding: 2px 8px;
  background: var(--surface-2);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-full);
  font-family: var(--font-mono);
}
.settings__hr {
  border: none;
  border-top: 1px solid var(--line-subtle);
  margin: var(--sp-1) 0;
}
</style>
