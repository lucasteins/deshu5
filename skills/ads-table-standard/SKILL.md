---
name: ads-table-standard
description: fz01（仿真档）/ sc01（生产档）统计报表层（ads_ 前缀表）统一设计规范：表命名四段式、必备公共字段、期间/机构维度口径、字段类型与命名规范、治理库元数据登记与索引要求；含 2026-08-31 存量 17 表标准化迁移后的新旧表名映射与遗留问题
type: prompt
whenToUse: 在 fz01/sc01 新增统计报表表（ads_）、接入外部报表/年鉴数据、评审或改造存量 ads_ 表结构，或需要核对统计表字段命名、期间与维度口径、类型选择、治理库/本体库同步范围时
---

# ads_ 统计报表表设计规范（fz01 / sc01）

> **库名变更（2026-09-14）**：`database01 → fz01`（仿真档 staging）、`marketing_40 → sc01`（生产档 production）；
> 治理/本体库同名迁移（`fz01_governance` / `sc01_governance`、`fz01_ontology` / `sc01_ontology`），
> 档位切换见 `core/db_profile.py`。本文库名均为新名；治理库/本体库随档位。

适用范围：`fz01`（仿真档；生产档 `sc01` 同构）统计报表层（17 张 `ads_` 表；同库还有 43 dim + 41 dwd）。
目标：**同名同义同型、维度口径唯一、期间格式唯一、可注释可检索**。新增 ads_ 表必须过第五节检查清单。

## 一、表命名

- 格式：`ads_<业务域>_<主题>_<粒度>_<统计周期>`，全小写下划线，如 `ads_cst_industry_power_mon`
- 业务域沿用现有四段：`cst`（营销客户）/ `grid`（电网负荷）/ `prj`（项目/专题）/ `urb`（城市建设）/ `tjj`（统计数据）
- 统计周期后缀语义固定：`mon` 月 / `da` 日 / `mi` 分钟级 / `all` 全量快照
- **禁止**：表名嵌源系统/接口编码（存量反例：`yk3300009`、`t_ts_sg_da_`、`ngdis_st2d_`）；同主题建两套表（存量反例：`ads_prj_01_ngdis_st2d_trade_dq_1` 与 `ads_prj_ngdis_st2d_trade_dq_1` 近乎重复）
- 含义不明的 `_do` / `_1` 后缀不再使用

## 二、必备公共字段（每张 ads_ 表固定 6 个，顺序置前）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT AUTO_INCREMENT PRIMARY KEY | 物理主键 |
| stat_period | VARCHAR(8) NOT NULL | 统计期间，见第三节口径 |
| mgt_org_code | VARCHAR(32) NOT NULL | 管理单位编码（禁止 int——编码可能带前导零；存量 16 张表是 int，属历史包袱） |
| mgt_org_name | VARCHAR(128) NOT NULL | 管理单位名称（冗余便于直读） |
| etl_time | DATETIME | 数据写入时间（替代 write_time/date_ope_time 等杂名） |
| remark | VARCHAR(255) | 可选，口径备注 |

`datasource_id`（INT，来源系统标识）为**可选字段**，有明确来源系统时再建，不作必备要求。

**例外（行政区划口径表）**：非电力域数据（如城市建设年鉴，域前缀 `urb`）无管理单位概念，
用 `region_name VARCHAR(64) + region_level VARCHAR(16)`（全国/省/城市）替代 mgt_org_* 一对，
并**必须带 `region_adcode VARCHAR(12)`**（GB/T 2260 六位区划码，跨域贯通主键）；
索引相应改为 `idx_region_period (region_adcode, stat_period)`。
**窄表（指标转行）**另加指标三件套：`indicator_code VARCHAR(64) + indicator_name VARCHAR(255) + unit VARCHAR(32)`，
度量值固定 `meas_value DECIMAL(20,4)`，并加 `KEY idx_indicator (indicator_code, stat_period)`。
**混合维度窄表**（行维度兼有地区与科目，如统计局主题表）采用两轴模型：
地区轴 `region_name/region_adcode/region_level`（**整表默认口径也要填**，如浙江省月度数据科目行默认 330000/省，禁止留空）+
科目轴 `dim_name`（行业/品种/科目名，纯地区行为空）；不再设 dim_type 类型列（地区与否看 dim_name 是否为空即可）。

## 三、维度与期间口径（唯一来源）

- **期间**：年度表 `stat_period` 存 `yyyy`（如 '2024'）；月度表存 `yyyymm`（如 '202605'）；日表存 `yyyy-mm-dd`；分钟级用独立 `ts DATETIME` 列。禁止 int 存期间、禁止 text 存日期时间字符串、禁止只存月份数字（存量反例：`tab_month` 值 '5' 无年份；`rq` text 存 '2026-06-23 00:00:00'；`period/periods/data_period/rpt_month` 五种命名并存）
- **机构（电力域）**：只用 `mgt_org_code / mgt_org_name` 一对。禁止 `dept_code / dept_id / org_no / org_name / extend_field_org_code / dept_code__` 等并行维度（存量中 yk3300009、qshydl_mi、prj 系列表均有此问题）
- **地区（行政区划域）**：`region_adcode + region_name + region_level` 三件套；region_adcode 以 GB/T 2260 为准（码值域 `region_adcode` 已预载 3347 条），region_name 尽量用 GB 标准名；region_level 码值域 `urb_region_level`（01全国/02省/03城市/04区县）；层级纯化纪律：city 表只存城市行（全国/省合计行删除，全国数在 nat 表、省数在 prov 表）
- **行业/分类维度**：`trade_code VARCHAR + trade_name VARCHAR` 成对；码值必须落在治理库码值域内

## 四、字段类型与命名规范

- 金额/电量/容量等度量：`DECIMAL(18,4)`，**禁止 DOUBLE**（浮点精度风险；存量 391 列 double、0 列 decimal）
- 比率：`DECIMAL(10,4)`，存数值不存 '%' 字符串（反例：qshydl_mi.`rate` 是 text）
- 计数：`INT`（超 21 亿用 BIGINT）；日期时间：`DATETIME`，字符串：`VARCHAR(n)` 定长
- **禁止 TEXT 存短文本**（存量 201 列 text，含机构名/类型标志——不可索引不可直聚）
- 指标列命名：英文优先 + 量纲后缀 `_qty`(电量) `_amt`(金额) `_rate`(比率) `_cnt`(次数) `_avg/_max/_min`；**禁止裸拼音缩写**（反例：`zjrl/ydlby/ydlsnty/bytb/yhs/lx/rq`，且 `zjrl` 在存量中 double/text 两型并存——同名必须同型）
- 同名列跨表类型必须一致（存量反例：`create_time` int/text、`dept_code` int/text、`id` int/bigint）
- 时序点值（96 点/5 分钟曲线）：窄表 `(meas_type, ts, meas_value)` 三列式，**禁止 288 列 v0000..v2355 宽表**（存量反例：ads_grid_t_ts_sg_lc_con_pwrgrid_b）

## 五、元数据登记、索引与入库检查清单

- **元数据登记是底线**：注释不写在物理表上，而是集中登记在治理库（`fz01_governance` / 生产档 `sc01_governance`，应用检索读这里）：
  - `schema_table_docs`：一表一行（table_name 唯一键），table_comment + domain_l1/l2/l3 业务域分级
  - `schema_column_docs`：一列一行（(table_name, column_name) 唯一键），column_comment + data_type + is_pk + doc_text
  - 物理表 COMMENT 可同步但非必需；治理库登记必须 100% 覆盖（存量 17 表 / 731 列已全覆盖）
- 索引底线：`KEY idx_org_period (mgt_org_code, stat_period)`；大表按需加 `(stat_period)` / `(trade_code)`
- 新表入库前逐项过：
  1. 表名四段式、无源系统编码
  2. 6 个公共字段齐全且类型符合第二节
  3. 期间/机构/行业维度口径符合第三节
  4. 无 DOUBLE、无 TEXT 短文本、无拼音裸缩写、无同名异型
  5. 治理库 schema_table_docs / schema_column_docs 已登记、注释 100%、**data_type 与物理类型一致**，idx_org_period 已建

## 六、存量表标准化状态（2026-08-31 已治理，备份 backup_ads_std_20260831_140352.sql）

17 张存量表已按本规范完成迁移（脚本 `tools/standardize_ads_tables.py`，幂等可复跑）：16 张改名重建 + 1 张删除。新旧映射：

| 新表名（现行） | 旧表名（已删） | 中文名 |
|---|---|---|
| ads_cst_exp_cust_mon | ads_cst_bb_yk3300009 | 省内业扩表二_用电户数统计表（全口径） |
| ads_cst_exp_app_cap_da | ads_cst_cust_elec_app_rec_and_cap_do | 业扩报装户数及容量 |
| ads_cst_bill_fee_mon | ads_cst_deg_exp_stat_do | 计费_电量电费统计 |
| ads_cst_rcvbl_acct_mon | ads_cst_rcvbl_acct_stat_do | 计费_应收台账统计 |
| ads_grid_carbon_idx_da | ads_grid_t_sg_da_tpzb | PCEI双碳14个指标汇总表 |
| ads_grid_prevday_power_da | ads_grid_t_ts_data_proces_ts_sg_da_zrdl | 昨日电量 |
| ads_grid_city_cap_da | ads_grid_t_ts_sg_da_dszj | 各地市装机容量 |
| ads_grid_pv_cap_da | ads_grid_t_ts_sg_da_fbsgf | 全社会分布式光伏装机 |
| ads_grid_type_cap_da | ads_grid_t_ts_sg_da_glzjrl | 各类型装机容量 |
| ads_grid_load_point_mi | ads_grid_t_ts_sg_lc_con_pwrgrid_b | 电网负荷量测数据（288 点宽表豁免保留） |
| ads_grid_load_curve_mi | ads_grid_t_ts_sg_lc_sdxqsh | 省地县全社会负荷 |
| ads_grid_day_power_da | ads_grid_t_ts_sg_lc_sdxrdl | 省地县全社会日电量 |
| ads_prj_plant_summary_mon | ads_prj_01_ngdis_st2b_pro_plant_summary_all | 发电生产综合情况表 |
| ads_prj_society_power_mon | ads_prj_ngdis_st2d_trade_dq_1 | 全社会用电情况数据（全口径） |
| —（已删除） | ads_prj_01_ngdis_st2d_trade_dq_1 | 行业用电分类信息（与保留表零重叠，业务确认删除） |
| ads_prj_county_power_mon | ads_prj_ngdis_ieq_te_trade_use_power_mon_pm | 全社会用电情况分区县（仅存区县，dept_id_level=4） |
| ads_prj_trade_power_mon | ads_prj_zb_fzgh_qshydl_mi | 全社会用电量-分行业-发展报表 |

迁移后状态：全部 16 表有 id 主键 + idx_org_period 索引、列注释 100%、无 DOUBLE/TEXT 遗留；治理库 schema_column_docs.data_type 与物理类型一致（原 731 处漂移清零）；qa_pairs 8 条 SQL 与模板 5 outline 已改写验证；本体库重建生效（v8，report 层 16 实体）。

期间列统一：月表 `stat_period VARCHAR(6)`（'yyyymm'）；日表 `stat_date DATE`；分钟表 `ts DATETIME`（load_point_mi 宽表为 stat_date）。

遗留问题（仅记录，不影响检索）：
- `ads_cst_rcvbl_acct_mon.mgt_org_code` 值在源端已被 int 溢出损毁（2147483647），name 为掩码值，不可回填
- `ads_prj_county_power_mon.dept_code` 同样溢出损毁（mgt_org_code 完好可用）
- `ads_prj_trade_power_mon` 保留 org_no/org_name（330100 体系）与 mgt_org_*（33401 体系）双编码，查询优先用 mgt_org_*
- `ads_cst_exp_cust_mon` 的机构列为 prov_org_code（省公司）+ mgt_org_code（dept_id 转入）
- 治理库 ingest_provenance / generation_logs / report_runs 与本体库 v1~v7 历史版本中保留旧表名（留痕，勿清理）


## 七、新报表接入工作流（任意格式 → 标准化入库 → 三库同步）

外部报表（Excel/年鉴/提资表）接入 fz01（生产档 sc01 同构）的标准动作。参考实现：`tools/import_urb_yearbook.py`
（2024 年城市建设统计年鉴试点 5 表，可直接复制改造成新脚本）。**先 dry-run 验证解析，再执行，最后跑验证清单。**

### 1. 源表解析与形态判定

- 多级表头宽表（年鉴典型）：表头 2~4 行，合并单元格横行，先人工读样例确定 **数据起始行、名称列、指标列映射、丢弃列**（重复名称列/英文行/注释行）
- 形态选择：**指标转窄表优先**（(地区, 指标, 值) 检索/问数最友好）；超宽时序点值表等特殊情况才保留宽表并在表注释注明豁免
- 行列维度转换发生时，指标/维度值必须注册码值域（见第 3 步）
- 空值纪律：空字符串/横线/`\N`（导出工具 NULL 字面量）→ NULL，窄表空值不落行；数值列含 `\N` 时用 `IF(col REGEXP '^[0-9.]+$', CAST(...), NULL)` 防 1292 错误
- 行过滤：跳过全空行、注释行（注/Note/说明）、表头残留行（名称/Year/Name）

### 2. 物理建表（fz01）

- 窄表列序固定：`id → stat_period → region_adcode/region_name/region_level（或 mgt_org_*）→ indicator_code/indicator_name/unit → meas_value → [datasource_id 可选] → etl_time`
- 行政区划口径表必须带 `region_adcode`（GB/T 2260 六位码；回填用 `tools/extend_region_adcode.py` 的 find_gb：俗名走 ALIAS，功能区/管委会无码留 NULL 并在表注释说明）
- 表注释写清：中文名 + 窄表说明 + 来源（文件名/sheet 号）；列注释 100%（物理/治理双写）
- 年度表 stat_period='yyyy'；同一主题不同层级（全国/省/城市）**分表不混层**，层级列标 region_level
- 幂等：Excel/年鉴类脚本重跑 = DROP 重建 + 治理库 upsert，禁止手工堆 INSERT；**例外（2026-09 起，用户要求）**：`tools/crawl_tjj_monthly.py`（统计局月度卡片，多期间连续追加场景）用增量幂等——CREATE TABLE IF NOT EXISTS + 仅按本次报告期 DELETE 重插，**禁止 DROP**（会误删其他期间存量），治理库 row_count 登记全表行数

### 3. 治理库同步（fz01_governance / sc01_governance，缺一不可）

1. `business_domains`：新业务域先登记（如 Urb/城市建设 + 二级 Urb02 供水、Urb03 排水和污水；source 注明外部接入）
2. `schema_table_docs`：table_comment + row_count + column_count + doc_text + domain_l1/l2（upsert by table_name）
3. `schema_column_docs`：逐列登记，**data_type 必须与物理类型一致**，doc_text 按 '表 X 的字段 Y，中文注释：Z，类型：T' 生成
4. 码值（行列转换时必需）：`code_values` 建域（如 urb_water_indicator / urb_sewage_indicator / urb_region_level）+ `code_value_items` 明细（item_code=指标编码，item_name 带单位；明细先清后插保幂等）；码值判定与治理口径归口 skill `code-value-governance`
5. `code_value_column_form`：标注存储形态——indicator_code=编码、indicator_name=名称、region_level=名称（RAG 注入依据）
6. `keyword_table_map`：主题词 → 表（(keyword, table_name) 唯一键，upsert）
7. `schema_relationship_docs`：有自然 JOIN 边才登记；行政区划窄表与电力表无可靠 FK 时**省略**（勿为凑数建弱关系）

### 4. 本体库同步（fz01_ontology / sc01_ontology）

- **报表不建逐表实体**（2026-09 起）：只把新表名并入所属报表实体域的 `member_tables`
  （工具函数 `tools/enrich_report_relations.py::attach_tables_to_domain`，域不存在才创建域）；
  域划分见第八节（ReportMarketing/Grid/Project/Urban/Stats）
- 程序化换版：`OntologyService().rebuild_proposal()` → `approve(pid)`（留痕于 ontology_proposals）
- 域实体 member_tables 必须先于 rebuild 更新，否则 builder 会把未覆盖的 ads 表自动建成裸实体

### 5. 验证清单（全部通过才算完成）

1. 值抽查：≥3 个单元格与源文件原值逐一比对（含首行/末行/合计行）
2. 层级分布：`region_level` 分组计数符合源表结构（城市分列表警惕省小计行混入）
3. 治理库登记行数齐全（table_docs 5 项、column_docs = 表数×列数、码值项数 = 指标数）
4. `safe_execute_sql` 跑 2 条真实问题 SQL 验证检索链路（走 db_profile 当前档位）
5. 本体新版本 classes/entities/properties 含新表，无旧名残留
6. **检索可达性（窄表生死线，2026-09-04 事故教训）**：窄表的指标名/报表名藏在数据行里，
   表级元数据只有主题名，关键词检索（schema_kb 按 doc_text 子串命中）永远打不中——
   必须把 distinct 报表名/科目词/地区覆盖写进 `schema_table_docs.doc_text`【检索清单】段，
   并把指标词注册进 `keyword_table_map`（工具 `tools/enrich_narrow_table_docs.py`）；
   值域精确性靠共享值域（如 tjj_report_name/tjj_indicator_name/tjj_dim_name）经码值通道注入；
   验证法：起应用后对报表名/科目词各问一题，确认生成 SQL 落在正确的表且条件值与数据逐字一致

### 6. 已接入的外部报表

- 2024 年城市建设统计年鉴（试点）：`ads_urb_water_supply_nat_yr`（历年）、`ads_urb_water_supply_prov_yr`/`ads_urb_water_supply_city_yr`（供水省/市）、`ads_urb_sewage_prov_yr`/`ads_urb_sewage_city_yr`（排水污水省/市）；其余 42 个 sheet 待按同法扩展（燃气/供热/轨交/道路/市容/园林等主题，二级域按需增 Urb04+）
- 浙江省统计局月度卡片（2025-02 ~ 2026-06；1 月源站不发月度卡片，故 202501/202601 缺）：14 张 `ads_tjj_<主题>_mon` 主题窄表（gdp/industry/transport/price/main/energy/retail/foreign/service/finance/employment/income/confidence/invest）；
  抓取链路 `tools/crawl_tjj_monthly.py`（mgop h5 网关，sign=md5("token=&ak=..&api=..&ts=..&data=null") 免 token，
  源站 WAF 拦非浏览器 UA → 经 Playwright 驱动本机 Chrome 发请求；一主题一报告期一次取全表 HTML；
  支持 `--from yyyymm --to yyyymm` 区间增量抓取（bgq 形为 YYYY00MM）；**增量入库**：CREATE TABLE IF NOT EXISTS + 按本次报告期 DELETE 重插，不 DROP（2026-09 起，用户要求保留其他期间存量）；
  注意：社零主题 ztCode 逐月不定（13 或 21）脚本两者都试；重名列头自动加前驱限定如"6月-同比±%"）；
  结构为两轴混合维度窄表：region_* 整表默认浙江省（330000/省），dim_name 仅放科目（地区行为空）


## 八、跨域贯通规范（电力报表 × 政府/外部报表）

fz01 存在三套编码体系：**电网紧凑码**（33101 省 / 33401~33411 地市 / 3340130 区县，层级靠码长）、
**行政区划码 GB/T 2260**（330000/330100…）、**中文名两套写法**（"国网…供电公司" vs "杭州市"）。
贯通唯一入口 = **`dim_org_region_map` 桥表**（dim 层；重建脚本 `tools/extend_region_adcode.py`，
区划底数来自 `db/database/pcas_code_gbt2260.json`）：

- **桥表 = GB/T 2260 全量预载（省 31 + 城市 342 + 区县 2974）LEFT JOIN 电网机构映射**：
  region_name 一律 GB/T 2260 标准名；region_adcode 唯一键（贯通主键）；无电网映射的行 mgt_org_* 留空
- 电网侧映射（108 条）自 `dim_cst_mgt_org` 全树推导：
  - 地市：dist_lv_desc='地市' 的 (city_code, city_name) → 地市标准名 + adcode
  - 区县：dist_lv_desc='区县' 的 (county_code, county_name) 名称解析（两种公司命名体系 + 分公司→区启发式 + 自治县兜底）；
    客服中心/配售电/作废撤销主体不映射；滨海/前湾/台州湾/南太湖/南城/新城等功能区 GB 无码，留未命中待人工
  - 省级：33101→浙江省(330000) 为人工规则（用户确认：省公司单位范围=浙江省）
  - 重名解析纪律：电网机构只认 33 前缀 adcode（防"西湖区"挂南昌、"南城区"挂东莞）
  - **region_level 与 urb 口径对齐**：县级市（义乌市等）在年鉴中按"城市"承载，桥表 region_level 记 '城市'
- 查询写法（adcode 为主键）：`电力ads.mgt_org_code → 桥表 → region_adcode → urb ads.region_adcode`，
  禁止查询层临时 CASE/名称模糊映射
- 损毁值纪律：int 溢出值（2147483647）不建映射、不猜测回填，备注留痕
- 新域接入义务：含地区维度的外部报表，必须带 region_adcode（按 GB/T 2260 回填，俗名走 ALIAS 表）再入库
- 本体：桥表注册为 master 层实体（"电网单位-行政区划映射"），换版随 rebuild

**跨域关系文档（schema_relationship_docs）**：统计表与桥表的 JOIN 路径必须登记
（进 SchemaPreloader 关系图 → 本体关系 + NL2SQL JOIN 召回）：
`tjj/urb 表.region_adcode = dim_org_region_map.region_adcode`（每表一条）+
`dim_org_region_map.mgt_org_code = dim_cst_mgt_org.mgt_org_code`；
格式遵循既有约定（title 'A → B'、path/join_conditions JSON、business_scenarios 标来源）。
参考实现 `tools/enrich_report_relations.py`。

**报表实体域（实体精炼层 report 层二级分类）**：统计报表按业务域聚为报表实体类——
ReportMarketing（营销类）/ ReportGrid（电网类）/ ReportProject（专题类）/ ReportUrban（城市建设类）/
ReportStats（统计类），member_tables 收编对应 ads 表。**不为单张报表建实体**（2026-09 起；
存量 35 个逐表实体已删除，仅留 5 个域实体）：新增报表表只需把表名并入对应域实体的
member_tables（`attach_tables_to_domain`），无对应域时才新建域实体。
**域实体 comment 必须富化**（规划器靠它做意图命中）：域说明 + 成员报表中文名清单 + 主题词清单，
工具 `tools/enrich_report_domain_meta.py`；新建域实体时主题词覆盖该域全部核心指标词
（如 ReportStats 含"统计局/GDP/投资/社零/进出口/CPI/居民收支…"）。规划器消费方式：
`modules/report/planner.py::_entity_vocab`（意图关键词经平台 jieba 分词后命中实体
名称/标签/注释/成员表，命中域带标签与成员表进提示词）+ `_report_table_vocab`（ads 表层目录）。

维表底座：`dim_cst_mgt_org` 已于 2026-09-01 由权威 Excel（5533 行浙江全树）全量重建
（fz01 / sc01 双档同步，脚本 `tools/load_mgt_org_dim.py`；剔除 mgt_org_code='0' 脏行 2 条，
`\N` 字面量转 NULL；sc01 有 dim_cst_dev FK，装载走 FK 临时关闭 + 孤儿校验）。
重建桥表顺序：load_mgt_org_dim.py（底座变更时）→ extend_region_adcode.py（桥表重建 + urb 回填一体）。
