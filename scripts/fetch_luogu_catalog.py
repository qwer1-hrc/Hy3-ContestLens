"""Fetch public Luogu metadata with normal cookies, caching and a polite delay."""
from pathlib import Path
import http.cookiejar
import json
import re
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'data' / 'luogu_cache'
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def fetch(path, cache_name):
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / (cache_name + '.json')
    if target.exists():
        return json.loads(target.read_text(encoding='utf-8'))
    url = 'https://www.luogu.com.cn' + path
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with opener.open(request, timeout=30) as response:
        html = response.read().decode('utf-8')
    match = re.search(r'<script id="lentille-context" type="application/json">(.*?)</script>', html, re.S)
    if not match:
        raise RuntimeError('Missing public metadata: ' + url)
    data = json.loads(match.group(1))['data']
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    time.sleep(1)
    return data

if __name__ == '__main__':
    for contest, years in [('NOIP', [2014,2015,2016,2018,2020,2021,2022,2025]), ('CSP-S', [2019,2020,2021,2024,2025])]:
        for year in years:
            key = f'{contest} {year}'
            data = fetch('/problem/list?keyword=' + urllib.parse.quote(key), f'list_{contest}_{year}')
            print(key, json.dumps(data, ensure_ascii=False)[:300], flush=True)
    from contest_inventory import SESSIONS
    for session in SESSIONS:
        for pair in session[-1].split():
            pid = pair.split(':')[1]
            problem = fetch('/problem/' + pid, pid)['problem']
            print(pid, problem['name'], problem['difficulty'], flush=True)
    config_request = urllib.request.Request('https://www.luogu.com.cn/_lfe/config', headers={'User-Agent':'Mozilla/5.0'})
    with opener.open(config_request, timeout=30) as response:
        config = json.load(response)
    from datetime import datetime, timezone
    (CACHE/'difficulty_config.json').write_text(json.dumps({'source':'https://www.luogu.com.cn/_lfe/config','checked_at':datetime.now(timezone.utc).isoformat(),'labels':config['ProblemDifficulty']},ensure_ascii=False,indent=2),encoding='utf-8')
