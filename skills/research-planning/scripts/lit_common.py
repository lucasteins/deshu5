# -*- coding: utf-8 -*-
"""research-planning 技能公共模块：检索结果存储与打印规范。

结果统一落 skills/research-planning/results/<topic>.json，paper 字段规范：
  source/id/title/authors(list)/venue/year/date/citations/url/abstract
stdout 打印规范：先逐条明细，最后打印 [SUMMARY] 段（对话只保留输出尾部，关键信息放尾部）。
"""
import json
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
RESULTS_DIR = os.path.join(SKILL_DIR, 'results')

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'

_NO_PROXY_OPENER = None


def opener(*extra_handlers):
    """绕过本机系统代理的 urllib opener（本机 127.0.0.1:7890 代理不稳会致 SSL EOF）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), *extra_handlers)


def urlopen(req, timeout=60):
    return opener().open(req, timeout=timeout)


def decode_q(s):
    """命令行白名单不允许空格，查询词中的 _ 还原为空格。"""
    return s.replace('_', ' ').strip()


def slugify(topic):
    s = re.sub(r'[^\w一-鿿-]+', '_', topic.strip())
    return s.strip('_')[:60] or 'topic'


def save_results(topic, source, papers):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, slugify(topic) + '.json')
    old = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            old = json.load(f).get('papers', [])
    seen = {(p.get('source'), p.get('id')) for p in old}
    merged = old + [p for p in papers if (p.get('source'), p.get('id')) not in seen]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'topic': topic, 'papers': merged}, f, ensure_ascii=False, indent=1)
    return path, len(old), len(merged)


def fmt_paper(p):
    au = ', '.join((p.get('authors') or [])[:3])
    cit = p.get('citations')
    cit_s = f'，被引{cit}' if cit else ''
    return (f"- [{p.get('date') or p.get('year') or '?'}] {p.get('title','')}\n"
            f"  {au} | {p.get('venue','')} ({p.get('year','?')}){cit_s} | {p.get('url') or p.get('id','')}\n"
            f"  摘要: {(p.get('abstract') or '')[:220]}")


def print_report(topic, source, papers, path, n_old, n_all):
    print(f'## {source} 检索结果：{topic}（新增 {len(papers)} 条）\n')
    for p in papers:
        print(fmt_paper(p))
    print('\n[SUMMARY]')
    print(f'topic={topic} source={source} 新增={len(papers)} 原库={n_old} 合并后={n_all}')
    print(f'结果文件: {os.path.relpath(path, os.getcwd())}')
    if papers:
        latest = max((p.get('date') or p.get('year') or '') for p in papers)
        print(f'最新文献日期: {latest}')
