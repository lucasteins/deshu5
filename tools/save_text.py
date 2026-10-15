# -*- coding: utf-8 -*-
"""把短文本暂存到 kjgl研究谋划/inbox/（供 compose_doc type=review 的 target= 读入等中间资料场景）。
用法: python tools/save_text.py <文件名.md> <内容>
约束：文件名仅 basename（.md/.txt），内容 ≤20000 字符，只允许写入 inbox 目录。
"""
import os
import sys

INBOX = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'kjgl研究谋划', 'inbox'))
MAX_CHARS = 20000


def main():
    if len(sys.argv) < 3:
        print('用法: python tools/save_text.py <文件名.md> <内容>')
        sys.exit(1)
    name = sys.argv[1].replace('\\', '/')
    if '/' in name or '..' in name:
        print('拒绝：文件名不能含路径')
        sys.exit(1)
    if os.path.splitext(name)[1].lower() not in ('.md', '.txt'):
        print('拒绝：仅支持 .md/.txt')
        sys.exit(1)
    content = ' '.join(sys.argv[2:])
    if len(content) > MAX_CHARS:
        print(f'拒绝：内容超过 {MAX_CHARS} 字符上限（当前 {len(content)}）')
        sys.exit(1)
    os.makedirs(INBOX, exist_ok=True)
    path = os.path.join(INBOX, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'已保存: kjgl研究谋划/inbox/{name}（{len(content)} 字符）')


if __name__ == '__main__':
    main()
