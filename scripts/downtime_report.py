#!/usr/bin/env python3
"""cmd_510第3便(軍師addendum設計・将軍下命への対応): 本番ダウンタイムの観測。

★新規ログは作らない(病五・唯一台帳の掟)。既存queue/casper_supervisor.logだけを読む純関数。
supervisor.logは既に「変更検知→auto-reload (停止 <pid>)」(停止時刻)と
「server launch pid=… sig=…」(起動時刻)を秒精度で持つ——その差がそのままダウンタイム窓である。

★reload起因(ロック未設置の編集保存によるreload=防げた事故)とdeath起因(bind失敗等の別因)を
必ず分けて集計する。混ぜれば「開発が壊した時間」が異常系に紛れて見えなくなる(軍師設計)。

★sigの同一性も記録する。直前launchと同一sigでの死亡は「書き込み途中を掴んで起動された」証跡
(2026-08-18 13:11-13:14事故の軍師分析)であり、機構が「編集中に起動された」ことを名指せる。

★replay corpus(AC6)・降車ログ(AC8)とは粒度が違うため混ぜない。週次観測報告ではこの二本
(replay=正しく動くか / downtime=動いていたか)を並べて出す設計とする。

Usage: python3 downtime_report.py [--since-hours N]
"""
import argparse
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
_SUPERVISOR_LOG = os.path.join(_REPO_ROOT, "queue", "casper_supervisor.log")

_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)$")
_STOP_RE = re.compile(r"変更検知→auto-reload \(停止 (\d+)\)")
_LAUNCH_RE = re.compile(r"server launch pid=(\d+) sig=(\d+)")
_DEATH_RE = re.compile(r"server死亡→再起動")


def _parse_line(line):
    m = _TS_RE.match(line.strip())
    if not m:
        return None, None
    ts_str, rest = m.group(1), m.group(2)
    try:
        ts = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None, None
    return ts, rest


def load(path=None):
    """★supervisor.logを読む唯一の関数。新規ログを足さず、既存の一行形式をそのまま読む。"""
    p = path or _SUPERVISOR_LOG
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().splitlines()
    except Exception:
        return []


def compute_windows(lines=None):
    """停止〜launchの窓を列挙する。reload起因/death起因を分け、同一sig死亡も記録する。
    戻り値: {"windows":[{stop_ts,launch_ts,seconds,reason,pid,same_sig_as_prior_launch}...],
             "reload_n","reload_sec","death_n","death_sec"}"""
    rows = load() if lines is None else lines
    events = []
    last_launch_sig = None
    last_launch_ts = None
    pending_stop = None
    pending_reason = None
    for ln in rows:
        ts, rest = _parse_line(ln)
        if ts is None:
            continue
        m_stop = _STOP_RE.search(rest)
        m_launch = _LAUNCH_RE.search(rest)
        m_death = _DEATH_RE.search(rest)
        if m_stop:
            pending_stop = ts
            pending_reason = "reload"
        elif m_death:
            # death行はstop行を伴わない(即死検知の別経路)。直前launch時刻を停止起点とみなす
            # (プロセスは直前launchからこのdeath行の時刻までのどこかで死んでいた=下限の見積り)。
            pending_stop = last_launch_ts
            pending_reason = "death"
        elif m_launch:
            launch_ts = ts
            pid, sig = m_launch.group(1), m_launch.group(2)
            same_sig = (sig == last_launch_sig)
            if pending_stop is not None:
                seconds = (launch_ts - pending_stop).total_seconds()
                events.append({"stop_ts": pending_stop.isoformat(), "launch_ts": launch_ts.isoformat(),
                               "seconds": seconds, "reason": pending_reason or "reload",
                               "pid": pid, "same_sig_as_prior_launch": same_sig})
            last_launch_ts = launch_ts
            pending_stop = None
            pending_reason = None
            last_launch_sig = sig
    reload_events = [e for e in events if e["reason"] == "reload"]
    death_events = [e for e in events if e["reason"] == "death"]
    return {
        "windows": events,
        "reload_n": len(reload_events), "reload_sec": round(sum(e["seconds"] for e in reload_events), 1),
        "death_n": len(death_events), "death_sec": round(sum(e["seconds"] for e in death_events), 1),
        "same_sig_deaths": sum(1 for e in death_events if e["same_sig_as_prior_launch"]),
    }


def print_report(result):
    print(f"downtime report: reload起因 {result['reload_n']}回 合計{result['reload_sec']}秒 / "
          f"death起因 {result['death_n']}回 合計{result['death_sec']}秒")
    if result["same_sig_deaths"]:
        print(f"  ⚠ 同一sigでの死亡 {result['same_sig_deaths']}件"
              "(書き込み途中を掴んで起動された=編集中に起動された証跡)")
    for e in result["windows"]:
        print(f"  [{e['reason']}] {e['stop_ts']} -> {e['launch_ts']} ({e['seconds']}秒) pid={e['pid']}"
              + ("  [同一sig]" if e["same_sig_as_prior_launch"] else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-hours", type=float, default=None)
    args = ap.parse_args()
    result = compute_windows()
    print_report(result)


if __name__ == "__main__":
    main()
