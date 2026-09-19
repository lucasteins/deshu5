<script setup lang="ts">
/**
 * 技能对话（训练模式二级页签）——选择 skill → 与该 skill 能力绑定的 LLM 多轮聊天。
 *
 * 左栏：skill 列表卡（名称 + 描述摘要，选中高亮）+ 该 skill 历史会话（新建 / 载入 / 删除）；
 * 主区：示例命令 chips（点击直发；右上「刷新示例」走 LLM 重生成，loading 态）、
 *       消息流（user 右 / assistant 左气泡，assistant 用 renderMarkdown 渲染，
 *       流式期间 DsThinkingPanel 思考流 + 正文增量，useAutoScroll 跟底）、
 *       底部输入框（Enter 发送 / Shift+Enter 换行，流式中禁用发送）。
 * SSE：POST /api/skillchat/chat/stream（{thinking} / {content} 增量帧 + done / error 终帧），
 * 卸载时 abort。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElButton } from 'element-plus'
import { useSSE } from '@/composables'
import {
  DsEmpty,
  DsError,
  DsThinkingPanel,
  confirm,
  toast,
  useAutoScroll,
  type ThinkingStream,
} from '@/components'
import {
  createSession,
  deleteSession,
  fetchExamples,
  fetchSessionMessages,
  fetchSessions,
  fetchSkills,
  refreshExamples,
  type SkillChatSession,
  type SkillChatSkill,
  type SkillChatStreamEvent,
  type SkillChatStreamResult,
} from '@/api/skillchat'
import { renderMarkdown } from '@/utils/markdown'

/* ================= 技能列表 ================= */
const skills = ref<SkillChatSkill[]>([])
const skillsLoading = ref(false)
const skillsError = ref('')
const currentSkill = ref('')

const currentSkillInfo = computed(
  () => skills.value.find((s) => s.name === currentSkill.value) ?? null,
)

async function loadSkills() {
  skillsLoading.value = true
  skillsError.value = ''
  try {
    const res = await fetchSkills()
    if (!res.success) throw new Error('技能列表加载失败')
    skills.value = res.items || []
  } catch (e) {
    skillsError.value = e instanceof Error ? e.message : '技能列表加载失败'
  } finally {
    skillsLoading.value = false
  }
}
onMounted(loadSkills)

function selectSkill(name: string) {
  if (name === currentSkill.value) return
  if (sending.value) {
    toast.warning('正在生成回复，请先等待完成或停止生成')
    return
  }
  currentSkill.value = name
  activeSessionId.value = ''
  messages.value = []
  thinkingText.value = ''
  void loadSessions()
  void loadExamples()
}

/* ================= 示例命令 ================= */
const examples = ref<string[]>([])
const examplesLoading = ref(false)
const examplesRefreshing = ref(false)

async function loadExamples() {
  if (!currentSkill.value) return
  examplesLoading.value = true
  try {
    const res = await fetchExamples(currentSkill.value)
    if (!res.success) throw new Error('示例加载失败')
    examples.value = res.examples || []
  } catch (e) {
    examples.value = []
    toast.warning(e instanceof Error ? e.message : '示例加载失败')
  } finally {
    examplesLoading.value = false
  }
}

async function onRefreshExamples() {
  if (!currentSkill.value || examplesRefreshing.value) return
  examplesRefreshing.value = true
  try {
    const res = await refreshExamples(currentSkill.value)
    if (!res.success) throw new Error('刷新示例失败')
    examples.value = res.examples || []
    toast.success(`示例已刷新（${examples.value.length} 条）`)
  } catch (e) {
    toast.danger({ title: '刷新示例失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    examplesRefreshing.value = false
  }
}

/* ================= 会话 ================= */
const sessions = ref<SkillChatSession[]>([])
const sessionsLoading = ref(false)
const activeSessionId = ref('')

async function loadSessions() {
  if (!currentSkill.value) return
  sessionsLoading.value = true
  try {
    const res = await fetchSessions(currentSkill.value)
    if (!res.success) throw new Error('会话列表加载失败')
    sessions.value = res.items || []
  } catch (e) {
    sessions.value = []
    toast.warning(e instanceof Error ? e.message : '会话列表加载失败')
  } finally {
    sessionsLoading.value = false
  }
}

async function onNewSession() {
  if (!currentSkill.value || sending.value) return
  try {
    const res = await createSession(currentSkill.value)
    if (!res.success || !res.session) throw new Error('创建会话失败')
    sessions.value = [res.session, ...sessions.value]
    activeSessionId.value = res.session.id
    messages.value = []
    thinkingText.value = ''
  } catch (e) {
    toast.danger({ title: '创建会话失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function selectSession(s: SkillChatSession) {
  if (s.id === activeSessionId.value) return
  if (sending.value) {
    toast.warning('正在生成回复，请先等待完成或停止生成')
    return
  }
  activeSessionId.value = s.id
  messages.value = []
  thinkingText.value = ''
  messagesLoading.value = true
  try {
    const res = await fetchSessionMessages(s.id)
    if (!res.success) throw new Error('会话加载失败')
    messages.value = (res.messages || []).map((m) => ({
      key: `m${m.id}`,
      role: m.role === 'user' ? ('user' as const) : ('assistant' as const),
      content: m.content,
    }))
    void nextTick(() => jumpToBottom())
  } catch (e) {
    toast.danger({ title: '加载会话失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    messagesLoading.value = false
  }
}

async function onDeleteSession(s: SkillChatSession) {
  if (sending.value) {
    toast.warning('正在生成回复，请先等待完成或停止生成')
    return
  }
  const ok = await confirm.l1({
    title: '删除会话',
    message: `确认删除会话「${s.title || '未命名会话'}」？此操作不可恢复。`,
    danger: true,
  })
  if (!ok) return
  try {
    const res = await deleteSession(s.id)
    if (!res.success) throw new Error('删除失败')
    if (activeSessionId.value === s.id) {
      activeSessionId.value = ''
      messages.value = []
    }
    sessions.value = sessions.value.filter((x) => x.id !== s.id)
    toast.success('已删除')
  } catch (e) {
    toast.danger({ title: '删除失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

function fmtTime(s?: string): string {
  return s ? s.slice(0, 16) : ''
}

/* ================= 消息流与 SSE ================= */
interface UiMessage {
  key: string
  role: 'user' | 'assistant'
  content: string
  /** 流式中（思考面板 + 正文增量 + 光标） */
  streaming?: boolean
  /** SSE 错误终态 */
  failed?: boolean
}

const messages = ref<UiMessage[]>([])
const messagesLoading = ref(false)
const input = ref('')
const sending = ref(false)
/** 当前流式回复的思考增量（仅流式期间展示，完成后收起） */
const thinkingText = ref('')
let msgSeq = 0

const sse = useSSE<SkillChatStreamEvent, SkillChatStreamResult>('/skillchat/chat/stream', {
  onEvent: (evt) => {
    if ('thinking' in evt && typeof evt.thinking === 'string') {
      thinkingText.value += evt.thinking
      scrollAfterTick()
    } else if ('content' in evt && typeof evt.content === 'string') {
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && last.streaming) last.content += evt.content
      scrollAfterTick()
    }
  },
})

const thinkingStreams = computed<ThinkingStream[]>(() =>
  thinkingText.value ? [{ phase: 'reasoning', text: thinkingText.value }] : [],
)

const { containerRef, showJump, notify, jumpToBottom } = useAutoScroll()
function scrollAfterTick() {
  void nextTick(() => notify())
}

/** 无当前会话时先创建（示例点击直发 / 首次输入均走此路径） */
async function ensureSession(): Promise<string | null> {
  if (activeSessionId.value) return activeSessionId.value
  try {
    const res = await createSession(currentSkill.value)
    if (!res.success || !res.session) throw new Error('创建会话失败')
    sessions.value = [res.session, ...sessions.value]
    activeSessionId.value = res.session.id
    return res.session.id
  } catch (e) {
    toast.danger({ title: '创建会话失败', desc: e instanceof Error ? e.message : String(e) })
    return null
  }
}

async function sendMessage(raw: string) {
  const content = raw.trim()
  if (!content) {
    toast.warning('请输入消息')
    return
  }
  if (!currentSkill.value) {
    toast.warning('请先选择技能')
    return
  }
  if (sending.value) return

  const sid = await ensureSession()
  if (!sid) return

  input.value = ''
  thinkingText.value = ''
  messages.value.push({ key: `u${++msgSeq}`, role: 'user', content })
  messages.value.push({ key: `a${++msgSeq}`, role: 'assistant', content: '', streaming: true })
  scrollAfterTick()

  sending.value = true
  try {
    await sse.start({ skill: currentSkill.value, session_id: sid, message: content })
  } finally {
    sending.value = false
    const last = messages.value[messages.value.length - 1]
    if (last?.streaming) last.streaming = false
  }

  const last = messages.value[messages.value.length - 1]
  if (sse.status.value === 'done') {
    // 以 done 帧完整内容为准（与增量拼接等价，兜底丢帧）
    if (last && sse.result.value?.content) last.content = sse.result.value.content
    // 标题 / updated_at / message_count 由后端更新，重拉列表
    void loadSessions()
  } else if (sse.status.value === 'error') {
    if (last) last.failed = true
    toast.danger({ title: '回复生成失败', desc: sse.error.value?.message || '流式响应中断' })
  }
  // aborted：保留已生成的部分内容
  scrollAfterTick()
}

function sendExample(ex: string) {
  if (sending.value) return
  void sendMessage(ex)
}

function submitInput() {
  void sendMessage(input.value)
}

/** Enter 发送 / Shift+Enter 换行；IME 组词中的 Enter（确认候选）不触发发送 */
function onEnterKey(e: KeyboardEvent) {
  if (e.isComposing) return
  e.preventDefault()
  submitInput()
}

onBeforeUnmount(() => sse.abort())
</script>

<template>
  <div class="skillchat">
    <!-- 左栏：技能 + 历史会话 -->
    <aside class="sc-side">
      <div class="sc-card">
        <div class="sc-card-hd">
          <h3>技能</h3>
          <span class="meta">{{ skills.length }} 个</span>
        </div>
        <div class="sc-card-bd skill-list">
          <div v-if="skillsLoading" class="sc-hint">加载中…</div>
          <DsError
            v-else-if="skillsError"
            variant="module"
            title="技能列表加载失败"
            :reason="skillsError"
            @retry="loadSkills"
          />
          <div v-else-if="!skills.length" class="sc-hint">未发现可用技能（skills/ 目录为空）</div>
          <button
            v-for="s in skills"
            :key="s.name"
            type="button"
            class="skill-item"
            :class="{ active: s.name === currentSkill }"
            @click="selectSkill(s.name)"
          >
            <span class="skill-title">{{ s.title || s.name }}</span>
            <span class="skill-name">{{ s.name }}</span>
            <span v-if="s.description" class="skill-desc">{{ s.description }}</span>
          </button>
        </div>
      </div>

      <div v-if="currentSkill" class="sc-card">
        <div class="sc-card-hd">
          <h3>历史会话</h3>
          <button class="btn-sm" type="button" :disabled="sending" @click="onNewSession">+ 新建会话</button>
        </div>
        <div class="sc-card-bd session-list">
          <div v-if="sessionsLoading" class="sc-hint">加载中…</div>
          <div v-else-if="!sessions.length" class="sc-hint">暂无历史会话，点击「新建会话」或直接发送消息开始</div>
          <div
            v-for="s in sessions"
            :key="s.id"
            class="session-item"
            :class="{ active: s.id === activeSessionId }"
            @click="selectSession(s)"
          >
            <div class="session-main">
              <div class="session-title">{{ s.title || '未命名会话' }}</div>
              <div class="session-meta">{{ fmtTime(s.updated_at) }} · {{ s.message_count ?? 0 }} 条</div>
            </div>
            <button class="session-del" type="button" title="删除会话" @click.stop="onDeleteSession(s)">✕</button>
          </div>
        </div>
      </div>
    </aside>

    <!-- 主区 -->
    <section class="sc-main">
      <div v-if="!currentSkill" class="sc-card sc-empty-wrap">
        <DsEmpty
          type="no-data"
          title="选择一个技能开始对话"
          desc="从左侧选择技能后，可查看示例命令，并与该技能能力绑定的模型多轮对话"
        />
      </div>

      <template v-else>
        <!-- 示例命令 -->
        <div class="sc-card">
          <div class="sc-card-hd">
            <h3>示例命令</h3>
            <button class="btn-sm" type="button" :disabled="examplesRefreshing" @click="onRefreshExamples">
              {{ examplesRefreshing ? '生成中…' : '刷新示例' }}
            </button>
          </div>
          <div class="sc-card-bd">
            <div v-if="examplesLoading" class="sc-hint">加载中…</div>
            <div v-else-if="!examples.length" class="sc-hint">暂无示例，可点击右上「刷新示例」由 LLM 生成</div>
            <div v-else class="example-chips">
              <button
                v-for="(ex, i) in examples"
                :key="i"
                type="button"
                class="example-chip"
                :disabled="sending"
                @click="sendExample(ex)"
              >
                {{ ex }}
              </button>
            </div>
          </div>
        </div>

        <!-- 消息流 + 输入 -->
        <div class="sc-card msg-card">
          <div ref="containerRef" class="msg-list">
            <div v-if="messagesLoading" class="sc-hint">加载中…</div>
            <template v-else-if="messages.length">
              <div v-for="m in messages" :key="m.key" class="msg-row" :class="m.role">
                <div class="msg-bubble">
                  <template v-if="m.role === 'assistant'">
                    <div v-if="m.streaming && thinkingText" class="msg-thinking">
                      <DsThinkingPanel :entries="[]" :streams="thinkingStreams" />
                    </div>
                    <div v-if="m.content" class="doc-md" v-html="renderMarkdown(m.content)" />
                    <span v-if="m.streaming" class="msg-caret" />
                    <div v-if="m.failed" class="msg-failed">生成失败，请重试</div>
                  </template>
                  <template v-else>{{ m.content }}</template>
                </div>
              </div>
            </template>
            <div v-else class="sc-hint msg-empty-hint">点击上方示例，或输入消息开始对话</div>
            <button v-if="showJump" class="jump-btn" type="button" @click="jumpToBottom">有新内容 ↓</button>
          </div>

          <div class="input-bar">
            <textarea
              v-model="input"
              class="msg-input"
              rows="2"
              spellcheck="false"
              :placeholder="`向「${currentSkillInfo?.title || currentSkill}」提问…`"
              @keydown.enter.exact="onEnterKey"
            />
            <div class="input-actions">
              <span class="hint">Enter 发送 · Shift+Enter 换行</span>
              <span class="spacer" />
              <ElButton v-if="sending" @click="sse.abort()">停止生成</ElButton>
              <ElButton type="primary" :disabled="sending || !input.trim()" @click="submitInput">发送</ElButton>
            </div>
          </div>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.skillchat {
  height: 100%;
  display: flex;
  gap: 16px;
  overflow: hidden;
}

/* ---- 卡片基座（对齐 QaView / TrainingView） ---- */
.sc-card {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  box-shadow: var(--e1);
  overflow: hidden;
}
.sc-card-hd {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-subtle);
}
.sc-card-hd h3 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}
.sc-card-hd .meta {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-3);
}
.sc-card-hd .btn-sm {
  margin-left: auto;
}
.sc-card-bd {
  padding: 12px 14px;
}
.sc-hint {
  padding: 8px 2px;
  font-size: 12.5px;
  color: var(--text-3);
  line-height: 20px;
}
.btn-sm {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 28px;
  padding: 0 10px;
  border-radius: var(--r-sm);
  border: 1px solid var(--line);
  background: var(--surface-1);
  color: var(--text-1);
  font-size: 12.5px;
  cursor: pointer;
}
.btn-sm:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--line-strong);
}
.btn-sm:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ---- 左栏 ---- */
.sc-side {
  width: 320px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}
.sc-side > * {
  flex: none;
}

/* 技能列表 */
.skill-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skill-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  text-align: left;
  padding: 10px 12px;
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  cursor: pointer;
  transition: border-color var(--dur-fast), background var(--dur-fast);
}
.skill-item:hover {
  border-color: var(--accent-line);
}
.skill-item.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.skill-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-strong);
}
.skill-name {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-3);
}
.skill-desc {
  margin-top: 2px;
  font-size: 12.5px;
  line-height: 18px;
  color: var(--text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 会话列表 */
.session-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  cursor: pointer;
  transition: background var(--dur-fast);
}
.session-item:hover {
  background: var(--surface-2);
}
.session-item.active {
  background: var(--accent-soft);
  border-color: var(--accent-line);
}
.session-main {
  flex: 1;
  min-width: 0;
}
.session-title {
  font-size: 13px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-meta {
  margin-top: 2px;
  font-size: 11.5px;
  color: var(--text-3);
}
.session-del {
  flex: none;
  width: 22px;
  height: 22px;
  border: none;
  background: none;
  border-radius: var(--r-xs);
  color: var(--text-3);
  font-size: 12px;
  cursor: pointer;
  visibility: hidden;
}
.session-item:hover .session-del {
  visibility: visible;
}
.session-del:hover {
  color: var(--danger);
  background: var(--danger-soft);
}

/* ---- 主区 ---- */
.sc-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
}
.sc-main > * {
  flex: none;
}
.sc-empty-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 示例命令 */
.example-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.example-chip {
  max-width: 100%;
  padding: 5px 12px;
  border-radius: var(--r-full);
  border: 1px solid var(--line);
  background: var(--surface-1);
  font-size: 12.5px;
  color: var(--text-1);
  text-align: left;
  cursor: pointer;
  transition: border-color var(--dur-fast), background var(--dur-fast), color var(--dur-fast);
}
.example-chip:hover:not(:disabled) {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}
.example-chip:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* 消息流 */
.msg-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.msg-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  position: relative;
  display: flex;
  flex-direction: column;
}
.msg-empty-hint {
  text-align: center;
  padding: 40px 0;
}
.msg-row {
  display: flex;
  margin-bottom: 12px;
}
.msg-row.user {
  justify-content: flex-end;
}
.msg-bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: var(--r-md);
  font-size: 14px;
  line-height: 24px;
  word-break: break-word;
}
.msg-row.user .msg-bubble {
  background: var(--accent);
  color: var(--text-on-accent);
  border-top-right-radius: var(--r-xs);
  white-space: pre-wrap;
}
.msg-row.assistant .msg-bubble {
  background: var(--surface-2);
  border: 1px solid var(--line-subtle);
  border-top-left-radius: var(--r-xs);
  color: var(--text-1);
}
.msg-thinking {
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--line-subtle);
}
.msg-caret {
  display: inline-block;
  width: 2px;
  height: 14px;
  background: var(--accent);
  vertical-align: -2px;
  animation: sc-blink 1000ms step-end infinite;
}
@keyframes sc-blink {
  0%,
  49% {
    opacity: 1;
  }
  50%,
  100% {
    opacity: 0;
  }
}
.msg-failed {
  margin-top: 6px;
  font-size: 12.5px;
  color: var(--danger);
}

/* assistant Markdown（复用 doc-md / rp-table 类名，样式按聊天密度自定） */
.doc-md :deep(h1),
.doc-md :deep(h2),
.doc-md :deep(h3),
.doc-md :deep(h4) {
  margin: 10px 0 6px;
  font-size: 14px;
  color: var(--text-strong);
  font-weight: 600;
}
.doc-md :deep(p) {
  margin: 0 0 8px;
  font-size: 14px;
  line-height: 24px;
  color: var(--text-1);
}
.doc-md :deep(p:last-child) {
  margin-bottom: 0;
}
.doc-md :deep(strong) {
  color: var(--text-strong);
}
.doc-md :deep(code) {
  font-family: var(--font-mono);
  font-size: 12.5px;
  background: var(--sunken);
  border-radius: var(--r-xs);
  padding: 1px 5px;
  color: var(--ember);
}
.doc-md :deep(ul),
.doc-md :deep(ol) {
  margin: 0 0 8px 20px;
  padding: 0;
}
.doc-md :deep(li) {
  margin: 3px 0;
  font-size: 14px;
  line-height: 24px;
  color: var(--text-1);
}
.doc-md :deep(hr) {
  border: none;
  border-top: 1px solid var(--line);
  margin: 10px 0;
}
.doc-md :deep(.rp-table) {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  margin: 8px 0 10px;
  font-size: 12.5px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
}
.doc-md :deep(.rp-table th) {
  text-align: left;
  padding: 6px 10px;
  background: var(--sunken);
  color: var(--text-2);
  font-weight: 500;
}
.doc-md :deep(.rp-table td) {
  padding: 6px 10px;
  border-top: 1px solid var(--line-subtle);
  color: var(--text-1);
}

/* 跳底按钮 */
.jump-btn {
  position: sticky;
  bottom: 8px;
  align-self: center;
  padding: 6px 14px;
  border-radius: var(--r-full);
  border: 1px solid var(--line);
  background: var(--surface-1);
  box-shadow: var(--e2);
  color: var(--accent);
  font-size: 12.5px;
  cursor: pointer;
}

/* 输入区 */
.input-bar {
  border-top: 1px solid var(--line-subtle);
  padding: 12px 16px;
}
.msg-input {
  width: 100%;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-family: var(--font-ui);
  font-size: 14px;
  line-height: 22px;
  color: var(--text-strong);
  min-height: 44px;
}
.msg-input::placeholder {
  color: var(--text-3);
}
.input-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}
.input-actions .hint {
  font-size: 12px;
  color: var(--text-3);
}
.input-actions .spacer {
  margin-left: auto;
}
</style>
