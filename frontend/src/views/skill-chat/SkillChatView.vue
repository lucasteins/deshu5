<script setup lang="ts">
/**
 * 技能对话（训练模式二级页签）——选择 skill → 与该 skill 能力绑定的 LLM 多轮聊天。
 *
 * 左栏：skill 列表卡（名称 + 描述摘要，选中高亮）+ 该 skill 历史会话（新建 / 载入 / 删除）；
 * 主区：示例命令 chips（点击直发；右上「刷新示例」走 LLM 重生成，loading 态）、
 *       消息流（user 右 / assistant 左气泡，assistant 用 renderMarkdown 渲染，
 *       流式期间 DsThinkingPanel 思考流 + 正文增量，useAutoScroll 跟底）、
 *       底部输入框（Enter 发送 / Shift+Enter 换行，流式中禁用发送）。
 * 命令执行：后端 agent 循环会自动执行回复中 ```bash 块里过白名单的命令并回填结果
 * （{assistant_start} 新一轮气泡 / {exec_start} 插流式执行卡 / {exec_out} 输出增量 /
 * {exec_done} 封板）；assistant 正文里的可执行命令仍渲染「可执行命令」条，
 * 点击手动执行走独立 useSSE 实例 POST /api/skillchat/execute/stream；
 * 同一时刻只允许一个手动执行。
 * 历史 role='exec' 消息渲染为只读执行结果卡（默认折叠）。
 * SSE：POST /api/skillchat/chat/stream（{thinking} / {content} 增量帧 + 自动执行帧 + done / error 终帧），
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
  type SkillChatExecEvent,
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
  if (runningExecKey.value) {
    toast.warning('有命令正在执行，请先等待完成')
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
  if (runningExecKey.value) {
    toast.warning('有命令正在执行，请先等待完成')
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
      role:
        m.role === 'user'
          ? ('user' as const)
          : m.role === 'exec'
            ? ('exec' as const)
            : ('assistant' as const),
      content: m.content,
      expanded: false,
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
  role: 'user' | 'assistant' | 'exec'
  content: string
  /** 流式中（思考面板 + 正文增量 + 光标） */
  streaming?: boolean
  /** SSE 错误终态 */
  failed?: boolean
  /** exec 历史结果卡：输出区展开态（默认折叠） */
  expanded?: boolean
}

const messages = ref<UiMessage[]>([])
const messagesLoading = ref(false)
const input = ref('')
const sending = ref(false)
/** 当前流式回复的思考增量（仅流式期间展示，完成后收起） */
const thinkingText = ref('')
let msgSeq = 0
/** 自动执行期间对应的流式 exec 消息 key（exec_out/exec_done 帧据此定位） */
let autoExecKey: string | null = null

const sse = useSSE<SkillChatStreamEvent, SkillChatStreamResult>('/skillchat/chat/stream', {
  onEvent: (evt) => {
    if ('thinking' in evt && typeof evt.thinking === 'string') {
      thinkingText.value += evt.thinking
      scrollAfterTick()
    } else if ('assistant_start' in evt) {
      // agent 循环新一轮：上一轮气泡已有内容则封板，另开一个流式气泡
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && last.streaming && last.content) {
        last.streaming = false
        messages.value.push({ key: `a${++msgSeq}`, role: 'assistant', content: '', streaming: true })
      }
      thinkingText.value = ''
      autoExecKey = null
      scrollAfterTick()
    } else if ('exec_start' in evt && typeof evt.exec_start === 'string') {
      // 自动执行：插入流式执行卡（复用 exec 历史卡渲染，展开态）
      const key = `e${++msgSeq}`
      autoExecKey = key
      messages.value.push({
        key,
        role: 'exec',
        content: `$ ${evt.exec_start}\n`,
        expanded: true,
        streaming: true,
      })
      scrollAfterTick()
    } else if ('exec_out' in evt && typeof evt.exec_out === 'string') {
      const m = autoExecKey ? messages.value.find((x) => x.key === autoExecKey) : undefined
      if (m) {
        m.content += evt.exec_out
        if (m.content.length > EXEC_OUTPUT_MAX) {
          const nl = m.content.indexOf('\n')
          m.content = m.content.slice(0, nl + 1) + m.content.slice(-EXEC_OUTPUT_MAX)
        }
      }
      scrollAfterTick()
    } else if ('exec_done' in evt && evt.exec_done === true) {
      // 自动执行终态：把「$ cmd\n<输出>」封板为「$ cmd\n[exit N]\n<输出>」
      const m = autoExecKey ? messages.value.find((x) => x.key === autoExecKey) : undefined
      if (m) {
        const nl = m.content.indexOf('\n')
        const head = nl === -1 ? m.content : m.content.slice(0, nl)
        const rest = nl === -1 ? '' : m.content.slice(nl + 1)
        const code = typeof evt.exit_code === 'number' ? evt.exit_code : -1
        m.content = `${head}\n[exit ${code}]\n${rest}`
        m.streaming = false
      }
      autoExecKey = null
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
    for (const m of messages.value) if (m.streaming) m.streaming = false
    autoExecKey = null
  }

  const last = messages.value[messages.value.length - 1]
  if (sse.status.value === 'done') {
    // 以 done 帧完整内容为准（最后一轮气泡；与增量拼接等价，兜底丢帧）
    if (last && last.role === 'assistant' && sse.result.value?.content)
      last.content = sse.result.value.content
    // 标题 / updated_at / message_count 由后端更新，重拉列表
    void loadSessions()
  } else if (sse.status.value === 'error') {
    if (last && last.role === 'assistant') last.failed = true
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

/* ================= 命令执行 ================= */
/** 命令白名单：与后端 parse_exec_command 同规则（脚本头校验，参数由后端 shlex 解析放行引号） */
const EXEC_SCRIPT_RE = /^(tools|skills)\/[A-Za-z0-9_\-./]+\.py$/
const BASH_BLOCK_RE = /```(?:bash|sh|shell)[^\S\n]*\n([\s\S]*?)```/g
/** 执行卡输出上限（字符），超出保留尾部 */
const EXEC_OUTPUT_MAX = 20000

function isExecCommand(line: string): boolean {
  const parts = line.trim().split(/\s+/)
  if (parts.length < 2 || parts[0] !== 'python') return false
  const script = parts[1].replace(/\\/g, '/')
  return EXEC_SCRIPT_RE.test(script) && !script.includes('..')
}

/** 提取 assistant 正文中全部可执行命令：逐行过白名单，跳过空行与 # 注释行 */
function extractExecCommands(content: string): string[] {
  const cmds: string[] = []
  for (const match of content.matchAll(BASH_BLOCK_RE)) {
    for (const raw of (match[1] ?? '').split('\n')) {
      const l = raw.trim()
      if (!l || l.startsWith('#')) continue
      if (isExecCommand(l)) cmds.push(l)
    }
  }
  return cmds
}

interface ExecCard {
  command: string
  status: 'running' | 'done' | 'error'
  exitCode: number | null
  output: string
  error: string
}

/** 执行卡按「消息 key:命令序号」存放；同一时刻只允许一个执行 */
const execCards = ref<Record<string, ExecCard>>({})
const runningExecKey = ref<string | null>(null)
let activeExecKey: string | null = null
/** exec_done 终态帧不带 done:true，useSSE 识别不到，需自行记录（见 api/skillchat.ts 注释） */
let execDoneSeen = false

const execPreEls = new Map<string, HTMLElement>()
function setExecPreEl(key: string, el: unknown) {
  if (el instanceof HTMLElement) execPreEls.set(key, el)
  else execPreEls.delete(key)
}

function execCardList(msgKey: string, idx: number): ExecCard[] {
  const card = execCards.value[`${msgKey}:${idx}`]
  return card ? [card] : []
}

function scrollExecOutput() {
  void nextTick(() => {
    const el = activeExecKey ? execPreEls.get(activeExecKey) : null
    if (el) el.scrollTop = el.scrollHeight
    notify()
  })
}

const execSse = useSSE<SkillChatExecEvent, null>('/skillchat/execute/stream', {
  onEvent: (evt) => {
    const card = activeExecKey ? execCards.value[activeExecKey] : undefined
    if (!card) return
    if ('exec_out' in evt && typeof evt.exec_out === 'string') {
      card.output += evt.exec_out
      if (card.output.length > EXEC_OUTPUT_MAX) card.output = card.output.slice(-EXEC_OUTPUT_MAX)
      scrollExecOutput()
    } else if ('exec_done' in evt && evt.exec_done === true) {
      execDoneSeen = true
      card.status = 'done'
      card.exitCode = typeof evt.exit_code === 'number' ? evt.exit_code : null
      scrollAfterTick()
    } else if ('error' in evt && typeof evt.error === 'string') {
      card.status = 'error'
      card.error = evt.error
      scrollAfterTick()
    }
  },
})

async function runExecCommand(msgKey: string, cmdIndex: number, command: string) {
  if (runningExecKey.value) return
  if (!activeSessionId.value) {
    toast.warning('当前没有会话，无法执行命令')
    return
  }
  const key = `${msgKey}:${cmdIndex}`
  execCards.value = {
    ...execCards.value,
    [key]: { command, status: 'running', exitCode: null, output: '', error: '' },
  }
  runningExecKey.value = key
  activeExecKey = key
  execDoneSeen = false
  scrollAfterTick()
  try {
    await execSse.start({ session_id: activeSessionId.value, command })
  } finally {
    runningExecKey.value = null
    activeExecKey = null
  }
  const card = execCards.value[key]
  if (!card) return
  if (execDoneSeen) {
    // 后端已持久化 role='exec' 消息并更新 updated_at；重拉会话列表即可，消息由执行卡呈现
    void loadSessions()
  } else if (card.status === 'running') {
    // 未收到任何终帧：HTTP 400/404/409（非 SSE）或流中断
    card.status = 'error'
    card.error = execSse.error.value?.message || '执行流中断'
  }
  scrollAfterTick()
}

/** 历史 role='exec' 消息正文："$ <command>\n[exit <code>]\n<stdout 尾部>" */
interface ExecHistory {
  command: string
  exitCode: number | null
  output: string
}

function parseExecMessage(content: string): ExecHistory {
  const nl = content.indexOf('\n')
  const firstLine = nl === -1 ? content : content.slice(0, nl)
  const rest = nl === -1 ? '' : content.slice(nl + 1)
  const m = rest.match(/^\[exit (-?\d+)\]\n?/)
  return {
    command: firstLine.replace(/^\$\s?/, ''),
    exitCode: m ? Number(m[1]) : null,
    output: m ? rest.slice(m[0].length) : rest,
  }
}

onBeforeUnmount(() => {
  sse.abort()
  execSse.abort()
})
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
                <div class="msg-col">
                  <!-- 历史 exec 消息：只读执行结果卡（默认折叠），不走 renderMarkdown -->
                  <div v-if="m.role === 'exec'" class="exec-card exec-card--history">
                    <div class="exec-card-head clickable" @click="m.expanded = !m.expanded">
                      <code class="exec-cmd-text">$ {{ parseExecMessage(m.content).command }}</code>
                      <span
                        class="exec-badge"
                        :class="
                          parseExecMessage(m.content).exitCode === 0
                            ? 'is-ok'
                            : parseExecMessage(m.content).exitCode === null
                              ? ''
                              : 'is-bad'
                        "
                      >
                        exit {{ parseExecMessage(m.content).exitCode ?? '—' }}
                      </span>
                      <span class="exec-toggle">{{ m.expanded ? '收起 ▲' : '展开 ▼' }}</span>
                    </div>
                    <pre v-if="m.expanded" class="exec-output">{{
                      parseExecMessage(m.content).output || '(无输出)'
                    }}</pre>
                  </div>
                  <template v-else>
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
                    <!-- 可执行命令条：```bash 块逐行过白名单，有命中即出现 -->
                    <div
                      v-if="m.role === 'assistant' && !m.streaming && extractExecCommands(m.content).length"
                      class="exec-strip"
                    >
                      <div class="exec-strip-title">可执行命令</div>
                      <div
                        v-for="(cmd, ci) in extractExecCommands(m.content)"
                        :key="ci"
                        class="exec-cmd-block"
                      >
                        <div class="exec-cmd-row">
                          <code class="exec-cmd-text">{{ cmd }}</code>
                          <button
                            class="btn-sm exec-run-btn"
                            type="button"
                            :disabled="!!runningExecKey"
                            @click="runExecCommand(m.key, ci, cmd)"
                          >
                            ▶ 执行
                          </button>
                        </div>
                        <!-- 执行卡：运行中 spinner / 退出码徽标 / 输出跟底 -->
                        <div v-for="card in execCardList(m.key, ci)" :key="card.command" class="exec-card">
                          <div class="exec-card-head">
                            <span v-if="card.status === 'running'" class="exec-spinner" />
                            <span
                              v-else-if="card.status === 'done'"
                              class="exec-badge"
                              :class="card.exitCode === 0 ? 'is-ok' : 'is-bad'"
                            >
                              exit {{ card.exitCode ?? '—' }}
                            </span>
                            <span v-else class="exec-badge is-bad">执行失败</span>
                            <span class="exec-card-status">
                              {{
                                card.status === 'running'
                                  ? '运行中…'
                                  : card.status === 'done'
                                    ? card.exitCode === 0
                                      ? '执行成功'
                                      : '执行失败'
                                    : '执行失败'
                              }}
                            </span>
                          </div>
                          <pre
                            v-if="card.output"
                            class="exec-output"
                            :ref="(el) => setExecPreEl(`${m.key}:${ci}`, el)"
                          >{{ card.output }}</pre>
                          <div v-if="card.status === 'error' && card.error" class="exec-error">
                            {{ card.error }}
                          </div>
                        </div>
                      </div>
                    </div>
                  </template>
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
.msg-col {
  max-width: 78%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.msg-bubble {
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

/* ---- 命令执行（命令条 / 执行卡 / 历史 exec 卡） ---- */
.exec-strip {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  background: var(--surface-1);
}
.exec-strip-title {
  font-size: 11.5px;
  color: var(--text-3);
}
.exec-cmd-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.exec-cmd-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.exec-cmd-text {
  flex: 1;
  min-width: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-1);
  overflow-x: auto;
  white-space: nowrap;
}
.exec-run-btn {
  flex: none;
  color: var(--accent);
  border-color: var(--accent-line);
}
.exec-run-btn:hover:not(:disabled) {
  background: var(--accent-soft);
  border-color: var(--accent);
}
.exec-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  background: var(--sunken);
}
.exec-card--history {
  background: var(--surface-1);
  border-color: var(--line);
}
.exec-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.exec-card-head.clickable {
  cursor: pointer;
}
.exec-card-status {
  font-size: 12px;
  color: var(--text-3);
}
.exec-badge {
  flex: none;
  padding: 1px 8px;
  border-radius: var(--r-full);
  font-family: var(--font-mono);
  font-size: 11.5px;
  background: var(--surface-2);
  color: var(--text-2);
}
.exec-badge.is-ok {
  background: var(--success-soft);
  color: var(--success);
}
.exec-badge.is-bad {
  background: var(--danger-soft);
  color: var(--danger);
}
.exec-toggle {
  margin-left: auto;
  flex: none;
  font-size: 11.5px;
  color: var(--text-3);
}
.exec-spinner {
  flex: none;
  width: 12px;
  height: 12px;
  border: 2px solid var(--accent-soft);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: exec-spin 800ms linear infinite;
}
@keyframes exec-spin {
  to {
    transform: rotate(360deg);
  }
}
.exec-output {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: var(--r-xs);
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 18px;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-all;
}
.exec-error {
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
