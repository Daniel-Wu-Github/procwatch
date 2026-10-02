"""Exercise the local read-only API with curl; save token-free QA results."""
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

url = urlsplit(sys.argv[1])
base = f'{url.scheme}://{url.netloc}'
token = parse_qs(url.query)['token'][0]
results = {}

page = subprocess.check_output(['curl', '--fail', '--silent', '--show-error', '--max-time', '15',
                                sys.argv[1]], text=True)
config_match = re.search(r'const config=(\{[^\n]*\});', page)
if not config_match or json.loads(config_match[1]).get('readOnly') is not True:
    raise SystemExit('Refusing checks: the page does not confirm read-only mode')


def request(name, path, body=None, authenticated=True):
    headers = []
    if body is None:
        if authenticated:
            path += ('&' if '?' in path else '?') + urlencode({'token': token})
    else:
        headers = ['-H', 'Content-Type: application/json', '-H', f'Origin: {base}',
                   '-H', f'X-Procwatch-Token: {token}', '-d', json.dumps(body)]
    output = subprocess.check_output(['curl', '--silent', '--show-error', '--max-time', '15',
                                      '-w', '\n%{http_code}', *headers, base+path], text=True)
    raw, status = output.rsplit('\n', 1)
    data = json.loads(raw)
    results[name] = {'status': int(status), 'body': data}
    return int(status), data


for metric in ('mem', 'cpu', 'gpu'):
    status, data = request(metric, '/api/snapshot?metric='+metric)
    assert status == 200
    if metric == 'mem':
        memory = data
    snapshot = data['snapshot']
    print(json.dumps({'check': metric, 'status': status, 'total': snapshot['total'], 'used': snapshot['used'],
                      'slices': len(snapshot['slices']), 'note': snapshot['note']}))
group = max(memory['snapshot']['groups'], key=lambda name: memory['snapshot']['groups'][name]['value'])
status, data = request('group', '/api/breakdown?'+urlencode({'metric':'mem', 'group':group}))
assert status == 200
print(json.dumps({'check':'group', 'status':status, 'group':group, 'total':data['total'], 'slices':data['slices']}))
parent = next(s['pid'] for s in data['slices'] if s.get('pid') is not None)
status, data = request('process', '/api/breakdown?'+urlencode({'metric':'mem', 'group':group, 'parent':parent}))
assert status == 200
print(json.dumps({'check':'process', 'status':status, 'parent':parent, 'total':data['total'], 'slices':data['slices']}))
pid = next(p['pid'] for p in memory['procs'] if not p['protected'])
status, data = request('preview', '/api/preview', {'pids':[pid]})
assert status == 200
print(json.dumps({'check':'preview', 'status':status, 'body':data}))
status, data = request('unauthorized', '/api/snapshot', authenticated=False)
assert status == 403
print(json.dumps({'check':'missing token', 'status':status, 'body':data}))
for action in ('stop', 'force'):
    status, data = request(action, '/api/'+action, {'pids':[pid]})
    assert status == 403 and 'read-only' in data['error']
    print(json.dumps({'check':action, 'status':status, 'body':data}))
Path('artifacts/read-only-api.json').write_text(json.dumps(results, indent=2))
print('PASS: all read-only API checks; full responses in artifacts/read-only-api.json')
