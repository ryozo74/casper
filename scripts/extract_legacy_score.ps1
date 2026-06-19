# 旧スコア(score_dir) 1PJを走査し「蒸留した暗黙知」をコンパクト JSON 抽出。
# Usage: powershell -File extract_legacy_score.ps1 -proj Ariel
param([string]$proj)
$root = "X:\cg\proj\score_dir\$proj"

function FinalVal($raw) {
    if (-not $raw) { return "" }
    $vals = ($raw -split "`n") | ForEach-Object { $_.Trim() } |
            Where-Object { $_ -ne "" -and $_ -ne "n/a" -and $_ -notmatch '^modified as ' }
    if ($vals) { return [string]($vals[-1]) } else { return "" }
}
function Inc($h, $k) {
    $k = [string]$k
    if ($k) { if ($h.ContainsKey($k)) { $h[$k]++ } else { $h[$k] = 1 } }
}

$depts = @{}; $artists = @{}; $statusDist = @{}; $msgs = @(); $entries = 0; $movieSessions = 0

if (Test-Path $root) {
    $cutDirs = Get-ChildItem $root -Recurse -Depth 3 -Directory -ErrorAction SilentlyContinue |
               Where-Object { $_.Name -match '^c?\d{2,}$' }
    foreach ($cd in $cutDirs) {
        $cut = $cd.Name
        foreach ($dd in (Get-ChildItem $cd.FullName -Directory -ErrorAction SilentlyContinue)) {
            $d = $dd.FullName; $dept = $dd.Name; $entries++
            Inc $depts $dept
            Inc $artists (FinalVal (Get-Content (Join-Path $d 'artist.txt') -Raw -ErrorAction SilentlyContinue))
            Inc $statusDist (FinalVal (Get-Content (Join-Path $d 'status.txt') -Raw -ErrorAction SilentlyContinue))
            # MSG = 暗黙知の核 (コンパクトに)
            $md = Join-Path $d 'MSG'
            if (Test-Path $md) {
                foreach ($mf in (Get-ChildItem $md -Filter *.json -ErrorAction SilentlyContinue)) {
                    try {
                        $j = Get-Content $mf.FullName -Raw | ConvertFrom-Json
                        $t = "$($j.msg)".Trim()
                        if ($t.Length -gt 600) { $t = $t.Substring(0,600) }
                        $msgs += [ordered]@{ date=$j.date; user=$j.user; cut=$cut; dept=$dept; msg=$t }
                    } catch {}
                }
            }
            # MOVIE は session(サブフォルダ)数のみ (高速)
            $mvd = Join-Path $d 'MOVIE'
            if (Test-Path $mvd) {
                $movieSessions += @(Get-ChildItem $mvd -Directory -ErrorAction SilentlyContinue).Count
            }
        }
    }
}
$out = [ordered]@{
    project = $proj; cut_dept_entries = $entries
    depts = $depts; artists = $artists; status_dist = $statusDist
    movie_sessions = $movieSessions; msg_count = $msgs.Count; msgs = $msgs
}
$dst = "$env:TEMP\legacy_$proj.json"
$out | ConvertTo-Json -Depth 5 -Compress | Out-File -Encoding UTF8 $dst
"proj=$proj entries=$entries msgs=$($msgs.Count) artists=$($artists.Count) -> $dst"
