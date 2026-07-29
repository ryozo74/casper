# Casper を LAN(携帯)へ通す中継を、**二度と張り直さずに済む形**で据える。
# 【管理者PowerShellで一度だけ実行】以後 WSL や Windows を再起動しても直す要なし。
#
# ■ なぜ「IP固定」ではなくこの形か（殿御下問2026-07-29「IP今のやつに固定できないの？」）
#   ・WSL2(NAT)のIPは再起動ごとに変わり、Windows 10 では静的化の正式な口が無い。
#     従来はWSLのIP(例 172.17.1.203)を中継先にしていたゆえ、変わる度に不通になっていた。
#   ・Windows 11 の mirrored networking(中継そのものが不要)は **本機は Windows 10 build 19045 ゆえ使えぬ**(実測確認)。
#   ・ゆえ **中継先を 127.0.0.1 にする**。WSL2 は Windows の localhost を WSL 内へ転送する仕組みを
#     標準で持ち(実測: 中継entryを持たぬ 8899 が Windows の 127.0.0.1:8899 で通った)、
#     127.0.0.1 は永久に変わらぬ。ゆえ一度据えれば張り直しが要らぬ。
#
#   経路: 携帯 → [ホストのLAN IP]:port → (portproxy) → 127.0.0.1:port → (WSL localhost転送) → Casper

$ports = 8443, 8770, 8100, 8201   # 8443=Casper HTTPS(携帯用) / 8770=Casper HTTP / 8100=Aurora / 8201=予備

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "管理者として実行してくだされ（右クリック→管理者として実行）" -ForegroundColor Red
    exit 1
}

Write-Host "中継先を 127.0.0.1 に据えまする（WSLのIPには依らぬ＝以後張り直し不要）" -ForegroundColor Cyan

foreach ($p in $ports) {
    # 古い entry(WSLのIPを指したもの)を必ず消してから張る。残したままの add は古い状態を引きずる。
    netsh interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 2>$null | Out-Null
    netsh interface portproxy delete v4tov4 listenport=$p listenaddress=* 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=127.0.0.1 | Out-Null
    if (-not (Get-NetFirewallRule -DisplayName "Casper port $p" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Casper port $p" -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $p -Profile Any | Out-Null
    }
    Write-Host ("  据え置き: 0.0.0.0:{0} -> 127.0.0.1:{0}" -f $p)
}

Write-Host "`n--- 現在の中継表 ---" -ForegroundColor Cyan
netsh interface portproxy show v4tov4

Write-Host "`n--- 検算(ホストのLAN IP経由で通るか) ---" -ForegroundColor Cyan
$lan = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and
                       $_.InterfaceAlias -notlike '*WSL*' } |
        Select-Object -First 1).IPAddress
$bad = 0
foreach ($p in $ports) {
    $ok = Test-NetConnection -ComputerName $lan -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue
    if (-not $ok) { $bad++ }
    Write-Host ("  {0}:{1} = {2}" -f $lan, $p, $(if ($ok) { '通る' } else { '不通' })) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
}
if ($bad) {
    Write-Host "`n不通が残り申した。そのポートで Casper が待受けているか(WSL内 ss -ltn)をご確認くだされ。" -ForegroundColor Yellow
} else {
    Write-Host "`n全て通り申した。携帯からは https://$lan`:8443/ にてお試しくだされ" -ForegroundColor Green
    Write-Host "（自署の証明書ゆえ初回は警告が出まする。以後この作業は不要にござる）" -ForegroundColor Green
}
