# feishu-claude

飞书消息 → Claude Code（DeepSeek 路由）→ 飞书回复

在手机飞书里直接和 Claude 对话，支持多会话管理，开机自启、完全后台静默运行。

## 功能

- 发任意消息 → Claude 执行并回复结果
- 多会话管理：查看 / 切换 / 新建 / 中断
- DeepSeek 路由：自动注入 `ANTHROPIC_BASE_URL` 等环境变量
- 单实例保证：多次启动只有一个实例运行
- 开机自启 + 每 5 分钟保活，完全无窗口后台运行

## 指令

| 发送内容 | 效果 |
|---------|------|
| `claude` | 显示可用指令 |
| `claude list` | 查看最近会话列表 |
| `claude switch N` | 切换到第 N 个会话 |
| `claude new` | 清除当前会话，下次开启新对话 |
| `claude exit` | 中断当前正在执行的任务 |
| 其他任意内容 | 发送给 Claude 执行 |

## 依赖

- Python 3.12+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 已安装并在 PATH 中
- Windows（`setup.ps1` 使用 PowerShell 计划任务）

## 快速部署

```powershell
git clone https://github.com/cxy9355-glitch/claude-deepseek-setup
cd claude-deepseek-setup

# 复制配置模板并填入真实值
cp .env.example .env
notepad .env

# 一键初始化：创建 venv、安装依赖、注册开机任务
.\setup.ps1
```

## 配置项（.env）

```env
# 飞书 Bot 凭证（必填）
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_CHAT_ID=oc_xxx        # 允许收发消息的群/单聊 ID

# DeepSeek API Key（必填）
DEEPSEEK_API_KEY=sk-xxx

# Claude 工作目录（留空则使用脚本所在目录）
WORKSPACE=G:\Codex\个人

# 模型配置
CLAUDE_MODEL=deepseek-v4-flash
CLAUDE_OPUS_MODEL=deepseek-v4-pro

# 单次任务超时秒数（默认 1800）
TASK_TIMEOUT=1800
```

## 多机部署

每台机器使用**独立的飞书 Bot**（不同的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`），其余代码完全相同：

```powershell
git clone https://github.com/cxy9355-glitch/claude-deepseek-setup
cd claude-deepseek-setup
cp .env.example .env   # 填入该机器专属的 Bot 凭证
.\setup.ps1
```

## 卸载

```powershell
.\uninstall.ps1
```

注销计划任务并终止 bridge 进程，`.venv/` 和 `data/` 保留。

## 文件说明

| 文件 | 说明 |
|------|------|
| `bridge.py` | 核心逻辑（约 400 行） |
| `watchdog.py` | 保活检查，由计划任务每 5 分钟调用 |
| `setup.ps1` | 一键初始化脚本 |
| `uninstall.ps1` | 卸载脚本 |
| `requirements.txt` | Python 依赖（lark-oapi、httpx） |
| `.env.example` | 配置模板 |
| `data/bridge.log` | 运行日志（自动创建） |
