# -*- coding: utf-8 -*-
"""浙江统计局月度卡片网关直调（经 mgop h5 网关，免 token，sign=md5 规则）

POST body 格式（页面实测）：{"postData": "<JSON字符串>", ...平铺参数}
内层 JSON: {"data":[{"vtype":"attr","name":"<参数名>","data":<值>}...]}
"""
import hashlib
import json
import subprocess
import time
import urllib.parse

AK = 'udqmn52d+2001941911+elgttz'
GW = 'https://mapi.zjzwfw.gov.cn/h5/mgop'
REFERER = 'https://mapi.zjzwfw.gov.cn/web/mgop/gov-open/zj/2001941911/reserved/index.html'


def _post_data(params, pagination=None):
    """params: {name: value}；pagination: 是否带分页字段。"""
    items = [{'vtype': 'attr', 'name': k, 'data': v} for k, v in params.items()]
    if pagination:
        items = [{'vtype': 'pagination', 'name': 'pagerows', 'data': pagination.get('pagerows', 500)},
                 {'vtype': 'pagination', 'name': 'totalrows', 'data': 0},
                 {'vtype': 'pagination', 'name': 'page', 'data': pagination.get('page', 1)},
                 {'vtype': 'pagination', 'name': 'sortName', 'data': ''},
                 {'vtype': 'pagination', 'name': 'sortOrder', 'data': ''}] + items
    inner = json.dumps({'data': items}, ensure_ascii=False)
    outer = {'postData': inner}
    outer.update({k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                  for k, v in params.items()})
    return outer


def call(api, params=None, pagination=False, timeout=40):
    ts = str(int(time.time() * 1000))
    sign = hashlib.md5(f'token=&ak={AK}&api={api}&ts={ts}&data=null'.encode()).hexdigest()
    qs = urllib.parse.urlencode({'ak': AK, 'api': api, 'ts': ts, 'sign': sign})
    body = json.dumps(_post_data(params or {}, pagination), ensure_ascii=False)
    r = subprocess.run(
        ['curl', '-sL', '--max-time', str(timeout), '-X', 'POST', f'{GW}?{qs}',
         '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0',
         '-H', f'Referer: {REFERER}',
         '-H', 'Content-Type: application/json',
         '--data-raw', body],
        capture_output=True, timeout=timeout + 10)
    txt = r.stdout.decode('utf-8', 'ignore')
    return json.loads(txt) if txt.strip() else {}


def call_grid(api, params=None, timeout=40):
    """解包 gridpanel：返回 (rows, totalrows)。"""
    r = call(api, params, pagination=True, timeout=timeout)
    for item in (r.get('data') or {}).get('data') or []:
        if item.get('vtype') == 'gridpanel':
            d = item.get('data') or {}
            return d.get('rows') or [], d.get('totalrows') or 0
    return [], 0


def call_attr(api, params=None, timeout=40):
    """解包 attr：返回 {name: data}。"""
    r = call(api, params, timeout=timeout)
    return {i.get('name'): i.get('data') for i in (r.get('data') or {}).get('data') or []}


if __name__ == '__main__':
    import sys
    api = sys.argv[1]
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(json.dumps(call(api, params), ensure_ascii=False, indent=1)[:4000])
