"""
feishu-claude bridge
====================
飞书 WebSocket → claude CLI (DeepSeek 路由) → 飞书回复

使用方法：
  python bridge.py

依赖：
  lark-oapi  — 飞书 WebSocket 长连接
  httpx      — 飞书 REST API（发消息）

配置：bridge.py 同级目录下的 .env 文件
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import lark_oapi as lark

# ─────────────────────────────────────────────────────────────
# 基础常量
# ─────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
SESSION_NOT_FOUND = "No conversation found with session ID"

# ─────────────────────────────────────────────────────────────
# 日志（必须在 HERE 之后定义和调用）
# ─────────────────────────────────────────────────────────────


def _setup_logging() -> None:
    """配置日志：pythonw.exe 无 stderr，输出到文件；python.exe 同时输出到 stderr。"""
    log_file = HERE / "data" / "bridge.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


log = logging.getLogger("bridge")   # 先拿 logger，main() 里再配置 handler


def _load_env(path: Path) -> None:
    """把 .env 文件中的键值对写入 os.environ（已存在的不覆盖）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass
class Config:
    feishu_app_id: str
    feishu_app_secret: str
    feishu_chat_id: str          # 白名单 chat_id，逗号分隔支持多个
    deepseek_api_key: str
    workspace: Path
    claude_model: str
    claude_opus_model: str
    task_timeout: int
    data_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        _load_env(HERE / ".env")

        def _require(key: str) -> str:
            v = os.environ.get(key, "").strip()
            if not v:
                log.error("缺少必填配置项：%s（请在 .env 中设置）", key)
                sys.exit(1)
            return v

        workspace_raw = os.environ.get("WORKSPACE", "").strip()
        workspace = Path(workspace_raw).expanduser().resolve() if workspace_raw else HERE

        data_dir = HERE / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            feishu_app_id=_require("FEISHU_APP_ID"),
            feishu_app_secret=_require("FEISHU_APP_SECRET"),
            feishu_chat_id=_require("FEISHU_CHAT_ID"),
            deepseek_api_key=_require("DEEPSEEK_API_KEY"),
            workspace=workspace,
            claude_model=os.environ.get("CLAUDE_MODEL", "deepseek-v4-flash"),
            claude_opus_model=os.environ.get("CLAUDE_OPUS_MODEL", "deepseek-v4-pro"),
            task_timeout=int(os.environ.get("TASK_TIMEOUT", "1800")),
            data_dir=data_dir,
        )

    @property
    def allowed_chat_ids(self) -> set[str]:
        return {c.strip() for c in self.feishu_chat_id.split(",") if c.strip()}


# ─────────────────────────────────────────────────────────────
# FeishuClient
# ─────────────────────────────────────────────────────────────

class FeishuClient:
    _DOMAIN = "https://open.feishu.cn"

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._token: str | None = None
        self._token_expires: float = 0.0

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self._DOMAIN}/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self._cfg.feishu_app_id,
                    "app_secret": self._cfg.feishu_app_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"飞书 token 获取失败: {data}")
            self._token = data["tenant_access_token"]
            self._token_expires = time.time() + int(data.get("expire", 7200)) - 60
            return self._token  # type: ignore[return-value]

    async def send_text(self, chat_id: str, text: str) -> None:
        token = await self._get_token()
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self._DOMAIN}/open-apis/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") not in (0, None):
                raise RuntimeError(f"飞书发消息失败: {data}")


# ─────────────────────────────────────────────────────────────
# SessionStore  (SQLite，两张表)
# ─────────────────────────────────────────────────────────────

class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self._db = str(db_path)
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dedup (
                    message_id TEXT PRIMARY KEY,
                    ts         TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_session (
                    chat_id    TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # 清理 7 天前的去重记录
            conn.execute(
                "DELETE FROM dedup WHERE ts < datetime('now', '-7 days')"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def seen_message(self, message_id: str) -> bool:
        """返回 True 表示已见过（重复），False 表示首次见到（并记录）。"""
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO dedup (message_id, ts) VALUES (?, datetime('now'))",
                    (message_id,),
                )
                conn.commit()
                return False
            except sqlite3.IntegrityError:
                return True

    def get_session(self, chat_id: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT session_id FROM active_session WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return row[0] if row else None

    def set_session(self, chat_id: str, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO active_session (chat_id, session_id, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(chat_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    updated_at = excluded.updated_at
                """,
                (chat_id, session_id),
            )
            conn.commit()

    def clear_session(self, chat_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM active_session WHERE chat_id = ?", (chat_id,)
            )
            conn.commit()


# ─────────────────────────────────────────────────────────────
# SessionIndex  (扫描 ~/.claude/projects/)
# ─────────────────────────────────────────────────────────────

@dataclass
class Session:
    session_id: str
    title: str
    updated_at: datetime


class SessionIndex:
    _PROJECTS = Path.home() / ".claude" / "projects"
    # bridge 自身的 inbox 路径，排除在外
    _INBOX_SUFFIX = "feishu-claude"

    def _is_bridge_internal(self, cwd: str | None) -> bool:
        if not cwd:
            return False
        return self._INBOX_SUFFIX in cwd.replace("\\", "/")

    def _extract_title(self, session_file: Path) -> tuple[str | None, str | None, datetime | None]:
        """返回 (cwd, title, last_updated)。"""
        cwd: str | None = None
        title: str | None = None
        last_ts: datetime | None = None
        try:
            lines = session_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None, None, None
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # 最新时间戳
            ts_raw = payload.get("timestamp")
            if ts_raw is not None:
                try:
                    ts = datetime.fromtimestamp(int(ts_raw) / 1000.0)
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
                except (ValueError, TypeError, OSError):
                    pass
            # cwd
            if cwd is None:
                c = str(payload.get("cwd") or "").strip()
                if c:
                    cwd = c
            # 标题：第一条有意义的 user 消息
            if title is None and payload.get("type") == "user":
                msg = payload.get("message") or {}
                if isinstance(msg, dict):
                    content_raw = msg.get("content", "")
                    if isinstance(content_raw, str):
                        text = content_raw.strip()
                    elif isinstance(content_raw, list):
                        text = ""
                        for block in content_raw:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = str(block.get("text") or "").strip()
                                if text:
                                    break
                    else:
                        text = ""
                    if text and not text.startswith("/") and not text.startswith("<"):
                        title = text
        return cwd, title, last_ts

    def list_sessions(self, limit: int = 20) -> list[Session]:
        if not self._PROJECTS.exists():
            return []
        sessions: list[Session] = []
        for proj_dir in self._PROJECTS.iterdir():
            if not proj_dir.is_dir():
                continue
            for sf in proj_dir.glob("*.jsonl"):
                cwd, title, last_ts = self._extract_title(sf)
                if self._is_bridge_internal(cwd):
                    continue
                if last_ts is None:
                    try:
                        last_ts = datetime.fromtimestamp(sf.stat().st_mtime)
                    except OSError:
                        continue
                sessions.append(
                    Session(
                        session_id=sf.stem,
                        title=(title or sf.stem[:8])[:100],
                        updated_at=last_ts,
                    )
                )
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions[:limit]

    def find_by_number(self, n: int) -> Session | None:
        sessions = self.list_sessions()
        if 1 <= n <= len(sessions):
            return sessions[n - 1]
        return None


# ─────────────────────────────────────────────────────────────
# ClaudeRunner
# ─────────────────────────────────────────────────────────────

class ClaudeRunner:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg

    @staticmethod
    def _resolve_claude() -> str | None:
        """找到 claude CLI 的完整路径。
        依次查找：CLAUDE_BIN 环境变量 → shutil.which → npm 默认位置。
        """
        import shutil

        # 1. 环境变量指定
        env_bin = os.environ.get("CLAUDE_BIN", "").strip()
        if env_bin and Path(env_bin).exists():
            return env_bin

        # 2. PATH 里查找（普通终端启动时有效）
        found = shutil.which("claude")
        if found:
            return found

        # 3. npm 全局安装的默认位置（pythonw 启动时 PATH 可能不含 npm）
        npm_dirs = [
            Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
            Path.home() / "AppData" / "Roaming" / "npm" / "claude",
        ]
        for p in npm_dirs:
            if p.exists():
                return str(p)

        return None

    def _make_env(self) -> dict[str, str]:
        env = dict(os.environ)
        key = self._cfg.deepseek_api_key
        if key:
            env["ANTHROPIC_BASE_URL"] = "https://api.deepseek.com/anthropic"
            env["ANTHROPIC_AUTH_TOKEN"] = key
            env["ANTHROPIC_API_KEY"] = key
            env.setdefault("ANTHROPIC_MODEL", self._cfg.claude_model)
            env.setdefault("ANTHROPIC_DEFAULT_OPUS_MODEL", self._cfg.claude_opus_model)
            env.setdefault("ANTHROPIC_DEFAULT_SONNET_MODEL", self._cfg.claude_model)
            env.setdefault("ANTHROPIC_DEFAULT_HAIKU_MODEL", self._cfg.claude_model)
            env.setdefault("CLAUDE_CODE_SUBAGENT_MODEL", self._cfg.claude_model)
            env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
            env["CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK"] = "1"
        return env

    def _snapshot_sessions(self) -> dict[str, float]:
        snap: dict[str, float] = {}
        root = Path.home() / ".claude" / "projects"
        if not root.exists():
            return snap
        for sf in root.rglob("*.jsonl"):
            try:
                snap[str(sf)] = sf.stat().st_mtime
            except OSError:
                pass
        return snap

    def _detect_new_session(self, snapshot: dict[str, float]) -> str | None:
        root = Path.home() / ".claude" / "projects"
        if not root.exists():
            return None
        best_path: Path | None = None
        best_mtime = 0.0
        for sf in root.rglob("*.jsonl"):
            try:
                mt = sf.stat().st_mtime
            except OSError:
                continue
            old_mt = snapshot.get(str(sf))
            if old_mt is None or mt > old_mt:
                if mt > best_mtime:
                    best_mtime = mt
                    best_path = sf
        return best_path.stem if best_path else None

    async def _run_subprocess(
        self,
        prompt: str,
        session_id: str | None,
        workspace: Path,
    ) -> tuple[int, str]:
        """执行 claude 子进程，返回 (returncode, stdout)。"""
        claude_bin = self._resolve_claude()
        if claude_bin is None:
            return 1, "❌ 找不到 claude 命令，请确认 claude CLI 已安装并在 PATH 中。"

        cmd = [
            claude_bin,
            "-p", prompt,
            "--output-format", "text",
            "--permission-mode", "bypassPermissions",
            "--add-dir", str(workspace),
        ]
        if session_id:
            cmd += ["--resume", session_id, "--fork-session"]

        env = self._make_env()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(workspace),
                env=env,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=self._cfg.task_timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return 124, f"⏱ 执行超时（{self._cfg.task_timeout} 秒），已终止。"
            return proc.returncode or 0, stdout.decode("utf-8", errors="replace").strip()
        except FileNotFoundError:
            return 1, "❌ 找不到 claude 命令，请确认 claude CLI 已安装并在 PATH 中。"

    async def run(
        self,
        prompt: str,
        session_id: str | None,
        workspace: Path,
    ) -> tuple[str, str | None]:
        """执行 claude，返回 (output_text, new_session_id | None)。"""
        snap = self._snapshot_sessions()
        rc, output = await self._run_subprocess(prompt, session_id, workspace)

        # Session 过期自动降级
        if rc != 0 and session_id and SESSION_NOT_FOUND in output:
            log.info("Session %s 已过期，以新会话重试", session_id)
            snap = self._snapshot_sessions()
            rc, output = await self._run_subprocess(prompt, None, workspace)
            if rc == 0:
                output = f"[会话已过期，已开启新对话]\n\n{output}"

        new_session_id = self._detect_new_session(snap)
        if rc != 0 and not output:
            output = f"❌ 执行失败（退出码 {rc}）"
        elif rc != 0:
            output = f"❌ 执行失败\n{output}"
        return output, new_session_id


# ─────────────────────────────────────────────────────────────
# CommandParser
# ─────────────────────────────────────────────────────────────

@dataclass
class Command:
    kind: str          # TALK | LIST | SWITCH | NEW
    text: str = ""     # TALK: 原始文本
    number: int = 0    # SWITCH: 编号


def parse_command(text: str) -> Command:
    t = text.strip()
    lower = t.lower()
    if lower == "claude list":
        return Command(kind="LIST")
    if lower == "claude new":
        return Command(kind="NEW")
    if lower.startswith("claude switch "):
        rest = t[len("claude switch "):].strip()
        if rest.isdigit():
            return Command(kind="SWITCH", number=int(rest))
        return Command(kind="SWITCH", number=-1)  # 无效编号
    return Command(kind="TALK", text=t)


# ─────────────────────────────────────────────────────────────
# MessageHandler
# ─────────────────────────────────────────────────────────────

class MessageHandler:
    def __init__(
        self,
        cfg: Config,
        feishu: FeishuClient,
        store: SessionStore,
        index: SessionIndex,
        runner: ClaudeRunner,
    ) -> None:
        self._cfg = cfg
        self._feishu = feishu
        self._store = store
        self._index = index
        self._runner = runner
        # 每个 chat_id 当前是否有任务在跑（内存，重启后清空）
        self._running: set[str] = set()

    async def handle(self, chat_id: str, message_id: str, text: str) -> None:
        # 1. 白名单
        if chat_id not in self._cfg.allowed_chat_ids:
            log.debug("忽略非白名单 chat: %s", chat_id)
            return

        # 2. 去重
        if self._store.seen_message(message_id):
            log.debug("重复消息，跳过: %s", message_id)
            return

        # 3. 解析指令
        cmd = parse_command(text)

        # 4. 非 TALK 指令直接处理
        if cmd.kind == "LIST":
            await self._handle_list(chat_id)
            return
        if cmd.kind == "NEW":
            self._store.clear_session(chat_id)
            await self._feishu.send_text(chat_id, "✅ 已清除会话，下次将开启新对话")
            return
        if cmd.kind == "SWITCH":
            await self._handle_switch(chat_id, cmd.number)
            return

        # 5. TALK：检查并发
        if chat_id in self._running:
            await self._feishu.send_text(chat_id, "⏳ 上一个任务仍在执行中，请稍候")
            return

        # 6. 执行
        self._running.add(chat_id)
        try:
            await self._feishu.send_text(chat_id, "⏳ 处理中...")
            session_id = self._store.get_session(chat_id)
            output, new_sid = await self._runner.run(
                cmd.text, session_id, self._cfg.workspace
            )
            # 保存新 session
            if new_sid:
                self._store.set_session(chat_id, new_sid)
            # 回复结果
            await self._feishu.send_text(chat_id, output or "（无输出）")
        except Exception as exc:
            log.exception("执行出错")
            await self._feishu.send_text(chat_id, f"❌ 内部错误：{exc}")
        finally:
            self._running.discard(chat_id)

    async def _handle_list(self, chat_id: str) -> None:
        sessions = self._index.list_sessions()
        if not sessions:
            await self._feishu.send_text(chat_id, "暂无历史会话")
            return
        current_sid = self._store.get_session(chat_id)
        lines = [f"最近会话（共 {len(sessions)} 个）："]
        for i, s in enumerate(sessions, 1):
            marker = "▶ " if s.session_id == current_sid else "  "
            lines.append(f"{i}. {marker}{s.title}")
        lines.append('\n回复 "claude switch N" 切换')
        await self._feishu.send_text(chat_id, "\n".join(lines))

    async def _handle_switch(self, chat_id: str, number: int) -> None:
        if number <= 0:
            await self._feishu.send_text(
                chat_id, '无效编号，请用 "claude list" 查看可用会话'
            )
            return
        session = self._index.find_by_number(number)
        if session is None:
            await self._feishu.send_text(
                chat_id, f'编号 {number} 不存在，请用 "claude list" 查看可用会话'
            )
            return
        self._store.set_session(chat_id, session.session_id)
        await self._feishu.send_text(chat_id, f"✅ 已切换到：{session.title}")


# ─────────────────────────────────────────────────────────────
# 单实例锁  (Win32 CreateFileW)
# ─────────────────────────────────────────────────────────────

def _acquire_lock() -> Any | None:
    """绑定并监听本地固定端口作为单实例锁。进程退出时 OS 自动释放。"""
    LOCK_PORT = 57384
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)      # 必须 listen，否则 watchdog 的 connect 检查会失败
        return s
    except OSError:
        s.close()
        return None


# ─────────────────────────────────────────────────────────────
# Feishu WS 事件解析
# ─────────────────────────────────────────────────────────────

def _extract_message(event: Any) -> tuple[str, str, str] | None:
    """从 lark-oapi 事件中提取 (chat_id, message_id, text)。"""
    data = getattr(event, "event", None)
    if data is None:
        return None
    msg = getattr(data, "message", None)
    if msg is None or getattr(msg, "message_type", None) != "text":
        return None
    chat_id = getattr(msg, "chat_id", None) or ""
    message_id = getattr(msg, "message_id", None) or ""
    content_raw = getattr(msg, "content", None) or ""
    try:
        content = json.loads(content_raw)
        text = str(content.get("text", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        text = str(content_raw).strip()
    if not chat_id or not message_id or not text:
        return None
    return chat_id, message_id, text


# ─────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────

def main() -> None:
    # 单实例锁必须最先检查，在任何初始化之前
    lock_handle = _acquire_lock()
    if lock_handle is None:
        os._exit(0)   # 快速退出，无需日志

    # 锁获取成功，才配置日志和初始化
    _setup_logging()
    cfg = Config.from_env()

    log.info("feishu-claude bridge 启动")
    log.info("工作目录: %s", cfg.workspace)
    log.info("允许的 chat: %s", cfg.allowed_chat_ids)

    feishu = FeishuClient(cfg)
    store = SessionStore(cfg.data_dir / "bridge.db")
    index = SessionIndex()
    runner = ClaudeRunner(cfg)
    handler = MessageHandler(cfg, feishu, store, index, runner)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _on_message(event: Any) -> None:
        extracted = _extract_message(event)
        if extracted is None:
            return
        chat_id, message_id, text = extracted
        log.info("收到消息 [%s] %s: %s", message_id[:8], chat_id[:12], text[:60])
        asyncio.run_coroutine_threadsafe(
            handler.handle(chat_id, message_id, text), loop
        )

    builder = lark.EventDispatcherHandler.builder("", "", lark.LogLevel.WARNING)
    dispatch = builder.register_p2_im_message_receive_v1(_on_message).build()

    client = lark.ws.Client(
        cfg.feishu_app_id,
        cfg.feishu_app_secret,
        log_level=lark.LogLevel.INFO,
        event_handler=dispatch,
        auto_reconnect=True,
    )

    try:
        loop.run_until_complete(_run_forever(loop, client))
    except KeyboardInterrupt:
        log.info("收到中断信号，退出。")
    finally:
        try:
            lock_handle.close()
        except Exception:
            pass


async def _run_forever(
    loop: asyncio.AbstractEventLoop,
    client: Any,
) -> None:
    """在 asyncio 事件循环里驱动 lark ws.Client（它内部使用线程）。"""
    import threading

    started = threading.Event()

    def _ws_thread() -> None:
        started.set()
        client.start()  # blocks until stop() or error

    t = threading.Thread(target=_ws_thread, daemon=True, name="lark-ws")
    t.start()
    started.wait()
    log.info("飞书 WebSocket 已连接，等待消息...")

    # 保持事件循环运行
    while t.is_alive():
        await asyncio.sleep(1)


if __name__ == "__main__":
    main()
