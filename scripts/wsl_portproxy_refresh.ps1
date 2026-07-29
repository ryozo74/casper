# Casper 携帯サーバ(HTTPS 8443)等を LAN へ通すための中継を張り直す。
# 【管理者PowerShellで実行】WSL2 のIPは再起動で変わるゆえ、通らなくなったら本script を走らせる。
#
# 由来: 2026-07-29「携帯サーバーが立ち上がっていない」の切り分け。
#   サーバ(WSL内 8443)は正常・ファイアウォール規則も 8770 と同一で有効・ポート予約範囲外。
#   壊れていたのは portproxy(8443)のみ——IP Helper は待受けるが WSL へ渡さぬ状態であった。
#   同じ仕組みの 8770 は通っていたゆえ、entry の張り直しで復すのが筋。

$ports = 8443, 8770, 8100, 8201   # 8443=Casper HTTPS(携帯) / 8770=Casper HTTP / 8100=Aurora / 8201=予備

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "管理者として実行してくだされ（右クリック→管理者として実行）" -ForegroundColor Red
    exit 1
}

$wslIp = (wsl.exe hostname -I).Trim().Split(' ')[0]
if (-not $wslIp) { Write-Host "WSL の IP が取れませぬ（WSL は起動しておりますか）" -ForegroundColor Red; exit 1 }
Write-Host "WSL IP = $wslIp" -ForegroundColor Cyan

foreach ($p in $ports) {
    # 古い entry を消してから張る(残したままの add は古い状態を引きずる)
    netsh interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 2>$null | Out-Null
    netsh interface portproxy delete v4tov4 listenport=$p listenaddress=* 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$wslIp | Out-Null
    if (-not (Get-NetFirewallRule -DisplayName "Casper port $p" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Casper port $p" -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $p -Profile Any | Out-Null
    }
    Write-Host ("  張り直し: {0} -> {1}:{0}" -f $p, $wslIp)
}

Write-Host "`n--- 検算(ホスト経由で通るか) ---" -ForegroundColor Cyan
$lan = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and
                       $_.InterfaceAlias -notlike '*WSL*' } |
        Select-Object -First 1).IPAddress
foreach ($p in $ports) {
    $ok = Test-NetConnection -ComputerName $lan -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue
    $col = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ("  {0}:{1} = {2}" -f $lan, $p, $(if ($ok) { '通る' } else { '不通' })) -ForegroundColor $col
}
Write-Host "`n携帯からは https://$lan:8443/ にてお試しくだされ（証明書は自署ゆえ初回は警告が出まする）" -ForegroundColor Cyan
