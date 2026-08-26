# -*- coding: utf-8 -*-
"""直接 Schema LLM 生成器：把表/字段注释、关联路径直接喂给大模型，让其生成 SQL。

与 SQLGenerator 的区别：
- 不使用意图解析器、模板匹配、RAG 历史 QA 对。
- 仅基于 Schema 语义 + 用户问题直接生成 SQL。
"""
import json
import os
import re
import sys
import time
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import requests
from core.database import DatabaseManager
from core.schema_kb import SchemaKnowledgeBase
from core.rag_retriever import RAGRetriever


class DirectSchemaSQLGenerator:
    """直接基于 Schema 知识让 LLM 生成 SQL"""

    def __init__(self):
        self.db = DatabaseManager()
        self.schema_kb = SchemaKnowledgeBase()
        self.rag_retriever = RAGRetriever(top_k=config.RAG_TOP_K)
        self.api_url = config.KIMI_API_URL
        self.api_key = config.KIMI_API_KEY
        self.model = config.KIMI_MODEL
        self._fast_temp = 0.6
        # LLM token 用量累加器（benchmark 效率统计用，纯附加；每次 generate 重置）
        self._usage_acc = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'llm_calls': 0}

    def _accumulate_usage(self, usage: Optional[Dict]):
        """累加一次 LLM HTTP 响应的 token 用量（无 usage 字段时仅计调用数）"""
        self._usage_acc['llm_calls'] += 1
        usage = usage or {}
        self._usage_acc['prompt_tokens'] += int(usage.get('prompt_tokens') or 0)
        self._usage_acc['completion_tokens'] += int(usage.get('completion_tokens') or 0)
        self._usage_acc['total_tokens'] += int(usage.get('total_tokens') or 0)

    def generate(self, user_question: str, **kwargs) -> Dict:
        """公有入口：包装 _generate_impl，在返回 dict 中附加本次 LLM token 用量（usage 字段）"""
        self._usage_acc = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'llm_calls': 0}
        result = self._generate_impl(user_question, **kwargs)
        if isinstance(result, dict):
            result['usage'] = dict(self._usage_acc)
        return result

    def _generate_impl(self, user_question: str, **kwargs) -> Dict:
        """主入口，返回与 SQLGenerator.generate 兼容的字典"""
        # 1. 检索相关 Schema（与 SQLGenerator 使用相同的 schema 检索口径）
        schema_docs = self.schema_kb.retrieve(
            user_question=user_question,
            top_k_tables=5,
            top_k_columns=15,
            top_k_relationships=5,
            top_k_patterns=0
        )
        schema_context = self._build_schema_context(schema_docs)

        # 2. 构造 Prompt
        prompt = self._build_prompt(user_question, schema_context)

        # 3. 调用 LLM
        raw_content = ""
        try:
            result = self._call_llm(prompt, user_question=user_question)
            raw_content = result['content']
        except Exception as e:
            return {
                'sql': '',
                'success': False,
                'error': f'API 调用失败: {e}',
                'tables_involved': [],
                'generation_mode': 'direct_schema_llm'
            }

        # 4. 提取并校验 SQL
        sql = self._extract_sql(raw_content)
        valid = self._validate_sql(sql)
        tables = self._extract_tables_from_sql(sql)

        if valid:
            return {
                'sql': sql,
                'success': True,
                'tables_involved': tables,
                'generation_mode': 'direct_schema_llm',
                'raw_response': raw_content,
                'prompt': prompt
            }
        return {
            'sql': sql,
            'success': False,
            'error': '生成的 SQL 未通过执行校验',
            'tables_involved': tables,
            'generation_mode': 'direct_schema_llm',
            'raw_response': raw_content,
            'prompt': prompt
        }

    def _build_schema_context(self, schema_docs: Dict) -> str:
        """把 Schema 知识库输出转成 prompt 文本"""
        lines = []

        # 表清单
        if schema_docs.get('tables'):
            lines.append("【相关表】")
            for t in schema_docs['tables']:
                lines.append(f"- {t['table_name']}: {t.get('table_comment', '')}")

        # 关键字段
        if schema_docs.get('columns'):
            lines.append("\n【关键字段】")
            shown = 0
            for c in schema_docs['columns']:
                if shown >= 20:
                    break
                comment = c.get('column_comment', '')
                lines.append(f"- {c['table_name']}.{c['column_name']}: {comment}")
                shown += 1

        # 关联路径
        if schema_docs.get('relationships'):
            lines.append("\n【推荐关联路径】")
            for r in schema_docs['relationships']:
                lines.append(f"- {r['title']}")
                for jc in r.get('join_conditions', []):
                    lines.append(f"  {jc}")

        return '\n'.join(lines)

    def _build_prompt(self, user_question: str, schema_context: str) -> str:
        date_hint = "日期过滤请使用 DATE_FORMAT(date_col, '%Y-%m') = 'YYYY-MM' 或 BETWEEN 语法。"
        return f"""你是电力营销数据仓库的 SQL 专家。请严格依据下方数据库 Schema 生成一条可执行的 MySQL SELECT 语句。

【业务说明】
本库为电力营销 4.0 共享层，核心数据链：
客户(dim_cst_cust) → 用电客户(dim_cst_elec_cons_cust) → 安装点(dim_cst_inst_elec_cons) → 计量点运行(dwd_cst_meter_run) → 日用电量(dwd_cst_meter_energy_day_h_xz)

【Schema 上下文】
{schema_context}

【用户问题】
{user_question}

【生成规则】
1. 只输出一条 SELECT 语句，不要 Markdown 代码块，不要解释性文字。
2. 必须使用 Schema 中真实存在的表名和字段名，禁止编造。
3. JOIN 必须基于 Schema 中明确列出的关联字段或推荐关联路径，不要捏造 JOIN 条件。
4. {date_hint}
5. 聚合函数必须有 GROUP BY，且分组字段必须出现在 SELECT 中。
6. TOP N 用 ORDER BY ... DESC LIMIT N。
7. SELECT 字段使用英文代码名，不要加中文别名。
8. 根据问题中的具体条件值生成 WHERE，不要复制示例中的条件值。

【输出】一行纯代码 SQL："""

    def _call_llm(self, prompt: str, user_question: str = None) -> Dict:
        from core.llm_config import current as _llm_current, build_request as _llm_build
        cfg = _llm_current()
        if not cfg.get('api_key'):
            raise ValueError(f"{cfg['provider']} API Key 未配置（请在前端设置页配置）")
        print(f"[DIRECT] prompt_length={len(prompt)}", flush=True)
        last_error = None

        # 快速模式（thinking=False）；Provider 参数差异统一由 llm_config.build_request 封装
        url, headers, payload = _llm_build(
            [{'role': 'user', 'content': prompt}], max_tokens=8000, thinking=False)
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            self._accumulate_usage(data.get('usage'))
            content = data['choices'][0]['message']['content']
            print(f"[DIRECT] fast content_len={len(content) if content else 0}", flush=True)
            if content and content.strip():
                return {'content': content, 'usage': data.get('usage', {})}
        except Exception as e:
            print(f"[DIRECT] fast error={e}", flush=True)
            last_error = e

        # 兜底：thinking 模式
        for attempt in range(2):
            url, headers, payload = _llm_build(
                [{'role': 'user', 'content': prompt}], max_tokens=16000, thinking=True)
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=120)
                response.raise_for_status()
                data = response.json()
                self._accumulate_usage(data.get('usage'))
                content = data['choices'][0]['message']['content']
                print(f"[DIRECT] reasoning attempt={attempt} content_len={len(content) if content else 0}", flush=True)
                if content and content.strip():
                    return {'content': content, 'usage': data.get('usage', {})}
                short_prompt = (
                    f"你是电力营销 SQL 专家。根据问题生成一行可执行 SELECT。\n"
                    f"问题：{user_question or ''}\n只输出一行 SELECT。"
                )
                prompt = short_prompt
            except Exception as e:
                last_error = e
                if attempt < 1:
                    time.sleep(min(2 ** attempt, 8))
        raise ValueError(f"LLM 调用失败或返回内容为空: {last_error}")

    def _extract_sql(self, content: str) -> str:
        if not content:
            return ''
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        content = re.sub(r'```sql\n?', '', content, flags=re.IGNORECASE)
        content = re.sub(r'```\n?', '', content)
        lines = content.split('\n')
        sql_lines = []
        in_sql = False
        allowed_prefixes = ['SELECT', 'WITH']
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('--') or stripped.startswith('//'):
                continue
            if any(stripped.upper().startswith(p) for p in allowed_prefixes):
                in_sql = True
            if in_sql:
                sql_lines.append(stripped)
        if sql_lines:
            sql = ' '.join(sql_lines)
            sql = re.sub(r'\s+', ' ', sql)
            return sql.strip()
        return ''

    def _validate_sql(self, sql: str) -> bool:
        if not sql:
            return False
        sql_upper = sql.strip().upper()
        if not any(sql_upper.startswith(p) for p in ['SELECT', 'WITH']):
            return False
        forbidden = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE']
        for kw in forbidden:
            if kw in sql_upper:
                return False
        test_sql = sql.rstrip(';').strip()
        test_sql = re.sub(r'\s+LIMIT\s+\d+\s*$', '', test_sql, flags=re.IGNORECASE)
        test_sql += ' LIMIT 1'
        try:
            with self.db.connect_business() as conn:
                conn.execute(test_sql)
            return True
        except Exception:
            return False

    def _extract_tables_from_sql(self, sql: str) -> List[str]:
        tables = set()
        for match in re.finditer(r'FROM\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        for match in re.finditer(r'JOIN\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        return sorted(tables)


if __name__ == '__main__':
    gen = DirectSchemaSQLGenerator()
    test_questions = [
        '统计各供电单位2026年4月的应收电费总额',
        '查询2026年4月用电量TOP10的计量点',
        '查询用电客户中的高压用户列表',
    ]
    for q in test_questions:
        print(f"\nQ: {q}")
        res = gen.generate(q)
        print('mode:', res.get('generation_mode'))
        print('success:', res.get('success'))
        print('sql:', res.get('sql'))
