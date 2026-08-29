#!/usr/bin/env python3
"""Casper セルフヘルス — トレースからの定点監視(Fable5 北極星 柱2)。

『人が張り付かなくても健全を保つ』最小構成。casper_trace.jsonl の各率を過去の頑健統計
(中央値 ± k·MAD)と比べ、逸脱を検知する。ML は使わない(それ自体が保守対象になる)。

出力2系統:
  ① vault/00_health/health.md … 日次ヘルス帯。RAG索引に載るので **Casper自身が『調子どう?』に答えられる**。
  ② 逸脱アラート … queue/casper_alerts.jsonl に追記(逸脱時のみ)。

監視する率(接地/アクションの健全性の代理指標):
  guarded_claim(既成事実化の打消)・abstained(棄権=上流障害の代理)・salvaged(ツール漏れ)・
  rag_zero(RAG空振り)。棄権率の急騰は Calendar 等 接地ソース異常の早期警報。
  ★率ものにはn_min=5を課す(cmd_518手当5)。n<5は0%/100%しか取れず率として無意味なため、
  emptyと同格のinsufficient(灰)とし判定しない。窓幅60分は据え置き——変えるのは下限。

gen_p95(生成時間)は cmd_518手当5 で★窓集計から外し、★イベント検知に切替えた。
  現行(窓p95)の弱点: 51.1秒という単発赤閾値が「冷間ロード実測51秒(17.3GBモデル初回ロード)」と
  ほぼ同値で、モデル揮発直後の一発目リクエストで必ず狼少年(誤検知)が再演する構造だった。
  新設計: health tickごとに前回tick以降の新規trace行のみを1件ごとに閾値判定する。
  ・単発120秒超 = red(SWITCH_GEN_TIMEOUTと同根拠・冷間ロード51秒を見切らぬ線)。
  ・51.1秒超が2件連続 = warn。
  警報はエッジ(初回超過)でのみ一度鳴らし、resolveは「その後の対話3件連続で閾値内」という
  ★件数条件で閉じる(時間で閉じると疎な利用下でresolveが出ないため)。
  前回処理位置(cursor)・連続カウンタは casper_health_state.json に持たせる。

CLI:
  python3 casper_health.py            # 監視を1回実行→health.md 更新＋逸脱表示
  python3 casper_health.py --show     # 現在の health.md を表示
"""
import datetime
import json
import os
import statistics
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TRACE = os.path.join(HERE, "casper_trace.jsonl")
HEALTH_DIR = os.path.join(HERE, "..", "vault", "00_health")
HEALTH_MD = os.path.join(HEALTH_DIR, "health.md")
ALERTS = os.path.join(HERE, "..", "..", "..", "queue", "casper_alerts.jsonl")
HEALTH_STATE = os.path.join(HERE, "casper_health_state.json")
K = 3.5                                                     # 逸脱閾値(中央値 ± K·MAD)
MIN_BASELINE_DAYS = 4                                       # これ未満はベースライン不足→逸脱判定せず観測のみ
WINDOW_MINUTES = 60                                         # cmd_516手当2: current側の窓幅。
RATE_N_MIN = 5                                              # cmd_518手当5: 率ものの最小サンプル数。
GEN_RED_SEC = 120.0                                         # cmd_518手当5: 単発赤(SWITCH_GEN_TIMEOUTと同根拠)。
GEN_WARN_SEC = 51.1                                         # cmd_518手当5: warn閾値(2件連続で成立)。
GEN_WARN_STREAK = 2                                         # 連続何件でwarnに切替わるか。
GEN_RESOLVE_STREAK = 3                                      # 連続何件が閾値内ならresolveとするか(件数条件)。
# ★軍師実測(subtask_516_strategy1): AC4(13:46赤→19:34緑)とAC5(08-20 15:05/18:45/21:49の
# 本物の悪化を捉える)の両方を満たす最も狭い窓として60分を採用。30分は過去14日15分毎で
# n=0が92.7%(観測不能が広すぎる)、120分は赤の検出率が現行(5.5%)に近づき窓を狭めた効果が
# 薄れる。利用頻度(本日実測=11turn/日)が変われば最適窓幅も変わりうる——数字でなく
# 「AC4とAC5の両方を満たす最も狭い窓」という考えを残す。turn数窓(直近N件)は不採用
# (疎な利用下では直近10〜50turnいずれも前日の760秒級を引きずり続けると実測済)。

# ── cmd_518手当9: probe心拍突合(将軍裁定・⚪窓の生死判別) ──
PROBE_LOG = os.path.join(HERE, "..", "..", "..", "queue", "casper_supervisor.log.failover")
PROBE_INTERVAL_SEC = 32                                     # casper_supervisor.sh FAILOVER_PROBE_EVERY(4)×8秒ループ実測値。
# ★「古い」の閾値=5分(=PROBE_INTERVAL_SEC×9.4回・約9〜10ティック分の猶予)。
# 根拠: 単発の一時的失敗(ネットワーク瞬断等)で即座に「死んでいる」と誤判定させぬための
# 猶予として、cmd_516手当1のcasper_failover.py NO_TRAFFIC_WINDOW_SEC(無通信窓=5分)と
# 同じ「5分」を採用——同一システム内で既に検証済みの時間単位を再利用し、当てずっぽうの
# 新閾値を増やさない(cmd_518手当5の教訓と同じ考え方)。60分健康窓(WINDOW_MINUTES)より
# 十分小さく、⚪(空窓)判定が出ている間により細かい粒度で生死を切り分けられる。
PROBE_STALE_SEC = 5 * 60

# ── cmd_518 残件③: 健康監視tick飢餓の是正(将軍案) ──
# chat_server.py._events_puller()の_tickカウンタはsupervisorのauto-reloadで
# chat_serverが再起動するたび0にリセットされる相対カウンタのため、reloadが
# 15分より短い間隔で起きると「_tick % 3 == 0」が一度も成立せず健康監視が
# 走らなくなる(2026-08-21 22:15以降の欠測が実例)。run()内部で絶対時刻の
# last_run_tsをcasper_health_state.jsonへ持たせ、reloadを跨いでも周期判定を
# 保つ。呼び出し側(_tick % 3 == 0)はそのまま残す——呼び出し頻度が上がっても
# ここで即returnするため実害はない。
HEALTH_RUN_INTERVAL_SEC = 15 * 60                           # casper_health.run()の想定発火周期(既存の_tick%3==0 ≈15分と同値)。


def _probe_last_success():
    """PROBE_LOGから直近のprobe-active結果を読み、最終probe成功(ok=true)の有無と
    ファイルの最終更新時刻(=直近tickの実行時刻の代理)を返す。
    ★probe側(casper_failover.py/casper_supervisor.sh)には一切触れず、既存ログの読取のみ。
    返り: {available(bool), last_success(bool|None), age_sec(float|None), reason(str)}。
    ・ファイルが無い/読めない → available=False(no silent caps・『probe情報なし』を正直に返す)。
    ・ファイルはあるが直近のprobe-active行のok値が判定できない → available=True, last_success=None。
    """
    if not os.path.exists(PROBE_LOG):
        return {"available": False, "last_success": None, "age_sec": None,
                "reason": "probeログ未存在(casper_supervisor未起動、または退避probe未実装区間)"}
    try:
        mtime = os.path.getmtime(PROBE_LOG)
        age_sec = max(0.0, datetime.datetime.now().timestamp() - mtime)
    except OSError as e:
        return {"available": False, "last_success": None, "age_sec": None,
                "reason": f"probeログ読取不能: {e}"}

    # 直近のprobe-active行を末尾から探す(probe-active出力は{"key":...,"ok":...,"ms":...,"state":...}の形。
    # decide出力は{"action":...}を持つため"action"キーの有無で区別する)。
    last_active_ok = None
    try:
        with open(PROBE_LOG, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        for ln in reversed(lines[-200:]):    # 直近200行のみ走査(1tickあたり3行程度・十分な範囲)
            ln = ln.strip()
            if not ln or not ln.startswith("{"):
                continue
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if "action" in rec:
                continue        # decide出力はスキップ(probe-active/homeの成否を見る)
            if "ok" in rec and "key" in rec:
                last_active_ok = bool(rec.get("ok"))
                break
    except OSError as e:
        return {"available": False, "last_success": None, "age_sec": None,
                "reason": f"probeログ読取不能: {e}"}

    if last_active_ok is None:
        return {"available": True, "last_success": None, "age_sec": age_sec,
                "reason": "直近probe行からok値を判定できず"}
    return {"available": True, "last_success": last_active_ok, "age_sec": age_sec,
            "reason": ""}


LAST_SKIPPED_SYNTHETIC = 0     # 直近の _load() が分母から外した合成トラフィックの件数(黙って落とさぬ)


def _load():
    """トレースを読む。★【殿御下命2026-08-29・丁】`synthetic: true` の行は**分母から外す**。

    2026-08-28、母艦の上で走らせた検証が本番を素手で撃ち、kiyotomo殿の発話を本人名義で再生した。
    その turn は health の率ものの母数に混ざり、rag_zero も棄権率も**人の実感と別の物**を測っていた。
    ★名札(synthetic)を立てただけでは消費者なきセンサーになる——外す側の配線をここに置く。
    ★黙って減らさぬ: 外した件数を LAST_SKIPPED_SYNTHETIC に残し health.md へ書く。
    ★既存行に synthetic キーは無い=Falsy=人として数える(従前の解釈のまま・遡って書き換えぬ)。
    """
    global LAST_SKIPPED_SYNTHETIC
    out, skipped = [], 0
    if os.path.exists(TRACE):
        for ln in open(TRACE, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("synthetic"):
                    skipped += 1
                    continue
                out.append(r)
    LAST_SKIPPED_SYNTHETIC = skipped
    return out


def _day(r):
    return str(r.get("ts", ""))[:10]


def _rates(rows):
    """1グループ(=1日 or 直近窓)の率を算出。gen_p95は含まぬ(cmd_518手当5でイベント検知へ分離)。
    ★n<RATE_N_MINの率は0%/100%しか取れず無意味なため、ここでは生のrateを返しつつ
    insufficient判定は呼び出し側(analyze)がnで行う(baseline側の日次集計はn_minを課さない
    ——1日分のnが少ない日が中央値・MAD計算から静かに消えると母集合が歪むため。n_minは
    current側の判定にのみ適用する・scope据え置き)。"""
    n = len(rows) or 1
    def rate(pred):
        return round(sum(1 for r in rows if pred(r)) / n, 3)
    return {"n": len(rows),
            "guarded_claim": rate(lambda r: r.get("guarded_claim")),
            "abstained": rate(lambda r: r.get("abstained")),
            "salvaged": rate(lambda r: r.get("salvaged")),
            "rag_zero": rate(lambda r: (r.get("rag_hits") or 0) == 0),
            "routed": rate(lambda r: r.get("routed"))}


def _mad(xs):
    """中央絶対偏差(頑健なばらつき)。"""
    if len(xs) < 2:
        return 0.0
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs]) or 0.0


def _ts(r):
    try:
        return datetime.datetime.fromisoformat(str(r.get("ts", "")))
    except Exception:
        return None


def _load_state():
    if os.path.exists(HEALTH_STATE):
        try:
            return json.load(open(HEALTH_STATE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state):
    # 原子書込(tmp+os.replace)。中断されても直前の正常な内容のまま(破損経路を塞ぐ)。
    d = os.path.dirname(HEALTH_STATE) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".chs_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, HEALTH_STATE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def scan_gen_events(rows, state):
    """cmd_518手当5: gen_secを窓集計でなくイベント検知で判定する。
    前回tick以降の新規行(cursor=前回処理済trace_idの集合の代わりに、処理済件数で代用
    ——trace.jsonlは追記のみでrows順序が安定しているため、処理済件数をcursorとする)を
    1件ごとに閾値判定し、二段(120秒単発red・51.1秒2件連続warn)・エッジ検知・
    件数条件resolveをstateに刻んで返す。
    返り: {status(ok/warn/red), streak_warn, streak_ok, new_events[], fired(bool)}。
    new_eventsは今回のtickで新規判定した各行の{ts, gen_sec, level}のログ(検証用)。
    ★cmd_518手当7是正(cmd_515手当3と同型の再演を断つ): state未作成(初回起動)時、
    cursorを0でなくlen(rows)へ寄せる。0初期化だと初回tickで過去trace全件を『今の異常』
    として発火させてしまう(alert_dispatch.py._load_stateと同じ設計を踏襲)。"""
    cursor = int(state.get("gen_cursor", len(rows)))
    new_rows = rows[cursor:]
    streak_warn = int(state.get("gen_streak_warn", 0))
    streak_ok = int(state.get("gen_streak_ok", 0))
    cur_level = state.get("gen_level", "ok")           # ok / warn / red
    fired_this_tick = []
    new_events = []

    for r in new_rows:
        gs = r.get("gen_sec", 0) or 0
        new_events.append({"ts": r.get("ts"), "gen_sec": gs})
        if gs > GEN_RED_SEC:
            # 単発赤: 即座にred。エッジ(前回redでなければ)発火。
            if cur_level != "red":
                fired_this_tick.append({"level": "red", "gen_sec": gs, "ts": r.get("ts")})
            cur_level = "red"
            streak_warn = 0
            streak_ok = 0
        elif gs > GEN_WARN_SEC:
            streak_warn += 1
            streak_ok = 0
            if streak_warn >= GEN_WARN_STREAK and cur_level == "ok":
                # 2件連続でwarnへ昇格。エッジ発火(okからwarnへ移る瞬間のみ)。
                fired_this_tick.append({"level": "warn", "gen_sec": gs, "ts": r.get("ts")})
                cur_level = "warn"
            elif cur_level == "red":
                pass                                    # redからは閾値内3件連続まで降りない
        else:
            streak_warn = 0
            if cur_level in ("warn", "red"):
                streak_ok += 1
                if streak_ok >= GEN_RESOLVE_STREAK:
                    cur_level = "ok"                     # ★件数条件でresolve(時間条件ではない)
                    streak_ok = 0
            else:
                streak_ok = 0

    state["gen_cursor"] = len(rows)
    state["gen_streak_warn"] = streak_warn
    state["gen_streak_ok"] = streak_ok
    state["gen_level"] = cur_level
    return {"status": cur_level, "streak_warn": streak_warn, "streak_ok": streak_ok,
            "fired": fired_this_tick, "new_events": new_events}


def scan_embed_health(state):
    """埋込機(bge-m3)の生死を health の窓へ載せる。三値: ok / down / unknown。

    ★病の型(2026-08-30に発見): センサー(casper_embed.embed_alive)は在ったが、
      結果を**記憶の中でしか**更新せず紙に残さなんだ。ゆえに落ちた刻も甦った刻も
      後から誰も辿れず、**消費者の居らぬセンサー**であった(この一年で五度目の型)。
      新しい届け先は作らぬ——既存の health → queue/casper_alerts.jsonl →
      alert_dispatch → 家老の inbox へ相乗りさせる。
    ★unknown を down とも ok とも名乗らせぬ: 「訊けなんだ」は「落ちておる」ではない
      (掟: 失敗とゼロを別の出口へ)。ゆえ metric を分ける(embed_down / embed_unknown)。
    ★呼ぶ側は窓が空(対話ゼロ)でも必ず通す——**対話の無い夜こそ死に気づけねばならぬ**。

    返り: {"status": ok|down|unknown, "reason": str, "changed": bool, "prev": str|None}
    """
    status, reason = "unknown", "casper_embed を読めなんだ"
    try:
        import casper_embed
        # ★観測路の関を使う(要求路 embed_alive ではない)。此処は背後の健診ゆえ、
        #   冷間の確認probe(長い)を撃ってよい——人の番を止めぬ。
        verdict, reason = casper_embed.embed_health_verdict()
        # ★cold は吠えぬ。冷間は事故でなく常態である(吠えれば狼少年)。
        status = {"ok": "ok", "cold": "ok", "down": "down"}.get(verdict, "unknown")
    except Exception as e:
        status, reason = "unknown", f"casper_embed を読めなんだ: {str(e)[:60]}"
    prev = state.get("embed_status")
    state["embed_status"] = status
    state["embed_reason"] = reason
    state["embed_checked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    if prev != status:
        state["embed_changed_at"] = state["embed_checked_at"]
    return {"status": status, "reason": reason, "changed": prev != status, "prev": prev}


def _embed_deviation(embed, n_window):
    """埋込の三値を deviations の1件へ写す(ok の時は何も出さぬ)。
    ★deviations は『いま超過中か』のスナップショット契約ゆえ、down の間は毎tick載せる。
      開始と復旧を一度ずつ知らせるのは受け取り側(alert_dispatch)の役目——二重に持たぬ。"""
    if embed["status"] == "ok":
        return None
    return {"metric": "embed_down" if embed["status"] == "down" else "embed_unknown",
            "current": embed["reason"], "threshold": "埋込は常に生きておること",
            "baseline_median": None, "n": n_window}


def analyze():
    """返り: {today, baseline_days, deviations[], today_rates, current_window, n, gen_event}。
    ★cmd_516手当2: current側は直近WINDOW_MINUTES分の純粋な時間窓(fallbackなし)。
    baseline側(hist_days・median・mad・K)は未変更(scope超過防止・AC-W5)。
    ★cmd_518手当5: gen_p95は本関数のdeviationsループから除外(イベント検知scan_gen_eventsへ
    分離)。率もの(guarded_claim等)はn<RATE_N_MINならinsufficient扱いとしdeviation判定しない
    (n_min=5・cmd_518手当5)。"""
    rows = _load()
    # ★埋込の生死は対話の有無と無関係に検める(対話の無い夜こそ死に気づけねばならぬ)。
    _st0 = _load_state()
    _embed0 = scan_embed_health(_st0)
    _save_state(_st0)
    if not rows:
        _dev0 = _embed_deviation(_embed0, 0)
        return {"today": None, "baseline_days": 0, "deviations": ([_dev0] if _dev0 else []),
                 "embed": _embed0, "today_rates": {},
                 "current_window": {"n": 0, "minutes": WINDOW_MINUTES, "status": "empty"},
                 "n": 0, "gen_event": {"status": "empty", "fired": [], "streak_warn": 0, "streak_ok": 0},
                 "synthetic_skipped": LAST_SKIPPED_SYNTHETIC,
                 "probe": _probe_last_success()}
    today = datetime.date.today().isoformat()
    by_day = {}
    for r in rows:
        by_day.setdefault(_day(r), []).append(r)
    hist_days = [d for d in by_day if d < today]

    now = datetime.datetime.now()
    window_rows = [r for r in rows if (ts := _ts(r)) is not None
                   and 0 <= (now - ts).total_seconds() <= WINDOW_MINUTES * 60]
    n_window = len(window_rows)
    tr = _rates(window_rows)
    rate_status = "ok" if n_window >= RATE_N_MIN else ("empty" if n_window == 0 else "insufficient")
    current_window = {"n": n_window, "minutes": WINDOW_MINUTES, "status": rate_status}

    deviations = []
    # ★軍師実測3の穴(n=0が静かに緑になる)への手当: 窓が空ならdeviationsを出さずempty明示。
    # ★cmd_518手当5(AC10): n<RATE_N_MINも同様にdeviation判定をせずinsufficient扱い。
    if rate_status == "ok" and len(hist_days) >= MIN_BASELINE_DAYS:
        for key in ("guarded_claim", "abstained", "salvaged", "rag_zero"):
            series = [_rates(by_day[d])[key] for d in sorted(hist_days)]
            med = statistics.median(series)
            mad = _mad(series)
            cur = tr[key]
            if mad > 0 and abs(cur - med) > K * mad and cur > med:      # 悪化方向のみ警報
                deviations.append({"metric": key, "current": cur, "baseline_median": round(med, 3),
                                   "threshold": round(med + K * mad, 3), "n": n_window})

    state = _load_state()
    gen_event = scan_gen_events(rows, state)
    _save_state(state)
    if gen_event["status"] in ("warn", "red"):
        threshold = GEN_RED_SEC if gen_event["status"] == "red" else GEN_WARN_SEC
        deviations.append({"metric": "gen_p95_event", "current": gen_event["status"],
                            "threshold": threshold, "baseline_median": None,
                            "streak_warn": gen_event["streak_warn"], "n": n_window})

    _dev_emb = _embed_deviation(_embed0, n_window)      # 埋込の生死(ok以外の時のみ載る)
    if _dev_emb:
        deviations.append(_dev_emb)

    # ★cmd_518手当9: probe突合はn_window==0(⚪)の時のみ意味を持つ(将軍処方の設計思想)。
    # 通常時(n>=5)はhealth.mdを無関係な情報で埋めぬためNoneのまま返す(AC-P5)。
    probe = _probe_last_success() if n_window == 0 else None

    return {"today": today, "baseline_days": len(hist_days), "deviations": deviations,
            "embed": _embed0,
            "today_rates": tr, "current_window": current_window, "n": len(rows),
            "gen_event": gen_event, "probe": probe,
            "synthetic_skipped": LAST_SKIPPED_SYNTHETIC}   # 【丁】分母から外した合成の件数(黙って減らさぬ)


def _fmt_probe_line(probe):
    """★cmd_518手当9: ⚪(空窓)の時のみ呼ばれる。probe心拍(利用者対話と無関係な唯一の脈)と
    突合し、『静かなだけ・無事』か『死んでいる』かを並べて示す(将軍処方の設計思想)。"""
    if probe is None:
        return None
    if not probe.get("available"):
        return f"- probe心拍: 情報なし（{probe.get('reason', '')}・生死は判定できぬ）"
    age = probe.get("age_sec")
    last_success = probe.get("last_success")
    age_str = f"{int(age)}秒前" if age is not None else "不明"
    if last_success is None:
        return (f"- probe心拍: 直近probe行の成否を判定できず（最終更新 {age_str}・"
                f"生死は判定できぬ）")
    if last_success and age is not None and age <= PROBE_STALE_SEC:
        return f"- probe心拍: 🟢 最終成功 {age_str}（静かなだけ・無事）"
    if last_success:
        return (f"- probe心拍: 🔴 最終成功は{age_str}（{PROBE_STALE_SEC}秒超・死んでいる可能性）")
    return f"- probe心拍: 🔴 直近probe失敗（最終更新 {age_str}・死んでいる可能性）"


def write_health_md(a):
    os.makedirs(HEALTH_DIR, exist_ok=True)
    tr = a.get("today_rates", {})
    cw = a.get("current_window", {})
    ge = a.get("gen_event", {})
    stamp = datetime.datetime.now().isoformat(timespec="minutes")
    if cw.get("status") == "empty":
        status = f"⚪ 直近{cw.get('minutes', WINDOW_MINUTES)}分に対話なし(測っておらぬ)"
    elif ge.get("status") == "red" or a.get("deviations"):
        status = "🔴 逸脱あり"
    elif ge.get("status") == "warn":
        status = "🟡 生成時間warn(51.1秒超が連続中)"
    elif cw.get("status") == "insufficient":
        status = f"🟡 率もの観測不足(n={cw.get('n', 0)}<{RATE_N_MIN}・insufficient)"
    elif a.get("baseline_days", 0) >= MIN_BASELINE_DAYS:
        status = "🟢 健全"
    else:
        status = "🟡 観測中(ベースライン蓄積中)"
    rate_line = "insufficient" if cw.get("status") == "insufficient" else None
    def fmt_rate(key, label, note=""):
        if rate_line:
            return f"- {label}: insufficient(n={tr.get('n', 0)}<{RATE_N_MIN}){note}"
        return f"- {label}: {tr.get(key, 0):.0%}{note}"
    lines = [f"# Casper セルフヘルス — {a.get('today')}",
             f"> 更新 {stamp} / 総トレース {a.get('n', 0)}件 / ベースライン {a.get('baseline_days', 0)}日分"
             f" / 直近{cw.get('minutes', WINDOW_MINUTES)}分窓 n={cw.get('n', 0)}"
             + (f" / 合成 {a.get('synthetic_skipped', 0)}件を分母から除外" if a.get("synthetic_skipped") else ""),
             "", f"## 状態: {status}", ""]
    if cw.get("status") == "empty":
        probe_line = _fmt_probe_line(a.get("probe"))
        if probe_line:
            lines.append(probe_line)
            lines.append("")
    lines += ["## 直近{}分の指標（対話 {} 件）".format(cw.get("minutes", WINDOW_MINUTES), tr.get("n", 0)),
             fmt_rate("guarded_claim", "既成事実化の打消 (guarded_claim)"),
             fmt_rate("abstained", "棄権率 (abstained)", "  ← 急騰は接地ソース(Calendar等)異常の代理指標"),
             fmt_rate("salvaged", "ツール漏れ掃除 (salvaged)"),
             fmt_rate("rag_zero", "RAG空振り (rag_zero)"),
             fmt_rate("routed", "先回り/ルーティング率 (routed)"),
             f"- 生成時間イベント検知 (gen_p95_event): {ge.get('status', 'ok')}"
             f"（連続warn {ge.get('streak_warn', 0)}件・連続okで解消まで {ge.get('streak_ok', 0)}/{GEN_RESOLVE_STREAK}）",
             ""]
    if a.get("deviations"):
        lines.append("## 🔴 逸脱（過去中央値から悪化 / イベント検知）")
        for d in a["deviations"]:
            if d["metric"] == "gen_p95_event":
                lines.append(f"- **gen_p95_event**: {d['current']}（連続warn {d.get('streak_warn', 0)}件・n={d.get('n', cw.get('n', 0))}）")
            elif d["metric"] in ("embed_down", "embed_unknown"):
                _label = "埋込機が落ちておる" if d["metric"] == "embed_down" else "埋込機の生死を確かめられなんだ"
                lines.append(f"- **{d['metric']}**: {_label}（{d['current']}）")
            else:
                lines.append(f"- **{d['metric']}**: 現在 {d['current']} > 閾値 {d['threshold']}"
                             f"（平常 {d['baseline_median']}・n={d.get('n', cw.get('n', 0))}）")
        lines.append("")
    open(HEALTH_MD, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return HEALTH_MD


def _alert(a):
    """逸脱を queue/casper_alerts.jsonl へ積む(受け手=alert_dispatch→家老の inbox)。

    ★2026-08-30 是正: 従前は「赤が無ければ一行も書かぬ」であった。だが受け手は
      **行のスナップショット**で『いま超過中か』を導くゆえ、収まった時に一行も来ねば
      『治った』を伝える術が無い(受け手は行の不在から復旧を推し量っており、
      それは**健診が止まっておる時まで吉報に化ける**危うい読みであった)。
      ゆえ**赤が収まった最初の一度だけ**、空の deviations を持つ行を積む。
      台帳は疎のまま(平時は一行も増えぬ)で、復旧だけが確と伝わる。"""
    fired = a.get("gen_event", {}).get("fired") or []
    _st = _load_state()
    _had = bool(_st.get("alert_had_deviations"))
    _now_red = bool(a.get("deviations")) or bool(fired)
    _st["alert_had_deviations"] = _now_red
    _save_state(_st)
    if not _now_red and not _had:
        return                                   # 平時は書かぬ(台帳を賑やかにせぬ)
    try:
        os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "source": "casper_health", "deviations": a["deviations"],
               "gen_event_fired": fired}
        with open(ALERTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _due_for_run(state):
    """last_run_tsを絶対時刻で判定し、前回実行から HEALTH_RUN_INTERVAL_SEC 未経過なら
    False(=何もせず即return)を返す。last_run_ts欄が無い/読めない場合(初回起動)は
    Trueとし即時実行する(reload直後に一度も走らないことを避けるための初回優先)。"""
    ts = state.get("last_run_ts")
    if not ts:
        return True
    try:
        last = datetime.datetime.fromisoformat(ts)
    except Exception:
        return True
    return (datetime.datetime.now() - last).total_seconds() >= HEALTH_RUN_INTERVAL_SEC


def run():
    state = _load_state()
    if not _due_for_run(state):
        return {"skipped": True, "reason": "last_run_tsから未経過(tick飢餓是正・cmd_518残件③)"}
    a = analyze()
    write_health_md(a)
    _alert(a)
    state = _load_state()
    state["last_run_ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save_state(state)
    return a


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--show":
        print(open(HEALTH_MD, encoding="utf-8").read() if os.path.exists(HEALTH_MD) else "(health.md 未生成)")
    else:
        a = run()
        if a.get("skipped"):            # tick未経過は「異常」ではない——率ものの鍵を引いて転ばぬ(2026-08-29)
            print(f"⏸ 見送り: {a.get('reason', '')}")
            sys.exit(0)
        cw = a.get("current_window", {})
        ge = a.get("gen_event", {})
        if cw.get("status") == "empty":
            st = f"⚪ 直近{cw.get('minutes', WINDOW_MINUTES)}分に対話なし(測っておらぬ)"
        elif ge.get("status") == "red" or a["deviations"]:
            st = "🔴 逸脱"
        elif ge.get("status") == "warn":
            st = "🟡 生成時間warn"
        elif cw.get("status") == "insufficient":
            st = f"🟡 insufficient(n<{RATE_N_MIN})"
        else:
            st = "🟢 健全" if a["baseline_days"] >= MIN_BASELINE_DAYS else "🟡 観測中"
        print(f"{st} / 直近{cw.get('minutes', WINDOW_MINUTES)}分窓 n={cw.get('n', 0)} / "
              f"ベースライン {a['baseline_days']}日 / 逸脱 {len(a['deviations'])}件 / "
              f"gen_event={ge.get('status', 'ok')}")
        for d in a["deviations"]:
            if d["metric"] == "gen_p95_event":
                print(f"  🔴 gen_p95_event: {d['current']} (連続warn={d.get('streak_warn', 0)})")
            elif d["metric"] in ("embed_down", "embed_unknown"):
                print(f"  {'🔴' if d['metric'] == 'embed_down' else '🟡'} {d['metric']}: {d['current']}")
            else:
                print(f"  🔴 {d['metric']}: {d['current']} > {d['threshold']} (n={d.get('n', 0)})")
        print(f"→ {HEALTH_MD}")
