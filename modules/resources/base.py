# -*- coding: utf-8 -*-
"""资源 Provider 抽象基类：统一生成模块可读、Web 可管的资源接口

P1 阶段定位：resources 层是新增面，全部 provider 包装现有对象/表，
不改 engine 任何类的行为；生成链路仍走原对象，P2/P3 再收口到本层。
"""
from abc import ABC, abstractmethod
from datetime import datetime
import json
import re


class ReadOnlyResourceError(ValueError):
    """只读资源被写入时抛出（ValueError 子类，REST 路由据此返回 405）。"""


# entry_schema 支持的字段类型 -> Python 类型
_FIELD_TYPES = {
    'str': str,
    'int': int,
    'bool': (bool, int),
    'list': list,
    'dict': dict,
}


def rows_to_dicts(cursor) -> list:
    """把 cursor 结果统一转为 dict 列表（兼容 sqlite3.Row 与 MySQL 元组游标）。"""
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, tuple(row))) for row in cursor.fetchall()]


def row_to_dict(cursor, row):
    """单行结果转 dict；row 为 None 时返回 None。"""
    if row is None:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, tuple(row)))


def now_str() -> str:
    """当前时间字符串（与 app.py 现有 ingest_time/created_at 写法一致）。"""
    return datetime.now().isoformat()


class ResourceProvider(ABC):
    """基础数据资源 Provider 抽象基类。

    entry_schema 为录入模板（P2 的 Web CRUD/导入也复用它）：
    [{'field': 'question', 'type': 'str', 'required': True}, ...]
    """

    name: str = ''           # 机器名，如 'qa_pair'
    label: str = ''          # 中文显示名，如 '问答对'
    description: str = ''
    entry_schema: list = []

    # ---------- 面向生成模块的语义检索（各 provider 自定义 payload）----------
    @abstractmethod
    def retrieve(self, query: dict) -> dict:
        """语义检索入口，payload 由各 provider 自定（见各自 docstring）。"""
        ...

    # ---------- 通用 CRUD ----------
    @abstractmethod
    def list(self, filters=None, limit=200, offset=0) -> list:
        ...

    @abstractmethod
    def get(self, item_id):
        """按主键取单条，不存在返回 None。"""
        ...

    @abstractmethod
    def create(self, item: dict) -> dict:
        """按 entry_schema 校验后写入，不合格抛 ValueError。"""
        ...

    @abstractmethod
    def update(self, item_id, item: dict) -> dict:
        """部分更新；item_id 不存在抛 LookupError。"""
        ...

    @abstractmethod
    def delete(self, item_id) -> bool:
        ...

    # ---------- 批量导入与校验 ----------
    def import_rows(self, rows: list) -> dict:
        """逐行校验 + 写入，返回 {accepted: n, rejected: [{row, reason}]}。"""
        accepted = 0
        rejected = []
        for row in rows:
            row = self._coerce_row(row)
            errors = self.validate_item(row)
            if errors:
                rejected.append({'row': row, 'reason': '; '.join(errors)})
                continue
            try:
                self.create(row)
                accepted += 1
            except Exception as e:
                rejected.append({'row': row, 'reason': str(e)})
        return {'accepted': accepted, 'rejected': rejected}

    def _coerce_row(self, row):
        """导入行归一：CSV/Excel 渠道的值多为字符串——int 字段数字串转型、
        list 字段尝试 JSON 解析、可选字段空串按未提供处理。"""
        if not isinstance(row, dict):
            return row
        out = dict(row)
        for spec in self.entry_schema:
            field = spec['field']
            if field not in out:
                continue
            value = out[field]
            if isinstance(value, str) and not value.strip():
                if not spec.get('required'):
                    out.pop(field)          # 可选字段空串 = 未提供
                continue
            ftype = spec.get('type', 'str')
            if ftype == 'int' and isinstance(value, str) and re.fullmatch(r'-?\d+', value.strip()):
                out[field] = int(value.strip())
            elif ftype in ('list', 'dict') and isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list if ftype == 'list' else dict):
                        out[field] = parsed
                except Exception:
                    pass
        return out

    def validate_item(self, item: dict, partial: bool = False) -> list:
        """按 entry_schema 校验，返回错误列表（空=通过）。partial=True 仅校验提供的字段。"""
        errors = []
        if not isinstance(item, dict):
            return ['条目必须是 JSON 对象']
        for spec in self.entry_schema:
            field = spec['field']
            value = item.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                if spec.get('required') and not partial:
                    errors.append(f'缺少必填字段: {field}')
                continue
            ftype = spec.get('type', 'str')
            pytype = _FIELD_TYPES.get(ftype)
            if pytype and not isinstance(value, pytype):
                errors.append(f'字段 {field} 类型应为 {ftype}')
            elif ftype == 'int' and isinstance(value, bool):
                errors.append(f'字段 {field} 类型应为 int')
        return errors

    def count(self) -> int:
        """条目总数（供 registry.summary 使用；大表应覆写为 COUNT(*) 查询）。"""
        return len(self.list(limit=10 ** 9))

    # ---------- 导入模板 ----------
    def template(self) -> dict:
        """导入模板（GET /api/resources/<rtype>/template）：字段说明 + 示例行。"""
        return {
            'resource': self.name,
            'label': self.label,
            'description': self.description,
            'entry_schema': self.entry_schema,
            'example_rows': self.template_examples(),
        }

    def template_examples(self) -> list:
        """模板示例行（各 provider 宜覆写为真实样例）；默认按 entry_schema 造占位行。"""
        placeholder = {'str': '示例文本', 'int': 1, 'bool': 1, 'list': [], 'dict': {}}
        row = {}
        for spec in self.entry_schema:
            row[spec['field']] = placeholder.get(spec.get('type', 'str'), '')
        return [row]

    # ---------- 只读资源复用 ----------
    def _raise_readonly(self):
        raise ReadOnlyResourceError(f'该资源本期只读: {self.label}({self.name})')
