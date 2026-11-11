"""Bhago-only encrypted credentials; neither secrets nor tokens are logged."""
import json
import os
from pathlib import Path
from cryptography.fernet import Fernet


def cipher():
    key = os.environ.get('BHAGO_CREDENTIAL_KEY')
    if not key:
        local = Path(__file__).resolve().parent / 'private' / 'cloud-credentials.key'
        if local.is_file():
            key = local.read_text().strip()
    if not key:
        raise ValueError('Bhago credential encryption key is not configured')
    return Fernet(key.encode())


def read_secret(name):
    if name not in ('google_oauth', 'samvaad_session'):
        raise ValueError('Unsupported Bhago credential')
    from insightdesk_bridge import get_value
    encrypted = get_value('bhago:18:credential:' + name)
    return json.loads(cipher().decrypt(encrypted.encode())) if encrypted else None


def save_secret(name, value):
    if name not in ('google_oauth', 'samvaad_session'):
        raise ValueError('Unsupported Bhago credential')
    from insightdesk_bridge import connect
    encrypted = cipher().encrypt(json.dumps(value).encode()).decode()
    with connect() as db, db.cursor() as cur:
        cur.execute("INSERT INTO app_settings(key,value,created_at,updated_at) VALUES(%s,%s,NOW(),NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=NOW()", ('bhago:18:credential:' + name, encrypted))
