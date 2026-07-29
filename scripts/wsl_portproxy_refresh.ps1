# Casper を LAN(携帯)へ通す中継を据え、**起動時に自動で張り直す**よう登録する。
# 【一度だけ実行】以後は Windows/WSL を再起動しても自動で直り、殿の手は要らぬ。
#
# ■ 経緯と設計の理由（殿御下問2026-07-29「IP今のやつに固定できないの？動かさなくていい方法で」）
#   ・WSL2(NAT)のIPは再起動ごとに変わり、Windows 10 に静的化の正式な口は無い。
#   ・Windows 11 の mirrored networking(中継自体が不要)は 本機が Windows 10 build 19045 ゆえ使えぬ(実測)。
#   ・★当職は一度「中継先を 127.0.0.1 にすれば不変」と考えたが**誤りであった**:
#     中継は 0.0.0.0(=127.0.0.1 も含む)で待受けるゆえ、127.0.0.1 へ繋ぐと**自分自身へ繋ぐ**形になり、
#     TCPの接続だけ成立して中身が流れぬ(実測: Test-NetConnection は True だが HTTP は 000)。
#     加えて WSL の localhost 転送をその港で覆い隠し、通っていた 8770 まで塞いだ。
#   ・ゆえ **中継先は WSL の実IP**(これは確実に通る)。変わる問題は「人が張り直す」のでなく
#     **起動時に自動で張り直す**ことで解く。それが「動かさなくていい方法」の正体にござる。
#
#   経路: 携帯 → [ホストのLAN IP]:port → (portproxy) → [WSLのIP]:port → Casper

param([switch]$Auto)     # -Auto = 自動実行(タスクスケジューラ経由)。待ち受けの一呼吸を置かぬ

$ports = 8443, 8770, 8100, 8201   # 8443=Casper HTTPS(携帯用) / 8770=Casper HTTP / 8100=Aurora / 8201=予備
$taskName = 'Casper portproxy refresh'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # 自ら昇格を求める(「管理者で実行せよ」と告げて終わるのは機構の仕事の放棄)。
    Write-Host "管理者権限が要るゆえ、昇格を求めまする（UACの確認にお応えくだされ）" -ForegroundColor Yellow
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($Auto) { $a += '-Auto' }
    try { Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $a | Out-Null }
    catch {
        Write-Host "昇格が拒まれ申した。右クリック→管理者として実行にてお願いいたす。" -ForegroundColor Red
        if (-not $Auto) { Read-Host "（Enterで閉じまする）" | Out-Null }
    }
    exit
}

# ── ① WSL の現在のIPを得る ───────────────────────────────────────────
$wslIp = ''
try { $wslIp = ((wsl.exe -e hostname -I) -join ' ').Trim().Split(' ')[0] } catch {}
if (-not $wslIp) {
    Write-Host "WSL の IP が取れませぬ（WSL が起動しておるかご確認くだされ）" -ForegroundColor Red
    if (-not $Auto) { Read-Host "（Enterで閉じまする）" | Out-Null }
    exit 1
}
Write-Host "WSL IP = $wslIp" -ForegroundColor Cyan

# ── ② 中継を張り直す(古いものを消してから) ───────────────────────────
foreach ($p in $ports) {
    netsh interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 2>$null | Out-Null
    netsh interface portproxy delete v4tov4 listenport=$p listenaddress=* 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$wslIp | Out-Null
    if (-not (Get-NetFirewallRule -DisplayName "Casper port $p" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Casper port $p" -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $p -Profile Any | Out-Null
    }
    Write-Host ("  張り直し: 0.0.0.0:{0} -> {1}:{0}" -f $p, $wslIp)
}

# ── ③ 起動時に自動で張り直すよう登録(=以後、人の手が要らぬ) ───────────
try {
    $act = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -Auto"
    $trg = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn))
    $pri = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    Register-ScheduledTask -TaskName $taskName -Action $act -Trigger $trg -Principal $pri `
        -Settings $set -Force | Out-Null
    Write-Host "`n☑ 起動時の自動張り直しを登録いたした（タスク名: $taskName）" -ForegroundColor Green
    Write-Host "  以後 Windows/WSL を再起動しても自動で直りまする（殿の手は要りませぬ）" -ForegroundColor Green
} catch {
    Write-Host "`n⚠ 自動張り直しの登録に失敗いたした: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  中継自体は張れておりますが、再起動後は本scriptを再度お走らせくだされ" -ForegroundColor Yellow
}

# ── ④ 検算: TCPだけでなく**実際にHTTPが返るか**を見る ─────────────────
#     (中継が自分自身へ繋ぐ誤りは「TCPは通るがHTTPは000」として現れた。ゆえ中身で確かめる)
Write-Host "`n--- 現在の中継表 ---" -ForegroundColor Cyan
netsh interface portproxy show v4tov4

$lan = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and
                       $_.InterfaceAlias -notlike '*WSL*' } |
        Select-Object -First 1).IPAddress
Write-Host "`n--- 検算(ホストのLAN IP $lan 経由で中身が返るか) ---" -ForegroundColor Cyan
$bad = 0
foreach ($p in $ports) {
    $scheme = if ($p -eq 8443) { 'https' } else { 'http' }
    $code = & curl.exe -sk -m 10 -o NUL -w '%{http_code}' "$scheme`://$lan`:$p/" 2>$null
    $ok = ($code -match '^[23]')
    if (-not $ok) { $bad++ }
    Write-Host ("  {0}://{1}:{2}/ -> HTTP {3} {4}" -f $scheme, $lan, $p, $code, $(if ($ok) { '通る' } else { '不通' })) `
        -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
}
if ($bad) {
    Write-Host "`n不通が残り申した。そのポートで待受けておるか(WSL内: ss -ltn)をご確認くだされ。" -ForegroundColor Yellow
} else {
    Write-Host "`n全て通り申した。携帯からは https://$lan`:8443/ にてお試しくだされ" -ForegroundColor Green
    Write-Host "（自署の証明書ゆえ初回は警告が出まする）" -ForegroundColor Green
}
if (-not $Auto) { Read-Host "`n（確認できましたら Enter で閉じまする）" | Out-Null }
