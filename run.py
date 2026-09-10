import os
import sys
import subprocess
import webbrowser
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("adoptima-startup.log", mode="w"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("AdOptima")

def main():
    log.info("====================================================")
    log.info("             AdOptima AI - Google Ads               ")
    log.info("             Optimization Assistant                 ")
    log.info("====================================================")
    
    # 1. Install dependencies check
    log.info("[*] Checking dependencies...")
    try:
        import fastapi
        import uvicorn
        import google.generativeai
        import pydantic
        try:
            import pandas
        except Exception:
            pass
        log.info("[+] All core dependencies are available.")
    except ImportError as e:
        log.error(f"[!] Missing dependency: {e}")
        log.info("[*] Installing via pip...")
        try:
            requirements_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", requirements_path])
            log.info("[+] Dependencies installed successfully.")
        except Exception as e:
            log.error(f"[-] Auto-installation failed: {e}")
            log.info("Please run manually: pip install -r requirements.txt")
            sys.exit(1)
            
    # 2. Start Uvicorn backend server
    log.info("[*] Starting backend server on http://127.0.0.1:8000...")
    
    # Get current directory and add backend to pythonpath
    cwd = os.path.dirname(os.path.abspath(__file__))
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    
    cmd = [
        sys.executable, 
        "-m", 
        "uvicorn", 
        "backend.app:app", 
        "--host", 
        "127.0.0.1", 
        "--port", 
        "8000",
        "--reload"
    ]
    
    try:
        # Run backend server; capture output to a log file so we can diagnose failures
        log_file = open("adoptima-server.log", "w", encoding="utf-8")
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log_file, stderr=subprocess.STDOUT)
        log.info(f"[*] Uvicorn process started (PID {proc.pid}). Waiting for it to bind to port 8000...")
        
        # Wait up to 15 seconds for the server to come up
        for i in range(15):
            time.sleep(1.0)
            if proc.poll() is not None:
                exit_code = proc.poll()
                log_file.flush()
                log.error(f"[-] Uvicorn exited early with code {exit_code}. Check adoptima-server.log for details.")
                sys.exit(exit_code)
        
        log.info("[+] Server should now be running at http://127.0.0.1:8000")
        
        # Open web dashboard
        log.info("[*] Launching dashboard in your browser...")
        webbrowser.open("http://127.0.0.1:8000")
        
        # Block on the server subprocess to keep console alive and print outputs
        proc.wait()
    except KeyboardInterrupt:
        log.info("\n[*] Shutting down AdOptima AI. Goodbye!")
        if 'proc' in locals() and proc.poll() is None:
            proc.terminate()
    except Exception as e:
        log.exception(f"[-] Failed to launch server: {e}")

if __name__ == "__main__":
    main()
