# -*- coding: utf-8 -*-
"""只读打印项目内文本文件内容（供技能对话 agent 读取项目资料，如 kjgl研究谋划/kjgl_base.json）。
用法: python tools/show_file.py <项目相对路径>
安全约束：仅限项目根目录内、文本扩展名白名单、敏感文件黑名单、输出截断。
"""
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MAX_CHARS = 8000
_TEXT_EXT = {'.md', '.txt', '.json', '.csv', '.yaml', '.yml', '.xml', '.html', '.log'}
_SECRET_NAMES = {'.env', 'llm_settings.json', 'db_profile.json'}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print('用法: python tools/show_file.py <项目相对路径>')
        sys.exit(1)
    rel = args[0].replace('\\', '/')
    name = os.path.basename(rel)
    if '..' in rel:
        print('拒绝：不允许 .. 路径穿越')
        sys.exit(1)
    if name in _SECRET_NAMES or os.path.splitext(name)[1] in ('.pem', '.key'):
        print(f'拒绝：敏感文件不可读取: {name}')
        sys.exit(1)
    if os.path.splitext(name)[1].lower() not in _TEXT_EXT:
        print(f'拒绝：仅支持文本文件 {sorted(_TEXT_EXT)}')
        sys.exit(1)
    path = os.path.abspath(os.path.join(PROJECT_ROOT, rel))
    if path != PROJECT_ROOT and not path.startswith(PROJECT_ROOT + os.sep):
        print('拒绝：路径必须位于项目根目录内')
        sys.exit(1)
    if not os.path.isfile(path):
        print(f'文件不存在: {rel}')
        sys.exit(2)
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f'\n…（文件过长已截断，共 {len(text)} 字符）'
    print(text)


if __name__ == '__main__':
    main()
