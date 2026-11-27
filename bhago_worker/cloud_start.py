"""Supervise the Bhago cloud browser and queue worker; never prints credentials."""
import json
import base64
import datetime as dt
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
PRIVATE = Path(os.environ.get('BHAGO_PRIVATE_DIR', '/data/private'))


class SessionExpired(ValueError):
    pass


def session_expiry(state):
    """Read expiry metadata only; Samvaad still validates the token itself."""
    for origin in state.get('origins', []):
        if origin.get('origin') != 'https://samvaad.sakhaa.ai':
            continue
        for item in origin.get('localStorage', []):
            if item.get('name') != 'token':
                continue
            try:
                payload = item['value'].split('.')[1]
                claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
                return dt.datetime.fromtimestamp(float(claims['exp']), dt.timezone.utc)
            except (KeyError, IndexError, ValueError, TypeError, OverflowError):
                return None
    return None


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
    reseed = not state_file.exists() or not version.exists() or version.read_text() != session['version']
    candidate = session['state'] if reseed else json.loads(state_file.read_text())
    expires = session_expiry(candidate)
    if expires and expires <= dt.datetime.now(dt.timezone.utc):
        raise SessionExpired('Samvaad cloud sign-in expired; renew the Bhago session. No campaign was submitted.')
    if reseed:
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
    # Railway's custom Start Command replaces the image ENTRYPOINT. Ensure the
    # browser still has an init/reaper when Railway starts Python as PID 1.
    if sys.platform == 'linux' and os.getpid() == 1:
        init = Path('/usr/bin/tini')
        if not init.is_file():
            raise SystemExit('Bhago container init is missing; rebuild the worker image')
        os.execv(str(init), [str(init), '-g', '--', sys.executable, '-u', str(Path(__file__).resolve())])
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
            for label, child in zip(('browser', 'bridge'), children):
                if child.poll() is not None:
                    print(f'Bhago {label} exited with code {child.returncode}; restarting safely.', flush=True)
        except SessionExpired as error:
            stamp=dt.datetime.now(dt.timezone.utc).isoformat()
            (PRIVATE/'bridge-browser.json').write_text(json.dumps({'ready':False,'updatedAt':stamp}))
            (PRIVATE/'status.json').write_text(json.dumps({'state':'samvaad_login_required','updatedAt':stamp,'message':str(error)}))
            try:
                from insightdesk_bridge import heartbeat
                heartbeat()
            except Exception:
                pass
            print(str(error), flush=True)
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
