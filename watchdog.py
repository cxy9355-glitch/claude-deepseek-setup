"""
watchdog.py — 保活检查（由计划任务每 5 分钟调用一次）
用 pythonw.exe 执行，完全后台无窗口。
"""
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK_PORT = 57384   # 与 bridge.py 保持一致


def _bridge_running() -> bool:
    """尝试连接锁端口：能连上说明 bridge 在运行。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(("127.0.0.1", LOCK_PORT))
        s.close()
        return True
    except OSError:
        return False


def main() -> None:
    if _bridge_running():
        sys.exit(0)   # 已在运行，什么都不做

    # bridge 不在运行，用 pythonw.exe 静默启动
    pythonw = HERE / ".venv" / "Scripts" / "pythonw.exe"
    bridge  = HERE / "bridge.py"

    if not pythonw.exists() or not bridge.exists():
        sys.exit(1)

    subprocess.Popen(
        [str(pythonw), str(bridge)],
        cwd=str(HERE),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


if __name__ == "__main__":
    main()
