"""Supervise the Bhago cloud browser and queue worker; never prints credentials."""
import json
import datetime as dt
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
PRIVATE = Path(os.environ.get('BHAGO_PRIVATE_DIR', '/data/private'))


def unresponsive(private, started, moment=None, limit=480):
    """Detect a living-but-stalled browser/bridge without depending on its database."""
    moment=moment or dt.datetime.now(dt.timezone.utc)
    try:
        record=json.loads((private/'bridge-browser.json').read_text())
        progress=dt.datetime.fromisoformat(record['updatedAt'].replace('Z','+00:00'))
    except (OSError,ValueError,KeyError,TypeError):
        progress=started
    return (moment-progress).total_seconds()>limit


def seed_session():
    from credential_store import read_secret
    session = read_secret('samvaad_session')
    if not session or session.get('account') != 'chlearbhago@gmail.com':
        raise ValueError('Bhago Samvaad cloud session authorization is missing')
    version = PRIVATE / 'session-seed-version.txt'
    state_file = PRIVATE / 'samvaad-state.json'
    if not state_file.exists() or not version.exists() or version.read_text() != session['version']:
        temporary = PRIVATE / 'samvaad-state.tmp'
        temporary.write_text(json.dumps(session['state']), encoding='utf-8')
        temporary.chmod(0o600)
        os.replace(temporary, state_file)
        version.write_text(session['version'])


def verify_connections():
    from insightdesk_bridge import IST, read_source

    previous_day = (dt.datetime.now(IST).date() - dt.timedelta(days=1)).isoformat()
    sources = ('swiggy', 'zomato', 'qwqer', 'payment_reminder')
    for source in sources:
        try:
            rows, _, _ = read_source({
                'source': source,
                'calling_type': 'payment' if source == 'payment_reminder' else 'lead',
                'start_date': previous_day,
                'end_date': previous_day,
                '_allow_empty': True,
            })
            print(f'Bhago {source}: sheet read verified ({len(rows)} eligible)', flush=True)
        except Exception as error:
            print(f'Bhago {source}: sheet verification failed ({type(error).__name__})', flush=True)
    try:
        seed_session()
        subprocess.run(['node', str(ROOT/'samvaad-worker.cjs'), 'cloud-verify'], check=True, timeout=90)
    except Exception as error:
        print(f'Bhago Samvaad verification incomplete ({type(error).__name__})', flush=True)


def main():
    if os.environ.get('BHAGO_RUNTIME') != 'cloud':
        raise SystemExit('Cloud runtime must be explicitly enabled')
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PRIVATE.chmod(0o700)
    os.environ.setdefault('BHAGO_GOOGLE_OAUTH_ENABLED', 'true')
    if os.environ.get('BHAGO_CLOUD_VERIFY_ONLY', 'true') == 'true':
        verify_connections()
        return
    children = []

    def stop(*_):
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        try:
            seed_session()
            (PRIVATE/'bridge-browser.json').write_text(json.dumps({'ready':False,'updatedAt':dt.datetime.now(dt.timezone.utc).isoformat()}))
            children[:] = [subprocess.Popen(['node', str(ROOT/'samvaad-worker.cjs'), 'cloud']),
                           subprocess.Popen([sys.executable, '-u', str(ROOT/'insightdesk_bridge.py')])]
            started=dt.datetime.now(dt.timezone.utc)
            while all(child.poll() is None for child in children):
                time.sleep(5)
                if unresponsive(PRIVATE,started):
                    print('Bhago workflow heartbeat stalled; restarting its children. Uncertain launches remain held.',flush=True)
                    break
        except Exception:
            print('Bhago cloud authorization or connection needs attention; no automatic campaign retry.', flush=True)
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
        time.sleep(30)


if __name__ == '__main__':
    main()
