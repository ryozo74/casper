# Casper チャットを LAN 公開する portproxy + firewall 設定 (要 管理者)
# 引数で WSL IP を受け取る (昇格コンテキストでの wsl 取得不安定を回避)。
param([string]$wslip = "")
$port = 8770
$log = "$env:TEMP\casper_lan_setup.log"
function L($m){ ($m | Out-String).Trim() | Tee-Object -FilePath $log -Append | Out-Null }
"=== $(Get-Date) ===" | Out-File $log
if (-not $wslip) { try { $wslip = (wsl hostname -I).Trim().Split(' ')[0] } catch {} }
L "wslip=$wslip port=$port"

L (netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$port 2>&1)
L (netsh interface portproxy add    v4tov4 listenaddress=0.0.0.0 listenport=$port connectaddress=$wslip connectport=$port 2>&1)

try {
    if (-not (Get-NetFirewallRule -DisplayName "Casper Chat $port" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Casper Chat $port" -Direction Inbound -LocalPort $port -Protocol TCP -Action Allow -ErrorAction Stop | Out-Null
        L "firewall: created"
    } else { L "firewall: exists" }
} catch { L "firewall ERROR: $_" }

L (netsh interface portproxy show v4tov4)
L "DONE"
