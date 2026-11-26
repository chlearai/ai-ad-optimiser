# Mini Robos reporting

Create a separate reporter key from the agent’s **Keys** panel. Store it in the reporter’s existing secret store under `ROBO_KEY`. Set `ROBO_BASE_URL` to the ops app’s HTTPS origin. Mini Robos displays the raw key once and keeps its SHA-256 hash. Use separate keys for separate reporters and revoke a key when it is retired.

Send a `finish` event after each slot with `status` set to `succeeded` or `failed`. Include the expected slot as an ISO 8601 timestamp with a timezone for fixed-time schedules. Interval agents can omit it. Send the actual output count where available. Include a short, sanitized failure reason or outcome without credentials, recipients’ details, or raw provider responses. Reports are limited to 8 KB and 120 requests per key per hour. The server keeps at most 2,000 characters each for the message and error. Fixed-slot finish reports with no run ID can be retried using the same slot.

For jobs where duration matters, send a `start` event first and return its `run_id` in the `finish` event. Re-use the ID for retries. A key can fetch published settings with `GET /api/mini-robos/agents/{slug}/config` after an editor enables **Live sync**. The control room exposes reported outcomes. A scheduler job that handles and suppresses its own errors can still appear successful at the scheduler level.

## curl

Set `ROBO_SLOT` to the expected current slot in UTC. Keep shell tracing off.

```sh
printf 'X-Robo-Key: %s\n' "$ROBO_KEY" |
  curl -fsS --max-time 5 -H @- -H 'Content-Type: application/json' \
    -d "{\"event\":\"finish\",\"slot\":\"$ROBO_SLOT\",\"status\":\"succeeded\",\"output_count\":1}" \
    "$ROBO_BASE_URL/api/mini-robos/heartbeat"
```

## Google Apps Script

Set `ROBO_KEY` and `ROBO_BASE_URL` in Script Properties. Pass the scheduled ISO 8601 slot, result and actual output count.

```javascript
function reportRobo(slot, status, count) {
  const p = PropertiesService.getScriptProperties(), key = p.getProperty('ROBO_KEY'), base = p.getProperty('ROBO_BASE_URL');
  if (!base || !key) return;
  const payload = JSON.stringify({event: 'finish', slot, status, output_count: count});
  try { UrlFetchApp.fetch(base + '/api/mini-robos/heartbeat', {method: 'post', contentType: 'application/json', headers: {'X-Robo-Key': key}, payload, muteHttpExceptions: true, followRedirects: false}); } catch (_) {}
}
```

## WhatsApp cloud runner (Python)

Call from the long-running runner after recording the send outcome. Keep the send result independent of telemetry. This helper sends from a daemon thread with a five-second request budget.

```python
import os
from threading import Thread

import requests


def report_robo(slot, sent, count):
    base, key = os.getenv("ROBO_BASE_URL"), os.getenv("ROBO_KEY")
    if not base or not key:
        return

    def post():
        try:
            requests.post(
                f"{base.rstrip('/')}/api/mini-robos/heartbeat",
                headers={"X-Robo-Key": key},
                json={"event": "finish", "slot": slot, "status": "succeeded" if sent else "failed",
                      "output_count": count, "error": None if sent else "WhatsApp send failed."},
                timeout=5, allow_redirects=False,
            )
        except requests.RequestException:
            pass

    try:
        Thread(target=post, daemon=True).start()
    except RuntimeError:
        pass
```

Agent pause stops a registered Ops app schedule on the running scheduler. For external agents, pause affects monitoring in the control room. Change execution at the external source. Choose Run now only for an enabled Ops app job. On interval jobs it can shift the next scheduled time.
