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
    """尝试绑定锁端口：绑定失败说明 bridge 正在运行并持有该端口。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("127.0.0.1", LOCK_PORT))
        s.close()
        return False   # 绑定成功 → 端口空闲 → bridge 未运行
    except OSError:
        return True    # 绑定失败 → 端口被占 → bridge 运行中


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
