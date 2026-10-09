# -*- coding: utf-8 -*-
"""arxiv 检索（通道一：开源预印本，免认证）。
用法: python skills/research-planning/scripts/search_arxiv.py q=all:ontology_AND_all:retrieval-augmented_generation topic=本体RAG max=8
参数约束: 查询式中空格一律用 _ 代替；topic 中文连写不带空格。
"""
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lit_common as C

NS = {'a': 'http://www.w3.org/2005/Atom'}


def parse_args(argv):
    args = {'max': '8'}
    for a in argv:
        if '=' in a:
            k, v = a.split('=', 1)
            args[k] = v
    if 'q' not in args:
        print('缺少 q= 参数，示例: q=all:ontology_AND_all:retrieval-augmented_generation')
        sys.exit(1)
    args['q'] = C.decode_q(args['q'])
    args.setdefault('topic', args['q'][:40])
    return args


def fetch(query, max_results):
    params = urllib.parse.urlencode({
        'search_query': query, 'sortBy': 'submittedDate',
        'sortOrder': 'descending', 'max_results': max_results})
    url = f'http://export.arxiv.org/api/query?{params}'
    req = urllib.request.Request(url, headers={'User-Agent': 'deshu5-research-planning/1.0'})
    last_err = None
    for attempt in range(4):  # arxiv 偶发 502，退避重试
        try:
            with C.urlopen(req, timeout=60) as resp:
                return ET.fromstring(resp.read().decode('utf-8'))
        except Exception as ex:
            last_err = ex
            time.sleep(10 * (attempt + 1))
    raise last_err


def main():
    args = parse_args(sys.argv[1:])
    root = fetch(args['q'], int(args['max']))
    papers = []
    for e in root.findall('a:entry', NS):
        aid = (e.findtext('a:id', '', NS) or '').rsplit('/abs/', 1)[-1]
        papers.append({
            'source': 'arxiv', 'id': aid,
            'title': ' '.join((e.findtext('a:title', '', NS) or '').split()),
            'authors': [a.findtext('a:name', '', NS) for a in e.findall('a:author', NS)][:5],
            'venue': 'arXiv preprint',
            'year': (e.findtext('a:published', '', NS) or '')[:4],
            'date': (e.findtext('a:published', '', NS) or '')[:10],
            'citations': None,
            'url': f'https://arxiv.org/abs/{aid}',
            'abstract': ' '.join((e.findtext('a:summary', '', NS) or '').split())[:600],
        })
    path, n_old, n_all = C.save_results(args['topic'], 'arxiv', papers)
    C.print_report(args['topic'], 'arxiv', papers, path, n_old, n_all)


if __name__ == '__main__':
    main()
