# -*- coding: utf-8 -*-
"""skills/ 目录扫描与 SKILL.md 极简解析（项目无 PyYAML，frontmatter 手写解析）。

- list_skills()      扫描 skills/*/SKILL.md，返回 [{name, title, description, when_to_use}]
- load_skill_body()  取去 frontmatter 的正文（截断 16000 字符，做对话 prompt 的 Skill 文档段）
- read_examples() / write_examples()  示例命令存取（skills/chat_examples.json，可手工维护）

frontmatter 支持两种写法：`key: value` 单行；`key: >`（折叠，换行变空格）/
`key: |`（字面，保留换行）多行——后续缩进行都属于该值
（deshu5-data-simulation 的 description 即折叠多行写法）。
"""
import json
import os
import re
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[3] / 'skills'
EXAMPLES_PATH = SKILLS_DIR / 'chat_examples.json'
BODY_MAX_CHARS = 16000

_FM_KEY = re.compile(r'^([A-Za-z0-9_\-]+):\s*(.*)$')


def _parse_frontmatter(text: str) -> tuple:
    """解析 YAML frontmatter。返回 (meta: dict, body: str)；无 frontmatter 时 meta 为空。"""
    if not text.startswith('---'):
        return {}, text
    lines = text.split('\n')
    if lines[0].strip() != '---':
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end = i
            break
    if end is None:
        return {}, text

    meta = {}
    key = None      # 处于多行值状态的 key
    style = None    # '>' 折叠 / '|' 字面
    buf = []

    def _flush():
        nonlocal key, style, buf
        if key is not None:
            if style == '|':
                meta[key] = '\n'.join(buf).strip('\n')
            else:  # '>' 折叠：换行折叠为空格
                meta[key] = ' '.join(l.strip() for l in buf if l.strip()).strip()
        key, style, buf = None, None, []

    for line in lines[1:end]:
        m = _FM_KEY.match(line)
        if m and not line.startswith((' ', '\t')):
            _flush()
            val = m.group(2).strip()
            if val in ('>', '|'):
                key, style, buf = m.group(1), val, []
            else:
                meta[m.group(1)] = val
        elif key is not None:
            buf.append(line)
    _flush()
    return meta, '\n'.join(lines[end + 1:])


def _scan() -> list:
    """扫描 skills/*/SKILL.md，返回 [(info, path)]；解析失败的目录跳过。"""
    items = []
    if not SKILLS_DIR.is_dir():
        return items
    for md in sorted(SKILLS_DIR.glob('*/SKILL.md')):
        try:
            meta, _ = _parse_frontmatter(md.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"[WARN] SKILL.md 解析失败 {md}: {e}", flush=True)
            continue
        name = (meta.get('name') or md.parent.name).strip()
        items.append(({
            'name': name,
            'title': (meta.get('title') or name).strip(),
            'description': (meta.get('description') or '').strip(),
            'when_to_use': (meta.get('whenToUse') or meta.get('when_to_use') or '').strip(),
        }, md))
    return items


def list_skills() -> list:
    """全部技能摘要：[{name, title, description, when_to_use}]（缺 title 用 name）。"""
    return [info for info, _ in _scan()]


def skill_exists(name: str) -> bool:
    return (name or '').strip() in {s['name'] for s in list_skills()}


def load_skill_body(name: str) -> str:
    """取指定技能去 frontmatter 的正文（截断 16000 字符）。name 必须在扫描结果内（防目录穿越）。"""
    name = (name or '').strip()
    for info, md in _scan():
        if info['name'] == name:
            _, body = _parse_frontmatter(md.read_text(encoding='utf-8'))
            return body.strip()[:BODY_MAX_CHARS]
    raise ValueError(f'未知技能: {name}')


def _load_examples_file() -> dict:
    """读示例文件，损坏/缺失误差容错返回 {}。"""
    try:
        data = json.loads(EXAMPLES_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_examples(name: str) -> list:
    """该技能的示例命令列表；文件缺失/损坏返回 []。"""
    items = _load_examples_file().get(name) or []
    return [str(x).strip() for x in items if str(x).strip()]


def write_examples(name: str, items: list):
    """写回该技能的示例命令（整体读改写，临时文件原子替换）。"""
    data = _load_examples_file()
    data[name] = [str(x).strip() for x in items if str(x).strip()]
    tmp = EXAMPLES_PATH.with_name(EXAMPLES_PATH.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, EXAMPLES_PATH)
