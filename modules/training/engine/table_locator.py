# -*- coding: utf-8 -*-
"""表定位协作者：全库名册、LLM 定位、报表层优先、三路打分合并、同族消歧、桥接补全、JOIN 路径
（自 engine/sql_generator.py 下沉；SQLGenerator 保留 5 个薄委托，其余方法完全内部化）"""
import json
import re
from typing import Dict, List, Optional

import config


class TableLocator:
    """schema_src_fn：Schema 知识源（本体/预加载器）；family_synonyms_fn：同族消歧同义词；
    onto：本体服务实例（可为 None）；call_llm：LLM 调用入口；wf：工作流配置；table_names_fn：合法表名"""

    # roster 关键列筛选：注释含这些业务词的列对定位最有判别力（曲线序列列单独经折叠纳入）
    ROSTER_KEY_KW = ('日期', '年月', '编号', '名称', '金额', '余额', '电量', '电能量', '功率', '负荷',
                      '电压', '电流', '状态', '分类', '类别', '原因', '类型', '标志', '标识', '倍率',
                      '示数', '渠道', '单位')

    # 与图谱模式一致的规则：mgt_org 是万能枢纽，禁止作为中转（仅允许作为端点）
    FK_PATH_BLOCKED_HUBS = ('dim_cst_mgt_org',)

    def __init__(self, schema_src_fn, family_synonyms_fn, onto, call_llm, wf, table_names_fn):
        self._schema_src_fn = schema_src_fn
        self._family_synonyms_fn = family_synonyms_fn
        self._onto = onto
        self._call_llm_fn = call_llm
        self._wf = wf
        self._table_names_fn = table_names_fn
        self._roster_cache = None

    def roster(self) -> str:
        """全部数据表名册（表名+注释+关键列摘要），静态文本，进程内缓存一次。
        关键列让定位 LLM 能判断“96点功率”这类诉求该选哪张表，而不是只靠表名猜。
        瘦身策略（trackC 验证准确率无损）：仅同族表（名+注释无法互相区分）保留关键列供消歧，
        其余表名+注释已自解释——定位 prompt 从 9.4K 字符降到 ~2.2K，恒推理模型下显著提速。"""
        roster = self._roster_cache
        if roster is None:
            roster = ''
            try:
                preloader = self._schema_src_fn()
                table_names = preloader.get_table_names()
                # 同族表判定：公共前缀 ≥15 且各自后缀 ≤4（如 curve_h/h_v/h_a、energy_day_l_xz/p）
                family = set()
                for i, a in enumerate(table_names):
                    for b in table_names[i + 1:]:
                        k = 0
                        while k < min(len(a), len(b)) and a[k] == b[k]:
                            k += 1
                        if k >= 15 and len(a) - k <= 4 and len(b) - k <= 4:
                            family.add(a)
                            family.add(b)
                lines = []
                for t in table_names:
                    comment = preloader.get_table_comment(t)
                    head = f"- {t}：{comment}" if comment else f"- {t}"
                    keys = []
                    if t in family:
                        for entry in self.collapse_series(preloader.get_columns(t)):
                            if entry[0] == 'range':
                                _, first, last, count = entry
                                keys.append(f"{first['name']}~{last['name']}({first.get('comment', '')}~{last.get('comment', '')})")
                            else:
                                c = entry[1]
                                cmt = c.get('comment') or ''
                                if c.get('pk') or any(kw in cmt for kw in self.ROSTER_KEY_KW):
                                    keys.append(f"{c['name']}({cmt})" if cmt else c['name'])
                    if len(keys) > 6:  # trackB-r1d: 12→6
                        keys = keys[:6] + ['…']
                    if keys:
                        head += ' | 关键列: ' + ', '.join(keys)
                    lines.append(head)
                roster = '\n'.join(lines)
            except Exception as e:
                print(f"[WARN] 构建表名册失败: {e}", flush=True)
            self._roster_cache = roster
        return roster

    def locate_tables_with_llm(self, user_question: str, fallback_tables: List[str]) -> List[str]:
        """LLM 定位与问题相关的数据表；失败时静默回退到规则识别结果，不阻断生成"""
        known_tables = set(self._table_names_fn())
        fallback = [t for t in (fallback_tables or []) if t in known_tables]
        located = []
        roster = self.roster()
        if roster:
            prompt = f"""以下是电力营销数据仓库的全部数据表：
{roster}

业务问题：{user_question}

请从上述表中选出回答该问题所必需的数据表（只选必需的，包括必须 JOIN 的中间关联表；不要选可选或仅可能相关的表）。
注意：问题中的每个筛选条件（时间、状态、缴费/结算渠道、分类等）和每个统计维度，都必须有能承载它的表。
只输出 JSON 数组（如 ["table_a", "table_b"]），不要输出任何其他内容。最多 8 张。"""
            # 单次尝试：定位只是三路提示通道之一，超时/失败由规则+RAG+草稿通道兜底
            try:
                # 恒推理模型（deepseek）忽略 thinking=False 且推理长尾可达 100s+，
                # 用独立短超时把 schema 阶段长尾封死在 wf.locate.timeout（默认 25s）
                result = self._call_llm_fn(prompt, user_question=user_question, max_tokens=512, thinking=False,
                                        timeout=self._wf['locate']['timeout'],
                                        model_override=getattr(config, 'AUX_MODEL', None))
                m = re.search(r'\[[^\]]*\]', result.get('content', '') or '', re.S)
                if m:
                    names = json.loads(m.group(0))
                    if isinstance(names, list):
                        located = [t for t in names if isinstance(t, str) and t in known_tables]
                if not located:
                    # 宽松回退：模型未按 JSON 输出时，直接扫描内容中出现的真实表名
                    content = result.get('content', '') or ''
                    located = [t for t in sorted(known_tables, key=len, reverse=True) if t in content]
                    # 去除被更长表名包含的短名（如 dim_cst_cust 被 dim_cst_cust_agrt 覆盖）
                    located = [t for t in located
                               if not any(t != o and t in o for o in located)]
            except Exception as e:
                print(f"[WARN] LLM 定位表失败: {e}", flush=True)
        print(f"[SQLGen] 定位表: LLM={located} 规则={fallback}", flush=True)
        # 只返回 LLM 原始选表；与规则表的打分合并、同族消歧、桥接补全统一由 _merge_located_tables 完成
        return located

    def locate_report_tables(self, user_question: str) -> List[str]:
        """报表层优先定位（knowledge.report_first）：省/市/县三级统计语义问题
        在本体 report 层实体中检索命中表；无命中返回空（明细层汇总兜底）。"""
        if not self._onto or not self._wf.get('knowledge', {}).get('report_first', True):
            return []
        try:
            return self._onto.locate_report_tables(user_question)
        except Exception as e:
            print(f'[WARN] 报表层定位失败: {e}', flush=True)
            return []

    def merge_located_tables(self, user_question: str, intent: Dict, llm_tables: List[str],
                              rule_tables: List[str], draft_tables: List[str], ctx: Dict,
                              max_tables: int = 8) -> List[str]:
        """打分制合并三路定位结果 → 同族消歧 → 封顶 → 桥接补全。
        证据权重：报表层优先(5，仅统计语义问题) > 草稿 SQL 实际用到(4) > LLM 语义定位(3) > 规则/RAG 召回(1)；
        仅规则命中的表若承载筛选条件字段则 +2 保命（条件关键词驱动是规则通道的职责）。"""
        scores = {}
        report_tables = self.locate_report_tables(user_question)
        for t in report_tables:
            scores[t] = scores.get(t, 0) + 5
        if report_tables:
            print(f"[SQLGen] 报表层优先命中: {report_tables}", flush=True)
        for t in draft_tables or []:
            scores[t] = scores.get(t, 0) + 4
        for t in llm_tables or []:
            scores[t] = scores.get(t, 0) + 3
        for t in rule_tables or []:
            scores[t] = scores.get(t, 0) + 1
        if not scores:
            return []
        for t in self.filter_carrier_tables(intent, list(scores)):
            if scores[t] <= 1:
                scores[t] += 2
        order_hint = {t: i for i, t in enumerate(rule_tables or [])}
        ranked = sorted(scores, key=lambda t: (-scores[t], order_hint.get(t, 99)))
        ranked = self.disambiguate_sibling_tables(ranked, user_question, protect=set(llm_tables or []))
        merged = ranked[:max_tables]
        print(f"[SQLGen] 定位表打分合并: {[(t, scores[t]) for t in merged]}", flush=True)
        expanded = self.expand_with_bridge_tables(merged, max_tables=12)
        # 记录表分级：分数前 N 的表给全量字段；桥接表与低分表只给关键列（防宽表撑爆 prompt）
        ctx['full_tables'] = set(merged[:self._wf['prompt']['full_column_top_n']])
        ctx['bridge_tables'] = set(expanded) - set(merged)
        ctx['located'] = (user_question, expanded)  # 供 v2.4 回退路径复用（与问题绑定，防串题）
        return expanded

    def filter_carrier_tables(self, intent: Dict, candidates: List[str]) -> set:
        """intent.filters 中的条件字段实际落在哪些候选表上（用于规则表的承载反证）"""
        fields = {str(f.get('field', '')).split('.')[-1] for f in (intent.get('filters') or []) if f.get('field')}
        if not fields:
            return set()
        carriers = set()
        try:
            preloader = self._schema_src_fn()
            for t in candidates:
                cols = {c['name'] for c in preloader.get_columns(t)}
                if fields & cols:
                    carriers.add(t)
        except Exception:
            pass
        return carriers

    def disambiguate_sibling_tables(self, tables: List[str], user_question: str,
                                     protect: set = None) -> List[str]:
        """同族表（公共前缀≥15 且各自剩余后缀≤4 字符，如 curve_h/h_v/h_a）按问题关键词消歧：
        关键词能命中族内部分成员时，裁掉未命中且不在 protect 中的成员；
        无法区分时保守全留。protect 用于保护有独立证据的表（如 LLM 定位结果）。"""
        if len(tables) < 2:
            return tables
        protect = protect or set()
        n = len(tables)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(n):
            for j in range(i + 1, n):
                a, b = tables[i], tables[j]
                k = 0
                while k < min(len(a), len(b)) and a[k] == b[k]:
                    k += 1
                if k >= 15 and len(a) - k <= 4 and len(b) - k <= 4:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj
        # 实体同族合并（精炼层）：同实体成员表视为一族；限 ≤4 成员的小实体，
        # 防"业务申请"这类大主题实体把语义不同的成员过度剪枝
        if self._onto is not None:
            try:
                t2e = self._onto.table_to_entity()
                ent_members = {}
                for i, t in enumerate(tables):
                    e = t2e.get(t)
                    if e:
                        ent_members.setdefault(e, []).append(i)
                for idxs in ent_members.values():
                    if 2 <= len(idxs) <= 4:
                        for j in idxs[1:]:
                            ri, rj = find(idxs[0]), find(j)
                            if ri != rj:
                                parent[ri] = rj
            except Exception:
                pass
        fams = {}
        for i in range(n):
            fams.setdefault(find(i), []).append(tables[i])
        pruned = []
        result = list(tables)
        for members in fams.values():
            if len(members) < 2:
                continue
            hits = {m: self.family_keyword_hit(m, members, user_question) for m in members}
            if not any(hits.values()):
                continue  # 关键词无法区分，保守全留
            for m in members:
                if not hits[m] and m not in protect and m in result:
                    result.remove(m)
                    pruned.append(m)
        if pruned:
            print(f"[SQLGen] 同族表消歧剔除: {pruned}", flush=True)
        return result or tables

    def family_keyword_hit(self, table: str, members: List[str], user_question: str) -> bool:
        """本表注释相对族内其他表的差异词（经同义词扩展）是否出现在问题中"""
        try:
            preloader = self._schema_src_fn()
            comment = preloader.get_table_comment(table) or ''
            others = ''.join((preloader.get_table_comment(o) or '') for o in members if o != table)
            diff = ''.join(ch for ch in comment if ch not in others).strip()
            keywords = set()
            for key, group in (self._family_synonyms_fn() or self.FAMILY_SYNONYMS).items():
                if key in diff or (key in comment and key not in others):
                    keywords.update(group)
            if len(diff) >= 2:
                keywords.add(diff)
            return any(kw in user_question for kw in keywords if len(kw) >= 2)
        except Exception:
            return False

    def build_fk_graph(self):
        """主外键邻接图 + 边 JOIN 条件索引（来自本体/启动预加载，按 knowledge.source 选源）"""
        rels = self._schema_src_fn().get_relationships()
        graph, conds = {}, {}
        for r in rels:
            a, b = r['path'][0], r['path'][1]
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
            conds[frozenset((a, b))] = r.get('join_conditions', [])
        return graph, conds

    def bfs_fk_path(self, graph, start, goal, blocked=()):
        """最短 FK 路径（BFS，blocked 节点不可作为中转）"""
        from collections import deque
        queue = deque([(start, [start])])
        seen = {start}
        while queue:
            node, path = queue.popleft()
            if node == goal:
                return path
            for nb in graph.get(node, ()):
                if nb in blocked and nb != goal:
                    continue
                if nb not in seen:
                    seen.add(nb)
                    queue.append((nb, path + [nb]))
        return None

    def expand_with_bridge_tables(self, tables: List[str], max_tables: int = 10) -> List[str]:
        """按主外键关系图补全桥接中间表：定位表两两求最短关联路径，路径上的中间表自动补入。
        解决 LLM 只挑"端点表"、漏掉看似无关但必须 JOIN 的中间表的问题。"""
        if len(tables) < 2:
            return tables
        try:
            graph, _ = self.build_fk_graph()
        except Exception:
            return tables

        result = list(tables)
        for i in range(len(tables)):
            for j in range(i + 1, len(tables)):
                path = self.bfs_fk_path(graph, tables[i], tables[j], blocked=self.FK_PATH_BLOCKED_HUBS)
                if path and len(path) > 2:
                    for mid in path[1:-1]:
                        if mid not in result:
                            print(f"[SQLGen] 补全桥接表: {mid} ({tables[i]} <-> {tables[j]})", flush=True)
                            result.append(mid)
        return result[:max_tables]

    def build_join_path_hint(self, tables: List[str], max_paths: int = 4) -> str:
        """基于定位表计算最短业务 JOIN 路径（含每跳条件），作为提示词脚手架。"""
        if len(tables) < 2:
            return ''
        try:
            graph, conds = self.build_fk_graph()
        except Exception:
            return ''
        lines = []
        seen_paths = set()
        for i in range(len(tables)):
            for j in range(i + 1, len(tables)):
                a, b = tables[i], tables[j]
                path = self.bfs_fk_path(graph, a, b, blocked=self.FK_PATH_BLOCKED_HUBS)
                if not path or len(path) < 2:
                    continue
                key = tuple(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                hop_conds = []
                for k in range(len(path) - 1):
                    hop_conds.extend(conds.get(frozenset((path[k], path[k + 1])), []))
                lines.append('- ' + ' → '.join(path))
                if hop_conds:
                    lines.append('  ' + '；'.join(hop_conds))
                if len(lines) >= max_paths * 2:
                    break
            if len(lines) >= max_paths * 2:
                break
        if not lines:
            return ''
        return '【推荐 JOIN 路径（按主外键图最短业务路径，已排除 mgt_org 中转）】\n' + '\n'.join(lines)

    @staticmethod
    def collapse_series(cols: List[Dict]) -> List[tuple]:
        """把 p1~p96 这类连续编号列折叠。返回有序条目：('col', c) 或 ('range', first, last, count)"""
        groups = {}
        for i, c in enumerate(cols):
            m = re.match(r'^(.*?)(\d+)$', c['name'])
            if m:
                groups.setdefault(m.group(1), []).append((int(m.group(2)), i))
        collapsed_at = {}  # 序列首个成员下标 -> (first, last, count)
        skip = set()       # 序列其余成员下标
        for items in groups.values():
            if len(items) < 4:
                continue
            nums = sorted(n for n, _ in items)
            if nums[-1] - nums[0] != len(nums) - 1:
                continue  # 编号不连续，不折叠
            positions = sorted(i for _, i in items)
            collapsed_at[positions[0]] = (cols[positions[0]], cols[positions[-1]], len(items))
            skip.update(positions[1:])
        entries = []
        for i, c in enumerate(cols):
            if i in collapsed_at:
                first, last, count = collapsed_at[i]
                entries.append(('range', first, last, count))
            elif i not in skip:
                entries.append(('col', c))
        return entries
