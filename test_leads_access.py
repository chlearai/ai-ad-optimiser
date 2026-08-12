import urllib.request, urllib.parse, json

with open('.env') as f:
    for line in f:
        if line.startswith('META_ACCESS_TOKEN='):
            token = line.strip().split('=', 1)[1]
            break

endpoints = [
    # Try business leads
    f"https://graph.facebook.com/v18.0/2601679363374777/leads?fields=id,created_time,field_data&limit=5&access_token={token}",
    # Try ad account leads
    f"https://graph.facebook.com/v18.0/act_577546498668650/leads?fields=id,created_time,field_data&limit=5&access_token={token}",
    # Try ad leads
    f"https://graph.facebook.com/v18.0/120249207991670382/leads?fields=id,created_time,field_data&limit=5&access_token={token}",
]

for url in endpoints:
    print("\n===", url.split('?')[0], "===")
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            print(json.dumps(data, indent=2)[:2000])
    except Exception as e:
        print('ERROR:', e)
        try:
            print(e.read().decode()[:500])
        except:
            pass
