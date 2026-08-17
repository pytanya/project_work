"""
EduTutor — автовосстановление сервера при ошибках.
Запускается в фоне, перезапускает uvicorn при падении.
"""

import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
VENV_SCRIPT = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
UVICORN_CMD = [
    str(VENV_SCRIPT),
    "-m", "uvicorn",
    "api.app:app",
    "--reload",
    "--host", "0.0.0.0",
    "--port", "8000",
]

def main():
    print(f"Starting EduTutor server at {PROJECT_DIR}")
    print(f"Frontend: http://localhost:5173")
    print(f"Backend:  http://localhost:8000")
    print(f"API Docs: http://localhost:8000/docs")
    print()
    
    process = subprocess.Popen(UVICORN_CMD, cwd=str(PROJECT_DIR))
    
    try:
        while process.poll() is None:
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nStopping server...")
        process.terminate()
        process.wait()
    finally:
        print("Server stopped.")

if __name__ == "__main__":
    main()
