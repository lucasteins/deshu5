<script setup lang="ts">
/**
 * 统计看板（F1.2 试点 · 图表首秀）
 *
 * 设计依据：§4.4.7 图表规范 + §4.5.3 统计看板（KPI 卡〔可点击下钻〕/ 折线 / 环形 / 横条 /
 * 热力网格 / 工作流运行表）；视觉基准：设计稿/mockups/02-统计看板.html（并排目验）。
 *
 * 数据（真实 API）：
 * - GET /api/stats   → KPI / 环形（错题归因分布）/ 横条（难度·来源分布）/ 生成健康
 * - GET /api/qa-pairs→ 折线（入库趋势）/ 热力（入库活跃度）——/api/stats 为快照、无历史序列
 * - GET /api/generation-logs → 工作流运行表（子组件，服务端分页）
 *
 * 与设计稿的偏差（记录于 02-交接日志）：
 * ① KPI sparkline 仅「问答对资产」有真实序列（问答对按周累计），其余卡片 API 无历史序列 → 留空不造假；
 * ② 折线/热力以「问答对入库」口径呈现（原设计为「提问量」，提问日志仅 19 条不足以成像）；
 * ③ 横条由「各供电单位资产覆盖」（无单位维度数据）改为「问答对难度·来源分布」，同为排序横条；
 * ④ 时间范围切换未做（/api/stats 为快照，无法驱动全部图表）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElSegmented } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  DsAsyncSection,
  DsChart,
  DsHeatGrid,
  DsKpiCard,
  buildBarOption,
  buildDonutOption,
  buildLineOption,
  useChartTokens,
  type BarRow,
  type ChartTokens,
  type DonutSlice,
  type LineSeriesSpec,
} from '@/components'
import { fetchStats, type StatsData } from '@/api/stats'
import { fetchAllQaPairs, type QaPair } from '@/api/qa'
import type { EChartsOption } from '@/components'
import { WEEKDAYS, heatMatrix, parseLocalDate, weeklyBuckets } from './aggregate'
import StatsWorkflowTable from './StatsWorkflowTable.vue'

const router = useRouter()
const tokens = useChartTokens()

/* ---------- 数据 ---------- */
const stats = ref<StatsData | null>(null)
const qaRows = ref<QaPair[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const updatedAt = ref<Date | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const [s, qa] = await Promise.all([fetchStats(), fetchAllQaPairs()])
    stats.value = s.data
    qaRows.value = qa.items
    updatedAt.value = new Date()
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}
onMounted(load)

const updatedText = computed(() => {
  const d = updatedAt.value
  if (!d) return '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
})

/* ---------- KPI 行（可点击下钻，§4.5.3 关键交互） ---------- */
const trendBuckets = computed(() => weeklyBuckets(qaRows.value, 12))

/** 近 7 天新增（真实：由 ingest_time 统计） */
const week7New = computed(() => {
  const since = Date.now() - 7 * 86400_000
  return qaRows.value.filter((r) => {
    const d = parseLocalDate(r.ingest_time)
    return d ? d.getTime() >= since : false
  }).length
})

/** 问答对资产累计序列（最近 12 周 + 窗口前的存量基数） */
const qaCumulative = computed(() => {
  const buckets = trendBuckets.value
  const inside = buckets.reduce((sum, b) => sum + b.total, 0)
  const base = Math.max(0, (stats.value?.qa_pairs.total ?? 0) - inside)
  let acc = base
  return buckets.map((b) => {
    acc += b.total
    return acc
  })
})

interface KpiSpec {
  key: string
  label: string
  value: number | string
  unit: string
  delta?: { text: string; direction: 'up' | 'down' | 'flat' } | null
  series?: number[]
  accent?: boolean
  hint: string
  to: { name: string; hash?: string }
}

/* ---------- KPI 趋势线 / 环比的数据源（F1.2 增补） ----------
 * 后端在每次 /api/stats 时把指标按天落一行快照（表 stats_snapshots），历史随使用积累。
 *  - 序列：≥3 期才画（1~2 个点画不出趋势，画了反而误导）
 *  - 环比：与「≤7 天前最近一条」比较，无基准则不给
 *  - 回退：快照不足时，问答对资产仍用其自身 `ingest_time` 的按周累计序列与「近 7 天新增」
 *    （该指标本就有真实时间列）——保证机制落地当天不出现空白回归。 */
const MIN_SERIES_POINTS = 3

function kpiSnapshotSeries(metricKey: string): number[] | undefined {
  const points = stats.value?.kpi_series?.series?.[metricKey] ?? []
  return points.length >= MIN_SERIES_POINTS ? points.map((p) => p.v) : undefined
}

function kpiSnapshotDelta(
  metricKey: string,
  unit: string,
): { text: string; direction: 'up' | 'down' | 'flat' } | null {
  const d7 = stats.value?.kpi_series?.delta7?.[metricKey]
  if (typeof d7 !== 'number') return null
  const direction = d7 > 0 ? 'up' : d7 < 0 ? 'down' : 'flat'
  const abs = Math.abs(d7)
  const num = Number.isInteger(abs) ? String(abs) : abs.toFixed(1)
  const sign = d7 > 0 ? '+' : d7 < 0 ? '−' : '±'
  return { text: `近 7 天 ${sign}${num}${unit}`, direction }
}

const kpis = computed<KpiSpec[]>(() => {
  const b = stats.value?.basic
  return [
    {
      key: 'tables',
      label: '业务表',
      value: b?.tables ?? '—',
      unit: '张',
      delta: kpiSnapshotDelta('tables', ' 张'),
      series: kpiSnapshotSeries('tables'),
      hint: '下钻到数据资源 · 目录模式',
      to: { name: 'resources', hash: '#tab=catalog' },
    },
    {
      key: 'columns',
      label: '字段总数',
      value: b?.columns ?? '—',
      unit: '个',
      delta: kpiSnapshotDelta('columns', ' 个'),
      series: kpiSnapshotSeries('columns'),
      hint: '下钻到数据资源 · 目录模式',
      to: { name: 'resources', hash: '#tab=catalog' },
    },
    {
      key: 'code_domains',
      label: '码值域',
      value: b?.code_domains ?? '—',
      unit: '个',
      delta: kpiSnapshotDelta('code_domains', ' 个'),
      series: kpiSnapshotSeries('code_domains'),
      hint: '下钻到数据资源 · 码值库',
      to: { name: 'resources', hash: '#tab=code' },
    },
    {
      key: 'qa_pairs',
      label: '问答对资产',
      value: stats.value?.qa_pairs.total ?? '—',
      unit: '条',
      delta: kpiSnapshotDelta('qa_pairs', ' 条') ?? {
        text: `近 7 天 +${week7New.value}`,
        direction: 'up',
      },
      series: kpiSnapshotSeries('qa_pairs') ?? qaCumulative.value,
      hint: '下钻到问答对库',
      to: { name: 'qa-lib' },
    },
    {
      key: 'success_rate',
      label: 'SQL 执行成功率',
      value: stats.value?.generation_health.exec_success_rate ?? '—',
      unit: '%',
      delta: kpiSnapshotDelta('exec_success_rate', 'pp'),
      series: kpiSnapshotSeries('exec_success_rate'),
      accent: true,
      hint: '下钻到错题集',
      to: { name: 'errors' },
    },
  ]
})

function drill(k: KpiSpec) {
  void router.push({ name: k.to.name, hash: k.to.hash })
}

/* ---------- 折线：问答对入库趋势（近 12 周 · 每周） ---------- */
const SERIES_TOTAL = '新增问答对'
const SERIES_USABLE = '其中可用'
const hiddenTrend = ref<Record<string, boolean>>({})

const trendLegend = computed(() => [
  { name: SERIES_TOTAL, color: tokens.value.categorical[0] },
  { name: SERIES_USABLE, color: tokens.value.emberFill },
])

const trendEmpty = computed(() => trendBuckets.value.every((b) => b.total === 0))

const trendOption = computed<EChartsOption>(() => {
  const t = tokens.value
  const buckets = trendBuckets.value
  const all: LineSeriesSpec[] = [
    {
      name: SERIES_TOTAL,
      data: buckets.map((b) => b.total),
      color: t.categorical[0],
      area: true,
      markPeak: true,
      peakSuffix: ' 条',
    },
    { name: SERIES_USABLE, data: buckets.map((b) => b.usable), color: t.emberFill },
  ]
  return buildLineOption(t, {
    categories: buckets.map((b) => b.label),
    series: all.filter((s) => !hiddenTrend.value[s.name]),
    unit: ' 条',
  })
})

function toggleTrend(name: string) {
  hiddenTrend.value = { ...hiddenTrend.value, [name]: !hiddenTrend.value[name] }
}

/* ---------- 环形：错题归因分布（占比） ---------- */
/**
 * 归因类型配色遵循 §4.5「4. 错题集」的语义映射
 * （选表错=琥珀 / 字段错=红 / 关联错=紫 / 条件错=蓝 / 聚合错=青 / 其他=灰）。
 */
function errorTypeColor(type: string, t: ChartTokens): string {
  if (type.includes('表选择')) return t.categorical[2]
  if (type.includes('字段')) return t.categorical[5]
  if (type.includes('关联')) return t.categorical[3]
  if (type.includes('条件')) return t.categorical[1]
  if (type.includes('聚合')) return t.categorical[0]
  return t.text4
}

const hiddenError = ref<Record<string, boolean>>({})

const donutSlices = computed<DonutSlice[]>(() => {
  const t = tokens.value
  const list = [...(stats.value?.error_records.type_distribution ?? [])].sort(
    (a, b) => b.count - a.count,
  )
  return list.map((item) => ({
    name: item.type,
    value: item.count,
    color: errorTypeColor(item.type, t),
    dimmed: hiddenError.value[item.type] === true,
  }))
})

const donutTotal = computed(() => stats.value?.error_records.total ?? 0)
const donutEmpty = computed(() => donutSlices.value.length === 0)

const donutOption = computed<EChartsOption>(() =>
  buildDonutOption(tokens.value, { slices: donutSlices.value, unit: ' 条' }),
)

const donutRows = computed(() => {
  const total = donutSlices.value.reduce((s, x) => s + x.value, 0)
  return donutSlices.value.map((s) => ({
    ...s,
    percent: total > 0 ? (s.value / total) * 100 : 0,
  }))
})

function toggleError(name: string) {
  hiddenError.value = { ...hiddenError.value, [name]: !hiddenError.value[name] }
}

/* ---------- 横条：问答对分布（难度 / 来源，必须排序） ---------- */
const barMode = ref<'difficulty' | 'source'>('difficulty')
const BAR_MODES = [
  { label: '按难度', value: 'difficulty' },
  { label: '按来源', value: 'source' },
]

function setBarMode(v: unknown) {
  if (v === 'difficulty' || v === 'source') barMode.value = v
}

const barRows = computed<BarRow[]>(() => {
  const dist = stats.value?.qa_pairs_dist
  const list = (barMode.value === 'difficulty' ? dist?.difficulty : dist?.source) ?? []
  return [...list]
    .sort((a, b) => b.count - a.count)
    .map((item) => ({ name: item.name, value: item.count }))
})

const barEmpty = computed(() => barRows.value.length === 0)

const barOption = computed<EChartsOption>(() =>
  buildBarOption(tokens.value, { rows: barRows.value, unit: ' 条' }),
)

/* ---------- 热力：知识入库活跃度（7×24） ---------- */
const heat = computed(() => heatMatrix(qaRows.value))
const heatEmpty = computed(() => heat.value.max === 0)
const heatPeakText = computed(() => {
  const p = heat.value.peak
  if (!p) return '暂无峰值'
  return `峰值 ${WEEKDAYS[p.day]} ${String(p.hour).padStart(2, '0')}:00 · ${p.count} 条`
})

/* ---------- 生成健康（沿用旧看板「工作流运行」口径） ---------- */
const health = computed(() => {
  const h = stats.value?.generation_health
  const total = stats.value?.generation.total ?? 0
  const latency = h?.avg_latency_ms ? `${(h.avg_latency_ms / 1000).toFixed(1)}s` : '—'
  return [
    { label: '平均生成耗时', value: latency },
    { label: '平均尝试次数', value: h ? String(h.avg_attempts) : '—' },
    { label: '执行成功率', value: h ? `${h.exec_success_rate}%` : '—' },
    { label: '生成总次数', value: total.toLocaleString('zh-CN') },
  ]
})
</script>

<template>
  <div class="stats">
    <div class="stats__toolbar">
      <span class="stats__updated">数据更新于 {{ updatedText }}</span>
      <ElButton :icon="Refresh" :loading="loading" @click="load">刷新</ElButton>
    </div>

    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="!stats"
      skeleton="cards"
      empty-title="还没有统计数据"
      empty-desc="导入数据资源或生成问答对后，看板将自动汇总指标"
      @retry="load"
    >
      <!-- KPI 行（5 张，等高；可点击下钻） -->
      <div class="stats__kpis">
        <DsKpiCard
          v-for="k in kpis"
          :key="k.key"
          :label="k.label"
          :value="k.value"
          :unit="k.unit"
          :delta="k.delta"
          :series="k.series"
          :accent="k.accent"
          :drill-hint="k.hint"
          @click="drill(k)"
        />
      </div>

      <!-- 折线 + 环形 -->
      <div class="stats__row stats__row--trend">
        <section class="stats-card">
          <header class="stats-card__hd">
            <h3>问答对入库趋势</h3>
            <span class="stats-card__sub">近 12 周 · 每周</span>
            <span class="stats-card__legend">
              <button
                v-for="s in trendLegend"
                :key="s.name"
                type="button"
                class="stats-legend__li"
                :class="{ 'is-off': hiddenTrend[s.name] }"
                @click="toggleTrend(s.name)"
              >
                <span class="stats-legend__sw" :style="{ background: s.color }" />
                {{ s.name }}
              </button>
            </span>
          </header>
          <div class="stats-card__bd">
            <DsChart
              :option="trendOption"
              :height="280"
              :empty="trendEmpty"
              empty-text="近 12 周暂无入库记录"
              aria-label="问答对入库趋势折线图"
            />
          </div>
        </section>

        <section class="stats-card">
          <header class="stats-card__hd">
            <h3>错题归因分布</h3>
            <span class="stats-card__sub">全量累计</span>
            <span class="stats-card__meta">共 {{ donutTotal.toLocaleString('zh-CN') }} 条</span>
          </header>
          <div class="stats-card__bd">
            <div v-if="!donutEmpty" class="stats-donut">
              <div class="stats-donut__chart">
                <DsChart
                  :option="donutOption"
                  :height="200"
                  empty-plain
                  aria-label="错题归因占比环形图"
                />
                <div class="stats-donut__center">
                  <span class="stats-donut__num">{{ donutTotal.toLocaleString('zh-CN') }}</span>
                  <span class="stats-donut__label">错题总数</span>
                </div>
              </div>
              <ul class="stats-donut__legend">
                <li v-for="s in donutRows" :key="s.name">
                  <button
                    type="button"
                    class="stats-legend__li stats-legend__li--row"
                    :class="{ 'is-off': hiddenError[s.name] }"
                    @click="toggleError(s.name)"
                  >
                    <span class="stats-legend__sw" :style="{ background: s.color }" />
                    <span class="stats-donut__name">{{ s.name }}</span>
                    <span class="stats-donut__val">{{ s.value }}</span>
                    <span class="stats-donut__pct">{{ s.percent.toFixed(1) }}%</span>
                  </button>
                </li>
              </ul>
            </div>
            <DsChart
              v-else
              :option="donutOption"
              :height="200"
              empty
              empty-plain
              empty-text="暂无错题记录"
            />
          </div>
        </section>
      </div>

      <!-- 热力 + 横条（热力需更宽容器，置于左列） -->
      <div class="stats__row stats__row--heat">
        <section class="stats-card">
          <header class="stats-card__hd">
            <h3>知识入库活跃度分布</h3>
            <span class="stats-card__sub">近 12 周 · 按小时</span>
            <span v-if="!heatEmpty" class="stats-card__meta stats-tag">{{ heatPeakText }}</span>
          </header>
          <div class="stats-card__bd">
            <DsHeatGrid
              v-if="!heatEmpty"
              :matrix="heat.matrix"
              unit="条"
              legend-note="单位：条 / 小时"
            />
            <p v-else class="stats-empty-inline">近 12 周暂无入库记录</p>
          </div>
        </section>

        <section class="stats-card">
          <header class="stats-card__hd">
            <h3>问答对分布</h3>
            <span class="stats-card__sub">已排序</span>
            <span class="stats-card__meta">
              <ElSegmented
                :model-value="barMode"
                :options="BAR_MODES"
                size="small"
                @update:model-value="setBarMode"
              />
            </span>
          </header>
          <div class="stats-card__bd">
            <DsChart
              :option="barOption"
              :height="260"
              :empty="barEmpty"
              empty-plain
              empty-text="暂无分布数据"
              aria-label="问答对分布横向条形图"
            />
          </div>
        </section>
      </div>

      <!-- 工作流运行 -->
      <section class="stats-card">
        <header class="stats-card__hd">
          <h3>工作流运行</h3>
          <span class="stats-card__sub">生成运行日志 · 按时间倒序</span>
        </header>
        <div class="stats-card__bd">
          <div class="stats-health">
            <div v-for="h in health" :key="h.label" class="stats-health__item">
              <span class="stats-health__val">{{ h.value }}</span>
              <span class="stats-health__label">{{ h.label }}</span>
            </div>
          </div>
          <StatsWorkflowTable />
        </div>
      </section>
    </DsAsyncSection>
  </div>
</template>

<style scoped>
.stats {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.stats__toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}
.stats__updated {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  color: var(--text-3);
}

/* ---------- 卡片 ---------- */
.stats-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  box-shadow: var(--e1);
}
.stats-card__hd {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: 13px 18px;
  border-bottom: 1px solid var(--line-subtle);
  min-height: 50px;
}
.stats-card__hd h3 {
  margin: 0;
  font-size: var(--fs-h3);
  font-weight: 600;
  color: var(--text-strong);
  letter-spacing: -0.005em;
  white-space: nowrap;
}
.stats-card__sub {
  font-size: var(--fs-caption);
  color: var(--text-3);
  white-space: nowrap;
}
.stats-card__meta {
  margin-left: auto;
  font-size: var(--fs-caption);
  color: var(--text-3);
  white-space: nowrap;
}
.stats-card__legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-3);
  margin-left: auto;
}
.stats-card__bd {
  padding: 18px;
  min-width: 0;
}

/* ---------- KPI ---------- */
.stats__kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--sp-4);
}

/* ---------- 行布局（对齐 mockup） ---------- */
.stats__row {
  display: grid;
  gap: var(--sp-4);
}
.stats__row--trend {
  grid-template-columns: 1.62fr 1fr;
}
.stats__row--heat {
  grid-template-columns: 1.55fr 1fr;
}

/* 区块纵向节奏（设计稿 §4.5.3 `.canvasgrid{gap:16px}`）。
   区块是 DsAsyncSection 内容槽的直接子元素，而该槽是普通 block 容器（无 gap），
   故由区块自带下边距形成间距；最后一张卡（工作流运行）与其下方的内容区内边距衔接，无需下边距。 */
.stats__kpis,
.stats__row {
  margin-bottom: var(--sp-4);
}

@media (max-width: 1180px) {
  .stats__kpis {
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  }
  .stats__row--trend,
  .stats__row--heat {
    grid-template-columns: 1fr;
  }
}

/* ---------- 图例（可点击隐藏系列） ---------- */
.stats-legend__li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: none;
  background: none;
  font-size: var(--fs-caption);
  color: var(--text-2);
  cursor: pointer;
}
.stats-legend__li--row {
  width: 100%;
  padding: 2px 0;
}
.stats-legend__li.is-off {
  color: var(--text-4);
  text-decoration: line-through;
}
.stats-legend__sw {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 2px;
}

/* ---------- 环形 ---------- */
.stats-donut {
  display: flex;
  align-items: center;
  gap: 22px;
}
.stats-donut__chart {
  position: relative;
  width: 200px;
  flex: none;
}
.stats-donut__center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}
.stats-donut__num {
  font-family: var(--font-mono);
  font-size: 26px;
  font-weight: 500;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}
.stats-donut__label {
  font-size: var(--fs-micro);
  color: var(--text-3);
}
.stats-donut__legend {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.stats-donut__name {
  flex: 1;
  color: var(--text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.stats-donut__val {
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  color: var(--text-2);
  width: 42px;
  text-align: right;
}
.stats-donut__pct {
  font-family: var(--font-mono);
  font-size: var(--fs-micro);
  color: var(--text-3);
  width: 46px;
  text-align: right;
}

/* ---------- 生成健康 ---------- */
.stats-health {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-3);
  padding: var(--sp-3);
  margin-bottom: var(--sp-3);
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
}
.stats-health__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stats-health__val {
  font-family: var(--font-mono);
  font-size: var(--fs-num-lg);
  font-weight: 500;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}
.stats-health__label {
  font-size: var(--fs-caption);
  color: var(--text-3);
}

/* ---------- 徽标 / 行内空态 ---------- */
.stats-tag {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--r-xs);
  font-size: var(--fs-micro);
  font-weight: 500;
  background: var(--accent-soft);
  color: var(--accent);
}
.stats-empty-inline {
  margin: 0;
  padding: var(--sp-8) 0;
  text-align: center;
  font-size: var(--fs-body-sm);
  color: var(--text-3);
}
</style>
