#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""simulator.py — deshu5 电网营销数据仿真引擎（主数据 + 业务数据）

设计要点
--------
1. **锚定真实组织机构**：以 fz01.dim_org_region_map 中 org_level='区县' 的
   区县供电公司为仿真根节点，每个区县独立生成一张"供电单元"。
2. **按真实分布抽样**：取值不硬编码，全部从 references/profiles.json（由 profile_db.py
   从真实库勘探）中按频次加权抽取，保证仿真数据的统计形态与真实库一致。
3. **拓扑自洽**：220kV站 →110kV线路→ 110kV站 →10kV线路→ 台区 →配电变压器，
   用户挂接在台区/线路/变电站上，层级关系写入 dim_cst_*_rela 关系表。
4. **量费勾稽闭环**：抄表读数 → 抄见量 → 计费卡结算量/电费 → 应收 → 交费 → 实收 → 余额，
   金额与电量在表间严格可对账（详见 references/03-business-data-spec.md）。
5. **可追溯可回滚**：每轮生成把"实际插入的主键清单"写入本地台账
   `scripts/manifest/{db}__{tag}__{stamp}.json`，`--purge` 据此逐主键精确删除，
   绝不触碰存量真实数据（不往业务库写任何台账表）。

用法示例
--------
    # 试跑 2 个区县，2025-01 至今，日粒度只生成最近 3 个月
    python simulator.py --counties 2 --from 202501 --daily recent

    # 正式全量：96 个区县全跑，日粒度全量
    python simulator.py --from 202501 --daily full

    # 只看要写什么，不落库
    python simulator.py --counties 1 --dry-run

    # 按台账清除本次仿真数据
    python simulator.py --purge --tag 模拟
"""
import argparse, base64, hashlib, json, math, os, random, sys, datetime as dt
from collections import defaultdict

try:
    import pymysql
except ImportError:
    sys.exit("需要 pymysql：pip install pymysql")

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, '..', 'references', 'profiles.json')
# 码表快照（export_codebook.py 导出）：码值一律从码表取，不自造
CODEBOOK = os.path.join(HERE, '..', 'references', 'codebook.json')

# 回滚台账目录：每轮生成把"实际插入的主键清单"落成本地 JSON，--purge 据此逐主键删除。
MANIFEST_DIR = os.environ.get('SIM_MANIFEST_DIR', os.path.join(HERE, 'manifest'))

# ============================================================================
# 一、可调参数（业务经验值；与真实库分布叠加使用）
# ============================================================================

# 电压等级标准描述（与 code_value_items.voltage_desc 一致）
V220, V110, V35, V10, V380, V220V = '交流220kV', '交流110kV', '交流35kV', '交流10kV', '交流380V', '交流220V'

# 变电站容量档（kVA）
CAP_220 = [120000, 150000, 180000, 240000]
CAP_110 = [40000, 50000, 63000, 80000, 100000, 120000]
CAP_35 = [20000, 31500, 40000]
# 台区配变容量档（kVA）
CAP_DIST = [100, 200, 315, 400, 500, 630, 800, 1000, 1250, 1600]

# 电价（元/kWh）——按用电类别，用于电量→电费勾稽
PRICE = {
    '城镇居民生活用电': 0.538, '乡村居民生活用电': 0.538, '居民生活用电': 0.538,
    '商业用电': 0.685, '非工业': 0.665, '一般工商业': 0.665, '大工业用电': 0.605,
    '农业生产用电': 0.452, '工业用电': 0.618, '非居民照明': 0.680,
}
PRICE_DEFAULT = 0.620

# 互感器变比：本体库口径「综合倍率 comp_rto = 电压变比 tv × 电流变比 ta」。
# 96 点曲线表的 tv/ta 就是这两个变比本身（不是电量/容量），必须与之严格一致。
PT_HV = 100        # 高供高计：10kV/100V → 电压变比 100
CT_HV = [1, 2, 3, 4, 5, 6, 10, 15, 20, 30, 40, 50, 75, 100]   # 高供高计 CT（5A 二次）
CT_HV_LOW = [30, 40, 60, 80, 100, 150, 200, 300]              # 高供低计 CT（PT=1）
RTO_LOW = [1]                      # 兼容旧口径：低压直抄综合倍率=1
RTO_HIGH = [r * PT_HV for r in CT_HV] + list(CT_HV_LOW)       # 兼容旧口径的高压倍率池

# 月电量季节性系数（1-12月），浙江口径：7-9 月迎峰、12-1 月取暖
SEASON = [1.08, 0.92, 0.95, 0.94, 0.98, 1.12, 1.28, 1.30, 1.15, 0.98, 0.95, 1.10]

# 月内日负荷形状（工作日/周末）
WEEKDAY_SHAPE = 1.0
WEEKEND_SHAPE = 0.93


def ym_add(ym, k):
    """YYYYMM 月份推进 k 个月。

    先把 YYYYMM 转成"绝对月序"再运算，不能直接对 YYYYMM 做 divmod——
    那样会把 202506 当成 20 万以上的月数（早期版本因此让 ym_range 只返回
    起始月，业务数据只生成了 1 个月）。
    """
    idx = (ym // 100) * 12 + (ym % 100 - 1) + k
    y, m = divmod(idx, 12)
    return y * 100 + m + 1


def ym_range(a, b):
    out, x = [], a
    while x <= b:
        out.append(x)
        x = ym_add(x, 1)
    return out


def month_days(ym):
    y, m = ym // 100, ym % 100
    return (dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.date(y, m, 1)).days


def now_ym():
    t = dt.date.today()
    return t.year * 100 + t.month


# ============================================================================
# 二、基础设施：ID 池 / 抽样器 / 批量写入器
# ============================================================================

class IdPool:
    """按实体类型分配互不冲突的数字 ID，形态贴近真实库（长度见表）。"""

    SPEC = {          # kind: (起始值, 步长)
        'cntrl_sta':  (990000000001, 1),   # 12 位，避开真实 11 位
        'pipeline':   (990000001, 1),      # 9 位
        'dist_sta':   (99000001, 1),       # 8 位
        'adj_volt':   (990000001, 1),
        'cust':       (99000000001, 1),    # 11 位
        'bp':         (99000000001, 1),
        'inst':       (99500000001, 1),
        'agrt':       (99500000001, 1),
        'settle':     (99000000001, 1),
        'dev':        (8199000000000001, 1),  # 16 位，前缀 8199 与真实 8100 区分
        'meter_run':  (8199000000000001, 1),
        'bilg_card':  (9900000000000001, 1),
        'calc':       (9900000000000001, 1),
        'sgmt':       (9910000000000001, 1),
        'addl':       (9920000000000001, 1),
        'spcl':       (9930000000000001, 1),
        'rcvbl':      (99000000001, 1),
        'rcvbl_addl': (991000000001, 1),
        'charg':      (9920000000000001, 1),
        'rcvd':       (993000000001, 1),
        'acct_bal':   (994000000001, 1),
        'mr':         (9900000000000001, 1),
        'wko':        (9900000000000001, 1),
        'step':       (9970000000000001, 1),
        'proc':       (9960000000000001, 1),
        'app':        (1800000000, 1),     # 目标列 int(32)，落在实测真实值之上的空档
        'preapp':     (9950000000000001, 1),
        'sch':        (1810000000, 1),     # accs_sch_id / *_sch_id 为 int(32)
        'invest':     (9990000000000001, 1),
        'devrec':     (9999000000000001, 1),
        'refund':     (9800000000000001, 1),
        'rela':       (999000000001, 1),          # 变电站↔线路
        'rela_ds':    (999900000001, 1),          # 线路↔台区（独立计数器，避免跨表撞键）

        # --- 电网设备域（PMS/GIS 侧台账）的文本主键"尾号" ---
        # 这些表的 id 是 text（真实形态为 `30000000-102414629` 这类 PMS 业务号），
        # 因此计数器只负责产出**尾号**，再由 _pms() 拼成带前缀的字符串。
        # 起始值取 9.9 亿档，保证 `前缀-尾号` 与真实库里 `前缀-小尾号` 不可能相撞。
        'pms_sta':     (990000001, 1),   # 变电站 PSR          → 30000000-
        'pms_sws':     (990000101, 1),   # 开关站 PSR          → 30000004-
        'pms_feeder':  (990000201, 1),   # 馈线 PSR            → 10000100-
        'pms_subline': (990000301, 1),   # 支线/线路 PSR       → 10000200-
        'pms_cable':   (990000401, 1),   # 电缆段 PSR          → 20800000-
        'pms_zone':    (990000501, 1),   # 低压台区 PSR        → 22 位 base64
        'pms_dyline':  (990000601, 1),   # 低压出线 PSR        → 10000300-
        'pms_tf':      (990000701, 1),   # 配电变压器 PSR      → 30200002-
        'pms_otf':     (990000801, 1),   # 专用变压器 PSR      → 30200001-
        'pms_pole':    (990000901, 1),   # 杆塔 PSR            → 10200001-
        'pms_rel':     (991000001, 1),   # 配变↔馈线关系       → 50000000-
        'pms_relpub':  (991000101, 1),   # 公变台账关联        → 60000000-
        'pms_relprv':  (991000201, 1),   # 专变台账关联        → 60000001-

        # 省级电网模型（D5000/GIS）走"前缀+区划码+序号"原生编码（见 _gridid），
        # 不占计数器；其区间由 preflight 逐表校验。

        # --- 业务旁支表 ---
        'dist_det':   (991100001, 1),              # 台区日线损明细 rec_id
        'invno':      (992100001, 1),              # 投资工单号（int 列，控制在 9 位）
        'affmtrl':    (9931000000000001, 1),       # 工单附属材料记录
        'gcapp':      (9941000000000001, 1),       # 发电客户申请记录
        'cgenapp':    (9951000000000001, 1),       # 并网发电申请
        'devdtl':     (9961000000000001, 1),       # 设备领用申请明细
        'curve':      (2145000000, 1),             # 96 点曲线 id（真实为 int 上限哨兵）
    }

    def __init__(self, base_shift=0):
        self.cur = {k: v[0] + base_shift for k, v in self.SPEC.items()}
        self.step = {k: v[1] for k, v in self.SPEC.items()}

    def n(self, kind, k=1):
        """分配一个 ID；k>1 时返回 k 个 ID 的列表。"""
        out = []
        for _ in range(k):
            out.append(self.cur[kind])
            self.cur[kind] += self.step[kind]
        return out[0] if k == 1 else out

    # 文本型编号
    def code(self, kind, width=10):
        return str(self.n(kind)).zfill(width)


class Sampler:
    """按真实分布加权抽样；分布为空时回退到给定默认值。"""

    def __init__(self, profiles, rng):
        self.P = profiles
        self.rng = rng

    def pick(self, path, default=None, key='v'):
        node = self.P
        for p in path.split('.'):
            if not isinstance(node, dict) or p not in node:
                node = None
                break
            node = node[p]
        if not node:
            return default
        if isinstance(node, dict) and 'v' in node:
            return node['v']
        weights = [max(1, int(x.get('n', 1))) for x in node]
        return self.rng.choices([x[key] for x in node], weights=weights, k=1)[0]

    def pick_num(self, path, default):
        v = self.pick(path, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return float(default)


class CodeBook:
    """码表取值器：码值一律从码表里取，不硬编码、不自造描述。

    数据源 references/codebook.json（export_codebook.py 从本体枚举 + 治理码表导出）。
    `columns` 是 (表.列) 的精确映射，`wildcards` 是按列名匹配的通配规则。

    用法：
        cb.code_of('dwd_cst_bus_app_form.bus_type', name='高压新装')   # -> '010101'
        cb.name_of('app_stat', '05')                                   # -> '归档'
        cb.pick('dist_lv')                                             # -> ('3', '地市')
        cb.pair('bus_type', '010102')                                  # -> ('010102', '高压增容')
    """

    def __init__(self, path, rng):
        self.rng = rng
        self.C = {}
        self.cols = {}
        self.wilds = {}
        try:
            d = json.load(open(path, encoding='utf-8'))
        except Exception:
            return
        self.C = {k: v['items'] for k, v in d.get('codes', {}).items()}
        self.cols = d.get('columns', {})
        self.wilds = d.get('wildcards', {})
        # 反查索引：code_name -> {code: name} / {name: [codes]}
        self._byname = {}
        self._bycode = {}
        for cn, items in self.C.items():
            bcode, bname = {}, {}
            for it in items:
                bcode.setdefault(it['code'], it['name'])
                bname.setdefault(it['name'], it['code'])
            self._bycode[cn] = bcode
            self._byname[cn] = bname

    # ---- 元信息 ----
    def names_of_col(self, table, column):
        """该列引用的码表名列表（精确映射优先，其次通配）。"""
        v = self.cols.get(f'{table}.{column}')
        if v:
            return v['codes'], v.get('form') or ''
        v = self.wilds.get(column)
        if v:
            return v['codes'], v.get('form') or ''
        return [], ''

    def code_name_of(self, table, column):
        ns, _ = self.names_of_col(table, column)
        return ns[0] if ns else None

    def has(self, cn):
        return bool(cn) and cn in self.C

    def has_code(self, cn, code):
        return str(code) in self._bycode.get(cn, {})

    # ---- 取值 ----
    def items(self, cn):
        return self.C.get(cn, [])

    def name_of(self, cn, code):
        return self._bycode.get(cn, {}).get(str(code))

    def code_of_name(self, cn, nm):
        return self._byname.get(cn, {}).get(str(nm))

    def name_by_col(self, table, column, code):
        """按 (表,列) 反查码项名称；找不到返回 None。"""
        ns, _ = self.names_of_col(table, column)
        for cn in ns:
            r = self.name_of(cn, code)
            if r is not None:
                return r
        return None

    def pair(self, cn, code):
        """返回 (码, 名)；名缺失时回退为码本身。"""
        if not self.has(cn):
            return str(code), str(code)
        nm = self.name_of(cn, code)
        return str(code), (nm if nm is not None else str(code))

    def pick(self, cn, default=None):
        """随机取一个合法码项，返回 (码, 名)。"""
        it = self.items(cn)
        if not it:
            return (default, default) if default is not None else (None, None)
        x = self.rng.choice(it)
        return str(x['code']), str(x['name'])

    def pick_named(self, cn, want, drop_leading_zero=False, default=None):
        """按语义名称取码（找不到则随机取一个），返回 (码, 名)。

        want 可为字符串或字符串序列；命中即用，全部未命中则随机。
        drop_leading_zero：表内同时存在 '01' 与 '1' 时，按真实库形态去掉前导零。
        """
        if not self.has(cn):
            d = default if default is not None else want
            d = d[0] if isinstance(d, (list, tuple)) else d
            return (str(d), str(d))
        cand = [want] if isinstance(want, str) else list(want)
        for w in cand:
            c = self.code_of_name(cn, w)
            if c is not None:
                return self._form(cn, c, drop_leading_zero), str(w)
        # 语义名未登记，退化为随机
        c, nm = self.pick(cn)
        return self._form(cn, c, drop_leading_zero), nm

    def _form(self, cn, code, drop_leading_zero):
        """按真实库形态决定是否去前导零。

        真实库同一码表在不同表里形态不同（如 bus_type 在 bus_app_form 存 '10101'、
        在 cust_elec_app_rec 存 '010102'），因此由调用方显式指定。
        """
        if not drop_leading_zero:
            return code
        return code.lstrip('0') or '0'

    def desc_for(self, table, column, code, fallback=None):
        """由码取该列的描述值（码表 item_name）。"""
        nm = self.name_by_col(table, column, code)
        return nm if nm is not None else fallback


class Writer:
    """缓冲 + 批量 INSERT，并登记生成台账。

    add() 会把行里真实表不存在的列自动丢弃并记录警告——库结构演进（加/删列）时
    引擎不会崩，只会在报告里提示"本表有 N 列未命中真实结构"。

    同时按 (表, 主键列) 记录本轮生成值的 [min, max] 区间。因为每个计数器都从
    目标库实测最大值之上单调递增，这些区间内不含真实数据，--purge 据此精确回滚。
    """

    def __init__(self, conn, dry_run=False, manifest=True):
        self.conn = conn
        self.dry = dry_run
        self.buf = defaultdict(list)
        self.counts = defaultdict(int)
        self.manifest = manifest
        self.ledger = defaultdict(list)   # table -> [pk values]（本轮真正插入的行）
        self.manifest_path = None         # 本轮台账 JSON 的固定路径（多轮次复用同一文件）
        self._cols_cache = {}
        self._pk_cache = {}
        self.ranges = {}                  # (table, col) -> [min, max, count]
        self.schema = self._load_schema()
        self.dropped = defaultdict(set)
        self.nulled = set()
        self.untracked = set()            # 写了却没登记主键的表（purge 无法覆盖，需告警）

    def _load_schema(self):
        """缓存每个表的列名与列类型。

        列类型用于类型自适应：deshu5 的 dwd 层沿用了源系统的 32 位 int 定义
        （如 dwd_cst_bus_app_form.cust_id、dwd_cst_inst_elec_sch.inst_id），
        装不下 dim 层的 bigint 主键。这些列直接置 NULL，避免 Out of Range，
        并如实反映"该字段在当前库结构下无法承载该关联"的现状。
        """
        cols, ints = defaultdict(set), defaultdict(set)
        with self.conn.cursor() as cur:
            cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = DATABASE()")
            for t, c, d in cur.fetchall():
                cols[t].add(c)
                if d in ('int', 'mediumint', 'smallint', 'tinyint'):
                    ints[t].add(c)
        self.int_cols = ints
        return cols

    INT32_MAX = 2147483647

    def _coerce(self, table, row):
        """把超出 32 位 int 范围的值在该列上置空（同时兼容数字字符串，如 inst_no）。"""
        bad = self.int_cols.get(table)
        if not bad:
            return row
        out = {}
        for k, v in row.items():
            if k in bad and not isinstance(v, bool):
                num = v if isinstance(v, int) else (
                    int(v) if isinstance(v, str) and v.lstrip('-').isdigit() else None)
                if num is not None and abs(num) > self.INT32_MAX:
                    out[k] = None
                    self.nulled.add(f"{table}.{k}")
                    continue
            out[k] = v
        return out

    def _track(self, table, col, val):
        if val is None:
            return
        try:
            v = int(val)
        except (TypeError, ValueError):
            return
        k = (table, col)
        r = self.ranges.get(k)
        if r is None:
            self.ranges[k] = [v, v, 1]
        else:
            r[0] = min(r[0], v)
            r[1] = max(r[1], v)
            r[2] += 1

    # 各表承载的"仿真 ID 列"（含主键与指向其他仿真实体的外键列）。
    # add() 会为这里声明的每一列记录生成区间，--purge 据此精确回滚，绝不误伤真实数据。
    ID_COLS = {
        'dim_cst_cntrl_sta': ['cntrl_sta_id'],
        'dim_cst_pipeline': ['pipeline_id'],
        'dim_cst_dist_sta': ['dist_sta_id'],
        'dim_cst_adj_volt_dev': ['adj_volt_dev_id', 'dist_sta_id', 'cust_id'],
        'dim_cst_business_partner': ['bp_id'],
        'dim_cst_cust': ['cust_id', 'bp_id', 'bus_srv_addr_id'],
        'dim_cst_elec_cons_cust': ['elec_cons_cust_id', 'bp_id', 'cust_id'],
        'dim_cst_settle_acct': ['settle_acct_id', 'bp_id'],
        'dim_cst_cust_agrt': ['cust_agrt_id', 'cust_id', 'settle_acct_id', 'srv_loc_id'],
        'dim_cst_inst_elec_cons': ['inst_id', 'cust_id', 'cust_agrt_id', 'prem_id', 'srv_loc_id',
                                   'iot_point_id', 'pipeline_id', 'dist_sta_id', 'cntrl_sta_id', 'loc_id'],
        'dim_cst_dev': ['dev_id'],
        'dim_cst_elec_meter': ['dev_id'],
        'dwd_cst_meter_run': ['meter_id', 'loc_id', 'meter_logic_id', 'inst_id', 'cust_id'],
        'dim_cst_cntrl_sta_pline_rela': ['cntrl_sta_pipe_line_rela_id', 'cntrl_sta_id', 'pipeline_id'],
        'dim_cst_pline_dist_sta_rela': ['pipe_line_dist_sta_rela_id', 'pipeline_id', 'dist_sta_id'],
        'dwd_cst_mr_data': ['m_r_data_id', 'calc_id', 'inst_id'],
        'dwd_cst_inst_bilg_card': ['bilg_card_id', 'calc_id', 'inst_snap_id', 'inst_id'],
        'dwd_cst_sgmt_qty_charg': ['sgmt_qty_charg_id', 'calc_id', 'bilg_card_id', 'inst_snap_id'],
        'dwd_cst_rcvbl_acct': ['rcvbl_acct_id', 'qty_charg_calc_id'],
        'dwd_cst_rcvbl_addl_acct': ['rcvbl_addl_charg_acct_id', 'rcvbl_acct_id', 'qty_charg_calc_id'],
        'dwd_cst_charg_acct': ['charg_acct_id', 'pay_order_id'],
        'dwd_cst_rcvd_acct': ['rcvd_acct_id', 'charg_acct_id', 'rcvbl_acct_id'],
        'dwd_cst_acct_bal': ['acct_bal_id'],
        'dwd_cst_meter_energy_day_h_xz': ['meter_asset_no'],
        'dwd_cst_es_meter_energy_day_l_xz': ['meter_asset_no'],
        'dwd_cst_bus_app_form': ['bus_app_form_id', 'cust_id', 'bp_id'],
        'dwd_cst_acc_sch': ['accs_sch_id', 'bus_app_form_id', 'bp_id'],
        'dwd_cst_cust_agrt_sch': ['cust_agrt_sch_id', 'cust_agrt_id', 'cust_id', 'bus_app_form_id'],
        'dwd_cst_inst_elec_sch': ['inst_elec_sch_id', 'inst_id', 'cust_id', 'dist_sta_id',
                                  'pipeline_id', 'cntrl_sta_id', 'cust_agrt_sch_id', 'bus_app_form_id'],
        'dwd_cst_meter_sch': ['meter_sch_id', 'meter_id', 'inst_id', 'cust_id',
                              'inst_elec_sch_id', 'cust_agrt_sch_id', 'bus_app_form_id'],
        'dwd_cst_wk_order': ['wk_order_id'],
        'dwd_cst_step_rec': ['step_acct_id', 'step_instc_id', 'proc_acct_id'],
        'dwd_cst_proc_acct': ['proc_acct_id', 'wk_order_id'],
        'dwd_cst_dev_inst_rmv_wk_rec': ['dev_inst_rmv_wk_rec_id', 'dev_id', 'inst_id'],
        'dwd_cst_cust_elec_app_rec': ['cust_elec_app_rec_id', 'cust_id', 'bus_app_form_id', 'bp_id'],
        'dwd_cst_connection_app_rec': ['conn_app_rec_id', 'bus_app_form_id', 'cust_id',
                                       'pipeline_id', 'dist_sta_id', 'cntrl_sta_id'],
        'dwd_cst_stop_rcvr_supl_app': ['stop_rcvr_supl_id'],
        'dwd_cst_dev_rcpt_app': ['app_form_id'],
        'dwd_cst_bilg_rs_rcpt': ['bilg_rs_rcpt_id', 'orgn_calc_id'],
        'dwd_cst_rs_inst_bilg_card': ['rs_inst_bilg_card_id', 'bilg_rs_rcpt_id', 'rs_qty_charg_calc_id'],
        'dwd_cst_preapp_order': ['prepp_id', 'bp_id', 'cust_id'],

        # ---- 电网设备台账（PMS 侧，主键为 text，形态 `30000000-<尾号>`） ----
        'dim_equ_t_p_pd_stationpsr': ['id', 'parent_id', 'root_id'],
        'dim_equ_t_p_pd_switchingstationpsr': ['id', 'parent_id', 'root_id'],
        'dim_ast_t_p_pd_feederlinepsr': ['id', 'parent_id', 'root_id'],
        'dim_equ_t_p_pd_linepsr': ['id', 'parent_id', 'root_id'],
        'dim_equ_t_p_pd_cablepsr': ['id', 'parent_id', 'root_id'],
        'dim_equ_t_p_dy_linepsr': ['id', 'parent_id', 'root_id'],
        'dim_ast_t_p_dy_stationzonepsr': ['id', 'parent_id', 'root_id'],
        'dim_equ_t_p_pdzn_transformerpsr': ['id', 'parent_id', 'root_id'],
        'dim_equ_t_p_pd_optransformerpsr': ['id', 'parent_id', 'root_id'],
        'dim_ast_t_odps_sync_v_p_pd_polesitepsr': ['id', 'parent_id', 'root_id'],
        'dim_equ_m_p_rel_pdtransformer_feederline': ['id', 'transformer_psr_id',
                                                     'feeder_psr_id', 'line_psr_id', 'root_id'],
        'dim_equ_t_p_rel_transformer_publ': ['id', 'dev_id'],
        'dim_equ_t_p_rel_transformer_priv': ['id', 'dev_id'],

        # ---- 省级电网模型（D5000，主键为 bigint） ----
        'dim_grid_t_ts_sg_da_con_pwrgrid_b': ['id'],
        'dim_grid_t_ts_sg_da_con_plant_b': ['id'],
        'dim_grid_t_ts_sg_da_con_plant_capacity': ['id'],
        'dim_grid_t_ts_sg_da_tddc': ['st_id'],
        'dim_grid_t_ts_sg_da_con_substation_b': ['id'],
        'dim_grid_t_ts_sg_da_con_commonsubstation_b': ['id'],
        'dim_grid_t_ts_sg_da_conversubstation_b': ['id'],
        'dim_grid_t_ts_sg_da_dev_generator_b': ['id'],
        'dim_grid_t_ts_sg_da_acline_b': ['id'],
        'dim_grid_t_ts_sg_da_aclineend_b': ['id'],
        'dim_grid_t_ts_sg_da_tline_b': ['id'],
        'dwd_grid_t_ts_sg_da_con_jlxdjbxx': ['id'],
        'dwd_grid_t_ts_sg_da_feederline_b_new': ['id'],
        'dim_grid_t_ts_sg_da_breaker_b': ['id'],
        'dim_grid_t_ts_sg_da_busbar_b': ['id'],
        'dim_grid_t_ts_sg_da_dev_pwrtransfm_b': ['id'],
        'dim_grid_t_ts_sg_da_transfmwd_b': ['id'],

        # ---- 业务旁支（从已有链条派生） ----
        'dwd_cst_addl_charg': ['addl_charg_id', 'calc_id', 'bilg_card_id', 'inst_snap_id'],
        'dwd_cst_special_expense': ['spcl_exp_id', 'calc_id', 'bilg_card_id', 'inst_snap_id'],
        'dwd_cst_a_ll_dist_det_day': ['rec_id', 'dist_sta_id'],
        'dwd_cst_es_meter_energy_day_p': ['meter_asset_no'],
        'dwd_cst_es_e_mp_comp_curve_h': ['id', 'meter_asset_no'],
        'dwd_cst_es_e_mp_comp_curve_h_a': ['id', 'meter_asset_no'],
        'dwd_cst_es_e_mp_comp_curve_h_v': ['id', 'meter_asset_no'],
        'dwd_cst_invest_order': ['invest_order_id', 'invest_order_no', 'bus_app_form_id'],
        'dwd_cst_wkorder_affilmtrl_rec': ['wk_order_affil_mtrl_rec_id', 'bus_app_form_id'],
        'dwd_cst_gc_app_rec': ['cust_gen_app_rec_id', 'bus_app_form_id', 'cust_app_rec_id'],
        'dwd_cst_conn_gen_power_app': ['conn_gen_power_app_id', 'bus_app_form_id', 'srv_loc_app_rec_id'],
        'dwd_cst_dev_rcpt_app_dtl_info': ['dev_rcpt_app_dtl_info_id', 'app_dtl_no'],
    }

    def add(self, table, row, pk=None, claims=()):
        """pk：本表主键列名；claims：额外需要纳入台账跟踪的 ID 列。

        若调用方未传 pk，则自动回退到 ID_COLS[table] 的首列（约定为表自身主键 /
        最能唯一标识本表行的 ID 列）。这样"写过的行必定进台账"，杜绝漏删——早期
        版本靠各调用点自觉传 pk，结果 41 张写入表里有 13 张没登记，purge 会漏删。
        """
        if self.dry:
            self.counts[table] += 1
            return
        known = self.schema.get(table)
        if known is None:                       # 表不存在 → 直接报错，避免静默丢数据
            raise RuntimeError(f"目标库无此表：{table}")
        miss = {k for k in row if k not in known}
        if miss:
            self.dropped[table] |= miss
            row = {k: v for k, v in row.items() if k in known}
        row = self._coerce(table, row)
        cols = list(row.keys())
        if not cols:
            raise RuntimeError(f"{table} 无可写列（列名与真实结构完全不符）")
        idcols = self.ID_COLS.get(table, [])
        pk_col = pk or (idcols[0] if idcols else None)
        for c in set(idcols) | ({pk} if pk else set()) | set(claims):
            if c in row:
                self._track(table, c, row[c])
        self._cols_cache[table] = cols
        self.buf[table].append(tuple(row[c] for c in cols))
        self.counts[table] += 1
        if pk_col and row.get(pk_col) is not None:
            self._pk_cache[table] = pk_col
            self.ledger[table].append(row[pk_col])
        else:
            self.untracked.add(table)           # 无法登记 → 明确告警，不静默
        if len(self.buf[table]) >= 2000:
            self._flush(table)

    def check(self, table, row):
        """返回 (命中列数, 未命中列名集合)，用于生成前自检。"""
        known = self.schema.get(table, set())
        miss = {k for k in row if k not in known}
        return len(row) - len(miss), miss


    def _flush(self, table):
        rows = self.buf.pop(table)
        if not rows:
            return
        cols = self._cols_cache[table]
        ph = '(' + ','.join(['%s'] * len(cols)) + ')'
        sql = f"INSERT INTO `{table}` ({','.join('`'+c+'`' for c in cols)}) VALUES " + \
              ','.join([ph] * len(rows))
        flat = [v for r in rows for v in r]
        with self.conn.cursor() as cur:
            cur.execute(sql, flat)
        self.conn.commit()

    def note_cols(self, table, cols):
        self._cols_cache[table] = list(cols)

    def flush(self):
        if self.dry:
            return
        for t in list(self.buf.keys()):
            self._flush(t)

    def write_manifest(self, tag, db, ym_from, ym_to):
        """把本轮实际写入的**主键清单**落成本地 JSON 台账，供 --purge 精确回滚。

        为什么用主键清单而不是 ID 区间：区间一旦跨轮次累积（LEAST/GREATEST），
        就可能把真实数据圈进来，删除时误伤存量。逐主键删除不存在这个问题——
        删的每一行都是本次真正插入过的行。

        路径在**一轮生成内保持不变**（首次调用时生成时间戳并缓存），因此
        每完成一个区县覆写同一文件即可，崩溃时文件里就是"已落库的那些行"。
        写入采用 tmp + os.replace 原子替换，避免中断时留下半截 JSON。
        """
        if self.dry or not self.manifest:
            return None
        os.makedirs(MANIFEST_DIR, exist_ok=True)
        if self.manifest_path is None:
            stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
            self.manifest_path = os.path.join(MANIFEST_DIR, f"{db}__{tag}__{stamp}.json")
        payload = {
            "tag": tag, "db": db, "ym_from": ym_from, "ym_to": ym_to,
            "created_at": dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "tables": {},
        }
        for table, pks in self.ledger.items():
            if not pks:
                continue
            pk = self._pk_cache.get(table)
            if not pk:
                continue
            # 去重：日电量表的主键（meter_asset_no）逐日重复，全量跑时台账会累积
            # 上百万个重复值。删除按 IN(集合) 执行，去重后语义完全等价，但台账体积
            # 和每轮重写开销都降一个数量级。
            uniq = list(dict.fromkeys(pks))
            payload["tables"][table] = {
                "pk": pk, "rows": len(pks), "count": len(uniq), "pks": uniq,
            }
        if not payload["tables"]:
            return None
        tmp = self.manifest_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, self.manifest_path)
        return self.manifest_path


# ============================================================================
# 三、仿真引擎主体
# ============================================================================

class Simulator:

    def __init__(self, cfg):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.P = json.load(open(PROFILE, encoding='utf-8'))
        self.S = Sampler(self.P, self.rng)
        self.CB = CodeBook(CODEBOOK, self.rng)
        self.idp = IdPool()
        self.conn = pymysql.connect(
            host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password,
            database=cfg.db, charset='utf8mb4', autocommit=False)
        self.W = Writer(self.conn, cfg.dry_run)
        self.ym_list = ym_range(cfg.ym_from, cfg.ym_to)
        self.tag = cfg.tag
        self.daily_months = self._daily_month_filter()
        # 营销域 → 设备域的桥接映射：数值主键 → 设备台账 text 主键。
        # 真实库里 dim_cst_cntrl_sta.pms_cntrl_sta_id / dim_cst_pipeline.pms_pipeline_id
        # 就是指向 PMS 设备台账的业务号，打通它，本体里 GridPlant/GridLine 与
        # MgtOrg/DistributionArea 之间的关联才真正可 JOIN（真实库这两处其实是断的）。
        self.pms_sta_of = {}
        self.pms_line_of = {}
        # 机构层级路径：org -> "地市码,区县码"（真实库 path_org_code 的写法）
        self.org_path = {}
        # 电网模型的"区划内序号"表：(表, adcode) -> 已用序号
        self._gseq = defaultdict(int)

    # ---------- 工具 ----------

    # 业扩业务语义池：(业务类型名, 业扩业务类型名)。两个码表名称体系不同，
    # 逐一配对，避免用 bus_type 的名称去填 bus_categ。
    # 池内每项都已核对：bus_type 去零码必须真实库确实出现过（见下方 REAL 集合注释），
    # 否则 check_code_values 会判 B 级（如 '低压居民新装'=010301 真实库从未出现，已剔除）。
    # 注意 bus_type 名与 bus_categ 名并非同名体系（031001 合同续签 ↔ 0310 合同签订服务、
    # 032401 居民普查工单 ↔ 0324 档案普查、033301 …申请 ↔ 0333 …调整），必须逐一配对。
    BIZ_POOL = [
        ('高压新装', '高压新装增容'), ('高压增容', '高压新装增容'),
        ('低压非居民新装', '低压非居民新装增容'), ('低压非居民增容', '低压非居民新装增容'),
        ('改类', '改类'), ('更名', '更名'), ('过户', '过户'),
        ('减容', '减容'), ('暂停恢复', '暂停恢复'),
        ('客户基础信息维护', '客户基础信息维护'), ('计量设备故障处理', '计量设备故障处理'),
        ('增值税信息维护', '增值税信息维护'), ('合同续签', '合同签订服务'),
        ('改造实施', '电网侧实施'), ('居民普查工单', '档案普查'),
        ('一户多人口阶梯基数申请', '一户多人口阶梯基数调整'),
    ]

    def _biz_type(self):
        """业务类型码值：一律取自码表，并按真实库形态决定是否去前导零。

        真实库各表形态并不一致，实测：
          dwd_cst_bus_app_form.bus_type    → 去零（10101/20101/21101）
          dwd_cst_cust_elec_app_rec.bus_type → 带零（021101/030201）
          dwd_cst_preapp_order.bus_type    → 去零（21101/10102）
          dwd_cst_gc_app_rec.bus_type      → 去零（40601/32302）
        """
        cb, rng = self.CB, self.rng
        bname, cname = rng.choice(self.BIZ_POOL)
        code, desc = cb.pick_named('bus_type', bname, drop_leading_zero=True)
        code_pad, _ = cb.pick_named('bus_type', bname)          # 带前导零形态
        ccode, cdesc = cb.pick_named('bus_categ', cname, drop_leading_zero=True)
        a_code, a_desc = cb.pick_named('app_stat', '归档', drop_leading_zero=True)
        return {'bus_type': code, 'bus_type_padded': code_pad, 'bus_type_desc': desc,
                'bus_categ': ccode, 'bus_categ_desc': cdesc,
                'app_stat': a_code, 'app_stat_desc': a_desc}

    def _pms(self, kind, prefix):
        """拼一个 PMS 业务号：`<前缀>-<9 位尾号>`，形如真实库的 `30000000-102414629`。"""
        return f"{prefix}{self.idp.n(kind)}"

    def _b64id(self, kind):
        """生成 22 位 base64url 形态的 ID，贴近真实库里 UUID 风格的 `mYgC0N1NS0GrC6XxqWW0Zw`。"""
        n = self.idp.n(kind)
        raw = hashlib.sha1(f"{self.cfg.seed}:{kind}:{n}".encode()).digest()[:16]
        return base64.urlsafe_b64encode(raw).decode().rstrip('=')

    def _daily_month_filter(self):
        """--daily 决定日粒度数据的月份范围。"""
        if self.cfg.daily == 'off':
            return []
        if self.cfg.daily == 'full':
            return list(self.ym_list)
        if self.cfg.daily == 'recent':
            return self.ym_list[-3:] if len(self.ym_list) >= 3 else list(self.ym_list)
        # sampled：每月抽 5 天
        return list(self.ym_list)

    def _daily_days(self, ym):
        n = month_days(ym)
        if self.cfg.daily == 'sampled':
            return sorted(self.rng.sample(range(1, n + 1), min(5, n)))
        return list(range(1, n + 1))

    def name(self, *parts):
        return ''.join([self.tag] + [str(p) for p in parts if p is not None])

    def dtstr(self, d):
        return d.strftime('%Y-%m-%d %H:%M:%S')

    def ymstr(self, ym):
        return f"{ym//100:04d}-{ym%100:02d}"

    # ---------- 主流程 ----------

    # 实体 → (承载表, 主键列)；用于启动时按目标库已有数据抬升 ID 起点
    ID_HOME = {
        'cntrl_sta': ('dim_cst_cntrl_sta', 'cntrl_sta_id'),
        'pipeline': ('dim_cst_pipeline', 'pipeline_id'),
        'dist_sta': ('dim_cst_dist_sta', 'dist_sta_id'),
        'adj_volt': ('dim_cst_adj_volt_dev', 'adj_volt_dev_id'),
        'cust': ('dim_cst_cust', 'cust_id'),
        'bp': ('dim_cst_business_partner', 'bp_id'),
        'inst': ('dim_cst_inst_elec_cons', 'inst_id'),
        'agrt': ('dim_cst_cust_agrt', 'cust_agrt_id'),
        'settle': ('dim_cst_settle_acct', 'settle_acct_id'),
        'dev': ('dim_cst_dev', 'dev_id'),
        'meter_run': ('dwd_cst_meter_run', 'meter_id'),
        'bilg_card': ('dwd_cst_inst_bilg_card', 'bilg_card_id'),
        'calc': ('dwd_cst_mr_data', 'calc_id'),
        'sgmt': ('dwd_cst_sgmt_qty_charg', 'sgmt_qty_charg_id'),
        'rcvbl': ('dwd_cst_rcvbl_acct', 'rcvbl_acct_id'),
        'rcvbl_addl': ('dwd_cst_rcvbl_addl_acct', 'rcvbl_addl_charg_acct_id'),
        'charg': ('dwd_cst_charg_acct', 'charg_acct_id'),
        'rcvd': ('dwd_cst_rcvd_acct', 'rcvd_acct_id'),
        'acct_bal': ('dwd_cst_acct_bal', 'acct_bal_id'),
        'mr': ('dwd_cst_mr_data', 'm_r_data_id'),
        'wko': ('dwd_cst_wk_order', 'wk_order_id'),
        'step': ('dwd_cst_step_rec', 'step_acct_id'),
        'proc': ('dwd_cst_proc_acct', 'proc_acct_id'),
        'app': ('dwd_cst_bus_app_form', 'bus_app_form_id'),
        'preapp': ('dwd_cst_preapp_order', 'prepp_id'),
        'sch': ('dwd_cst_inst_elec_sch', 'inst_elec_sch_id'),
        'devrec': ('dwd_cst_dev_rcpt_app', 'app_form_id'),
        'refund': ('dwd_cst_rs_inst_bilg_card', 'rs_inst_bilg_card_id'),
        'rela': ('dim_cst_cntrl_sta_pline_rela', 'cntrl_sta_pipe_line_rela_id'),
        'rela_ds': ('dim_cst_pline_dist_sta_rela', 'pipe_line_dist_sta_rela_id'),

        # 电网设备台账（text 主键，尾号计数器）
        'pms_sta': ('dim_equ_t_p_pd_stationpsr', 'id'),
        'pms_sws': ('dim_equ_t_p_pd_switchingstationpsr', 'id'),
        'pms_feeder': ('dim_ast_t_p_pd_feederlinepsr', 'id'),
        'pms_subline': ('dim_equ_t_p_pd_linepsr', 'id'),
        'pms_cable': ('dim_equ_t_p_pd_cablepsr', 'id'),
        'pms_zone': ('dim_ast_t_p_dy_stationzonepsr', 'id'),
        'pms_dyline': ('dim_equ_t_p_dy_linepsr', 'id'),
        'pms_tf': ('dim_equ_t_p_pdzn_transformerpsr', 'id'),
        'pms_otf': ('dim_equ_t_p_pd_optransformerpsr', 'id'),
        'pms_pole': ('dim_ast_t_odps_sync_v_p_pd_polesitepsr', 'id'),
        'pms_rel': ('dim_equ_m_p_rel_pdtransformer_feederline', 'id'),
        'pms_relpub': ('dim_equ_t_p_rel_transformer_publ', 'id'),
        'pms_relprv': ('dim_equ_t_p_rel_transformer_priv', 'id'),

        # 省级电网模型：走"前缀+区划码+序号"原生编码，见 _gridid()，
        # 因此不占计数器；其区间由 preflight 单独校验（GRID_NATIVE）。

        # 业务旁支
        'addl': ('dwd_cst_addl_charg', 'addl_charg_id'),
        'spcl': ('dwd_cst_special_expense', 'spcl_exp_id'),
        'dist_det': ('dwd_cst_a_ll_dist_det_day', 'rec_id'),
        'invno': ('dwd_cst_invest_order', 'invest_order_no'),
        'affmtrl': ('dwd_cst_wkorder_affilmtrl_rec', 'wk_order_affil_mtrl_rec_id'),
        'gcapp': ('dwd_cst_gc_app_rec', 'cust_gen_app_rec_id'),
        'cgenapp': ('dwd_cst_conn_gen_power_app', 'conn_gen_power_app_id'),
        'devdtl': ('dwd_cst_dev_rcpt_app_dtl_info', 'dev_rcpt_app_dtl_info_id'),
        'curve': ('dwd_cst_es_e_mp_comp_curve_h', 'id'),
    }

    # 列名 → 计数器。用于把 ID_COLS 中声明的每一列归到对应计数器，
    # 从而让窗口分配同时规避"主键列"和"所有外键列"上的真实数据。
    COLUMN_KIND = {
        'cntrl_sta_id': 'cntrl_sta', 'pipeline_id': 'pipeline', 'dist_sta_id': 'dist_sta',
        'adj_volt_dev_id': 'adj_volt', 'cust_id': 'cust', 'bp_id': 'bp',
        'inst_id': 'inst', 'cust_agrt_id': 'agrt', 'settle_acct_id': 'settle',
        'dev_id': 'dev', 'meter_id': 'dev', 'cust_no': None,
        'meter_asset_no': 'dev', 'rcvbl_acct_id': 'rcvbl', 'charg_acct_id': 'charg',
        'rcvd_acct_id': 'rcvd', 'acct_bal_id': 'acct_bal',
        'qty_charg_calc_id': 'calc', 'calc_id': 'calc', 'bilg_card_id': 'bilg_card',
        'inst_snap_id': 'calc', 'sgmt_qty_charg_id': 'sgmt',
        'rcvbl_addl_charg_acct_id': 'rcvbl_addl',
        'wk_order_id': 'wko', 'step_acct_id': 'step', 'step_instc_id': 'wko',
        'proc_acct_id': 'proc', 'bus_app_form_id': 'app', 'prepp_id': 'preapp',
        'accs_sch_id': 'sch', 'cust_agrt_sch_id': 'sch', 'inst_elec_sch_id': 'sch',
        'meter_sch_id': 'sch', 'app_form_id': 'devrec',
        'bilg_rs_rcpt_id': 'refund', 'rs_inst_bilg_card_id': 'refund',
        'rs_qty_charg_calc_id': 'calc', 'orgn_calc_id': 'calc',
        'cust_elec_app_rec_id': 'app', 'conn_app_rec_id': 'app', 'stop_rcvr_supl_id': 'app',
        'dev_inst_rmv_wk_rec_id': 'wko',
        'cntrl_sta_pipe_line_rela_id': 'rela', 'pipe_line_dist_sta_rela_id': 'rela_ds',
        # 业务旁支
        'addl_charg_id': 'addl', 'spcl_exp_id': 'spcl', 'rec_id': 'dist_det',
        'invest_order_id': 'invest', 'invest_order_no': 'invno',
        'app_dtl_no': 'devdtl', 'srv_loc_app_rec_id': 'app', 'cust_app_rec_id': 'app',
        'wk_order_affil_mtrl_rec_id': 'affmtrl',
        'cust_gen_app_rec_id': 'gcapp', 'conn_gen_power_app_id': 'cgenapp',
        'dev_rcpt_app_dtl_info_id': 'devdtl',
    }
    # 注意：电网主数据表的 `id` / `st_id` 是 text / 哨兵值，**故意不映射到这里**。
    # 它们由 ID_HOME 与 COUNTER_USAGE 显式登记，避免 'id' 这个通用列名误伤其他表。
    # 不作为关联键、无需纳入窗口规避的列
    COLUMN_KIND_SKIP = {'bus_srv_addr_id', 'prem_id', 'srv_loc_id', 'iot_point_id', 'loc_id',
                        'meter_logic_id', 'pay_order_id'}

    def _seed_ids(self, n_counties):
        """为每个计数器定一个"安全起点"。

        规则：起点必须严格大于**该计数器所服务的全部列**在目标库中的最大值；
        若列类型是 32 位 int（如 bus_app_form_id）则会越过该上限，此时改为在
        int 范围内向下寻找一个长度足够的空闲窗口。这样既不会撞已有主键，也不会
        因区间覆盖而误删真实数据。
        """
        from collections import defaultdict as _dd
        self.usage = _dd(list)
        # 从 ID_COLS 反推每个计数器实际服务的全部 (表, 列)
        for table, cols in Writer.ID_COLS.items():
            for c in cols:
                kind = self.COLUMN_KIND.get(c)
                if kind and (table, c) not in self.usage[kind]:
                    self.usage[kind].append((table, c))
        for kind, uses in self.COUNTER_USAGE.items():
            for t, c in uses:
                if (t, c) not in self.usage[kind]:
                    self.usage[kind].append((t, c))
        for kind, (t, c) in self.ID_HOME.items():
            if (t, c) not in self.usage[kind]:
                self.usage[kind].append((t, c))

        typed, relocated = [], []
        self.relocated_kinds = set()
        self.text_kinds = {}
        with self.conn.cursor() as cur:
            cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = DATABASE()")
            ctypes = {(t, c): d for t, c, d in cur.fetchall()}

            for kind, uses in self.usage.items():
                # --- 文本型主键（电网设备台账 `前缀-尾号`）---
                # BETWEEN 用在 text 列上只会做"字符串→数字"转换比较（'10000100-6755' → 10000100），
                # 既挡不住重号、也无法做窗口分配。这里改为把起点抬到：
                #   max(存量业务号尾号, 声明起点) 与 声明起点 + 本表存量行数 的较大者。
                # 前者避免撞真实业务号，后者保证同一 tag 反复生成/不 purge 也不会重号。
                tcols = [(t, c) for t, c in uses
                         if ctypes.get((t, c)) in ('text', 'varchar', 'char')]
                if len(tcols) == len(uses) and tcols:
                    m, cnt = 0, 0
                    for t, c in uses:
                        try:
                            cur.execute(
                                f"SELECT MAX(CAST(SUBSTRING_INDEX(CAST(`{c}` AS CHAR),'-',-1) AS UNSIGNED))"
                                f" FROM `{t}` WHERE `{c}` LIKE '%-%'")
                            m = max(m, int(cur.fetchone()[0] or 0))
                            cur.execute(f"SELECT COUNT(*) FROM `{t}`")
                            cnt = max(cnt, int(cur.fetchone()[0]))
                        except Exception:
                            continue
                    start = max(IdPool.SPEC[kind][0], m + 1, IdPool.SPEC[kind][0] + cnt)
                    for t, c in uses:
                        # 注意：这里带参数，LIKE 里的字面 % 必须写成 %%，
                        # 否则 pymysql 会把它当格式符（曾因此报 unsupported format character）。
                        cur.execute(
                            f"SELECT COUNT(*) FROM `{t}` WHERE `{c}` LIKE '%%-%%' AND "
                            f"CAST(SUBSTRING_INDEX(CAST(`{c}` AS CHAR),'-',-1) AS UNSIGNED) >= %s",
                            (start,))
                        if cur.fetchone()[0]:
                            sys.exit(f"[ID] 文本主键计数器 {kind} 的起点 {start} 仍与 {t}.{c} 存量重叠")
                    self.idp.cur[kind] = start
                    self.text_kinds[kind] = start
                    continue

                # --- 数值型主键：按列类型容量求最小上限，再找空闲窗口 ---
                # 取值上限 = 该计数器所服务**全部列**类型容量的最小值。
                # 只要有一列是 32 位 int（deshu5 的 dwd 层大量沿用源系统 int 定义），
                # 整个计数器的 ID 就必须落在 int 范围内，否则关联列会被迫置空、
                # 生成的数据无法跨表 JOIN。因此这里统一取紧凑 ID 空间。
                ceilings = [2147483647 if ctypes.get((t, c)) in
                            ('int', 'mediumint', 'smallint', 'tinyint') else 2**62
                            for t, c in uses]
                ceiling = min(ceilings) if ceilings else 2**62
                reserve = self._reserve(kind, n_counties)
                free = self._alloc_window(cur, kind, uses, reserve, ceiling)
                if free is None:
                    sys.exit(f"[ID] 无法为计数器 {kind} 找到长度为 {reserve} 的空闲 ID 窗口"
                             f"（上限 {ceiling}），请调整 IdPool.SPEC 起始值")
                self.idp.cur[kind] = free

        if getattr(self, 'relocated_kinds', None):
            print(f"[ID] 改用紧凑 ID 空间（受 32 位 int 关联列约束）："
                  f"{', '.join(sorted(self.relocated_kinds))}")
        if self.text_kinds:
            print(f"[ID] 文本主键计数器起点（已抬到存量业务号之上）："
                  f"{', '.join(f'{k}={v}' for k, v in sorted(self.text_kinds.items()))}")
        print(f"[ID] 已为 {len(self.usage)} 类计数器分配安全 ID 窗口")

    # 候选起始点：先贴近声明的语义区间，再回退到紧凑区，最后在低位逐段扫描
    CANDIDATE_STARTS = [None, 200000000, 1000000000, 100000000, 50000000,
                        2000000, 1000000, 2, 1]

    def _alloc_window(self, cur, kind, uses, reserve, ceiling):
        """在 [1, ceiling) 内为计数器找一段长度 reserve、且不与真实数据重叠的空闲窗口。"""
        decl = IdPool.SPEC[kind][0]
        seen = set()
        for s in [decl] + [x for x in self.CANDIDATE_STARTS if x]:
            for cand in (s, s + reserve):
                if cand in seen or cand < 1 or cand + reserve >= ceiling:
                    continue
                seen.add(cand)
                w = self._scan(cur, uses, cand, reserve, ceiling)
                if w is not None:
                    if cand != decl:
                        self.relocated_kinds.add(kind)
                    return w
        # 兜底：从 1 起逐段扫描
        return self._scan(cur, uses, 1, reserve, ceiling, max_steps=4000)

    @staticmethod
    def _scan(cur, uses, start, reserve, ceiling, max_steps=200):
        """从 start 起向上寻找无重叠窗口，最多尝试 max_steps 段。"""
        for _ in range(max_steps):
            hi = start + reserve
            if hi >= ceiling:
                return None
            ok = True
            for t, c in uses:
                try:
                    cur.execute(f"SELECT 1 FROM `{t}` WHERE `{c}` BETWEEN %s AND %s LIMIT 1",
                                (start, hi))
                    if cur.fetchone():
                        ok = False
                        break
                except Exception:
                    continue
            if ok:
                return start
            start = hi + 1
        return None


    # 计数器 → 实际接收其值的 (表, 列)。预检据此逐列验证区间不与真实数据重叠。
    COUNTER_USAGE = {
        'app': [('dwd_cst_bus_app_form', 'bus_app_form_id'),
                ('dwd_cst_acc_sch', 'bus_app_form_id'),
                ('dwd_cst_cust_agrt_sch', 'bus_app_form_id'),
                ('dwd_cst_meter_sch', 'bus_app_form_id'),
                ('dwd_cst_cust_elec_app_rec', 'cust_elec_app_rec_id'),
                ('dwd_cst_connection_app_rec', 'conn_app_rec_id'),
                ('dwd_cst_stop_rcvr_supl_app', 'stop_rcvr_supl_id')],
        'sch': [('dwd_cst_acc_sch', 'accs_sch_id'),
                ('dwd_cst_inst_elec_sch', 'inst_elec_sch_id'),
                ('dwd_cst_cust_agrt_sch', 'cust_agrt_sch_id'),
                ('dwd_cst_meter_sch', 'meter_sch_id'),
                ('dwd_cst_meter_sch', 'inst_elec_sch_id'),
                ('dwd_cst_meter_sch', 'cust_agrt_sch_id'),
                ('dwd_cst_inst_elec_sch', 'cust_agrt_sch_id')],
        'cntrl_sta': [('dim_cst_cntrl_sta', 'cntrl_sta_id')],
        'pipeline': [('dim_cst_pipeline', 'pipeline_id')],
        'dist_sta': [('dim_cst_dist_sta', 'dist_sta_id')],
        'cust': [('dim_cst_cust', 'cust_id')],
        'inst': [('dim_cst_inst_elec_cons', 'inst_id')],
        'settle': [('dim_cst_settle_acct', 'settle_acct_id')],
        'rcvbl': [('dwd_cst_rcvbl_acct', 'rcvbl_acct_id')],
        'charg': [('dwd_cst_charg_acct', 'charg_acct_id')],
        'rcvd': [('dwd_cst_rcvd_acct', 'rcvd_acct_id')],
        'acct_bal': [('dwd_cst_acct_bal', 'acct_bal_id')],
        'dev': [('dim_cst_dev', 'dev_id')],
        'meter_run': [('dwd_cst_meter_run', 'meter_id')],
        'rela': [('dim_cst_cntrl_sta_pline_rela', 'cntrl_sta_pipe_line_rela_id')],
        'rela_ds': [('dim_cst_pline_dist_sta_rela', 'pipe_line_dist_sta_rela_id')],

        # 电网设备台账（text 主键）与省级电网模型（bigint 主键）
        'pms_sta': [('dim_equ_t_p_pd_stationpsr', 'id')],
        'pms_sws': [('dim_equ_t_p_pd_switchingstationpsr', 'id')],
        'pms_feeder': [('dim_ast_t_p_pd_feederlinepsr', 'id')],
        'pms_subline': [('dim_equ_t_p_pd_linepsr', 'id')],
        'pms_cable': [('dim_equ_t_p_pd_cablepsr', 'id')],
        'pms_zone': [('dim_ast_t_p_dy_stationzonepsr', 'id')],
        'pms_dyline': [('dim_equ_t_p_dy_linepsr', 'id')],
        'pms_tf': [('dim_equ_t_p_pdzn_transformerpsr', 'id')],
        'pms_otf': [('dim_equ_t_p_pd_optransformerpsr', 'id')],
        'pms_pole': [('dim_ast_t_odps_sync_v_p_pd_polesitepsr', 'id')],
        'pms_rel': [('dim_equ_m_p_rel_pdtransformer_feederline', 'id')],
        'pms_relpub': [('dim_equ_t_p_rel_transformer_publ', 'id')],
        'pms_relprv': [('dim_equ_t_p_rel_transformer_priv', 'id')],

        # 业务旁支
        'addl': [('dwd_cst_addl_charg', 'addl_charg_id')],
        'spcl': [('dwd_cst_special_expense', 'spcl_exp_id')],
        'dist_det': [('dwd_cst_a_ll_dist_det_day', 'rec_id')],
        'invno': [('dwd_cst_invest_order', 'invest_order_no')],
        'affmtrl': [('dwd_cst_wkorder_affilmtrl_rec', 'wk_order_affil_mtrl_rec_id')],
        'gcapp': [('dwd_cst_gc_app_rec', 'cust_gen_app_rec_id')],
        'cgenapp': [('dwd_cst_conn_gen_power_app', 'conn_gen_power_app_id')],
        'devdtl': [('dwd_cst_dev_rcpt_app_dtl_info', 'dev_rcpt_app_dtl_info_id')],
        'curve': [('dwd_cst_es_e_mp_comp_curve_h', 'id')],
    }

    # 文本主键的"重号"防线不由 BETWEEN 承担（见 _seed_ids 里的文本分支），
    # 而是在 _seed_ids 中把起点抬到存量业务号尾号之上，并逐列复核。
    # 电网模型的原生编码（前缀+区划码+序号）则在 preflight_native_grid() 中校验。

    # 每个计数器**每区县**预计分配的 ID 个数（宽松上界，用于预留窗口大小）
    PER_COUNTY = {
        'cntrl_sta': 4, 'pipeline': 8, 'dist_sta': 8, 'adj_volt': 8, 'rela': 8, 'rela_ds': 16,
        'cust': 30, 'bp': 30, 'inst': 30, 'agrt': 30, 'settle': 30, 'dev': 30, 'meter_run': 30,
        'app': 16, 'sch': 64, 'wko': 16, 'step': 80, 'proc': 16, 'preapp': 16,
        'devrec': 2, 'refund': 4, 'acct_bal': 30,
        'calc': 500, 'mr': 500, 'bilg_card': 500, 'sgmt': 1500,
        'rcvbl': 500, 'rcvbl_addl': 500, 'charg': 500, 'rcvd': 500,
        # 电网设备域（每区县一套配网设备 + 一片电网模型）
        'pms_sta': 3, 'pms_sws': 2, 'pms_feeder': 6, 'pms_subline': 10, 'pms_cable': 3,
        'pms_zone': 8, 'pms_dyline': 16, 'pms_tf': 8, 'pms_otf': 3, 'pms_pole': 4,
        'pms_rel': 8, 'pms_relpub': 8, 'pms_relprv': 3,
        # 业务旁支
        'addl': 2000, 'spcl': 300, 'dist_det': 3000, 'curve': 300,
        'invno': 20, 'affmtrl': 40, 'gcapp': 20, 'cgenapp': 20, 'devdtl': 10,
    }

    def _reserve(self, kind, counties):
        per = self.PER_COUNTY.get(kind, 40)
        return int(per * max(1, counties) * 1.5) + 1000

    def preflight(self, n_counties):
        """写前安全校验：确认每个计数器预留的 ID 窗口内没有真实数据。

        这是本引擎不误伤生产数据的硬保证——若有重叠直接中止，绝不带病写入。
        """
        problems = []
        with self.conn.cursor() as cur:
            for kind, uses in self.COUNTER_USAGE.items():
                start = self.idp.cur[kind]
                hi = start + self._reserve(kind, n_counties)
                for t, c in uses:
                    cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE "
                                "table_schema=DATABASE() AND table_name=%s AND column_name=%s",
                                (t, c))
                    if not cur.fetchone()[0]:
                        continue
                    cur.execute(f"SELECT COUNT(*) FROM `{t}` WHERE `{c}` BETWEEN %s AND %s",
                                (start, hi))
                    n = cur.fetchone()[0]
                    if n:
                        problems.append((kind, t, c, start, hi, n))
        if problems:
            print("\n[预检失败] 以下仿真 ID 窗口与目标库已有数据重叠，已中止写入：")
            for kind, t, c, lo, hi, n in problems:
                print(f"    [{kind}] {t}.{c} 窗口 [{lo}, {hi}] 命中已有 {n} 行")
            print("  处理建议：换一个 --tag 重新生成（ID 会再次抬升），或人工确认后调整 "
                  "IdPool.SPEC 中该实体的起始值。")
            sys.exit(2)
        print(f"[预检] {len(self.COUNTER_USAGE)} 类计数器 ID 窗口均与存量数据无重叠，可安全写入")
        self.preflight_native_grid(getattr(self, '_counties', []))

    def preflight_native_grid(self, counties):
        """电网模型的"原生编码"主键（前缀+区划码+序号）冲突校验。

        这些表的主键不是计数器生成的，而是按真实编码规则拼出来的，因此必须单独
        确认目标库里没有落在同一区划段的既有行——否则会静默覆盖真实电网模型。
        """
        adcodes = sorted({str(c.get('region_adcode') or '') for c in counties
                          if c.get('region_adcode')})
        if not adcodes:
            return
        cities = sorted({a[:4] + '00' for a in adcodes})
        problems = []
        with self.conn.cursor() as cur:
            # 区县粒度：id 的第 4~9 位是区划码
            for table in self.GRID_PREFIX:
                try:
                    cur.execute(
                        f"SELECT COUNT(*) FROM `{table}` WHERE "
                        f"SUBSTRING(CAST(`id` AS CHAR),4,6) IN ({','.join(['%s'] * len(adcodes))})",
                        adcodes)
                    n = cur.fetchone()[0]
                except Exception as e:
                    problems.append((table, f"校验失败：{e}"))
                    continue
                if n:
                    problems.append((table, f"已存在 {n} 行 id 的区划段落在本次仿真区划内"))
            # 省/地市粒度：候选主键已存在的行（真实字典已覆盖）直接剔除，只补缺的
            plan = self._grid_region_plan(counties)
            skipped = 0
            for table, items in plan.items():
                col = 'st_id' if table.endswith('_tddc') else 'id'
                ids = [x[0] for x in items]
                cur.execute(f"SELECT `{col}` FROM `{table}` WHERE `{col}` IN "
                            f"({','.join(['%s'] * len(ids))})", ids)
                exist = {r[0] for r in cur.fetchall()}
                if exist:
                    skipped += len(exist)
                    plan[table] = [x for x in items if x[0] not in exist]
            self._grid_plan = plan
        if problems:
            print("\n[预检失败] 电网模型原生编码与目标库已有数据冲突，已中止写入：")
            for t, why in problems:
                print(f"    {t}: {why}")
            sys.exit(3)
        extra = f"，{skipped} 个省市主键真实库已有、将跳过" if skipped else ""
        print(f"[预检] 电网模型原生编码无冲突（{len(adcodes)} 个区县表区划、"
              f"{len(cities)} 个地市、{len(self.GRID_PREFIX)} 张区县表 + {len(plan)} 张省市表{extra}）")


    def run(self):
        counties = self.P['counties']
        if self.cfg.county_codes:
            wanted = set(self.cfg.county_codes)
            counties = [c for c in counties if c['mgt_org_code'] in wanted]
        elif self.cfg.counties:
            counties = counties[:self.cfg.counties]
        print(f"[仿真] 区县 {len(counties)} 个｜期间 {self.ym_list[0]}~{self.ym_list[-1]}"
              f"（{len(self.ym_list)} 个月）｜日粒度 {self.cfg.daily}｜目标库 {self.cfg.db}")
        if not self.cfg.dry_run:
            self._counties = counties
            self._seed_ids(len(counties))
            self.preflight(len(counties))
        # 电网模型里"省/地市"粒度的表（电网、电厂、变电站、公共电站）全省只有一份，
        # 必须在区县循环之外生成一次，否则会按区县重复造出 96 个"浙江省电网"。
        self._gen_grid_model_regions(counties)
        for i, cty in enumerate(counties, 1):
            self.gen_county(cty, i)
            # 每个区县落一次：先 flush 落库，再把"已插入的主键清单"写进台账文件，
            # 即使中途崩溃，台账也覆盖了所有已落库的行，可安全回滚。
            self.W.flush()
            if not self.cfg.dry_run:
                self.W.write_manifest(self.cfg.tag, self.cfg.db,
                                      self.cfg.ym_from, self.cfg.ym_to)
            if i % 10 == 0 or i == len(counties):
                print(f"  ... 已完成 {i}/{len(counties)} 个区县，累计写入 "
                      f"{sum(self.W.counts.values()):,} 行")
        self.W.flush()
        if not self.cfg.dry_run:
            self.conn.commit()
            path = self.W.write_manifest(self.cfg.tag, self.cfg.db,
                                         self.cfg.ym_from, self.cfg.ym_to)
            n_tab = sum(len(v) for v in self.W.ledger.values())
            print(f"[台账] 主键清单已写入 {path}")
            print(f"       覆盖 {len([k for k, v in self.W.ledger.items() if v])} 张表、"
                  f"{n_tab:,} 个主键（tag={self.cfg.tag}）")
        self.report()

    # ---------- 单个区县的完整生成 ----------

    def gen_county(self, cty, idx):
        org = cty['mgt_org_code']
        abbr = (cty.get('region_name') or cty['mgt_org_name'] or 'X')[:6]
        rng = self.rng
        # 机构层级路径（真实库 path_org_code 存的是已登记的机构/层级码；少数未登记的
        # 区县机构码回退到地市码，避免写出码表外的值）
        pc = cty.get('county_code') or org
        if not self.CB.has_code('path_org_code', pc):
            pc = cty.get('city_code') or pc
        self.org_path[org] = pc

        # ---- A. 电网侧拓扑 ----
        sta220 = self._station(org, abbr, '220kV', 220, cap_pool=CAP_220)
        line110 = self._line(org, abbr, '110kV', 110, idx)
        sta110 = self._station(org, abbr, '110kV', 110, cap_pool=CAP_110)
        self._rela(org, sta220, line110)
        self._rela(org, line110, sta110)
        # 220kV 站同时直连 110kV 站所在母线路径（拓扑闭合）

        n_line10 = 2
        lines10 = [self._line(org, abbr, '10kV', 10, idx * 10 + k) for k in range(n_line10)]
        for l in lines10:
            self._rela(org, sta110, l)

        n_dist = rng.randint(3, 5)
        dist_stas, dist_devs = [], []
        for k in range(n_dist):
            ds, tr = self._dist_sta(org, abbr, idx, k)
            dist_stas.append(ds)
            line = lines10[k % n_line10]
            self._pline_dist_rela(org, line, ds)
            aid = self._adj_volt_dev(org, abbr, ds, tr, k)
            dist_devs.append({'dist_sta': ds, 'cap': tr, 'adj_volt': aid, 'line': line})

        # ---- B. 客户侧主数据 + 计量装置 ----
        n_user = rng.randint(18, 22)
        users = []
        for u in range(n_user):
            users.append(self._customer(org, cty, abbr, idx, u, dist_stas, lines10, sta110))

        ctx = {'org': org, 'abbr': abbr, 'idx': idx, 'cty': cty,
               'sta220': sta220, 'sta110': sta110, 'line110': line110,
               'lines10': lines10, 'dist_stas': dist_stas, 'dist_devs': dist_devs}

        # ---- C. 业务数据 ----
        for cu in users:
            self._gen_monthly_business(cu)
            self._gen_daily_energy(cu, dist_stas)
        self._gen_account_balance(users)
        ctx['flow'] = self._gen_workflow(org, cty, abbr, idx, users)
        self._gen_refund(org, users)

        # ---- D. 电网设备域台账 + 省级电网模型（与上面拓扑一一镜像） ----
        self._gen_grid_assets(ctx, users)

        # ---- E. 业务旁支表（从已生成的链条派生，保证勾稽一致） ----
        self._gen_derived_business(ctx, users)

    # ---------- A. 电网侧主数据 ----------

    def _station(self, org, abbr, label, kv, cap_pool):
        sid = self.idp.n('cntrl_sta')
        cap = self.rng.choice(cap_pool)
        # pms 业务号：不是"再取一个 cntrl_sta"，而是独立的设备域计数器。
        # 旧写法 f"9{self.idp.n('cntrl_sta')}" 有两个毛病：一是白耗一个营销 ID，
        # 二是与设备台账对不上号，桥接天然断链。
        pms = self._pms('pms_sta', '30000000-')
        self.pms_sta_of[sid] = pms
        row = {
            'cntrl_sta_id': sid,
            'resrc_supl_code': self.idp.code('cntrl_sta', 10)[:10],
            'resrc_supl_name': f"{abbr}{label}{self.tag}变电站",
            'resrc_supl_stat_desc': self.S.pick('flags.cntrl_sta_status', '运行'),
            'srv_kind_desc': self.S.pick('flags.srv_kind', '电类'),
            'det_addr': f"{abbr}{self.tag}{label}变电站",
            'voltage_desc': V220 if kv == 220 else (V110 if kv == 110 else V35),
            'dev_cap': float(cap),
            'pms_cntrl_sta_id': pms,
            'conn_line_id': None,
            'mgt_org_code': org,
        }
        self.W.note_cols('dim_cst_cntrl_sta', row.keys())
        self.W.add('dim_cst_cntrl_sta', row, pk='cntrl_sta_id')
        return sid

    def _line(self, org, abbr, label, kv, seq):
        pid = self.idp.n('pipeline')
        pub = '专线' if kv >= 110 else self.S.pick('flags.pipeline_publ_clg', '公线')
        pms = self._pms('pms_feeder', '10000100-')
        self.pms_line_of[pid] = pms
        row = {
            'pipeline_id': pid,
            'pipeline_no': f"{seq:03d}"[-3:],
            'pipeline_name': f"{abbr}{label}{self.tag}{seq}线",
            'branch_flag_desc': '否',
            'publ_clg_flag': 2 if pub == '专线' else 1,
            'publ_clg_flag_desc': pub,
            'pipeline_len': round(self.rng.uniform(1.2, 18.5), 2),
            'voltage_desc': {220: V220, 110: V110, 10: V10}.get(kv, V10),
            'pip_line_run_stat_desc': self.S.pick('flags.pipeline_run_stat', '运行'),
            'bus_srv_addr_id': None,
            'pms_pipeline_id': pms,
            # 真实库 path_org_code 是逗号分隔的层级路径（地市,区县），非单一机构码
            'path_org_code': self.org_path.get(org, org),
            'path_org_name': f"{abbr}{self.tag}",
            'cust_id': None,
            'mgt_org_code': org,
            'line_publ_clg_flag': '02' if pub == '专线' else '01',
            'line_publ_clg_flag_desc': pub,
        }
        self.W.note_cols('dim_cst_pipeline', row.keys())
        self.W.add('dim_cst_pipeline', row, pk='pipeline_id')
        return pid

    def _dist_sta(self, org, abbr, ci, k):
        did = self.idp.n('dist_sta')
        cap = self.rng.choice(CAP_DIST[:9])
        row = {
            'dist_sta_id': did,
            'resrc_supl_code': self.idp.code('dist_sta', 10)[:10],
            'resrc_supl_name': f"{abbr}{self.tag}{k+1}号公变台区",
            'publ_clg_flag_desc': '公变',
            'resrc_supl_stat_desc': self.S.pick('flags.dist_sta_status', '运行'),
            'srv_kind_desc': '电类',
            'voltage_desc': V10,
            'dist_stacap': float(cap),
            'mgt_org_code': org,
        }
        self.W.note_cols('dim_cst_dist_sta', row.keys())
        self.W.add('dim_cst_dist_sta', row, pk='dist_sta_id')
        return did, cap

    def _adj_volt_dev(self, org, abbr, dist_sta, cap, k):
        """台区下的配电变压器。"""
        aid = self.idp.n('adj_volt')
        row = {
            'adj_volt_dev_id': aid,
            'dist_sta_id': dist_sta,
            'dev_no': str(aid),
            'dev_name': f"{abbr}{self.tag}{k+1}号配变",
            'dev_type_desc': '变压器',
            'publ_clg_flag_desc': '公变',
            'np_cap': float(cap),
            # 真实库该表 run_stat_desc 主流是"运行"（C 级：码表 dev_stat 有 20=运行）
            'run_stat_desc': self.CB.name_of('dev_stat', '20'),
            'voltage_desc': V10,
            'primary_side_bearing_desc': V10,
            'secondary_side_bearing_desc': V380,
            'adj_volt_dev_asset_id': aid,
            'cust_id': None,
            'loc_id': aid,
            'mgt_org_code': org,
        }
        self.W.note_cols('dim_cst_adj_volt_dev', row.keys())
        self.W.add('dim_cst_adj_volt_dev', row, pk='adj_volt_dev_id')
        return aid

    def _rela(self, org, sta_or_line_a, b):
        """变电站↔线路 关系。"""
        rid = self.idp.n('rela')
        row = {'cntrl_sta_pipe_line_rela_id': rid,
               'cntrl_sta_id': sta_or_line_a, 'pipeline_id': b}
        self.W.note_cols('dim_cst_cntrl_sta_pline_rela', row.keys())
        self.W.add('dim_cst_cntrl_sta_pline_rela', row)

    def _pline_dist_rela(self, org, line, dist_sta):
        rid = self.idp.n('rela_ds')
        row = {'pipe_line_dist_sta_rela_id': rid, 'pipeline_id': line, 'dist_sta_id': dist_sta}
        self.W.note_cols('dim_cst_pline_dist_sta_rela', row.keys())
        self.W.add('dim_cst_pline_dist_sta_rela', row)

    # ---------- B. 客户侧主数据 ----------

    def _customer(self, org, cty, abbr, ci, ui, dist_stas, lines10, sta110):
        rng = self.rng
        S = self.S
        # 用电类别：约 60% 低压居民、25% 低压非居民、15% 高压
        r = rng.random()
        if r < 0.60:
            cls, volt, categ = '低压居民', V220V, '城镇居民生活用电'
        elif r < 0.85:
            cls, volt, categ = '低压非居民', V380, '商业用电'
        else:
            cls, volt, categ = '高压', V10, '大工业用电'
        # 允许少量由真实分布覆盖
        categ = S.pick('customer.ec_categ', categ) or categ
        prc_code = S.pick('customer.prc_code', '1070')
        prc_name = S.pick('customer.ctlg_prc_name', None)
        price = PRICE.get(categ, PRICE_DEFAULT)

        cap = {'低压居民': round(rng.uniform(2, 10), 1),
               '低压非居民': round(rng.uniform(4, 30), 1),
               '高压': float(rng.choice([100, 160, 200, 250, 315, 400, 500, 630, 800]))}[cls]

        bp_id = self.idp.n('bp')
        cust_id = self.idp.n('cust')
        cust_no = f"{cust_id % 1000000:06d}"
        dist_sta = rng.choice(dist_stas)
        line = rng.choice(lines10)
        is_high = cls == '高压'

        # 业务伙伴（真实库 bp_categ_desc 是"个人/组织"，bp_type_desc 是"居民/非居民"）
        bp_name = f"{abbr}{self.tag}用户{ui+1:02d}"
        self.W.add('dim_cst_business_partner',
                   {'bp_id': bp_id, 'prtr_no': f"{bp_id % 100000000:08d}", 'bp_name': bp_name,
                    'bp_categ_desc': self.CB.name_of('bp_categ', '01' if cls != '高压' else '02'),
                    'bp_type_desc': self.CB.name_of('bp_type', '01' if cls != '高压' else '02'),
                    'bp_stat': '有效'}, pk='bp_id')

        # 客户
        self.W.note_cols('dim_cst_cust',
                         ['cust_id', 'cust_no', 'cust_name', 'bp_id', 'creat_date', 'ec_addr',
                          'urbanrural_categ', 'urbanrural_categ_desc', 'cust_ind_cls',
                          'cust_ind_cls_desc', 'cust_ind_ustry_cls', 'impt_lv_desc',
                          'billing_unit_no', 'mr_unit_no', 'bus_srv_addr_id', 'mgt_org_code', 'write_time'])
        self.W.add('dim_cst_cust', {
            'cust_id': cust_id, 'cust_no': cust_no, 'cust_name': bp_name, 'bp_id': bp_id,
            'creat_date': self.dtstr(dt.date(2025, 1, 1) - dt.timedelta(days=rng.randint(30, 3000))),
            'ec_addr': f"{abbr}{self.tag}用户{ui+1:02d}号地址",
            'urbanrural_categ': '02' if rng.random() < .4 else '01',
            'urbanrural_categ_desc': S.pick('customer.urbanrural_categ', '城市'),
            'cust_ind_cls': None,
            'cust_ind_cls_desc': S.pick('customer.cust_ind_cls_desc', '城镇居民'),
            'cust_ind_ustry_cls': S.pick('customer.cust_ind_ustry_cls', '城镇居民'),
            'impt_lv_desc': S.pick('customer.impt_lv', '非重要用户'),
            'billing_unit_no': f"H{org}{ui:06d}",
            'mr_unit_no': f"{org}{ui:06d}",
            'bus_srv_addr_id': cust_id, 'mgt_org_code': org,
            'write_time': self.dtstr(dt.datetime.now()),
        }, pk='cust_id')

        # 用电客户（宽表，只填关键列）
        self.W.note_cols('dim_cst_elec_cons_cust',
                         ['elec_cons_cust_id', 'bp_id', 'cust_id', 'cust_no', 'cust_name',
                          'estab_acct_date', 'ecc_stat_desc', 'ctrt_cap', 'run_cap',
                          'cust_cls_desc', 'ec_categ_desc', 'cust_volt_desc', 'impt_lv_desc',
                          'urbanrural_flag_desc', 'bus_county_code', 'mgt_org_code', 'write_time'])
        self.W.add('dim_cst_elec_cons_cust', {
            'elec_cons_cust_id': cust_id, 'bp_id': bp_id, 'cust_id': cust_id, 'cust_no': cust_no,
            'cust_name': bp_name, 'estab_acct_date': self.dtstr(dt.date(2025, 1, 1)), 'ecc_stat_desc': '正常',
            'ctrt_cap': cap, 'run_cap': cap, 'cust_cls_desc': cls, 'ec_categ_desc': categ,
            'cust_volt_desc': volt, 'impt_lv_desc': S.pick('customer.impt_lv', '非重要用户'),
            'urbanrural_flag_desc': S.pick('customer.urbanrural', '城市'),
            'bus_county_code': cty.get('region_adcode'), 'mgt_org_code': org,
            'write_time': self.dtstr(dt.datetime.now()),
        }, pk='elec_cons_cust_id')

        # 结算账户
        settle_id = self.idp.n('settle')
        self.W.note_cols('dim_cst_settle_acct',
                         ['settle_acct_id', 'settle_acct_no', 'settle_acct_name', 'settle_acct_alias',
                          'settle_acct_categ_desc', 'bill_mode_desc', 'pay_mode_desc', 'prepay_mode_desc',
                          'inv_mode_desc', 'note_type_desc', 'valid_flag_desc', 'mgt_org_code',
                          'bp_id', 'settle_acct_stat', 'cust_no', 'acct_usage_type', 'pay_chan_desc'])
        pay_mode = S.pick('customer.pay_mode', '电力网点交费')
        self.W.add('dim_cst_settle_acct', {
            'settle_acct_id': settle_id, 'settle_acct_no': f"{3}{settle_id % 10**11:011d}",
            'settle_acct_name': bp_name, 'settle_acct_alias': bp_name,
            'settle_acct_categ_desc': S.pick('customer.settle_acct_categ', '普通'),
            'bill_mode_desc': S.pick('customer.bill_mode', '单开'),
            'pay_mode_desc': pay_mode, 'prepay_mode_desc': '现金',
            'inv_mode_desc': S.pick('customer.inv_mode', '单开'),
            'note_type_desc': S.pick('customer.note_type', '全电普通发票'),
            'valid_flag_desc': '有效', 'mgt_org_code': org, 'bp_id': bp_id,
            'settle_acct_stat': '02', 'cust_no': cust_no, 'acct_usage_type': '01',
            'pay_chan_desc': pay_mode,
        }, pk='settle_acct_id')

        # 客户协议
        agrt_id = self.idp.n('agrt')
        self.W.note_cols('dim_cst_cust_agrt',
                         ['cust_agrt_id', 'cust_id', 'ctrt_cap', 'run_cap', 'cust_cls_desc',
                          'ec_categ_desc', 'cust_volt_desc', 'urbanrural_flag_desc', 'agrt_categ_desc',
                          'prc_code', 'bilg_std_ver_no', 'ctlg_prc_name', 'time_sec_num',
                          'timesec_flag_desc', 'srv_loc_id', 'settle_acct_id', 'mgt_org_code'])
        self.W.add('dim_cst_cust_agrt', {
            'cust_agrt_id': agrt_id, 'cust_id': cust_id, 'ctrt_cap': cap, 'run_cap': cap,
            'cust_cls_desc': cls, 'ec_categ_desc': categ, 'cust_volt_desc': volt,
            'urbanrural_flag_desc': S.pick('customer.urbanrural', '城市'),
            'agrt_categ_desc': S.pick('customer.agrt_categ', '消费协议'),
            'prc_code': prc_code, 'bilg_std_ver_no': '2147483647',
            'ctlg_prc_name': prc_name or f"202112{cls}：{volt}：单费率：单一制",
            'time_sec_num': 2 if rng.random() < .4 else 1,
            'timesec_flag_desc': '是' if rng.random() < .4 else '否',
            'srv_loc_id': cust_id, 'settle_acct_id': settle_id, 'mgt_org_code': org,
        }, pk='cust_agrt_id')

        # 计量点
        inst_id = self.idp.n('inst')
        inst_cap = cap
        affil = '高压侧' if is_high else '低压侧'
        self.W.note_cols('dim_cst_inst_elec_cons',
                         ['inst_id', 'inst_no', 'inst_name', 'cust_id', 'inst_cls_desc',
                          'inst_char_desc', 'inst_affil_side_desc', 'inst_usage_cls_desc',
                          'inst_cap', 'prc_code', 'ctlg_prc_name', 'cust_agrt_id', 'prem_id',
                          'srv_loc_id', 'iot_point_id', 'pipeline_id', 'dist_sta_id',
                          'cntrl_sta_id', 'mgt_org_code', 'loc_id'])
        self.W.add('dim_cst_inst_elec_cons', {
            'inst_id': inst_id, 'inst_no': f"{inst_id % 10**11:011d}", 'inst_name': f"{bp_name}计量点",
            'cust_id': cust_id, 'inst_cls_desc': '用电客户', 'inst_char_desc': '结算',
            'inst_affil_side_desc': affil, 'inst_usage_cls_desc': '售电侧结算',
            'inst_cap': inst_cap, 'prc_code': prc_code,
            'ctlg_prc_name': prc_name or f"202112{cls}：{volt}：单费率：单一制",
            'cust_agrt_id': agrt_id, 'prem_id': cust_id, 'srv_loc_id': cust_id,
            'iot_point_id': cust_id, 'pipeline_id': line, 'dist_sta_id': dist_sta,
            'cntrl_sta_id': sta110, 'mgt_org_code': org, 'loc_id': inst_id,
        }, pk='inst_id')

        # 电能表（设备台账 + 电能表参数 + 计量点运行）
        dev_id = self.idp.n('dev')
        # 计量方式决定变比：高压 80% 高供高计(PT=100)、20% 高供低计(PT=1)；低压直通(1:1)。
        # 先定 pt/ct 再算 rto，保证 comp_rto == tv × ta，曲线表 tv/ta 与此天然一致。
        if is_high:
            if rng.random() < 0.8:
                pt, ct = PT_HV, rng.choice(CT_HV)
            else:
                pt, ct = 1, rng.choice(CT_HV_LOW)
        else:
            pt, ct = 1, 1
        rto = pt * ct
        self.W.note_cols('dim_cst_dev',
                         ['dev_id', 'bar_code', 'asset_no', 'dev_cls_desc', 'categ_desc',
                          'dev_stat_desc', 'instal_date', 'mgt_org_code'])
        self.W.add('dim_cst_dev', {
            'dev_id': dev_id, 'bar_code': f"333{dev_id % 10**17:017d}"[:22],
            'asset_no': f"{dev_id % 10**14:014d}", 'dev_cls_desc': '电能表',
            'categ_desc': S.pick('metering.meter_categ', '智能电能表'),
            # 真实库该表 1716/1751 行是"在运"（码表未登记该名称，取真实主流形态）
            'dev_stat_desc': '运行', 'instal_date': self.dtstr(dt.date(2025, 1, 1)),
            'mgt_org_code': org,
        }, pk='dev_id')

        wire = S.pick('metering.wire_mode_desc', '单相' if not is_high else '三相四线')
        self.W.note_cols('dim_cst_elec_meter',
                         ['dev_id', 'bar_code', 'rv_desc', 'cali_cur_desc', 'wire_mode_desc',
                          'self_rto', 'bidi_meter_flag_desc', 'prc_id', 'asset_no', 'mgt_org_code'])
        self.W.add('dim_cst_elec_meter', {
            'dev_id': dev_id, 'bar_code': f"333{dev_id % 10**17:017d}"[:22],
            'rv_desc': S.pick('metering.rv_desc', V380 if is_high else V220V),
            'cali_cur_desc': S.pick('metering.cali_cur_desc', '5(60)A'),
            'wire_mode_desc': wire, 'self_rto': ct,
            'bidi_meter_flag_desc': S.pick('metering.bidi_flag', '否'),
            'prc_id': prc_code, 'asset_no': f"{dev_id % 10**14:014d}", 'mgt_org_code': org,
        })

        meter_asset_no = f"{dev_id % 10**14:014d}"
        mr_id = self.idp.n('meter_run')
        self.W.note_cols('dwd_cst_meter_run',
                         ['meter_id', 'comp_rto', 'ref_meter_flag_desc', 'dev_cls_desc',
                          'share_flag_desc', 'loc_id', 'meter_logic_id', 'inst_id', 'cust_id',
                          'dev_no', 'bar_code', 'mgt_org_code', 'meter_asset_no'])
        self.W.add('dwd_cst_meter_run', {
            'meter_id': dev_id, 'comp_rto': rto, 'ref_meter_flag_desc': '否', 'dev_cls_desc': '电能表',
            'share_flag_desc': '否', 'loc_id': dev_id, 'meter_logic_id': mr_id,
            'inst_id': inst_id, 'cust_id': cust_id, 'dev_no': meter_asset_no,
            'bar_code': f"333{dev_id % 10**17:017d}"[:22], 'mgt_org_code': org,
            'meter_asset_no': meter_asset_no,
        })

        return {'org': org, 'abbr': abbr, 'cust_id': cust_id, 'cust_no': cust_no,
                'bp_id': bp_id, 'bp_name': bp_name, 'inst_id': inst_id, 'dev_id': dev_id,
                'settle_id': settle_id, 'agrt_id': agrt_id, 'cls': cls, 'categ': categ,
                'volt': volt, 'prc_code': prc_code, 'price': price, 'cap': cap, 'rto': rto,
                'pt': pt, 'ct': ct,
                'is_high': is_high, 'dist_sta': dist_sta, 'line': line, 'sta110': sta110,
                'meter_asset_no': meter_asset_no}

    # ---------- C. 业务数据：月度量费闭环 ----------

    def _monthly_qty(self, cu, ym):
        """按容量/类别/季节性推月电量（kWh）。"""
        base = {'低压居民': 260, '低压非居民': 1500, '高压': 45000}[cu['cls']]
        if cu['cls'] == '高压':
            base = cu['cap'] * self.rng.uniform(55, 95)
        elif cu['cls'] == '低压非居民':
            base = cu['cap'] * self.rng.uniform(30, 70)
        season = SEASON[(ym % 100) - 1]
        return max(1.0, round(base * season * self.rng.uniform(0.88, 1.12), 2))

    def _gen_monthly_business(self, cu):
        org = cu['org']
        cu.setdefault('bills', [])      # 供业务旁支表（加收明细/力调）精确引用，保证勾稽
        for ym in self.ym_list:
            price = cu['price']
            qty = self._monthly_qty(cu, ym)
            calc_id = self.idp.n('calc')
            bilg_id = self.idp.n('bilg_card')
            deg_exp = round(qty * price, 2)
            addl = round(deg_exp * self.rng.uniform(0, 0.06), 2)
            settle_exp = round(deg_exp + addl, 2)
            # 分段（峰/尖/谷）：高压走三费率，低压单费率全部进 settle_qty_03。
            # 用"先取整前两段、余额给第三段"的方式，保证 int 之和严格等于 int(总电量)，
            # 避免各自 round 后合计与计费卡总电量差 1-2 kWh。
            buckets = [0.0] * 5
            tot_q = int(qty)
            if cu['cls'] == '高压':
                b2 = int(tot_q * 0.35)
                b3 = int(tot_q * 0.40)
                buckets[2], buckets[3], buckets[4] = b2, b3, tot_q - b2 - b3
            else:
                buckets[2] = tot_q

            # 1) 抄表
            month_len = month_days(ym)
            d1 = dt.date(ym // 100, ym % 100, 1)
            d2 = d1 + dt.timedelta(days=min(27, month_len - 1))
            last_mr = round(self.rng.uniform(0, 50000), 2)
            this_mr = round(last_mr + qty / max(cu['rto'], 1), 2)
            self.W.note_cols('dwd_cst_mr_data',
                             ['m_r_data_id', 'calc_id', 'qty_charg_ym', 'plan_no', 'inst_id',
                              'meter_asset_no', 'mr_sn', 'read_type', 'last_read_frz_date',
                              'last_read_act_date', 'last_m_r', 'last_mr_qty', 'this_read_frz_date',
                              'this_read_act_date', 'this_m_r', 'this_mr_qty', 'comp_rati',
                              'mr_stat', 'data_src', 'mr_abnor_categ', 'actl_mr_mode', 'exp_ymd',
                              'dist_sta_no', 'cust_volt_code'])
            self.W.add('dwd_cst_mr_data', {
                'm_r_data_id': self.idp.n('mr'), 'calc_id': calc_id, 'qty_charg_ym': ym,
                'plan_no': f"1{calc_id % 10**12:012d}", 'inst_id': cu['inst_id'],
                'meter_asset_no': cu['meter_asset_no'], 'mr_sn': self.rng.randint(1, 90),
                'read_type': self.S.pick('bill_ratio.read_type', '01'),
                'last_read_frz_date': self.dtstr(d1), 'last_read_act_date': self.dtstr(d1),
                'last_m_r': last_mr, 'last_mr_qty': round(qty * self.rng.uniform(0.9, 1.1), 2),
                'this_read_frz_date': self.dtstr(d2), 'this_read_act_date': self.dtstr(d2),
                'this_m_r': this_mr, 'this_mr_qty': round(qty * self.rng.uniform(0.9, 1.1), 2),
                'comp_rati': cu['rto'], 'mr_stat': '02',
                'data_src': self.S.pick('bill_ratio.data_src', '06'),
                'mr_abnor_categ': '01', 'actl_mr_mode': '08',
                'exp_ymd': int(d2.strftime('%Y%m%d')), 'dist_sta_no': str(cu['dist_sta']),
                'cust_volt_code': '117' if cu['is_high'] else ('108' if cu['volt'] == V380 else '107'),
                'mgt_org_code': org,
            })

            # 2) 计费卡
            inst_snap = self.idp.n('calc')
            self.W.note_cols('dwd_cst_inst_bilg_card',
                             ['bilg_card_id', 'calc_id', 'qty_charg_ym', 'calc_time', 'prc_ec_categ',
                              't_settle_qty', 't_settle_exp', 't_ctlg_exp', 'deg_exp', 't_addl_charg',
                              'ext_settle_qty', 't_self_cons_elec_qty', 't_self_cons_elec_charg',
                              'settle_qty_01', 'settle_qty_02', 'settle_qty_03', 'settle_qty_04',
                              'settle_qty_05', 'inst_snap_id', 'inst_id', 'mgt_org_code'])
            self.W.add('dwd_cst_inst_bilg_card', {
                'bilg_card_id': bilg_id, 'calc_id': calc_id, 'qty_charg_ym': ym,
                'calc_time': self.dtstr(dt.datetime.combine(d2, dt.time(self.rng.randint(0, 7), self.rng.randint(0, 59)))),
                'prc_ec_categ': '05' if cu['is_high'] else ('03' if cu['cls'] == '低压非居民' else '0200'),
                't_settle_qty': int(qty), 't_settle_exp': settle_exp, 't_ctlg_exp': deg_exp,
                'deg_exp': deg_exp, 't_addl_charg': addl, 'ext_settle_qty': int(qty),
                't_self_cons_elec_qty': 0, 't_self_cons_elec_charg': 0.0,
                'settle_qty_01': int(buckets[0]), 'settle_qty_02': int(buckets[1]),
                'settle_qty_03': int(buckets[2]), 'settle_qty_04': int(buckets[3]),
                'settle_qty_05': int(buckets[4]),
                'inst_snap_id': inst_snap, 'inst_id': cu['inst_id'],
                'mgt_org_code': org,
            }, pk='bilg_card_id')

            # 3) 分段量费
            attr = ['0101', '0601', '0602'] if cu['is_high'] else ['0101']
            segs = [buckets[2], buckets[3], buckets[4]] if cu['is_high'] else [buckets[2]]
            for i, (a, q) in enumerate(zip(attr, segs)):
                if q <= 0:
                    continue
                self.W.note_cols('dwd_cst_sgmt_qty_charg',
                                 ['sgmt_qty_charg_id', 'calc_id', 'qty_charg_ym', 'sgmt_exp_attr_cls',
                                  'settle_qty', 'deg_up', 'deg_exp', 'exec_ctlg_up', 'ctlg_exp',
                                  'ctlg_cls', 'lvling_diff', 'ext_settle_qty', 'plan_no', 'bilg_card_id',
                                  'prc_ec_categ', 'inst_snap_id', 'cust_cls', 'cust_volt_code',
                                  'cust_ec_categ'])
                self.W.add('dwd_cst_sgmt_qty_charg', {
                    'sgmt_qty_charg_id': self.idp.n('sgmt'), 'calc_id': calc_id, 'qty_charg_ym': ym,
                    'sgmt_exp_attr_cls': a, 'settle_qty': int(q), 'deg_up': price, 'deg_exp': round(q * price, 2),
                    'exec_ctlg_up': price, 'ctlg_exp': round(q * price, 2), 'ctlg_cls': '0%d' % (i + 2),
                    'lvling_diff': 0.0, 'ext_settle_qty': int(q),
                    'plan_no': f"1{calc_id % 10**12:012d}", 'bilg_card_id': bilg_id,
                    'prc_ec_categ': '05' if cu['is_high'] else '0200', 'inst_snap_id': inst_snap,
                    'cust_cls': '01' if cu['is_high'] else '03', 'cust_volt_code': '117' if cu['is_high'] else '107',
                    'cust_ec_categ': '0502' if cu['is_high'] else '0302',
                })

            # 4) 应收
            rcvbl_id = self.idp.n('rcvbl')
            self.W.note_cols('dwd_cst_rcvbl_acct',
                             ['rcvbl_acct_id', 'qty_charg_calc_id', 'rcvbl_ym', 'acct_no', 'ec_qty',
                              'rcvbl_amt', 'rcvd_amt', 'in_trnst_amt', 'arer_bal', 'write_off_amt',
                              'rcvbl_lqd_damg', 'rcvd_lqd_damg', 'issu_date', 'mgt_org_code'])
            self.W.add('dwd_cst_rcvbl_acct', {
                'rcvbl_acct_id': rcvbl_id, 'qty_charg_calc_id': calc_id, 'rcvbl_ym': ym,
                'acct_no': str(rcvbl_id), 'ec_qty': int(qty), 'rcvbl_amt': settle_exp,
                'rcvd_amt': settle_exp, 'in_trnst_amt': 0.0, 'arer_bal': 0.0, 'write_off_amt': 0.0,
                'rcvbl_lqd_damg': 0.0, 'rcvd_lqd_damg': 0.0,
                'issu_date': self.dtstr(d2 + dt.timedelta(days=self.rng.randint(0, 8))),
                'mgt_org_code': org,
            }, pk='rcvbl_acct_id')

            # 5) 应收加收明细（仅高压/加收>0）
            if addl > 0:
                self.W.note_cols('dwd_cst_rcvbl_addl_acct',
                                 ['rcvbl_addl_charg_acct_id', 'rcvbl_acct_id', 'rcvbl_ym', 'acct_no',
                                  'qty_charg_calc_id', 'cust_cls', 'dereg_attr_cls', 'ec_categ',
                                  'volt_code', 'ec_qty', 'addl_charg_ctlg_code', 'rcvbl_addl_charg_amt',
                                  'rcvd_addl_charg_amt'])
                self.W.add('dwd_cst_rcvbl_addl_acct', {
                    'rcvbl_addl_charg_acct_id': self.idp.n('rcvbl_addl'), 'rcvbl_acct_id': rcvbl_id,
                    'rcvbl_ym': ym, 'acct_no': str(rcvbl_id), 'qty_charg_calc_id': calc_id,
                    'cust_cls': '01' if cu['is_high'] else '03',
                    'dereg_attr_cls': '0107', 'ec_categ': '0505' if cu['is_high'] else '0302',
                    'volt_code': '0117' if cu['is_high'] else '0107', 'ec_qty': int(qty),
                    'addl_charg_ctlg_code': self.S.pick('bill_ratio.rcvbl_addl_charg_ctlg', '9904'),
                    'rcvbl_addl_charg_amt': addl, 'rcvd_addl_charg_amt': addl,
                })

            # 6) 交费 → 实收（约 94% 当月足额回收，其余部分/延期）
            pay_ratio = 1.0 if self.rng.random() < 0.94 else self.rng.choice([0.3, 0.5, 0.7, 0.0])
            rcvd = round(settle_exp * pay_ratio, 2)
            if rcvd > 0:
                charg_id = self.idp.n('charg')
                pay_d = d2 + dt.timedelta(days=self.rng.randint(3, 25))
                self.W.add('dwd_cst_charg_acct', {
                    'charg_acct_id': charg_id, 'charg_ym': ym, 'charg_date': self.dtstr(pay_d),
                    'charg_amt': rcvd, 'acct_no': str(rcvbl_id), 'acct_ym': ym,
                    'acct_date': self.dtstr(pay_d), 'chan_no': self.S.pick('customer.pay_mode', '02'),
                    'pay_order_id': charg_id, 'mgt_org_code': org,
                }, pk='charg_acct_id')

                self.W.note_cols('dwd_cst_rcvd_acct',
                                 ['rcvd_acct_id', 'charg_acct_id', 'rcvbl_acct_id', 'rcvbl_ym',
                                  'rcvd_ym', 'rcvd_amt', 'arer_bal', 'acct_no', 'mgt_org_code'])
                self.W.add('dwd_cst_rcvd_acct', {
                    'rcvd_acct_id': self.idp.n('rcvd'), 'charg_acct_id': charg_id,
                    'rcvbl_acct_id': rcvbl_id, 'rcvbl_ym': ym, 'rcvd_ym': ym,
                    'rcvd_amt': rcvd, 'arer_bal': rcvd, 'acct_no': str(rcvbl_id),
                    'mgt_org_code': org,
                })

            # 7) 登记本期账务事实，供 D 段"业务旁支表"精确派生（保证金额/电量可对账）
            cu['bills'].append({
                'ym': ym, 'calc_id': calc_id, 'bilg_id': bilg_id, 'inst_snap': inst_snap,
                'qty': qty, 'price': price, 'deg_exp': deg_exp, 'addl': addl,
                'settle_exp': settle_exp, 'rcvbl_id': rcvbl_id, 'pay_d': d2,
            })

    def _gen_daily_energy(self, cu, dist_stas):
        """日粒度电量：低压用户 → 低压日电量；专变 → 高压日电量；台区 → 台区日电量。"""
        cu.setdefault('daily', [])      # [(date, pap)]，供台区日线损/正反向电量/96点曲线复用
        if not self.daily_months:
            return
        org = cu['org']
        for ym in self.daily_months:
            days = self._daily_days(ym)
            if not days:
                continue
            month_qty = self._monthly_qty(cu, ym)
            for d in days:
                date = dt.date(ym // 100, ym % 100, d)
                shape = WEEKEND_SHAPE if date.weekday() >= 5 else WEEKDAY_SHAPE
                q = month_qty / month_days(ym) * shape * self.rng.uniform(0.82, 1.18)
                pap = round(q, 1)
                cu['daily'].append((date, pap))
                if cu['is_high']:
                    self.W.note_cols('dwd_cst_meter_energy_day_h_xz',
                                     ['data_date', 'meter_asset_no', 't_factor', 'pap_e', 'rap_e',
                                      'prp_e', 'mgt_org_code'])
                    self.W.add('dwd_cst_meter_energy_day_h_xz', {
                        'data_date': self.dtstr(date), 'meter_asset_no': cu['meter_asset_no'],
                        't_factor': float(cu['rto']), 'pap_e': pap,
                        'rap_e': round(pap * 0.02, 1), 'prp_e': None, 'mgt_org_code': org,
                    })
                else:
                    self.W.add('dwd_cst_es_meter_energy_day_l_xz', {
                        'data_date': self.dtstr(date), 'meter_asset_no': cu['meter_asset_no'],
                        'pap_e': pap, 'rap_e': 0.0, 'mgt_org_code': org,
                    })

    def _gen_account_balance(self, users):
        """每个客户一条账户余额快照。"""
        for cu in users:
            self.W.note_cols('dwd_cst_acct_bal',
                             ['acct_bal_id', 'cust_no', 't_bal', 'frz_amt', 'tmp_frz_amt',
                              'cur_avail_bal', 'rcvd_adv_bal', 'rchg_card_bal', 'spcl_bal',
                              'thrd_prty_bal', 'mgt_org_code'])
            bal = round(self.rng.uniform(0, 200), 2)
            self.W.add('dwd_cst_acct_bal', {
                'acct_bal_id': self.idp.n('acct_bal'), 'cust_no': cu['cust_no'], 't_bal': bal,
                'frz_amt': 0.0, 'tmp_frz_amt': 0.0, 'cur_avail_bal': bal, 'rcvd_adv_bal': bal,
                'rchg_card_bal': 0.0, 'spcl_bal': 0.0, 'thrd_prty_bal': 0.0, 'mgt_org_code': cu['org'],
            })

    # ---------- C. 业务数据：业扩/工单链路 ----------

    def _biz_pair(self, bus_type_codes):
        """按【业务类型码】给出真实库口径的 (bus_type, desc, bus_categ, categ_desc)。

        真实库 gc_app_rec 该字段是 int 列，只出现 40601（分布式电源改类）与
        32302（计量设备更换）两种去零形态；业务类别码 = 业务类型码前 4 位
        （0406 / 0323），其 desc 取码表 bus_categ 名称。
        切勿用"同族随机抽"——会抽到 032313 等真实库从未出现的码。
        """
        code = self.rng.choice(list(bus_type_codes))
        cat = code[:4]
        tn = self.CB.name_of('bus_type', code) or code
        bn = self.CB.name_of('bus_categ', cat) or cat
        return (code.lstrip('0') or code, tn,
                cat.lstrip('0') or cat, bn)

    def _gen_workflow(self, org, cty, abbr, ci, users):
        """为部分用户生成业扩报装链路（申请→方案→工单→环节→装拆）。

        返回本区县的申请清单，供 D 段派生投资工单 / 工单附属材料。
        """
        rng = self.rng
        n = max(1, int(len(users) * 0.25))
        flow = []
        CUST_CITY = {'area': '华东'}
        for cu in rng.sample(users, n):
            app_id = self.idp.n('app')
            apply_ym = rng.choice(self.ym_list)
            apply_d = dt.date(apply_ym // 100, apply_ym % 100, rng.randint(1, 28))
            bt = self._biz_type()
            loc = {'mgt_org_code': org, 'mgt_org_name': f"{abbr}{self.tag}",
                   # 真实库 county_code/county_name 存的是【供电机构码/机构名】，
                   # 不是行政区划码（对照组：dim_cst_elec_cons_cust.bus_county_code 才是 adcode）
                   'county_code': cty.get('county_code') or org,
                   'county_name': cty.get('county_name') or f"{abbr}{self.tag}"}

            # 业务申请单
            self.W.add('dwd_cst_bus_app_form', {
                'bus_app_form_id': app_id, 'app_no': str(app_id), 'cust_id': cu['cust_id'],
                'bp_id': cu['bp_id'], 'bus_type': bt['bus_type'], 'bus_type_desc': bt['bus_type_desc'],
                'bus_categ': bt['bus_categ'], 'bus_categ_desc': bt['bus_categ_desc'], 'srv_kind': '1',
                'acpt_date': self.dtstr(apply_d), 'acmp_time': self.dtstr(apply_d + dt.timedelta(days=5)),
                'app_stat': bt['app_stat'], 'app_stat_desc': bt['app_stat_desc'], 'acpt_chan_no': '22',
                **loc,
            }, pk='bus_app_form_id')

            # 接入方案 / 协议方案 / 安装点方案 / 表计方案
            acc_id = self.idp.n('sch')
            self.W.add('dwd_cst_acc_sch', {
                'accs_sch_id': acc_id, 'app_no': str(app_id), 'bus_app_form_id': app_id,
                'bp_id': cu['bp_id'], 'sch_drft_stf': '仿真', 'sch_no': str(acc_id),
                'sch_accs_sch_prepared_date': self.dtstr(apply_d),
                'sch_draft_opn': f"{abbr}{self.tag}接入方案", 'mgt_org_code': org,
            })
            agrt_sch_id = self.idp.n('sch')
            self.W.add('dwd_cst_cust_agrt_sch', {
                'cust_agrt_sch_id': agrt_sch_id, 'cust_agrt_id': cu['agrt_id'],
                'cust_id': cu['cust_id'], 'bus_app_form_id': app_id,
                'agrt_categ_desc': self.S.pick('customer.agrt_categ', '消费协议'),
                'srv_kind': '1', 'srv_kind_desc': '电类', 'mgt_org_code': org,
            }, pk='cust_agrt_sch_id')
            inst_sch_id = self.idp.n('sch')
            self.W.add('dwd_cst_inst_elec_sch', {
                'inst_elec_sch_id': inst_sch_id, 'inst_id': cu['inst_id'],
                'inst_no': f"{cu['inst_id'] % 10**11:011d}", 'app_no': str(app_id),
                'cust_id': cu['cust_id'], 'srv_kind': '1', 'srv_kind_desc': '电类',
                'inst_name': f"{cu['bp_name']}计量点", 'inst_cls': '01', 'inst_cls_desc': '用电客户',
                'inst_char': '1', 'inst_char_desc': '结算', 'inst_stat': '2', 'inst_stat_desc': '在用',
                'inst_usage_cls': '01', 'inst_usage_cls_desc': '售电侧结算', 'inst_cap': cu['cap'],
                'dist_sta_id': cu['dist_sta'], 'pipeline_id': cu['line'], 'cntrl_sta_id': cu['sta110'],
                'ec_categ': '05' if cu['is_high'] else '0200',
                'voltage': '117' if cu['is_high'] else '107',
                'cust_agrt_sch_id': agrt_sch_id, 'bus_app_form_id': app_id, **loc,
            }, pk='inst_elec_sch_id')
            self.W.add('dwd_cst_meter_sch', {
                'meter_sch_id': self.idp.n('sch'), 'meter_id': cu['dev_id'],
                'inst_id': cu['inst_id'], 'cust_id': cu['cust_id'], 'dev_no': cu['meter_asset_no'],
                'app_no': str(app_id), 'bar_code': f"333{cu['dev_id'] % 10**17:017d}"[:22],
                'comp_rto': cu['rto'], 'inst_no': f"{cu['inst_id'] % 10**11:011d}",
                'inst_elec_sch_id': inst_sch_id, 'cust_agrt_sch_id': agrt_sch_id,
                'bus_app_form_id': app_id, 'chg_desc': '1', 'chg_desc_desc': '新增',
                'instal_date': self.dtstr(apply_d + dt.timedelta(days=6)),
                'mgt_org_code': org,
            }, pk='meter_sch_id')

            # 工单（该表无 cust/app 外键，用 bus_attr_no 承载业务单号）
            # 真实库 wk_order_type 用 BM 类编码（BM01 业扩接入），工单状态为"归档"
            wko_id = self.idp.n('wko')
            wko_code, wko_desc = self.CB.pick_named('wk_order_type', '业扩接入')
            wks_code, wks_desc = self.CB.pick_named('wk_order_stat', '归档', drop_leading_zero=True)
            self.W.add('dwd_cst_wk_order', {
                'wk_order_id': wko_id, 'wk_order_no': str(wko_id),
                'wk_order_name': f"{bt['bus_type_desc']}工单", 'wk_order_type': wko_code,
                'wk_order_type_desc': wko_desc, 'bus_attr_no': str(app_id),
                'bus_attr_name': f"{cu['bp_name']}{bt['bus_type_desc']}",
                'creat_time': self.dtstr(apply_d), 'wk_order_stat': wks_code,
                'wk_order_stat_desc': wks_desc,
                **loc,
            }, pk='wk_order_id')

            # 环节台账（真实的环节表用 step_acct_id / rcv_time / acmp_time）
            ss_code, ss_desc = self.CB.pick_named('step_stat', '完成', drop_leading_zero=True)
            for st, stname in enumerate(['受理', '勘查', '方案答复', '装表接电', '归档'], 1):
                sd = apply_d + dt.timedelta(days=st * rng.randint(1, 4))
                self.W.add('dwd_cst_step_rec', {
                    'step_acct_id': self.idp.n('step'), 'step_instc_id': wko_id,
                    'step_no': str(st), 'step_name': stname, 'step_stat': ss_code,
                    'step_stat_desc': ss_desc,
                    'rcv_time': self.dtstr(sd), 'acmp_time': self.dtstr(sd + dt.timedelta(hours=rng.randint(1, 20))),
                    'mgt_org_code': org,
                }, pk='step_acct_id')

            pd = apply_d + dt.timedelta(days=rng.randint(3, 12))
            ps_code, ps_desc = self.CB.pick_named('proc_stat', '完成', drop_leading_zero=True)
            self.W.add('dwd_cst_proc_acct', {
                'proc_acct_id': self.idp.n('proc'), 'wk_order_id': wko_id,
                'creat_time': self.dtstr(apply_d), 'acmp_time': self.dtstr(pd),
                'proc_stat': ps_code, 'proc_stat_desc': ps_desc, 'mgt_org_code': org,
            }, pk='proc_acct_id')

            # 设备装拆记录（真实库 inst_rmv_categ_desc 是"安装/拆除"，非"装表"）
            ir_code, ir_desc = self.CB.pick_named('inst_rmv_categ', '安装', drop_leading_zero=True)
            self.W.add('dwd_cst_dev_inst_rmv_wk_rec', {
                'dev_inst_rmv_wk_rec_id': self.idp.n('wko'), 'dev_id': cu['dev_id'],
                'inst_id': cu['inst_id'], 'inst_rmv_categ': ir_code, 'inst_rmv_categ_desc': ir_desc,
                'inst_rmv_date': self.dtstr(pd), 'inst_rmv_org': org, 'inst_rmv_stf': '仿真',
                'app_no': str(app_id), 'asset_no': cu['meter_asset_no'],
                'dev_cls_desc': '电能表', 'mgt_org_code': org,
            }, pk='dev_inst_rmv_wk_rec_id')

            # 用电申请记录（真实库该表 bus_type 带前导零）
            self.W.add('dwd_cst_cust_elec_app_rec', {
                'cust_elec_app_rec_id': self.idp.n('app'), 'cust_id': cu['cust_id'],
                'bus_app_form_id': app_id, 'app_no': str(app_id), 'cust_no': cu['cust_no'],
                'cust_name': cu['bp_name'], 'bp_id': cu['bp_id'],
                'bus_type': bt['bus_type_padded'], 'bus_type_desc': bt['bus_type_desc'],
                'acpt_date': self.dtstr(apply_d), 'app_stat': bt['app_stat'],
                'app_stat_desc': bt['app_stat_desc'],
                'ctrt_cap': cu['cap'], 'run_cap': cu['cap'], 'mgt_org_code': org,
            }, pk='cust_elec_app_rec_id')

            # 预申请单（真实库 stat 用去零短码）
            stt_code, stt_desc = self.CB.pick_named('stat', '已处理', drop_leading_zero=True)
            self.W.add('dwd_cst_preapp_order', {
                'prepp_id': self.idp.n('preapp'), 'preapp_no': str(self.idp.n('preapp')),
                'bp_id': cu['bp_id'], 'cust_id': cu['cust_id'],
                'bus_type': bt['bus_type'], 'bus_type_desc': bt['bus_type_desc'],
                'bus_categ': bt['bus_categ'], 'bus_categ_desc': bt['bus_categ_desc'],
                'preapp_time': self.dtstr(apply_d - dt.timedelta(days=rng.randint(1, 5))),
                'stat': stt_code, 'stat_desc': stt_desc, 'mgt_org_code': org,
            }, pk='prepp_id')

            flow.append({'app_id': app_id, 'cu': cu, 'apply_d': apply_d,
                         'bus_type': bt, 'wko_id': wko_id, 'pd': pd, 'loc': loc})

        # 接电申请 / 停复供 / 设备领用（每区县少量）
        if users:
            cu = rng.choice(users)
            self.W.add('dwd_cst_connection_app_rec', {
                'conn_app_rec_id': self.idp.n('app'), 'app_no': str(self.idp.n('app')),
                'bus_app_form_id': self.idp.n('app'), 'cust_id': cu['cust_id'],
                'pipeline_id': cu['line'], 'dist_sta_id': cu['dist_sta'],
                'cntrl_sta_id': cu['sta110'], 'acpt_date': self.dtstr(dt.date(2025, 3, 15)),
                'voltage_desc': cu['volt'], 'orgn_cap': cu['cap'], 'dmd_cap': cu['cap'],
                'mgt_org_code': org,
            }, pk='conn_app_rec_id')
            # 停复供类别取码表（真实库是"停电/复电"）
            sc_code, sc_desc = self.CB.pick_named('stop_rcvr_supl_categ',
                                                  rng.choice(['停电', '复电']),
                                                  drop_leading_zero=True)
            self.W.add('dwd_cst_stop_rcvr_supl_app', {
                'stop_rcvr_supl_id': self.idp.n('app'), 'app_no': str(self.idp.n('app')),
                'cust_no': cu['cust_no'], 'acpt_date': self.dtstr(dt.date(2025, 6, 20)),
                'stop_rcvr_supl_categ': sc_code, 'stop_rcvr_supl_categ_desc': sc_desc,
                'srv_kind': '1', 'srv_kind_desc': '电类', 'mgt_org_code': org,
            }, pk='stop_rcvr_supl_id')
            rcpt_id = self.idp.n('devrec')
            rcpt_app_no = self.idp.n('app')
            self.W.add('dwd_cst_dev_rcpt_app', {
                'app_form_id': rcpt_id, 'app_no': str(rcpt_app_no),
                'app_date': self.dtstr(dt.date(2025, 5, 10)), 'applnt': cu['bp_name'],
                'dev_cls': '01', 'dev_cls_desc': '电能表',
                'rcpt_type': self.CB.pair('rcpt_type', '01')[0],
                'rcpt_type_desc': self.CB.name_of('rcpt_type', '01'),
                'rcv_ret_flag': self.CB.pair('rcv_ret_flag', '01')[0],
                'rcv_ret_flag_desc': self.CB.name_of('rcv_ret_flag', '01'),
                'plan_num': 1,
                'mgt_org': org, 'valid_flag': '02', 'valid_flag_desc': '有效',
            }, pk='app_form_id')
            flow.append({'rcpt_app_form_id': rcpt_id, 'rcpt_app_no': rcpt_app_no,
                         'cu': cu, 'apply_d': dt.date(2025, 5, 10)})
        return flow

    def _gen_refund(self, org, users):
        """少量退补单（bilg_rs_rcpt 为退补受理单，rs_inst_bilg_card 为退补计费卡）。"""
        if not users:
            return
        for cu in self.rng.sample(users, max(1, int(len(users) * 0.08))):
            ym = self.rng.choice(self.ym_list)
            qty = self._monthly_qty(cu, ym)
            exp = round(qty * cu['price'], 2)
            rcp_id = self.idp.n('refund')
            self.W.add('dwd_cst_bilg_rs_rcpt', {
                'bilg_rs_rcpt_id': rcp_id, 'cust_no': cu['cust_no'],
                'rs_ym': ym, 'rs_rcvbl_ym': ym, 'actl_out_acct_mon': ym,
                'orgn_calc_id': self.idp.n('calc'), 'app_no': str(self.idp.n('app')),
                'app_date': self.dtstr(dt.date(ym // 100, ym % 100, 15)),
                'hndl_stat': self.CB.pair('hndl_stat', '05')[0],
                'rs_cls': self.CB.pair('rs_cls', '03')[0], 'dereg_attr_cls': '0107',
                'err_occur_date': self.dtstr(dt.date(ym // 100, ym % 100, 1)),
                'rs_reason': f"{self.tag}计费差错退补",
            }, pk='bilg_rs_rcpt_id')
            self.W.add('dwd_cst_rs_inst_bilg_card', {
                'rs_inst_bilg_card_id': self.idp.n('refund'), 'actl_out_acct_mon': ym,
                'qty_charg_ym': ym, 't_settle_qty': int(-qty * 0.05),
                't_settle_exp': round(-exp * 0.05, 2), 't_ctlg_exp': round(-exp * 0.05, 2),
                't_addl_charg': 0.0, 't_ecc': round(-exp * 0.05, 2),
                't_mr_qty': 0, 'ext_settle_qty': 0.0,
                'bilg_rs_rcpt_id': rcp_id, 'rs_qty_charg_calc_id': self.idp.n('calc'),
                'app_no': str(self.idp.n('app')), 'rs_prc_src': '3',
                'prc_ec_categ': '05' if cu['is_high'] else '0200',
            })


    # ========================================================================
    # D. 电网设备域台账（PMS / D5000 侧）
    #
    # 本体 master 层里，GridPlant / GridLine / GridTransformer / GridDevice /
    # GridMeterProfile 与 MgtOrg / Customer 是并列的一等实体。上一版仿真只覆盖
    # 营销域（dim_cst_* / dwd_cst_*），这几个实体在仿真数据里整块为空——凡是要
    # 跨"营销域 ↔ 设备域"取数的场景（如"台区配变 → 所属馈线 → 台区线损"）都无数据。
    #
    # 本段按「1 个区县 = 1 套配网设备台账 + 1 片电网模型」补齐，并让两条桥接真正可 JOIN：
    #     dim_cst_cntrl_sta.pms_cntrl_sta_id → dim_equ_t_p_pd_stationpsr.id
    #     dim_cst_pipeline.pms_pipeline_id   → dim_ast_t_p_pd_feederlinepsr.id
    # （真实库里这两条桥接其实是断的——14 条专线 0 命中——仿真把它补完整。）
    # ========================================================================

    SENT = 2147483647          # 真实库用 int 上限当"空值"哨兵，仿真沿用同一约定

    # 电网模型表 → 表类型码（真实 id 的前 3 位）
    GRID_PREFIX = {
        'dim_grid_t_ts_sg_da_acline_b': '121',
        'dim_grid_t_ts_sg_da_aclineend_b': '120',
        'dim_grid_t_ts_sg_da_breaker_b': '132',
        'dim_grid_t_ts_sg_da_busbar_b': '130',
        'dim_grid_t_ts_sg_da_dev_generator_b': '110',
        'dim_grid_t_ts_sg_da_dev_pwrtransfm_b': '131',
        'dim_grid_t_ts_sg_da_transfmwd_b': '134',
        'dim_grid_t_ts_sg_da_tline_b': '124',
        'dwd_grid_t_ts_sg_da_feederline_b_new': '170',
    }
    VTYPE = {220: 1004, 110: 1003, 10: 1008}

    def _gid(self, table, adcode, n=1):
        """电网模型主键：`表类型码(3)+区划码(6)+序号(9)` = 18 位，与真实编码同构。

        真实库这几张表的区划段是 `033000`/`133000`（0/1 开头），仿真填真实区县
        adcode（3301xx…），因此天然不冲突；preflight 另有逐表区间校验兜底。
        """
        pref = self.GRID_PREFIX[table]
        out = []
        for _ in range(n):
            self._gseq[(table, adcode)] += 1
            out.append(int(f"{pref}{adcode}{self._gseq[(table, adcode)]:09d}"))
        return out[0] if n == 1 else out

    @staticmethod
    def _natid(pref, adcode, seq, tail):
        return int(f"{pref}{adcode}{seq:0{tail}d}")

    @staticmethod
    def _hex32(s):
        """真实库的 district/depart/dev_master 是 32 位十六进制，这里确定性生成。"""
        return hashlib.md5(s.encode()).hexdigest().upper()

    @staticmethod
    def _adc(cty):
        a = str(cty.get('region_adcode') or '330000')
        return a, a[:4] + '00', '330000'

    def _gen_grid_assets(self, ctx, users):
        """为单个区县补齐配网设备台账（PMS 侧）+ 电网模型（D5000 侧）。"""
        org, abbr = ctx['org'], ctx['abbr']
        cty, idx = ctx['cty'], ctx['idx']
        adcode, city_adc, prov_adc = self._adc(cty)
        owner = int(city_adc)
        W, rng = self.W, self.rng
        org_hex = self._hex32(org)
        dep_hex = self._hex32(org + 'dep')
        sec_hex = self._hex32(org + 'sec')
        team_hex = self._hex32(org + 'team')
        master_hex = self._hex32(org + 'mst')
        geo = f"{adcode[:4]}-{adcode[4:]}-11e7-95b9-00aa00a1{adcode[-3:]}"
        today = dt.date.today()

        # ---------- 1) 变电站 PSR（220kV / 110kV / 开关站） ----------
        sta_psr = {}                       # 电压 kV → PSR id
        for sid, kv, cls, cap_pool in ((ctx['sta220'], 220, 'PWStationPSR', CAP_220),
                                       (ctx['sta110'], 110, 'PWStationPSR', CAP_110)):
            pms = self.pms_sta_of[sid]
            sta_psr[kv] = pms
            W.add('dim_equ_t_p_pd_stationpsr', {
                'id': pms, 'name': f"{abbr}{kv}kV{self.tag}变电站", 'alias_name': f"{abbr}{kv}kV",
                'class_name': cls, 'voltagelevel_id': 22, 'parent_id': pms, 'root_id': pms,
                'parent_class_name': cls, 'root_class_name': cls, 'use_nature': 1,
                'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                'maintenance_team': team_hex, 'dev_master': master_hex,
                'run_status': 20, 'run_date': self.dtstr(dt.date(2018, 6, 1)), 'run_no': str(sid),
                'mrid': pms, 'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                'georegion': geo, 'subgeoregion': geo,
                'path_id': pms, 'path_name': f"{abbr}{kv}kV变电站",
                'region_type': '01', 'is_rural_grid': 0, 'is_independent_building': 0, 'is_agent': 0,
                'pd_transformer_amount': rng.randint(1, 3), 'incoming_bay_number': rng.randint(1, 4),
                'outlet_bay_number': rng.randint(2, 8), 'idle_capacity': 0.0,
                'have_medvoltage_ouutlet': 1, 'ground_resistance': 0.5,
                'capacity': float(rng.choice(cap_pool)), 'arrentment_form': '01',
                'anti_misoperation_mode': '01', 'address': f"{abbr}{self.tag}{kv}kV变电站",
                'power_supply_type': '0', 'pollution_grade': '01',
                'longitude': round(rng.uniform(119.0, 122.0), 6),
                'latitude': round(rng.uniform(28.0, 31.0), 6),
                'significance': '01', 'is_looped_network': 1,
                'odsdatauptime': self.SENT,
            })

        # 开关站（约 40% 的区县有）
        if rng.random() < 0.4:
            sws = self._pms('pms_sws', '30000004-')
            W.add('dim_equ_t_p_pd_switchingstationpsr', {
                'id': sws, 'name': f"{abbr}{self.tag}开关站", 'class_name': 'PWSwitchingStationPSR',
                'voltagelevel_id': 22, 'parent_id': sws, 'root_id': sws,
                'parent_class_name': 'PWSwitchingStationPSR', 'root_class_name': 'PWSwitchingStationPSR',
                'use_nature': 3, 'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                'maintenance_team': team_hex, 'dev_master': master_hex, 'run_status': 20,
                'run_date': self.dtstr(dt.date(2019, 9, 1)), 'run_no': str(idx),
                'mrid': sws, 'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                'georegion': geo, 'subgeoregion': geo, 'path_id': sws,
                'path_name': f"{abbr}{self.tag}开关站", 'region_type': '01', 'is_agent': 0,
                'is_rural_grid': 0, 'is_large_station': 0, 'is_independent_building': 0,
                'is_automation_station': 1, 'incoming_bay_number': 2, 'outlet_bay_number': 4,
                'standby_bay_number': 1, 'ground_resistance': 0.5,
                'anti_misoperation_mode': '01', 'arrentment_form': '01',
                'address': f"{abbr}{self.tag}开关站", 'significance': '03',
                'longitude': round(rng.uniform(119.0, 122.0), 6),
                'latitude': round(rng.uniform(28.0, 31.0), 6), 'odsdatauptime': self.SENT,
            })

        # ---------- 2) 馈线 PSR + 支线 + 电缆段 ----------
        feed_psr = {}                      # pipeline_id → 馈线 PSR id
        for kv, pid in ([(110, ctx['line110'])] + [(10, l) for l in ctx['lines10']]):
            pms = self.pms_line_of[pid]
            feed_psr[pid] = pms
            W.add('dim_ast_t_p_pd_feederlinepsr', {
                'id': pms, 'name': f"{abbr}{kv}kV{self.tag}{pid % 100}线", 'alias_name': None,
                'class_name': 'PWFeederLinePSR', 'voltagelevel_id': 22,
                'parent_id': pms, 'root_id': pms,
                'parent_class_name': 'PWFeederLinePSR', 'root_class_name': 'PWFeederLinePSR',
                'use_nature': 1 if kv >= 110 else 3,
                'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                'maintenance_team': team_hex, 'dev_master': master_hex,
                'run_status': 20, 'run_date': self.dtstr(dt.date(2016, 3, 1)), 'run_no': str(pid),
                'mrid': pms, 'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                'georegion': geo, 'subgeoregion': geo, 'path_id': pms,
                'path_name': f"{abbr}{kv}kV馈线", 'dispatch_level': '70', 'dispatch_depart': dep_hex,
                'start_station_id': sta_psr.get(110) or sta_psr.get(220),
                'start_station_class_name': 'PWStationPSR', 'significance': '02',
                'layout_mode': '02' if kv >= 110 else '01', 'region_type': '01',
                'length': round(rng.uniform(1.2, 18.5), 3),
                'cable_length': round(rng.uniform(0.1, 4.0), 3),
                'conductor_length': round(rng.uniform(1.0, 14.0), 3),
                'insulatedwire_length': round(rng.uniform(0.5, 8.0), 3),
                'basewire_length': 0.0, 'supply_radius': round(rng.uniform(0.5, 5.0), 2),
                'switchstation_count': 0, 'switch_count': rng.randint(2, 12),
                'interconnecyion_line': 0, 'is_nsubo': 0, 'fuse_count': rng.randint(1, 9),
                'loop_unit_count': rng.randint(1, 5), 'powercable_count': rng.randint(0, 4),
                'section_count': rng.randint(1, 6), 'pole_count': rng.randint(10, 120),
                'is_agent': 0, 'is_rural_grid': 0,
                'voltagelevel_name': f"交流{kv}kV", 'district_name': cty.get('region_name'),
                'depart_name': abbr, 'section_name': abbr, 'maintenance_team_name': abbr,
                'dev_master_name': '仿真', 'odsdatauptime': self.SENT,
            })

            # 支线（PWSubLinePSR）：每条馈线 1~2 条
            for k in range(rng.randint(1, 2)):
                sub = self._pms('pms_subline', '10000200-')
                W.add('dim_equ_t_p_pd_linepsr', {
                    'id': sub, 'name': f"{abbr}{self.tag}{pid % 100}线{k+1}号支线",
                    'alias_name': None, 'class_name': 'PWSubLinePSR', 'voltagelevel_id': 22,
                    'parent_id': pms, 'root_id': pms,
                    'parent_class_name': 'PWLinePSR', 'root_class_name': 'PWFeederLinePSR',
                    'use_nature': 1 if kv >= 110 else 3,
                    'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                    'maintenance_team': team_hex, 'dev_master': master_hex, 'run_status': 20,
                    'run_date': self.dtstr(dt.date(2017, 5, 1)), 'run_no': str(k + 1),
                    'mrid': sub, 'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                    'georegion': geo, 'subgeoregion': geo, 'path_id': sub,
                    'path_name': f"{abbr}支线", 'dispatch_level': '70', 'dispatch_depart': dep_hex,
                    'is_agent': 0, 'is_rural_grid': 0, 'line_effect': '01',
                    'start_dev_id': pms, 'start_dev_class_name': 'PWLinePSR',
                    'length': round(rng.uniform(0.2, 6.0), 3),
                    'cable_length': 0.0, 'conductor_length': round(rng.uniform(0.2, 5.0), 3),
                    'conductor_layout_mode': '1', 'cable_layout_mode': '1',
                    'layout_mode': '01', 'pole_count': rng.randint(5, 60),
                    'voltagelevel_name': f"交流{kv}kV", 'district_name': cty.get('region_name'),
                    'depart_name': abbr, 'section_name': abbr, 'maintenance_team_name': abbr,
                    'dev_master_name': '仿真', 'run_status_desc': '在运', 'odsdatauptime': self.SENT,
                })

            # 电缆段（10kV 才有）
            if kv == 10:
                cab = self._pms('pms_cable', '20800000-')
                W.add('dim_equ_t_p_pd_cablepsr', {
                    'id': cab, 'name': f"{abbr}{self.tag}{pid % 100}线电缆段",
                    'class_name': 'PWCablePSR', 'voltagelevel_id': 22, 'parent_id': pms, 'root_id': pms,
                    'parent_class_name': 'PWLinePSR', 'root_class_name': 'PWFeederLinePSR',
                    'use_nature': 3, 'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                    'maintenance_team': team_hex, 'dev_master': master_hex, 'run_status': 20,
                    'run_date': self.dtstr(dt.date(2018, 4, 1)), 'run_no': str(idx),
                    'mrid': cab, 'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                    'georegion': geo, 'subgeoregion': geo, 'path_id': cab,
                    'path_name': f"{abbr}{self.tag}{pid % 100}线电缆段",
                    'start_dev_id': pms, 'start_dev_class_name': 'PWLinePSR',
                    'end_dev_id': None, 'end_dev_class_name': None, 'type': '01',
                    'cable_sec_count': 1, 'region_type': '01',
                    'length': round(rng.uniform(0.05, 1.5), 3), 'odsdatauptime': self.SENT,
                })

        # ---------- 3) 低压台区 / 低压出线 / 配变 / 杆塔 ----------
        for dd in ctx['dist_devs']:
            ds, aid, pid = dd['dist_sta'], dd['adj_volt'], dd['line']
            zone = self._b64id('pms_zone')
            dd['zone_psr'] = zone
            feed = feed_psr.get(pid)
            W.add('dim_ast_t_p_dy_stationzonepsr', {
                'id': zone, 'name': f"{abbr}{self.tag}{dd['dist_sta'] % 100}号公变",
                'alias_name': f"{abbr}{self.tag}公变", 'class_name': 'DYStationZonePSR',
                'voltagelevel_id': 8, 'parent_id': feed, 'root_id': feed,
                'parent_class_name': 'PWFeederLinePSR', 'root_class_name': 'PWFeederLinePSR',
                'use_nature': 3, 'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                'mabigintenance_team': team_hex, 'dev_master': master_hex, 'run_status': 20,
                'run_date': self.dtstr(dt.date(2019, 8, 1)), 'run_no': str(idx),
                'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                'georegion': geo, 'subgeoregion': geo, 'path_id': zone,
                'path_name': f"{abbr}公变", 'layout_mode': '01',
                'length': round(rng.uniform(0.05, 0.9), 3),
                'cable_length': round(rng.uniform(0.0, 0.2), 3),
                'conductor_length': round(rng.uniform(0.05, 0.7), 3),
                'supply_radius': round(rng.uniform(0.1, 0.6), 2),
                'transformer_capacity': float(dd['cap']), 'transformer_count': 1,
                'pole_count': rng.randint(3, 25), 'power_psr_id': aid,
                'power_psr_class_name': 'PWTransformerPSR', 'customer_tqid': ds,
                'admin_region_id': int(adcode), 'asset_depart': dep_hex,
                'install_location': f"{abbr}{self.tag}台区", 'belong_type': '1',
                'odsdatauptime': self.SENT,
            })

            # 低压出线（DYLinePSR / DYTopLinePSR）：每台区 1~2 条
            for k in range(rng.randint(1, 2)):
                dy = self._pms('pms_dyline', '10000300-')
                W.add('dim_equ_t_p_dy_linepsr', {
                    'id': dy, 'name': f"{abbr}{self.tag}{dd['dist_sta'] % 100}号公变{k+1}号出线",
                    'class_name': 'DYLinePSR' if k else 'DYTopLinePSR', 'voltagelevel_id': 8,
                    'parent_id': feed, 'root_id': zone,
                    'parent_class_name': 'DYFeederLinePSR', 'root_class_name': 'DYStationZonePSR',
                    'use_nature': 3, 'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                    'maintenance_team': team_hex, 'dev_master': master_hex, 'run_status': 20,
                    'run_date': self.dtstr(dt.date(2019, 8, 1)), 'run_no': str(k + 1),
                    'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                    'georegion': geo, 'subgeoregion': geo, 'path_id': dy,
                    'path_name': f"{abbr}低压出线",
                    'start_dev_id': zone, 'start_dev_class_name': 'DYStationZonePSR',
                    'length': round(rng.uniform(0.01, 0.4), 3), 'cable_length': 0.0,
                    'conductor_length': round(rng.uniform(0.01, 0.3), 3),
                    'insulatedwire_length': round(rng.uniform(0.01, 0.3), 3),
                    'basewire_length': 0.0, 'layout_mode': '01',
                    'phase_sequence': '01', 'phase_type': '01', 'odsdatauptime': self.SENT,
                })

            # 配电变压器 PSR（PWTransformerPSR）
            tf = self._pms('pms_tf', '30200002-')
            dd['tf_psr'] = tf
            W.add('dim_equ_t_p_pdzn_transformerpsr', {
                'id': tf, 'name': f"{abbr}{self.tag}{dd['dist_sta'] % 100}号公变",
                'class_name': 'PWTransformerPSR', 'voltagelevel_id': 22,
                'parent_id': feed, 'root_id': feed,
                'parent_class_name': 'PWBayPSR', 'root_class_name': 'PWStationPSR',
                'use_nature': 3, 'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                'maintenance_team': team_hex, 'dev_master': master_hex, 'run_status': 20,
                'run_date': self.dtstr(dt.date(2019, 8, 1)), 'run_no': str(idx),
                'mrid': tf, 'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                'georegion': geo, 'subgeoregion': geo, 'path_id': tf,
                'path_name': f"{abbr}配变", 'is_agent': 0, 'trustee': 0, 'is_rural_grid': 0,
                'capacity': float(dd['cap']), 'significance': '03', 'region_type': '01',
                'pwline_id': feed, 'yx_tg_id': ds, 'memo': f"{self.tag}仿真配变",
                'voltagelevel_name': '交流10kV', 'district_name': cty.get('region_name'),
                'depart_name': abbr, 'section_name': abbr, 'maintenance_team_name': abbr,
                'dev_master_name': '仿真', 'use_nature_desc': '公用', 'odsdatauptime': self.SENT,
            })

            # 配变 ↔ 馈线关系
            W.add('dim_equ_m_p_rel_pdtransformer_feederline', {
                'id': self._pms('pms_rel', '50000000-'),
                'transformer_psr_id': tf, 'transformer_class_name': 'PWTransformerPSR',
                'feeder_psr_id': feed, 'feeder_class_name': 'PWFeederLinePSR',
                'line_psr_id': feed, 'line_class_name': 'PWSubLinePSR',
                'depart': dep_hex, 'district': org_hex, 'modify_time': self.dtstr(today),
                'root_id': sta_psr.get(110), 'root_class_name': 'PWStationPSR',
                'use_nature': 1, 'odsdatauptime': self.SENT,
            })

            # 公变台账关联（带客户号，跨到营销域）
            cu0 = next((u for u in users if u['dist_sta'] == ds), None)
            W.add('dim_equ_t_p_rel_transformer_publ', {
                'id': self._pms('pms_relpub', '60000000-'), 'dev_id': tf, 'equip_id': None,
                'run_status': 20, 'dev_class_name': 'PWTransformerPSR',
                'dev_name': f"{abbr}{self.tag}{dd['dist_sta'] % 100}号公变", 'flag': 1,
                'equip_name': f"{abbr}{self.tag}{dd['dist_sta'] % 100}号公变",
                'c_mp_id': self.SENT, 'tg_id': str(ds),
                'tg_name': f"{abbr}{self.tag}{dd['dist_sta'] % 100}号公变",
                'tg_no': f"{ds % 10**10:010d}", 'cons_no': (cu0 or {}).get('cust_no'),
                'odsdatauptime': self.SENT,
            })

            # 杆塔（每条馈线抽 1 基）
            if rng.random() < 0.6:
                pole = self._pms('pms_pole', '10200001-')
                W.add('dim_ast_t_odps_sync_v_p_pd_polesitepsr', {
                    'id': pole, 'name': f"{abbr}{self.tag}{pid % 100}线{rng.randint(1, 60)}号杆",
                    'alias_name': None, 'class_name': 'PWPolesitePSR', 'voltagelevel_id': 22,
                    'parent_id': feed, 'parent_class_name': 'PWSubLinePSR',
                    'root_id': feed, 'root_class_name': 'PWFeederLinePSR',
                    'use_nature': 3, 'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                    'maintenance_team': team_hex, 'dev_master': master_hex, 'run_status': 20,
                    'run_date': self.dtstr(dt.date(2017, 6, 1)), 'run_no': str(idx),
                    'mrid': pole, 'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                    'georegion': geo, 'subgeoregion': geo, 'path_id': pole,
                    'path_name': f"{abbr}杆塔", 'type': '01', 'orientation': '01',
                    'angle': 0.0, 'has_more_polesite': 0, 'is_agent': 0, 'is_rural_grid': 0,
                    'phase_sequence': '01', 'pole_id': pole, 'order_no': str(rng.randint(1, 60)),
                    'conductor_layout_mode': '1', 'is_end': 0, 'ruling_span': 45.0,
                    'span': 45.0, 'is_shared': 0, 'loop_number': 1,
                    'pole_class_name': 'PWPolesitePSR', 'belong_type': '1',
                    'odsdatauptime': self.SENT,
                })

        # ---------- 4) 专变（高压自备变）与专变台账关联 ----------
        for cu in users:
            if not cu['is_high']:
                continue
            otf = self._pms('pms_otf', '30200001-')
            W.add('dim_equ_t_p_pd_optransformerpsr', {
                'id': otf, 'name': cu['bp_name'], 'class_name': 'PWOPTransformerPSR',
                'voltagelevel_id': 22, 'parent_id': feed_psr.get(cu['line']),
                'root_id': feed_psr.get(cu['line']),
                'parent_class_name': 'PWSubLinePSR', 'root_class_name': 'PWFeederLinePSR',
                'use_nature': 1, 'district': org_hex, 'depart': dep_hex, 'section': sec_hex,
                'maintenance_team': team_hex, 'dev_master': master_hex, 'run_status': 20,
                'run_date': self.dtstr(dt.date(2020, 5, 1)), 'run_no': str(cu['cust_id'] % 10**6),
                'mrid': otf, 'create_time': self.dtstr(today), 'modify_time': self.dtstr(today),
                'georegion': geo, 'subgeoregion': geo, 'path_id': otf,
                'path_name': cu['bp_name'], 'is_agent': 0, 'trustee': 0, 'is_rural_grid': 0,
                'capacity': float(cu['cap']), 'significance': '02', 'region_type': '01',
                'customer_no': cu['cust_no'], 'customer_transformer_name': cu['bp_name'],
                'customer_transformer_id': str(cu['adj_volt']) if 'adj_volt' in cu else None,
                'memo': f"{self.tag}仿真专变", 'odsdatauptime': self.SENT,
            })
            W.add('dim_equ_t_p_rel_transformer_priv', {
                'id': self._pms('pms_relprv', '60000001-'), 'dev_id': otf,
                'equip_id': cu['cust_id'] % self.SENT, 'run_status': 20,
                'dev_class_name': 'PWTransformerPSR', 'dev_name': cu['bp_name'], 'flag': 1,
                'cons_no': cu['cust_no'], 'cons_id': cu['cust_id'] % self.SENT,
                'tg_id': cu['dist_sta'], 'yx_tg_name': None, 'enddevice_no': None,
                'yx_mp_id': None, 'odsdatauptime': self.SENT,
            })

        # ---------- 5) 电网模型（D5000）：本区县的站内设备与线路 ----------
        self._gen_grid_model_county(cty, adcode, city_adc, prov_adc, owner, idx, ctx, users)

    def _gen_grid_model_county(self, cty, adcode, city_adc, prov_adc, owner, idx, ctx, users):
        """D5000 电网模型：区县粒度。id 用 `前缀+adcode+序号` 原生编码。"""
        W, rng = self.W, self.rng
        abbr = ctx['abbr']
        dcc = int('21' + adcode)
        grid = int('101' + adcode)
        stamp = f"{owner}_0061{adcode}_2026-01-01 00:00:00"
        assets_co = f"001{prov_adc}00000010"

        # 交流线路 + 端点 + 馈线（新）+ 计量档案（交流线段基础信息）
        for kv, pid in ([(110, ctx['line110'])] + [(10, l) for l in ctx['lines10']]):
            lid = self._gid('dim_grid_t_ts_sg_da_acline_b', adcode)
            nm = f"{abbr}{self.tag}{kv}kV{pid % 100}线"
            W.add('dim_grid_t_ts_sg_da_acline_b', {
                'id': lid, 'line_id': self.SENT, 'name': nm, 'owner': owner,
                'st_id': self.SENT, 'start_bay_id': None, 'end_bay_id': None,
                # linetype：该列真实行全为 NULL，无真实口径可对照，故按治理码表
                # linetype（1 主线/2 支线/3 分支线/4 分段线路/5 馈线）取 1；
                # 勿套用 D5000 四位码 1001（那是 line_type 列的口径）。
                'erectingmethod': '1001', 'linetype': '1',
            })
            W.add('dim_grid_t_ts_sg_da_aclineend_b', {
                'id': self._gid('dim_grid_t_ts_sg_da_aclineend_b', adcode),
                'dispatch_org_id': dcc, 'end_st_id': self.SENT, 'erectingmethod': 1004,
                'grid_id': grid, 'name': nm, 'owner': owner, 'running_state': 1003,
                'start_st_id': self.SENT, 'sys_flag': '1', 'voltage_type': self.VTYPE[kv],
            })
            W.add('dwd_grid_t_ts_sg_da_feederline_b_new', {
                'id': self._gid('dwd_grid_t_ts_sg_da_feederline_b_new', adcode),
                'dcc_id': dcc, 'feeder_type': 1002, 'grid_id': grid, 'name': nm,
                'start_st_id': self.SENT, 'voltage_type': self.VTYPE[kv], 'running_state': 1006,
            })
            # 交流线段基础信息与交流线路同源，共享 id
            W.add('dwd_grid_t_ts_sg_da_con_jlxdjbxx', {
                'id': lid, 'check_code': '0000', 'dev_id': None, 'gateway_tag': None,
                'line_id': self.SENT, 'name': nm, 'off_time': None, 'on_time': None,
                'owner': owner, 'st_id': self.SENT, 'stamp': stamp, 'tmr_org_id': dcc,
            })
        # 联络线
        W.add('dim_grid_t_ts_sg_da_tline_b', {
            'id': self._gid('dim_grid_t_ts_sg_da_tline_b', adcode), 'dcc_id': dcc,
            'grid_id': grid, 'line_type': '1001',
            'name': f"{abbr}{self.tag}110kV联络线", 'running_state': 1006,
            'start_st_id': self.SENT, 'tlinetype': 2002, 'tnode_id': self.SENT,
            'voltage_type': self.VTYPE[110],
        })

        # 断路器 / 母线 / 主变 / 绕组
        for kv in (220, 110):
            for k in range(3):
                W.add('dim_grid_t_ts_sg_da_breaker_b', {
                    'id': self._gid('dim_grid_t_ts_sg_da_breaker_b', adcode), 'bay_id': None,
                    'name': f"{abbr}{self.tag}{kv}kV{k+1}号开关", 'normal_state': '1001',
                    'running_state': 1003, 'st_id': self.SENT, 'voltage_type': self.VTYPE[kv],
                    'model': 'LTB245E1' if kv == 220 else 'ZN63A-12',
                    'breaking_capacity': 50.0 if kv == 220 else 25.0,
                    'operate_date': self.dtstr(dt.date(2015, 6, 1)), 'expiry_date': self.dtstr(dt.date(2035, 6, 1)),
                    'stamp': stamp, 'owner': owner, 'check_code': '0000', 'on_time': None,
                    'dev_id': None, 'off_time': None, 'dispatch_org_id': str(dcc),
                    'supplier_id': None, 'monitor_org_id': f"002{dcc % 10**6:06d}",
                    'assets_company_id': assets_co, 'license_org_id': f"002{dcc % 10**6:06d}",
                    'maint_org_id': f"003{dcc % 10**6:06d}", 'sys_flag': '1', 'tag': None,
                })
            for k in range(2):
                W.add('dim_grid_t_ts_sg_da_busbar_b', {
                    'id': self._gid('dim_grid_t_ts_sg_da_busbar_b', adcode),
                    'busbartype': 1001, 'arrange_type': '3001', 'dispatch_org_id': dcc,
                    'name': f"{abbr}{self.tag}{kv}kV{k+1}号母线", 'owner': owner,
                    'running_state': 1003, 'st_id': self.SENT, 'voltage_type': self.VTYPE[kv],
                    'assets_company_id': assets_co, 'bay_id': None, 'check_code': None,
                    'dev_id': None, 'expiry_date': None, 'license_org_id': f"002{dcc % 10**6:06d}",
                    'maint_org_id': f"003{dcc % 10**6:06d}", 'model': None,
                    'monitor_org_id': f"002{dcc % 10**6:06d}", 'off_time': None, 'on_time': None,
                    'operate_date': self.dtstr(dt.date(2015, 6, 1)), 'stamp': stamp,
                    'supplier_id': None, 'sys_flag': '1', 'tag': None,
                })
            # 主变 + 绕组
            trf = self._gid('dim_grid_t_ts_sg_da_dev_pwrtransfm_b', adcode)
            W.add('dim_grid_t_ts_sg_da_dev_pwrtransfm_b', {
                'id': trf, 'assets_company_id': assets_co, 'dispatch_org_id': dcc,
                'insulating_medium': '1001', 'license_org_id': f"002{dcc % 10**6:06d}",
                'maint_org_id': f"003{dcc % 10**6:06d}",
                'model': 'SSZ11-120000/220' if kv == 220 else 'SZ11-50000/110',
                'monitor_org_id': f"002{dcc % 10**6:06d}",
                'mva_rate': 120.0 if kv == 220 else 50.0,
                'name': f"{abbr}{self.tag}{kv}kV1号主变", 'operate_date': self.dtstr(dt.date(2015, 6, 1)),
                'owner': str(owner), 'running_state': 1003, 'st_id': self.SENT, 'stamp': stamp,
                'structural_style': '1001', 'supplier_id': None, 'sys_flag': '1', 'usage': 1001,
                'vol_regulating_mode': '1002', 'voltage_type': self.VTYPE[kv], 'winding_n': '3',
                'check_code': '0000', 'dev_id': None, 'expiry_date': None,
                'off_time': None, 'on_time': None,
            })
            for k in range(2):
                W.add('dim_grid_t_ts_sg_da_transfmwd_b', {
                    'id': self._gid('dim_grid_t_ts_sg_da_transfmwd_b', adcode),
                    'mva_rate': 60.0, 'name': f"{abbr}{self.tag}{kv}kV1号主变{k+1}侧绕组",
                    'owner': owner, 'regulating_type': '1001', 'st_id': self.SENT,
                    'tmr_org_id': f"002{dcc % 10**6:06d}", 'transfm_id': self.SENT,
                    'voltage_type': str(self.VTYPE[kv]), 'wind_type': '1001', 'wire': '1001',
                    'bay_id': None, 'check_code': '0000', 'dev_id': None, 'off_time': None,
                    'on_time': None, 'r': 0.0, 'r0': 0.0, 'stamp': stamp, 'tap_c': 0.0,
                    'tap_h': 0.0, 'tap_l': 0.0, 'tap_n': 17.0, 'v_rate': float(kv),
                    'v_tap_n': 0.0, 'x': 0.0, 'x0': 0.0,
                })

        # 分布式电源（每区县 1~2 台）
        for k in range(rng.randint(1, 2)):
            W.add('dim_grid_t_ts_sg_da_dev_generator_b', {
                'id': self._gid('dim_grid_t_ts_sg_da_dev_generator_b', adcode),
                'assets_company_id': assets_co, 'dispatch_org_id': dcc, 'enviro_pro': None,
                'fuel_type': 1001, 'is_incrcapacity': None, 'license_org_id': None,
                'maint_org_id': None, 'max_output': float(rng.choice([6.0, 10.0, 20.0, 30.0])),
                'model': '分布式光伏', 'mva_rate': float(rng.choice([6.0, 10.0, 20.0])),
                'name': f"{abbr}{self.tag}{k+1}号分布式光伏", 'operate_date': self.dtstr(dt.date(2022, 9, 1)),
                'owner': owner, 'power_rate': 0.98, 'power_type': '1001', 'running_state': 1003,
                'st_id': self.SENT, 'stamp': stamp, 'supplier_id': None, 'sys_flag': '1',
                'terminal_voltage': '1008', 'tmr_org_id': f"002{dcc % 10**6:06d}",
                'voltage_online': 1003, 'voltage_type': 1008,
            })

    def _grid_region_plan(self, counties):
        """省/地市级电网模型的候选主键计划：{表: [(id, adcode, 名称)]}，地市去重。"""
        cities = {}
        for c in counties:
            a = str(c.get('region_adcode') or '')
            if len(a) >= 6:
                cities.setdefault(a[:4] + '00', c.get('city_name') or a[:4] + '00')
        plan = defaultdict(list)
        for adc, cname in sorted(cities.items()):
            plan['dim_grid_t_ts_sg_da_con_plant_b'].append(
                (self._natid('111', adc, 10, 4), adc, cname))
            plan['dim_grid_t_ts_sg_da_con_plant_capacity'].append(
                (self._natid('111', adc, 10, 4), adc, cname))
            plan['dim_grid_t_ts_sg_da_con_substation_b'].append(
                (self._natid('112', adc, 10, 4), adc, cname))
            plan['dim_grid_t_ts_sg_da_con_commonsubstation_b'].append(
                (self._natid('111', adc, 100, 4), adc, cname))
            plan['dim_grid_t_ts_sg_da_con_pwrgrid_b'].append((int('101' + adc), adc, cname))
            plan['dim_grid_t_ts_sg_da_tddc'].append((int(adc + '001'), adc, cname))
        plan['dim_grid_t_ts_sg_da_con_pwrgrid_b'].append(
            (101330000, '330000', '国网浙江省电力有限公司'))
        plan['dim_grid_t_ts_sg_da_conversubstation_b'].append(
            (self._natid('113', '330900', 1, 4), '330900', '舟山'))
        return plan

    def _gen_grid_model_regions(self, counties):
        """D5000 电网模型：全省 / 地市粒度（全省只生成一次，与区县循环无关）。

        真实库里 con_pwrgrid / con_plant / con_substation 这类是"一张网一个实例"，
        按区县重复生成是错的，这里按 省 → 地市 去重。目标库里已存在的行（真实数据
        已经覆盖了省电网 + 部分地市电网）由 preflight 剔除，只补缺失的地市——这样既
        不对真实字典做覆盖，又把电网清单补全。
        """
        W = self.W
        plan = getattr(self, '_grid_plan', None) or self._grid_region_plan(counties)
        for tid, adc, cname in plan.get('dim_grid_t_ts_sg_da_con_pwrgrid_b', []):
            W.add('dim_grid_t_ts_sg_da_con_pwrgrid_b', {
                'id': tid, 'name': cname or f"{adc}电网",
                'name_abbreviation': (cname or adc).replace('国网浙江省电力有限公司', '')[:6] or adc,
            })
        for tid, adc, cname in plan.get('dim_grid_t_ts_sg_da_con_plant_b', []):
            short = (cname or adc).replace('国网浙江省电力有限公司', '').replace(
                '供电公司', '').replace('电力有限公司', '') or adc
            stamp = f"{adc}_0061{111}{adc}_2026-01-01 00:00:00"
            W.add('dim_grid_t_ts_sg_da_con_plant_b', {
                'id': tid, 'address': short, 'altitude': 10.0, 'assets_ownership': 1001,
                'assets_ownership_com_id': self.SENT, 'company_id': self.SENT,
                'connective_pg_id': int('101' + adc), 'dcc_id': int('21' + adc),
                'email': None, 'expiry_date': self.dtstr(dt.date(2050, 12, 31)), 'fax_no': None,
                'latitude': 0.0, 'longitude': 0.0, 'max_voltage_type': 1003,
                'name': f"{short}{self.tag}电厂", 'name_abbreviation': f"{short}{self.tag}电厂",
                'operate_date': self.dtstr(dt.date(2008, 5, 1)), 'operate_state': 1003,
                'owner': int(adc), 'phone_no': None, 'plant_type': 1001, 'postcode': '310000',
                'region': int(adc), 'register_name': f"{short}{self.tag}发电有限公司",
                'stamp': stamp, 'state_code': 11011, 'sys_flag': '1',
            })
            W.add('dim_grid_t_ts_sg_da_con_plant_capacity', {
                'id': tid,
                'capacity': float(self.rng.choice([120.0, 300.0, 600.0, 1000.0])),
                'name': f"{short}{self.tag}电厂", 'fuel_type': '煤', 'plant_type': '火',
            })
        for tid, adc, cname in plan.get('dim_grid_t_ts_sg_da_tddc', []):
            short = (cname or adc).replace('国网浙江省电力有限公司', '').replace(
                '供电公司', '').replace('电力有限公司', '') or adc
            W.add('dim_grid_t_ts_sg_da_tddc', {
                'st_id': tid, 'st_name': f"{short}{self.tag}电厂",
                'capacity': float(self.rng.choice([120.0, 300.0, 600.0, 1000.0])),
                'plant_type': '火电',
            })
        for tid, adc, cname in plan.get('dim_grid_t_ts_sg_da_con_substation_b', []):
            short = (cname or adc).replace('国网浙江省电力有限公司', '').replace(
                '供电公司', '').replace('电力有限公司', '') or adc
            W.add('dim_grid_t_ts_sg_da_con_substation_b', {
                'id': tid, 'altitude': 9.4, 'assets_ownership': 1001,
                'assets_ownership_com_id': int('1033' + adc[2:]),
                'dcc_id': int('21' + adc), 'latitude': 0.0, 'longitude': 0.0,
                'manage_dept_id': int('1033' + adc[2:]), 'name': f"{short}{self.tag}变",
                'operate_date': self.dtstr(dt.date(2010, 6, 1)), 'operate_state': 1003,
                'owner': int(adc), 'pg_id': int('101' + adc), 'region': int(adc),
                'stamp': f"{adc}_0061112{adc}_2026-01-01 00:00:00", 'state_code': 11010,
                'top_ac_voltage_type': 1003, 'type': 2001, 'expiry_date': None,
                'check_code': '0000', 'dc_voltage_type': None, 'scs_tag': None, 'comm_tag': None,
            })
        for tid, adc, cname in plan.get('dim_grid_t_ts_sg_da_con_commonsubstation_b', []):
            short = (cname or adc).replace('国网浙江省电力有限公司', '').replace(
                '供电公司', '').replace('电力有限公司', '') or adc
            W.add('dim_grid_t_ts_sg_da_con_commonsubstation_b', {
                'id': tid, 'connective_pg_id': int('101' + adc),
                'name': f"{short}{self.tag}公共电站", 'owner': int(adc),
                'plant_station_type': 1003, 'stamp': None, 'top_voltage_type': 1005,
            })
        for tid, adc, cname in plan.get('dim_grid_t_ts_sg_da_conversubstation_b', []):
            W.add('dim_grid_t_ts_sg_da_conversubstation_b', {
                'id': tid, 'actodc_volttype': 1005, 'dc_voltage_type': 2009, 'dcc_id': 21330900,
                'hvdcsys_id': self.SENT, 'name': f"舟山{self.tag}换流站", 'operate_state': 1003,
                'owner': 330900, 'pg_id': 101330900, 'region': 330902, 'topacvolttype': 1005,
                # 真实库换流站 type=3001（变电站才用 2001），此处不可与变电站同值
                'type': 3001, 'state_code': 11010,
            })

    # ========================================================================
    # E. 业务旁支表（从 C 段已生成的链条派生，保证金额/电量可对账）
    # ========================================================================

    def _gen_derived_business(self, ctx, users):
        if self.cfg.derived == 'off':
            return
        self._gen_addl_charg(users)
        self._gen_special_expense(users)
        self._gen_dist_line_loss(ctx, users)
        self._gen_meter_energy_p(users)
        self._gen_curve96(users)
        self._gen_invest_and_material(ctx)
        self._gen_gen_power(ctx, users)

    # ---- E1. 加收电费明细（SUM = 计费卡 t_addl_charg，严格对账） ----
    def _gen_addl_charg(self, users):
        W, rng = self.W, self.rng
        for cu in users:
            for b in cu.get('bills', []):
                if b['addl'] <= 0:
                    continue
                # 拆成"政府性基金及附加" + "其他附加"两条，金额合计精确等于 t_addl_charg。
                # 注意：金额必须直接写入，不能用 round(num × prc, 2) 反算——addl_prc 只保留
                # 6 位小数，高压客户月电量上万时反算误差可达 0.05 元，会打破对账。
                num = max(1, int(b['qty']))
                tot = round(b['addl'], 2)
                r1 = round(tot * 0.62, 2)
                rows = [('9904', '03', '0602', r1), ('0700', '02', '0601', round(tot - r1, 2))]
                for ctlg, cls, attr, amt in rows:
                    if amt <= 0:
                        continue
                    prc = round(amt / num, 6)
                    if round(num * prc, 2) != round(amt, 2):   # 6 位小数放不下时提精度，
                        prc = round(amt / num, 10)             # 保证 num × prc 仍等于金额
                    W.add('dwd_cst_addl_charg', {
                        'addl_charg_id': self.idp.n('addl'), 'calc_id': b['calc_id'],
                        'qty_charg_ym': b['ym'], 'addl_num': num, 'addl_prc': prc,
                        'addl_amt': round(amt, 2), 'addl_charg_ctlg': ctlg, 'ctlg_cls': cls,
                        'plan_no': f"1{b['calc_id'] % 10**12:012d}", 'bilg_card_id': b['bilg_id'],
                        'prc_ind_cls': '9420', 'prc_ind_ustry_cls': '第三产业',
                        'prc_ind_cls_desc_1': '十一、公共服务及管理组织', 'exp_attr_cls': attr,
                        'exec_rng_type': '01', 'disc_mode_cls': '01', 'prc_volt_code': 'AC02',
                        'prc_ec_categ': '05' if cu['is_high'] else '03',
                        'rpt_ec_categ': '  2、非工业用电', 'prc_type': '其他',
                        'inst_snap_id': b['inst_snap'], 'meter_mode': '01',
                        'inst_usage_type': '01', 'gen_cons_type': '01', 'gen_mode': '01',
                        'cust_pscateg': '01', 'gc_type': '01', 'e_consp_mode': '01',
                        'cust_cls': '01' if cu['is_high'] else '03',
                        'high_ec_ind_cls': '01' if cu['is_high'] else None,
                        'cust_volt_code': '0117' if cu['is_high'] else '0107',
                        'cust_ec_categ': '0502' if cu['is_high'] else '0302',
                        'urbanruran_categ': '01', 'as_ym': b['ym'], 'calc_bus_type': '01',
                        'prc_ind_cls_desc': '十一、公共服务及管理组织',
                    })

    # ---- E2. 力调（功率因数考核）专项费用：仅高压 ----
    def _gen_special_expense(self, users):
        W, rng = self.W, self.rng
        for cu in users:
            if not cu['is_high']:
                continue
            for b in cu.get('bills', []):
                qty = float(b['qty'])
                # 先定金额再回推单价，保证 settle_exp_qty_val × settle_prc 仍等于 settle_exp
                tgt = round(qty * rng.uniform(-0.013, 0.013), 2)
                prc = round(tgt / qty, 6)
                if round(qty * prc, 2) != tgt:
                    prc = round(tgt / qty, 10)
                W.add('dwd_cst_special_expense', {
                    'spcl_exp_id': self.idp.n('spcl'), 'calc_id': b['calc_id'],
                    'qty_charg_ym': b['ym'], 'scpl_fee_categ': '03', 'spcl_exp_scnd_lv_cls': '0303',
                    'settle_exp_qty_val': round(qty, 2), 'settle_prc': prc,
                    'settle_exp': tgt,
                    'calc_actl_pf_ap_q': int(qty), 'calc_actl_pf_rp_q': int(qty * rng.uniform(0.05, 0.12)),
                    'actl_p_f': 1.0, 'pf_std_code': '03', 'pf_std_value': 0.85,
                    'plan_no': f"1{b['calc_id'] % 10**12:012d}", 'bilg_card_id': b['bilg_id'],
                    'prc_ind_cls': '8390', 'prc_ind_ustry_cls': '第三产业',
                    'prc_ind_cls_desc_1': '十一、公共服务及管理组织', 'prc_volt_code': 'AC05',
                    'prc_ec_categ': '05', 'rpt_ec_categ': '  2、非工业用电', 'prc_type': '其他',
                    'inst_snap_id': b['inst_snap'], 'meter_mode': '01', 'inst_usage_type': '01',
                    'gen_cons_type': '01', 'gen_mode': '01', 'cust_pscateg': '01', 'gc_type': '01',
                    'e_consp_mode': '01', 'cust_cls': '01', 'high_ec_ind_cls': '01',
                    'cust_volt_code': '0117', 'cust_ec_categ': '0502', 'as_ym': b['ym'],
                    'per_mon_kva_qty': float(cu['cap']), 'unit_eval_times': 1,
                    'power_ratio': 0.85, 'exp_ymd': int(f"{b['ym']}01"),
                    'prc_ind_cls_desc': '十一、公共服务及管理组织',
                })

    # ---- E3. 台区日线损明细（台区供电量 = 该台区用户用电量 /(1-线损率)） ----
    def _gen_dist_line_loss(self, ctx, users):
        W, rng = self.W, self.rng
        org = ctx['org']
        # 真实库该列只有省级机构码 33101（码表 pro_mgt_org_code 亦仅此一项）
        prov = ctx['cty'].get('province_code') or '33101'
        by_sta = defaultdict(list)
        for cu in users:
            by_sta[cu['dist_sta']].append(cu)
        for ds, members in by_sta.items():
            daymap = defaultdict(float)
            for cu in members:
                for date, pap in cu.get('daily', []):
                    daymap[date] += pap
            if not daymap:
                continue
            loss = rng.uniform(0.03, 0.08)          # 台区综合线损率
            last = round(rng.uniform(0, 5000), 2)
            for date in sorted(daymap):
                supply = round(daymap[date] / (1 - loss), 2)
                this = round(last + supply, 2)
                W.add('dwd_cst_a_ll_dist_det_day', {
                    'rec_id': self.idp.n('dist_det'), 'stat_date': self.dtstr(date),
                    'pro_mgt_org_code': prov, 'dist_sta_id': ds, 'energy_type': '04',
                    'meter_dev_id': int(f"{ds}0"), 'meter_asset_no': f"{ds % 10**10:010d}",
                    'iot_acq_obj_id': int(f"{ds}1"), 'comp_ratio': 1.0, 'inst_lv': 1,
                    'pq_data_type': '01', 'last_read': last, 'this_read': this,
                    'coll_pq': supply, 'est_pq': 0.0, 'mr_pq': supply, 'adj_pq': 0.0,
                    'pq_abnor_ctn_days': 0.0, 'pq_abnor_days': 0.0,
                })
                last = this

    # ---- E4. 表计正反向日电量（复用同一批日电量，保证与 _xz 表一致） ----
    def _gen_meter_energy_p(self, users):
        W = self.W
        for cu in users:
            daily = cu.get('daily') or []
            if not daily:
                continue
            # light 模式只抽样低压表，避免总量失控；高压表全量
            if self.cfg.derived != 'full' and not cu['is_high'] and (cu['cust_id'] % 4):
                continue
            for date, pap in daily:
                W.add('dwd_cst_es_meter_energy_day_p', {
                    'data_date': self.dtstr(date), 'meter_asset_no': cu['meter_asset_no'],
                    't_factor': float(cu['rto']) if cu['is_high'] else 1.0,
                    'pap_e': pap, 'pap_e1': pap, 'pap_e2': 0.0, 'pap_e3': 0.0, 'pap_e4': 0.0,
                    'rap_e': '0', 'pap_e_quality': '1', 'rap_e_quality': None,
                    'pap_e_fix': None, 'pap_r_fix': round(pap * (cu['rto'] if cu['is_high'] else 1), 2),
                    'rap_e_fix': None, 'rap_r_fix': None,
                })

    # ---- E5. 计量点 96 点曲线（有功功率 / A 相电流 / 电压） ----
    def _gen_curve96(self, users):
        if not self.cfg.curve_days:
            return
        W, rng = self.W, self.rng
        # 每月取固定两天做抽样，保证与 --daily 的月份范围一致而不放大体量
        for cu in users:
            if not cu['is_high']:
                continue
            picks = [(d, p) for d, p in (cu.get('daily') or [])
                     if d.day <= self.cfg.curve_days]
            for date, pap in picks:
                common = {
                    'meter_asset_no': cu['meter_asset_no'], 'cust_no': cu['cust_no'],
                    'cust_cls': '01', 'ec_categ': '0502', 'cust_volt_code': '117',
                    'data_date': self.dtstr(date), 'mgt_org_code': cu['org'],
                }
                # p1..p96 是「15 分钟平均功率(kW)」而非电量：先把日电量拆成 96 段电量
                # （Σ段电量 = pap），再换算成功率 p = 段电量 / 0.25h，故 Σp × 0.25 = pap。
                seg = self._spread96(pap, lo=0.6, hi=1.4)
                pw = [round(v / 0.25, 4) for v in seg]
                # 电流按功率反算：一次侧 I = P / (√3 × U × cosφ)，再除以 CT 折算为二次侧
                # （表计上送的是互感器二次值：电压≈PT 二次 100V、电流≈CT 二次 <5A）
                ka = 1000.0 / (math.sqrt(3) * 10.0 * 1000.0 * 0.9) / max(cu['ct'], 1)
                ca = [round(max(0.0, p * ka) * rng.uniform(0.95, 1.05), 4) for p in pw]
                va = [round((100.0 if cu['pt'] > 1 else 230.0) + rng.uniform(-3, 3), 2)
                      for _ in range(96)]
                W.add('dwd_cst_es_e_mp_comp_curve_h', {
                    **common, 'id': self.idp.n('curve'), 'data_type': 1,
                    'data_whole_flag': None, 'data_point_flag': None,
                    'tv': float(cu['pt']), 'ta': float(cu['ct']),
                    **{f"p{i+1}": pw[i] for i in range(96)},
                })
                W.add('dwd_cst_es_e_mp_comp_curve_h_a', {
                    **common, 'id': self.idp.n('curve'), 'phase_flag': 1,
                    'data_whole_flag': None, 'data_point_flag': None,
                    **{f"a{i+1}": ca[i] for i in range(96)},
                })
                W.add('dwd_cst_es_e_mp_comp_curve_h_v', {
                    **common, 'id': self.idp.n('curve'), 'phase_flag': 3,
                    'data_whole_flag': None, 'data_point_flag': None,
                    **{f"v{i+1}": va[i] for i in range(96)},
                })

    def _spread96(self, total, lo=0.6, hi=1.4):
        """把一整天的电量拆成 96 段（每段 15 分钟）：随机形状 + 末段兜底，保证 Σ段 = total。"""
        rng = self.rng
        w = [rng.uniform(lo, hi) for _ in range(96)]
        s = sum(w)
        parts = [round(total * x / s, 4) for x in w]
        parts[-1] = round(parts[-1] + (total - sum(parts)), 4)
        return parts

    # ---- E6. 投资工单 / 工单附属材料 / 设备领用明细 ----
    def _gen_invest_and_material(self, ctx):
        W, rng = self.W, self.rng
        org = ctx['org']
        for f in ctx.get('flow', []):
            cu, app_id, loc = f.get('cu'), f.get('app_id'), f.get('loc') or {}
            cu = cu or f.get('cu')
            # 设备领用申请明细：挂在领用单上，与业扩申请单无关，单独处理
            if f.get('rcpt_app_form_id') is not None:
                ap_d = f.get('apply_d') or dt.date(2025, 5, 10)
                W.add('dwd_cst_dev_rcpt_app_dtl_info', {
                    'dev_rcpt_app_dtl_info_id': self.idp.n('devdtl'), 'chk_rslt': None,
                    # dev_use_stat 为 int 列：真实行存 3（desc 超期），码表 3/03 均映射超期
                    'chk_rslt_desc': None, 'app_dtl_no': self.SENT, 'dev_use_stat': '03',
                    'dev_use_stat_desc': '超期', 'app_no': f.get('rcpt_app_no') or self.SENT,
                    'dev_id': None,
                    'rcpt_date': self.dtstr(ap_d), 'return_flag': 1, 'return_flag_desc': '否',
                    'data_src': 1, 'data_src_desc': '监测', 'return_app_no': None,
                    'write_time': self.dtstr(dt.datetime.now()),
                })
                continue
            if app_id is None or cu is None:
                continue
            ap_d = f['apply_d']
            if rng.random() < 0.6:                     # 投资界面工单
                inv = self.idp.n('invest')
                inv_no = self.idp.n('invno')
                W.add('dwd_cst_invest_order', {
                    'invest_order_id': inv, 'invest_order_no': inv_no, 'app_no': app_id,
                    'app_elec_cap': int(cu['cap']), 'app_elec_categ': 302 if not cu['is_high'] else 502,
                    'app_elec_categ_desc': '城镇居民生活用电' if not cu['is_high'] else '非工业',
                    'app_ind_cls': '9910', 'ind_cls_desc': '城镇居民',
                    'ind_ustry_cls': '城镇居民', 'rpt_ind_ustry_cls': '城镇居民',
                    'ind_cls_level': 3, 'arch_status': 2, 'arch_status_desc': '已归档',
                    'bld_mode': '04', 'bld_mode_desc': '自建', 'bus_app_form_id': app_id,
                    'invest_date': self.dtstr(ap_d), 'invest_opn': f"{ctx['abbr']}{self.tag}投资界面",
                    'invest_stf': '仿真', 'invest_remark': f"{self.tag}仿真投资工单",
                    'land_char': 1, 'land_char_desc': '自有', 'mgt_org_code': org,
                    'mgt_org_name': f"{ctx['abbr']}{self.tag}",
                    'county_code': ctx['cty'].get('county_code') or org,
                    'county_name': ctx['cty'].get('county_name') or f"{ctx['abbr']}{self.tag}",
                    'dist_lv': 4, 'dist_lv_desc': '区县',
                    'no_ps_reason': None, 'acmp_time': self.dtstr(ap_d + dt.timedelta(days=6)),
                    'write_time': self.dtstr(dt.datetime.now()), 'voltage': 117 if cu['is_high'] else 107,
                    'voltage_desc': cu['volt'], 'veri_elec_cons_cap': int(cu['cap']),
                    **loc,
                }, pk='invest_order_id')
            # 工单附属材料（现场勘察/中间检查/竣工验收）——类别与描述均取码表
            cls_code = rng.choice(['01', '02', '03'])
            cls_desc = self.CB.name_of('wk_order_affil_mtrl_rec_cls', cls_code) or cls_code
            proj_code = rng.choice(['04', '06', '05'])       # 受电/变电/土建工程（真实库有值）
            am_code = rng.choice(['02', '03', '05'])
            am_desc = self.CB.name_of('app_mode', am_code) or am_code
            W.add('dwd_cst_wkorder_affilmtrl_rec', {
                'wk_order_affil_mtrl_rec_id': self.idp.n('affmtrl'),
                'wk_order_affil_mtrl_rec_cls': cls_code,
                'wk_order_affil_mtrl_rec_cls_desc': cls_desc,
                'proj_categ': proj_code,
                'proj_categ_desc': self.CB.name_of('proj_categ', proj_code) or proj_code,
                'app_no': app_id, 'applnt': cu['bp_name'], 'app_org': f"{ctx['abbr']}{self.tag}",
                'app_cont': cu['bp_name'], 'app_date': self.dtstr(ap_d),
                'requirement': f"{self.tag}仿真资料要求", 'receiver': '仿真',
                'rcv_time': self.dtstr(ap_d + dt.timedelta(days=1)),
                'contact': cu['bp_name'], 'contact_tel': '0571-00000000',
                'spot_chk_flag': '02', 'spot_chk_flag_desc': '是',
                'cstr_rec': '02', 'cstr_rec_desc': '有',
                'valid_flag': '02', 'valid_flag_desc': '有效',
                'rvw_person': '仿真', 'veri_rslt': 1, 'veri_rslt_desc': '通过',
                'aprv_date': self.dtstr(ap_d + dt.timedelta(days=3)), 'the_times': 1,
                'dept_org': org, 'exam_beg_time': self.dtstr(ap_d),
                'exam_end_time': self.dtstr(ap_d + dt.timedelta(days=2)),
                'bus_app_form_id': app_id,
                'app_mode': am_code, 'app_mode_desc': am_desc,
                'write_time': self.dtstr(dt.datetime.now()),
            })

    # ---- E7. 分布式发电客户：申请记录 + 并网申请 ----
    def _gen_gen_power(self, ctx, users):
        W, rng = self.W, self.rng
        org = ctx['org']
        pool = [u for u in users if u['is_high']] or list(users)
        for cu in rng.sample(pool, max(1, int(len(pool) * 0.6))):
            # 注意：flow 里混有两类条目——业扩申请（有 app_id）与设备领用单（有 rcpt_app_form_id），
            # 必须按 app_id 过滤，否则首元素是领用单时会 KeyError。
            flow = [f for f in ctx.get('flow', [])
                    if f.get('cu') is cu and f.get('app_id') is not None]
            app_id = flow[0]['app_id'] if flow else self.idp.n('app')
            ap_d = flow[0]['apply_d'] if flow else dt.date(2025, 4, 1)
            loc = flow[0].get('loc', {}) if flow else {}
            gc_id = self.idp.n('gcapp')
            cb = self.CB
            # 业务类型/类别：真实库 gc_app_rec 只落 040601（分布式电源改类）与
            # 032302（计量设备更换）两码，其余同族码真实库从未出现，不可扩张抽样。
            g_bt, g_btd, g_bc, g_bcd = self._biz_pair(['040601', '032302'])
            # 省份/地市/区县允许并网标志：码表 prov_alow_flag 只有 01 否 / 02 是
            y_code = '02'
            W.add('dwd_cst_gc_app_rec', {
                'cust_gen_app_rec_id': gc_id, 'bus_app_form_id': app_id, 'app_no': app_id,
                'cust_id': cu['cust_id'], 'cust_no': cu['cust_no'], 'tax_rate': 1,
                'gc_stat': 1, 'gc_stat_desc': '正常发电客户',
                'gc_type': 2, 'gc_type_desc': '低压', 'volt_lv': 107, 'volt_lv_desc': '交流220V',
                'gen_mode': 1, 'gen_mode_desc': '太阳能发电', 'gen_mode_level': 1, 'gen_mode_1': 1,
                'app_cap': float(cu['cap']), 'ctrt_cap': float(cu['cap']),
                'inst_cap': float(cu['cap']), 't_cap': float(cu['cap']),
                'ec_date': self.dtstr(ap_d + dt.timedelta(days=20)),
                'instal_loc': 1, 'instal_loc_desc': '屋顶',
                'cust_pscateg': '1', 'cust_pscateg_desc': '分布式电源',
                'plant_type': '01', 'plant_type_desc': '户用',
                'accs_mode': 1, 'accs_mode_desc': '全额上网', 'accs_cap': float(cu['cap']),
                'e_consp_mode': '3', 'e_consp_mode_desc': '自发自用余电上网',
                'invest_mode': '1', 'invest_mode_desc': '自投资',
                'prov_alow_flag': y_code, 'city_alow_flag': y_code, 'county_alow_flag': y_code,
                'pv_paflag': '05',
                'cust_categ': '3', 'arch_status': '02', 'arch_status_desc': '变更',
                'ind_cls': 9910, 'ind_cls_desc': '城镇居民', 'ind_cls_level': 3,
                'impt_lv': '05', 'impt_lv_desc': '非重要用户', 'bp_id': cu['bp_id'] % self.SENT,
                'mgt_org_code': org, 'mgt_org_name': f"{ctx['abbr']}{self.tag}",
                'county_code': ctx['cty'].get('county_code') or org,
                'county_name': ctx['cty'].get('county_name') or f"{ctx['abbr']}{self.tag}",
                'dist_lv': 4, 'dist_lv_desc': cb.name_of('dist_lv', '4') or '区县',
                'chg_date': self.dtstr(ap_d), 'cust_name': cu['bp_name'],
                'ec_addr': f"{ctx['abbr']}{self.tag}光伏地址", 'creat_date': self.dtstr(ap_d),
                'urbanrural_flag': '02', 'urbanrural_flag_desc': '农村',
                'bus_categ': g_bc, 'bus_categ_desc': g_bcd,
                'bus_type': g_bt, 'bus_type_desc': g_btd,
                'app_mode': '05', 'app_mode_desc': '营业厅受理', 'acpt_date': self.dtstr(ap_d),
                'srv_form': '01', 'srv_form_desc': '线上', 'app_stat': '5', 'app_stat_desc': '归档',
                'proc_stat': '2', 'proc_stat_desc': '完成',
                'wk_order_stat': '5', 'wk_order_stat_desc': '归档',
                'write_time': self.dtstr(dt.datetime.now()),
                'conn_gen_power_voltage': '0107', 'conn_gen_power_voltage_desc': '交流220V',
                'natural_person_flag': '02', 'natural_person_flag_desc': '是',
                **loc,
            }, pk='cust_gen_app_rec_id')

            W.add('dwd_cst_conn_gen_power_app', {
                'conn_gen_power_app_id': self.idp.n('cgenapp'),
                'conn_gen_power_id': self.SENT, 'conn_gen_power_no': self.SENT,
                'protect_mode': 99, 'protect_mode_desc': '其他', 'supl_type': 5,
                'supl_type_desc': '公变', 'supl_char': 1, 'supl_char_desc': '主供电源',
                'lay_mode': '1', 'lay_mode_desc': '电缆直埋', 'pr_point': 1, 'pr_point_name': '并网点',
                'voltage': 107, 'voltage_desc': '交流220V',
                'run_mode': '04', 'run_mode_desc': '运行',
                'dist_sta_id': cu['dist_sta'], 'pipeline_id': cu['line'],
                'srv_loc_id': cu['cust_id'] % self.SENT, 'cust_id': cu['cust_id'],
                # arch_status 为 int 列，真实行存 1（desc 新增）；写 '02' 会落成 2 与真实不符
                'chg_date': self.dtstr(ap_d), 'arch_status': '01', 'arch_status_desc': '新增',
                'srv_loc_app_rec_id': app_id, 'app_no': app_id, 'accs_cap': float(cu['cap']),
                'orgn_cap': float(cu['cap']), 'ps_dev_type': '01', 'ps_dev_type_desc': '管线',
                'bus_app_form_id': app_id, 'srv_loc_name': cu['bp_name'],
                'srv_loc_addr': f"{ctx['abbr']}{self.tag}光伏地址",
                'mgt_org_code': org, 'mgt_org_name': f"{ctx['abbr']}{self.tag}",
                'county_code': ctx['cty'].get('county_code') or org,
                'county_name': ctx['cty'].get('county_name') or f"{ctx['abbr']}{self.tag}",
                'dist_lv': 4, 'dist_lv_desc': cb.name_of('dist_lv', '4') or '区县',
                'write_time': self.dtstr(dt.datetime.now()), **loc,
            }, pk='conn_gen_power_app_id')

    # ---------- 汇报 ----------

    def report(self):
        total = sum(self.W.counts.values())
        print("\n" + "=" * 66)
        print(f"{'[dry-run] 预计写入' if self.cfg.dry_run else '[ok] 已写入'} {total:,} 行 / "
              f"{len(self.W.counts)} 张表")
        print("=" * 66)
        for t, n in sorted(self.W.counts.items(), key=lambda x: -x[1]):
            print(f"  {n:>9,}  {t}")
        if self.W.dropped:
            print("\n[结构对齐提示] 以下列在目标库不存在，已自动跳过：")
            for t, cols in sorted(self.W.dropped.items()):
                print(f"  {t}: {', '.join(sorted(cols))}")
        if self.W.nulled:
            print(f"\n[类型自适应] 以下列在目标库为 32 位 int，装不下 bigint 主键，已置 NULL"
                  f"（共 {len(self.W.nulled)} 列）：")
            for c in sorted(self.W.nulled):
                print(f"  {c}")
        if self.W.untracked:
            print(f"\n[回滚缺口] 以下表已写入但未登记主键，--purge 无法清除："
                  f"{', '.join(sorted(self.W.untracked))}")
            print("  请为其补充 ID_COLS 定义，或在该 add() 调用处显式传 pk=")
        if not self.cfg.dry_run:
            print("\n提示：清除本次仿真数据可用 `python simulator.py --purge --tag "
                  f"{self.cfg.tag}`")


def _load_manifests(db, tag):
    """扫描台账目录，返回匹配 (db, tag) 的清单文件列表与合并后的主键集合。

    文件名约定：`{db}__{tag}__{stamp}.json`。因此 db 与 tag 都不得包含 '__'。
    """
    if '__' in tag:
        sys.exit("[purge] tag 不能包含 '__'（台账文件名以它作为分隔符）")
    if not os.path.isdir(MANIFEST_DIR):
        return [], {}
    files = []
    for fn in sorted(os.listdir(MANIFEST_DIR)):
        if not fn.endswith('.json'):
            continue
        parts = fn[:-5].split('__')
        if len(parts) < 3:
            continue
        if parts[0] == db and parts[1] == tag:
            files.append(os.path.join(MANIFEST_DIR, fn))
    merged = defaultdict(set)     # table -> {pk values}
    pkcol = {}                    # table -> pk column name
    for p in files:
        try:
            obj = json.load(open(p, encoding='utf-8'))
        except Exception as e:
            print(f"[purge] 跳过无法解析的台账 {p}：{e}")
            continue
        for t, info in (obj.get('tables') or {}).items():
            pkcol.setdefault(t, info.get('pk'))
            merged[t].update(info.get('pks') or [])
    return files, {'pkcol': pkcol, 'merged': merged}


def purge(cfg):
    """按**本地主键清单台账**精确清除仿真数据。

    台账由每轮生成写入 MANIFEST_DIR/{db}__{tag}__{stamp}.json，记录本轮真正插入的
    每一行主键。删除时逐表执行 `DELETE FROM t WHERE pk IN (...)`——删掉的每一行都是
    当时确实插入过的，绝不会碰到此前就存在的真实数据（这正是旧的 ID 区间方案出错的
    地方：区间跨轮次累积后会把存量圈进来）。

    加 --dry-run 只预览将要删除的行数，不落任何删除。
    """
    files, plan = _load_manifests(cfg.db, cfg.tag)
    if not files:
        print(f"[purge] 台账目录 {MANIFEST_DIR} 中未找到 db={cfg.db} tag={cfg.tag} 的记录，"
              f"无可清除数据")
        return
    merged, pkcol = plan['merged'], plan['pkcol']
    total_plan = sum(len(v) for v in merged.values())
    print(f"[purge] 目标库 {cfg.db}｜tag={cfg.tag}｜匹配台账 {len(files)} 个文件"
          f"｜拟删 {len(merged)} 张表、{total_plan:,} 个主键")
    for p in files:
        print(f"    · {os.path.basename(p)}")
    for t in sorted(merged, key=lambda x: -len(merged[x])):
        print(f"    {len(merged[t]):>9,}  {t}  (pk={pkcol[t]})")
    if cfg.dry_run:
        print("[purge] dry-run，未执行删除")
        return

    conn = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user,
                           password=cfg.password, database=cfg.db, charset='utf8mb4',
                           autocommit=False)
    deleted, chunk = 0, 1000
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for t, ids in merged.items():
                pk = pkcol[t]
                if not pk:
                    print(f"[purge] 跳过 {t}：台账缺少主键列名")
                    continue
                vals = list(ids)
                for i in range(0, len(vals), chunk):
                    batch = vals[i:i + chunk]
                    ph = ','.join(['%s'] * len(batch))
                    try:
                        cur.execute(
                            f"DELETE FROM `{t}` WHERE `{pk}` IN ({ph})", batch)
                        deleted += cur.rowcount
                    except Exception as e:
                        print(f"[purge] 删除 {t} 报错：{e}")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()
    finally:
        conn.close()

    # 归档已消费的台账，避免重复清除；真出问题可去掉 .purged 后缀重放
    for p in files:
        try:
            os.replace(p, p + '.purged')
        except OSError:
            pass
    print(f"[purge] 已删除 {deleted:,} 行；{len(files)} 个台账已归档为 *.purged")


def main():
    ap = argparse.ArgumentParser(description='deshu5 电网营销数据仿真引擎')
    ap.add_argument('--db', default=os.environ.get('SIM_DB', 'fz01'))
    ap.add_argument('--host', default=os.environ.get('MYSQL_HOST', 'localhost'))
    ap.add_argument('--port', type=int, default=int(os.environ.get('MYSQL_PORT', '3306')))
    ap.add_argument('--user', default=os.environ.get('MYSQL_USER', 'root'))
    ap.add_argument('--password', default=os.environ.get('MYSQL_PASSWORD', ''))
    ap.add_argument('--counties', type=int, default=0, help='只跑前 N 个区县（0=全部）')
    ap.add_argument('--county-codes', nargs='*', help='只跑指定 mgt_org_code')
    ap.add_argument('--from', dest='ym_from', type=int, default=202501, help='起始年月 YYYYMM')
    ap.add_argument('--to', dest='ym_to', type=int, default=now_ym(), help='结束年月 YYYYMM')
    ap.add_argument('--daily', choices=['full', 'recent', 'sampled', 'off'], default='recent',
                    help='日粒度电量范围：full=全部月份 / recent=最近3月 / sampled=每月抽5天 / off=不生成')
    ap.add_argument('--derived', choices=['full', 'light', 'off'], default='light',
                    help='业务旁支表（加收明细/线损/正反向电量/投资工单/并网申请等）密度：'
                         'light=默认（低压表按 25%% 抽样）/ full=与日粒度表同密度 / off=不生成')
    ap.add_argument('--curve-days', dest='curve_days', type=int, default=2,
                    help='每月抽样生成 96 点曲线的天数（0=不生成曲线；曲线单行 108 列，'
                         '调大要留意体量）')
    ap.add_argument('--tag', default='模拟', help='生成数据名称标记，用于识别与回滚')
    ap.add_argument('--seed', type=int, default=20260912)
    ap.add_argument('--dry-run', action='store_true', help='只统计不落库；--purge 时表示只看清单')
    ap.add_argument('--purge', action='store_true', help='按台账删除该 tag 的仿真数据')
    a = ap.parse_args()
    if not a.password:
        sys.exit('缺少 MySQL 密码：设置 MYSQL_PASSWORD 环境变量或传 --password')
    if a.purge:
        purge(a)
    else:
        Simulator(a).run()


if __name__ == '__main__':
    main()
