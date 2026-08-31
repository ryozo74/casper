#!/usr/bin/env python3
r"""内部機構の名乗り(合鍵)と、持ち主なきカードの門の回帰ゲート
(殿御下命2026-08-29・Fable処方「丙」)。全PASSで exit 0。

実測(2026-08-28): kiyotomo殿の「承認ボタンがでてこない」を私は孤児カードのせいと誤診した。
実際の下手人は別(表を焼く取り直し)であったが、掘り返す途中で**もう一つの病**が露わになった——
 ・母艦の上で走らせた検証が `X-Actor-User-Id: 31` を付けて本番を撃ち、
   kiyotomo殿の発話が一字一句そのまま**本人名義で**再生されていた(苦情文まで)。
 ・identify() が `client_address が 127.0.0.1 なら無条件に信ずる` 造りゆえ、
   母艦の上の何者でも任意の uid に成りすませ、しかも**観測にはただの利用者として映る**。
 ・その再生が uid 空のまま aurora_append のカードを6枚立て、いずれも孤児のまま残った。

守る掟:
 ① loopback はいかなる権威も与えぬ。**合鍵(X-Casper-Host-Secret)の一致のみ**が内部機構を名乗れる。
 ② 合鍵は機構が自ら用意する(不在なら生成し 0600 で永続化)。
    ★でなければ正当なハーネスが黙って匿名へ落ち、それは「失敗とゼロを同じ出口へ流す」型になる。
 ③ 主体には**名札**が付く(actor_origin: host / jwt / anon、synthetic: 真偽)。合成を人と同じ分母で数えぬ為。
 ④ 持ち主(uid)の無い承認カードは立てぬ。台帳の照会は悉く uid で引く=届かぬカードだからである。
    ★道具名で場合分けはせぬ(語彙表の穴)。「持ち主が無い」という台帳の性質で断つ。
 ⑤ 弾く時は**必ず理由と逃げ道**を。「中身が足りぬ」と言えば理由の取り違えになる(2026-08-28の教訓)。
 ★突然変異: 各機構を殺すと赤化することを実証する。
"""
import ast
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_synthetic; gate_synthetic.shield(isolate=False)   # ★2026-08-31: 実呼出を合成と名乗る(消費者が分母から外せるように)
import casper_secrets                                   # noqa: E402

SRC = os.path.join(HERE, "chat_server.py")
SRC_TEXT = open(SRC, encoding="utf-8").read()

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


WANT_F = ["_host_trusted", "identify", "_register_pending",
          "_guard_card_promise", "_resolve_send_mentions", "_send_mention_line_hit",
          # 台帳の口は接地の注記を足す(本体の検めは gate_aurora_grounding.py)
          "aurora_grounding_note", "aurora_material_recall", "aurora_ungrounded_facts",
          "aurora_fact_tokens", "_aurora_body_key"]
WANT_PREFIX = ("_CARD_PROMISE", "_SEND_MENTION", "_SEND_HELD", "_DM_BODY_INCOMPLETE",
               "_NO_ACTOR", "_DM_QUOTED", "PENDING_ACTIONS", "_EMAIL_UID_CACHE",
               "_FACT_", "_AURORA_MATERIAL")

_LEDGER_DIR = tempfile.mkdtemp(prefix="gate_host_trust_")


class _OB:
    """承認台帳の身代わり。★本物と同じく **uid で引く**(持ち主の無いカードは誰の画面にも出ぬ)。"""
    rows = []

    @staticmethod
    def propose(tool, args, uid, summary, thread=None, origin="user", query=None, trace_id=None,
                grounding=None):        # grounding: 接地の証跡(gate_aurora_grounding.py が本体を検める)
        rec = {"id": "pid%d" % (len(_OB.rows) + 1), "tool": tool, "uid": str(uid or ""), "ts": "t"}
        _OB.rows.append(rec)
        return rec

    @staticmethod
    def pending(uid=None):
        return [r for r in _OB.rows if str(r.get("uid")) == str(uid) and str(uid or "").strip()]


class Hdr(dict):
    def get(self, k, d=""):                              # BaseHTTPRequestHandler.headers と同じ契約
        return dict.get(self, k, d)


class Handler:
    def __init__(self, headers=None, ip="127.0.0.1"):
        self.headers = Hdr(headers or {})
        self.client_address = (ip, 51234)


def build(src_text):
    tree = ast.parse(src_text)
    picked, seen = [], set()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in WANT_F:
            picked.append(n); seen.add(n.name)
        if isinstance(n, ast.Assign):
            t = getattr(n.targets[0], "id", "")
            if t.startswith(WANT_PREFIX):
                picked.append(n); seen.add(t)
    missing = [w for w in WANT_F if w not in seen]
    if missing:
        return None, missing
    M = {}
    exec("import re, os, json, uuid, datetime, http.cookies, threading", M)
    exec(compile(ast.Module(body=picked, type_ignores=[]), SRC, "exec"), M)
    M["_casper_secrets"] = casper_secrets                # 本物の合鍵機構を挿す(身代わりにせぬ)
    M["casper_outbox"] = _OB
    M["HERE"] = _LEDGER_DIR
    M["PENDING_ACTIONS"] = {}
    M["_verify_score_token"] = lambda tok: None          # JWT経路は本ゲートの検分対象外
    M["_email_to_uid"] = lambda em: ""
    return M, []


M, missing = build(SRC_TEXT)
if missing:
    print(f"❌ chat_server.py に機構が見当たらぬ: {missing}")
    sys.exit(1)

SECRET = casper_secrets.host_secret()

# ── ① loopback はもはや権威を与えぬ ─────────────────────────────────────
print("── ① 名乗りは合鍵のみ ──")
chk("① loopback + 合鍵なし → 内部機構と認めぬ",
    M["_host_trusted"](Handler(ip="127.0.0.1")) is False)
chk("① loopback + 誤った合鍵 → 認めぬ",
    M["_host_trusted"](Handler({"X-Casper-Host-Secret": "deadbeef"})) is False)
chk("① loopback + 空の合鍵 → 認めぬ",
    M["_host_trusted"](Handler({"X-Casper-Host-Secret": ""})) is False)
chk("① 正しい合鍵 → 内部機構と認める",
    M["_host_trusted"](Handler({"X-Casper-Host-Secret": SECRET})) is True)
chk("① LAN の第三者が正しい合鍵を持てば認める(経路でなく鍵で判ずる)",
    M["_host_trusted"](Handler({"X-Casper-Host-Secret": SECRET}, ip="192.168.44.77")) is True)

# ── ② 成りすましの道が塞がっている ───────────────────────────────────────
print("── ② X-Actor-User-Id の成りすまし ──")
who = M["identify"](Handler({"X-Actor-User-Id": "31"}, ip="127.0.0.1"))
chk("② ★母艦の上から uid=31 を名乗っても本人にならぬ(8/28の実害)", who["uid"] == "")
chk("② 認証済とも名乗らぬ", who["authed"] is False)
chk("② 名札は anon", who["actor_origin"] == "anon")
chk("② 合成とも数えぬ(合鍵が無いのだから内部機構ですらない)", who["synthetic"] is False)

who2 = M["identify"](Handler({"X-Actor-User-Id": "31", "X-Casper-Host-Secret": SECRET}))
chk("② 合鍵を提げた内部機構は名乗れる(ハーネスが黙って匿名へ落ちぬ)", who2["uid"] == "31")
chk("② その名札は host", who2["actor_origin"] == "host")
chk("② ★合成の名札が立つ(人の発話と同じ分母で数えぬ為)", who2["synthetic"] is True)

who3 = M["identify"](Handler({"X-Actor-User-Id": "28"}, ip="192.168.44.77"))
chk("② LAN の第三者が殿(uid=28)を騙れぬ", who3["uid"] == "" and who3["authed"] is False)

# ── ③ 合鍵は機構が自ら用意する ───────────────────────────────────────────
print("── ③ 合鍵の在処 ──")
chk("③ 起動時に確定する呼びが chat_server に在る", "_casper_secrets.host_secret()" in SRC_TEXT)
chk("③ 冪等(呼ぶ度に変わらぬ)", casper_secrets.host_secret() == SECRET)
chk("③ 長さが十分(32バイト=64桁)", len(SECRET) >= 64)
_env = os.path.join(os.path.expanduser("~"), ".config", "casper", "secrets.env")
chk("③ 0600 で永続化されている", os.path.exists(_env) and (os.stat(_env).st_mode & 0o777) == 0o600)
chk("③ 空の提示は常に偽", casper_secrets.host_secret_matches("") is False)

# ── ④ 持ち主なきカードは立てぬ ───────────────────────────────────────────
print("── ④ 孤児カードの門 ──")
_OB.rows = []
_led = os.path.join(_LEDGER_DIR, "casper_orphan_card.jsonl")
pid_none = M["_register_pending"]("aurora_append", {"doc_id": "d78e9ca6", "body": "本文"}, "",
                                  "Aurora修正", query="2. BOKAN 担当事項に UE を追加して")
chk("④ uid 空では承認カードが立たぬ", pid_none is None)
chk("④ 台帳にも積まぬ(届かぬ物を『立てました』と数えぬ)", _OB.rows == [])
_lines = open(_led, encoding="utf-8").read().splitlines() if os.path.exists(_led) else []
chk("④ 黙って落とさず刻む(数えられる)", len(_lines) == 1 and '"blocked": true' in _lines[0])
pid_ok = M["_register_pending"]("aurora_append", {"doc_id": "d78e9ca6", "body": "本文"}, "31", "Aurora修正")
chk("④ 持ち主が在れば従前どおり立つ(過剰阻止せぬ)", pid_ok == "pid1")
chk("④ 立ったカードは持ち主の照会で引ける", len(_OB.pending("31")) == 1)
chk("④ 道具名の表で場合分けしておらぬ(語彙表の穴を作らぬ)",
    M["_register_pending"]("project_import", {"x": 1}, "", "起票") is None)

# ── ⑤ 弾く時は理由と逃げ道 ───────────────────────────────────────────────
print("── ⑤ 理由と逃げ道 ──")
_OB.rows = []
PROMISE = "承認ボタンを押すと Aurora に保存されます。"
out_anon = M["_guard_card_promise"](PROMISE, [], uid="")
chk("⑤ 名乗り無き時は『名乗りが確かめられぬ』と申す", "名乗り" in out_anon)
chk("⑤ 逃げ道(ログイン)を示す", "ログイン" in out_anon)
chk("⑤ ★理由を取り違えぬ(『中身が足りぬ』とは言わぬ)", "中身がまだ入っておりませぬ" not in out_anon)
chk("⑤ 出る嘘は消えている", "承認ボタンを押すと Aurora に保存されます" not in out_anon)
out_known = M["_guard_card_promise"](PROMISE, [], uid="31")
chk("⑤ 名乗りが在る時は従前の案内のまま(過剰発動せぬ)", "承認カードは出ておりませぬ" in out_known)

held = ["DMをお送りしました。"]
snt_anon = M["_resolve_send_mentions"]("承知いたした。\nDMをお送りしました。", held, [], uid="")
chk("⑤ 送信言及の差替も理由を取り違えぬ",
    "名乗り" in snt_anon and "中身がまだ入っておりませぬ" not in snt_anon)
snt_known = M["_resolve_send_mentions"]("承知いたした。\nDMをお送りしました。", held, [], uid="31")
chk("⑤ 名乗りが在り中身が無い時は従前の文言のまま",
    "中身がまだ入っておりませぬ" in snt_known)

# ── ⑥ 結線 ───────────────────────────────────────────────────────────────
print("── ⑥ 結線 ──")
chk("⑥ chat_server に loopback を信ずる文字列が残っておらぬ",
    "_origin_ok" not in SRC_TEXT and '"127.0.0.1", "::1", "localhost"' not in SRC_TEXT)
chk("⑥ 自律投稿(/api/thread/post)も同じ関を通す",
    "_trusted = _host_trusted(self)" in SRC_TEXT)
chk("⑥ identify も同じ関を通す(判定が二本に割れておらぬ)",
    SRC_TEXT.count("_host_trusted(") >= 3)
for h in ["casper_eval.py", "gate_notify_delivery.py", "gate_upload_binary_roundtrip.py",
          os.path.join("eval", "run_holdout.py")]:
    _p = os.path.join(HERE, h)
    _t = open(_p, encoding="utf-8").read() if os.path.exists(_p) else ""
    chk(f"⑥ ハーネス {h} が合鍵を提げる",
        "X-Casper-Host-Secret" in _t and "host_secret()" in _t)

# ── ⑦ 名札の消費者(合成を人と同じ分母で数えぬ) ───────────────────────────
print("── ⑦ 名札の消費者 ──")
import json as _json                                     # noqa: E402
import casper_health as _health                          # noqa: E402

chk("⑦ 会話台帳に名札を刻む", '"actor_origin": who.get("actor_origin", "")' in SRC_TEXT
    and '"synthetic": bool(who.get("synthetic"))' in SRC_TEXT)
chk("⑦ トレースにも名札を載せる", '"synthetic": bool(synthetic), "actor_origin": actor_origin' in SRC_TEXT)
chk("⑦ 呼出側が who の名札を渡している(既定値のまま素通りせぬ)",
    'synthetic=bool(who.get("synthetic")), actor_origin=who.get("actor_origin", "")' in SRC_TEXT)

_tmp_trace = os.path.join(_LEDGER_DIR, "trace.jsonl")
_rows = ([{"ts": "2026-08-29T03:00:00", "rag_hits": 3, "gen_sec": 1.0}] * 4 +
         [{"ts": "2026-08-29T03:00:00", "rag_hits": 0, "gen_sec": 1.0, "synthetic": True}] * 6)
with open(_tmp_trace, "w", encoding="utf-8") as _f:
    for _r in _rows:
        _f.write(_json.dumps(_r) + "\n")
_orig_trace = _health.TRACE
_health.TRACE = _tmp_trace
_loaded = _health._load()
chk("⑦ ★合成は分母から外れる(人 4 / 合成 6 → 4件)", len(_loaded) == 4)
chk("⑦ 外した件数を数えている(黙って減らさぬ)", _health.LAST_SKIPPED_SYNTHETIC == 6)
chk("⑦ ★rag_zero が合成に汚されぬ(混ざれば 60%・外せば 0%)",
    _health._rates(_loaded)["rag_zero"] == 0.0)
chk("⑦ 名札の無い旧行は人として数える(遡って解釈を変えぬ)",
    len([r for r in _loaded if not r.get("synthetic")]) == 4)
chk("⑦ health.md に除外件数が現れる", "分母から除外" in open(
    os.path.join(HERE, "casper_health.py"), encoding="utf-8").read())
_health.TRACE = _orig_trace

# ── ★突然変異 ────────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
_old_trust = '''    if _casper_secrets is None:
        return False                                   # 合鍵の機構が無い＝内部経路も無い(fail-closed)
    try:
        return _casper_secrets.host_secret_matches(handler.headers.get("X-Casper-Host-Secret", ""))
    except Exception:
        return False'''
assert SRC_TEXT.count(_old_trust) == 1, "変異が当たっていない(ゲートの自己点検)"
mut = SRC_TEXT.replace(_old_trust, '''    _cip = handler.client_address[0] if getattr(handler, "client_address", None) else ""
    return _cip in ("127.0.0.1", "::1", "localhost")''')
M2, _ = build(mut)
chk("★変異(loopback信頼を戻す): 母艦の上から uid=31 に成りすませてしまう(赤化実証)",
    M2["identify"](Handler({"X-Actor-User-Id": "31"}))["uid"] == "31")

_old_block = '''        except Exception:
            pass
        return None
    # ★接地の注記は**この一点**で足す'''
assert SRC_TEXT.count(_old_block) == 1, "変異が当たっていない(ゲートの自己点検)"
mut2 = SRC_TEXT.replace(_old_block, '''        except Exception:
            pass
    # ★接地の注記は**この一点**で足す''')
M3, _ = build(mut2)
_OB.rows = []
chk("★変異(孤児の門を殺す): 持ち主なきカードが立ってしまう(赤化実証)",
    M3["_register_pending"]("aurora_append", {"body": "本文"}, "", "Aurora修正") is not None)

_old_reason = '''    if not str(uid or "").strip():
        note = _NO_ACTOR_MSG          # 【丙】uid 無しでは台帳に持ち主が無く、カードは誰の画面へも出ぬ'''
assert SRC_TEXT.count(_old_reason) == 1, "変異が当たっていない(ゲートの自己点検)"
mut3 = SRC_TEXT.replace(_old_reason, '''    if False:
        note = _NO_ACTOR_MSG''')
M4, _ = build(mut3)
_OB.rows = []
chk("★変異(理由の枝を殺す): 名乗り無き者に理由を告げなくなる(赤化実証)",
    "名乗り" not in M4["_guard_card_promise"](PROMISE, [], uid=""))

_HEALTH_SRC = open(os.path.join(HERE, "casper_health.py"), encoding="utf-8").read()
_old_skip = """                if r.get("synthetic"):
                    skipped += 1
                    continue
"""
assert _HEALTH_SRC.count(_old_skip) == 1, "変異が当たっていない(ゲートの自己点検)"
_mutH = _HEALTH_SRC.replace(_old_skip, "")
_MH = {"__file__": os.path.join(HERE, "casper_health.py"), "__name__": "_mut_health"}
exec(compile(_mutH.replace('if __name__ == "__main__":', 'if False:'),
             os.path.join(HERE, "casper_health.py"), "exec"), _MH)
_MH["TRACE"] = _tmp_trace
chk("★変異(合成の除外を殺す): 合成が分母に混ざり rag_zero が 60% に化ける(赤化実証)",
    _MH["_rates"](_MH["_load"]())["rag_zero"] == 0.6)

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
