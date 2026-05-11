$lock = "G:\\Codex\\feishu-claude\\data\\bridge.lock"
$pid_file = "G:\\Codex\\feishu-claude\\data\\bridge.pid"
$python = "G:\\Codex\\feishu-claude\\.venv\\Scripts\\python.exe"
$bridge = "G:\\Codex\\feishu-claude\\bridge.py"

# 检查锁文件是否被独占持有（即 bridge 是否在运行）
$kernel32 = Add-Type -MemberDefinition @'
[DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
public static extern IntPtr CreateFile(string lpFileName, uint dwDesiredAccess,
    uint dwShareMode, IntPtr lpSecurityAttributes, uint dwCreationDisposition,
    uint dwFlagsAndAttributes, IntPtr hTemplateFile);
[DllImport("kernel32.dll")]
public static extern bool CloseHandle(IntPtr hObject);
'@ -Name "K32" -Namespace "Win32" -PassThru

$GENERIC_READ = 0x80000000
$FILE_SHARE_NONE = 0
$OPEN_EXISTING = 3
$h = [Win32.K32]::CreateFile($lock, $GENERIC_READ, $FILE_SHARE_NONE, [IntPtr]::Zero, $OPEN_EXISTING, 0, [IntPtr]::Zero)
$INVALID = [IntPtr](-1)
if ($h -ne $INVALID -and $h -ne [IntPtr]::Zero) {
    # 能打开说明没有独占持有，bridge 没在运行
    [Win32.K32]::CloseHandle($h) | Out-Null
    Start-Process -FilePath $python -ArgumentList $bridge -WorkingDirectory "G:\Codex\feishu-claude" -WindowStyle Hidden
}
