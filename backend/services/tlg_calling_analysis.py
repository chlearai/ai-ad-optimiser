"""Aggregate-only calling snapshots; no customer details are accepted or stored."""
from datetime import date, datetime, timezone, timedelta

METRICS = ('leads', 'called', 'not_called', 'contacted', 'not_contactable', 'interested',
           'not_interested', 'junk', 'pending', 'scheduled', 'not_scheduled', 'visited', 'not_visited')
REASONS = {'Denied enquiry', 'Adult classes', 'Age unsuitable', 'Location mismatch', 'No child', 'Unspecified junk'}

def validate_snapshot(data):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Unsupported calling report')
    checked = datetime.fromisoformat(data['checked_at'].replace('Z', '+00:00'))
    if checked.tzinfo is None or checked > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError('Invalid calling report verification time')
    centres = data['centres']
    if not isinstance(centres, list) or not 1 <= len(centres) <= 200:
        raise ValueError('Invalid centre list')
    names = set()
    for centre in centres:
        if not isinstance(centre.get('name'), str) or not centre['name'].strip() or len(centre['name']) > 120 or centre['name'] in names:
            raise ValueError('Invalid or repeated centre')
        names.add(centre['name'])
        if not isinstance(centre.get('daily'), list) or len(centre['daily']) > 5000:
            raise ValueError('Invalid daily calling data')
        days = set()
        for row in centre['daily']:
            day = row['date']
            if day != 'unknown' and date.fromisoformat(day).isoformat() != day:
                raise ValueError('Invalid lead date')
            if day in days:
                raise ValueError('Repeated lead date')
            days.add(day)
            m = row['metrics']
            if set(m) != set(METRICS) or any(type(m[k]) is not int or m[k] < 0 for k in METRICS):
                raise ValueError('Invalid calling counts')
            identities = [('leads', ('called', 'not_called')), ('called', ('contacted', 'not_contactable')),
                          ('contacted', ('interested', 'not_interested', 'junk', 'pending')),
                          ('interested', ('scheduled', 'not_scheduled')), ('scheduled', ('visited', 'not_visited'))]
            if any(m[parent] != sum(m[k] for k in children) for parent, children in identities):
                raise ValueError('Calling stages do not reconcile')
            reasons = row.get('junk_reasons', {})
            if set(reasons) - REASONS or any(type(v) is not int or v < 0 for v in reasons.values()) or sum(reasons.values()) != m['junk']:
                raise ValueError('Junk reasons do not reconcile')
            if type(row.get('invalid_numbers', 0)) is not int or not 0 <= row.get('invalid_numbers', 0) <= m['not_contactable']:
                raise ValueError('Invalid number count exceeds Not Contactable')
    return {'version': 1, 'checked_at': checked.astimezone(timezone.utc).isoformat(),
            'centres': [{'name': c['name'], 'daily': [{'date': d['date'],
                'metrics': {k: d['metrics'][k] for k in METRICS},
                'junk_reasons': dict(d.get('junk_reasons', {})),
                'invalid_numbers': d.get('invalid_numbers', 0)} for d in c['daily']]} for c in centres]}

def aggregate_snapshot(snapshot, start, end):
    rows = []
    totals = dict.fromkeys(METRICS, 0)
    for centre in snapshot['centres']:
        metrics, reasons = dict.fromkeys(METRICS, 0), {}
        invalid = unknown = 0
        for day in centre['daily']:
            if day['date'] != 'unknown' and not str(start) <= day['date'] <= str(end):
                continue
            for key in METRICS:
                metrics[key] += day['metrics'][key]
            for key, count in day.get('junk_reasons', {}).items():
                reasons[key] = reasons.get(key, 0) + count
            invalid += day.get('invalid_numbers', 0)
            if day['date'] == 'unknown':
                unknown += day['metrics']['leads']
        rows.append({'centre': centre['name'], **metrics, 'junk_reasons': reasons, 'invalid_numbers': invalid, 'unknown_dates': unknown})
        for key in METRICS:
            totals[key] += metrics[key]
    checked = datetime.fromisoformat(snapshot['checked_at'].replace('Z', '+00:00'))
    return {'rows': rows, 'total': {'centre': 'Total', **totals}, 'checked_at': snapshot['checked_at'],
            'start': str(start), 'end': str(end), 'stale': datetime.now(timezone.utc) - checked > timedelta(hours=26),
            'schedule': 'Daily at 9am IST', 'source': 'Branch Calling Sheets'}
