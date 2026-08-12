from dotenv import load_dotenv
import os as _os
load_dotenv(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '.env'))
import requests
from backend.routes.auth import create_access_token
from sqlalchemy import create_engine, text

DB_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'adoptima.db')
engine = create_engine(f"sqlite:///{DB_PATH}")
with engine.connect() as conn:
    email = conn.execute(text("SELECT email FROM users WHERE role='superadmin' LIMIT 1")).scalar()

token = create_access_token(data={"sub": email, "role": "superadmin"})
url = "http://127.0.0.1:8000/api/reports/dsu/budget-mis?start_date=2026-08-01&end_date=2026-08-04"
r = requests.get(url, headers={"Authorization": f"Bearer {token}"})
print("status:", r.status_code)
for row in r.json().get("campus3_rows", []):
    print(row["course"], row["status"])
print("campus4:", r.json().get("campus4_rows", []))
