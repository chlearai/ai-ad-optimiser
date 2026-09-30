import os
import sys
import uvicorn

if __name__ == "__main__":
    # Ensure stdout/stderr are flushed immediately
    os.environ["PYTHONUNBUFFERED"] = "1"

    # Read port assigned by Railway/Cloud platform or default to 8000
    raw_port = os.getenv("PORT", "8000")
    try:
        port = int(str(raw_port).strip())
    except (ValueError, TypeError):
        port = 8000

    host = os.getenv("HOST", "0.0.0.0")
    print(f"[AdOptima] Starting server on {host}:{port}...")

    # Run uvicorn with proxy headers enabled for reverse-proxy compatibility on Railway
    uvicorn.run(
        "backend.app:app",
        host=host,
        port=port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
