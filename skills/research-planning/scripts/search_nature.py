# -*- coding: utf-8 -*-
"""Nature 检索（通道三：Nature 系期刊元数据，免认证；全文需 CARSI 机构访问，见 SKILL.md）。
用法: python skills/research-planning/scripts/search_nature.py q=AI_data_centre_carbon topic=算电碳协同 max=10
机制: GET nature.com/search 按日期排序，解析结果列表（标题/作者/期刊/日期/类型/链接）。
"""
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lit_common as C


def parse_args(argv):
    args = {'max': '10', 'order': 'relevance'}
    for a in argv:
        if '=' in a:
            k, v = a.split('=', 1)
            args[k] = v
    if 'q' not in args:
        print('缺少 q= 参数，示例: q=AI_data_centre_carbon order=date')
        sys.exit(1)
    args['q'] = C.decode_q(args['q'])
    args['order'] = 'date_desc' if args['order'] == 'date' else 'relevance'
    args.setdefault('topic', args['q'][:40])
    return args


def strip_tags(s):
    return re.sub(r'<[^>]+>|\s+', ' ', s or '').strip()


def search(query, max_n, order='relevance'):
    papers, page = [], 1
    while len(papers) < max_n and page <= 3:
        url = ('https://www.nature.com/search?q=' + urllib.parse.quote(query) +
               f'&order={order}&page={page}')
        req = urllib.request.Request(url, headers={'User-Agent': C.UA})
        with C.urlopen(req, timeout=60) as resp:
            html = resp.read().decode('utf-8', errors='replace')
        parts = re.split(r'<li[^>]*class="[^"]*app-article-list-row__item[^"]*"[^>]*>', html)
        if len(parts) <= 1:
            break
        for seg in parts[1:]:
            m = re.search(r'itemprop="name headline"[^>]*>(.*?)</a>', seg, re.S)
            title = strip_tags(m.group(1)) if m else ''
            m = re.search(r'<a[^>]*href="([^"]+)"[^>]*data-track-action="view article"', seg)
            link = m.group(1) if m else ''
            m = re.search(r'<time[^>]*datetime="([^"]+)"', seg)
            date = m.group(1) if m else ''
            m = re.search(r'data-test="author-list"[^>]*>(.*?)</ul>', seg, re.S)
            authors = [a.strip() for a in re.split(r',| {2,}', strip_tags(m.group(1))) if a.strip()][:5] if m else []
            m = re.search(r'class="c-meta__type"[^>]*>([^<]+)', seg)
            atype = m.group(1).strip() if m else ''
            jm = re.search(r'data-test="journal-title-and-link"[^>]*>(.*?)</div>', seg, re.S) or \
                 re.search(r'class="u-text-uppercase[^"]*"[^>]*>(.*?)</', seg, re.S)
            venue = strip_tags(jm.group(1)) if jm else 'Nature Portfolio'
            if title:
                papers.append({
                    'source': 'nature', 'id': link.rsplit('/', 1)[-1] or link,
                    'title': title, 'authors': authors, 'venue': venue,
                    'year': date[:4], 'date': date, 'citations': None,
                    'url': 'https://www.nature.com' + link if link.startswith('/') else link,
                    'abstract': f'[{atype}]' if atype else '',
                })
        page += 1
    return papers[:max_n]


def main():
    args = parse_args(sys.argv[1:])
    papers = search(args['q'], int(args['max']), args['order'])
    if not papers:
        print('未取到结果（可能触发站点风控，可稍后重试）')
        sys.exit(2)
    path, n_old, n_all = C.save_results(args['topic'], 'nature', papers)
    C.print_report(args['topic'], 'nature', papers, path, n_old, n_all)


if __name__ == '__main__':
    main()
