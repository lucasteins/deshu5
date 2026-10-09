# -*- coding: utf-8 -*-
"""IEEE Xplore 检索（通道二：IEEE 元数据，免认证；全文需 CARSI 机构访问，见 SKILL.md）。
用法: python skills/research-planning/scripts/search_ieee.py q=carbon-aware_data_center_scheduling topic=算电碳协同 max=10
机制: 先 GET 检索页取 Cookie，再 POST /rest/search 拿 JSON 元数据（标题/作者/出处/年/被引/摘要/DOI）。
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import http.cookiejar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lit_common as C

BASE = 'https://ieeexplore.ieee.org'


def parse_args(argv):
    args = {'max': '10'}
    for a in argv:
        if '=' in a:
            k, v = a.split('=', 1)
            args[k] = v
    if 'q' not in args:
        print('缺少 q= 参数，示例: q=carbon-aware_data_center_scheduling')
        sys.exit(1)
    args['q'] = C.decode_q(args['q'])
    args.setdefault('topic', args['q'][:40])
    return args


def clean_abstract(s):
    # IEEE 高亮标记形如 [::data::]，去掉标记保留文本
    return re.sub(r'\[::|\s*::\]', '', s or '').replace('::]', '').strip()


def search(query, max_n):
    cj = http.cookiejar.CookieJar()
    opener = C.opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [('User-Agent', C.UA)]
    # 先访问检索页建立会话 Cookie（否则 rest/search 返回 418）
    opener.open(BASE + '/search/searchresult.jsp?queryText=' +
                urllib.parse.quote(query), timeout=30).read(1)
    papers, page = [], 1
    while len(papers) < max_n and page <= 3:
        body = json.dumps({
            'queryText': query, 'highlight': True, 'returnFacets': ['ALL'],
            'returnType': 'SEARCH', 'pageSize': min(25, max_n), 'pageNumber': page,
        }).encode('utf-8')
        req = urllib.request.Request(BASE + '/rest/search', data=body, headers={
            'Content-Type': 'application/json', 'Accept': 'application/json',
            'Referer': BASE + '/search/searchresult.jsp?queryText=' + urllib.parse.quote(query),
            'User-Agent': C.UA, 'X-Requested-With': 'XMLHttpRequest',
        })
        with opener.open(req, timeout=60) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        records = data.get('records') or []
        if not records:
            break
        for r in records:
            papers.append({
                'source': 'ieee', 'id': str(r.get('articleNumber', '')),
                'title': r.get('articleTitle') or r.get('title') or '',
                'authors': [a.get('preferredName', '') for a in r.get('authors', [])][:5],
                'venue': clean_abstract(r.get('displayPublicationTitle') or r.get('publicationTitle') or ''),
                'year': str(r.get('publicationYear') or ''),
                'date': r.get('publicationDate') or str(r.get('publicationYear') or ''),
                'citations': r.get('citationCount'),
                'url': f"{BASE}/document/{r.get('articleNumber','')}",
                'doi': r.get('doi') or '',
                'abstract': clean_abstract(r.get('abstract'))[:600],
            })
        page += 1
    return papers[:max_n]


def main():
    args = parse_args(sys.argv[1:])
    papers = search(args['q'], int(args['max']))
    if not papers:
        print('未取到结果（可能触发站点风控，可稍后重试或改用浏览器通道）')
        sys.exit(2)
    path, n_old, n_all = C.save_results(args['topic'], 'ieee', papers)
    C.print_report(args['topic'], 'ieee', papers, path, n_old, n_all)


if __name__ == '__main__':
    main()
