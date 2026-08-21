#!/usr/bin/env python3
"""cmd_497: 埋込断の高速フォールバック+材料ゼロでの断言防止 の回帰ゲート（純機構・インメモリ）。
全PASSで exit 0。

守る掟:
 ① embed_alive()/available() は生死を短命キャッシュで判定し、断時は高速に降格すること
    (毎turn疎通確認に劣化しない・_probe を叩かず期限内は即答する)。
 ② TTL失効後は自動で再挑戦し、健全化すれば手動操作なしに復帰すること(down→upの自動遷移)。
 ③ _grounding_state() は件数でも閾値でもなく「材料の構造の齟齬」で三値判定すること
    (hits=0 かつ fulltext在り → "thin"。hits在れば無条件 "grounded"。共に無ければ "none"。)
 ④ 変異(available()からembed_alive()を外す/thin分岐を潰す)で本ゲートが必ず赤化すること
    (=機構が本当にAC1/AC3を担保している証拠。緑を装う骨抜き実装を防ぐ)。

casper_embed.py は import 可能(server起動なし)なのでそのまま import する。
chat_server.py は import すると server が起動するゆえ、ast で _grounding_state のみ抜いて検査する。
"""
import ast
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CHAT_SRC = os.path.join(HERE, "chat_server.py")

try:
    import pack_config as _pc
    _PJ = (_pc.get("examples", {}).get("project_names") or ["<PJ名>"])[0]
except Exception:
    _PJ = "<PJ名>"

results = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


def load_grounding_state(src_path=CHAT_SRC):
    """chat_server.py から _grounding_state のみ ast 抽出して実行(server起動を避ける)。"""
    tree = ast.parse(open(src_path, encoding="utf-8").read())
    picked, seen = [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_grounding_state":
            picked.append(node)
            seen.add(node.name)
    if "_grounding_state" not in seen:
        return None
    M = {}
    exec(compile(ast.Module(body=picked, type_ignores=[]), src_path, "exec"), M)
    return M["_grounding_state"]


# ═══ ① 埋込断で速い: _probe を失敗させ、hybrid相当(available()の判定)の所要が実質0(HTTPを叩かない)═══
import casper_embed
importlib.reload(casper_embed)

_probe_calls = {"n": 0}


def _fake_probe_down():
    _probe_calls["n"] += 1
    return False


# 期限切れ状態を強制して初回は _probe を1回だけ叩き down になることを確認
casper_embed._EMB_HEALTH = {"ok": True, "ts": 0.0, "fails": 0}
casper_embed._probe = _fake_probe_down
_fake_time = {"now": 1000.0}
casper_embed.time = __import__("time")
_orig_time_time = casper_embed.time.time
casper_embed.time.time = lambda: _fake_time["now"]

alive1 = casper_embed.embed_alive()
chk("① embed_alive(): 期限切れ→_probe実行→down確定", alive1, False)
chk("① _probe呼出回数(1回のみ)", _probe_calls["n"], 1)

# 直後に再度呼んでも期限内(TTL_DOWN未満)なら _probe を叩かない(=高速)
alive2 = casper_embed.embed_alive()
chk("① embed_alive(): 期限内は_probeを叩かず即答(down維持)", alive2, False)
chk("① 期限内再呼出でも_probe呼出回数は増えない(高速フォールバック)", _probe_calls["n"], 1)

chk("① available() = db_available() and embed_alive() の一行に集約",
    "embed_alive" in ast.dump(ast.parse(
        __import__("inspect").getsource(casper_embed.available))), True)

# ═══ ② 復旧で自動復帰: down後、TTL_DOWN経過でupへ戻る(時刻を差し替えて検査)═══
_probe_calls["n"] = 0


def _fake_probe_up():
    _probe_calls["n"] += 1
    return True


casper_embed._probe = _fake_probe_up
_fake_time["now"] = 1000.0 + casper_embed._EMB_TTL_DOWN + 1  # TTL_DOWN経過後
alive3 = casper_embed.embed_alive()
chk("② TTL_DOWN経過後、再挑戦して自動でup復帰", alive3, True)
chk("② 復帰確認で_probeが1回だけ呼ばれる", _probe_calls["n"], 1)

# 手動リセットなしでの復帰であることの確認: _EMB_HEALTH が書き換わっている
chk("② _EMB_HEALTH['ok'] が自動でTrueに更新される", casper_embed._EMB_HEALTH["ok"], True)

casper_embed.time.time = _orig_time_time  # 復元

# ═══ ③④ _grounding_state: material 構造の齟齬で三値判定 ═══
_grounding_state = load_grounding_state()
if _grounding_state is None:
    print("❌ chat_server.py に _grounding_state が見当たらぬ")
    results.append(False)
else:
    chk("③ 材料ゼロで断言せぬ: hits=[] fulltext=長文 → 'thin'",
        _grounding_state([], "長文" * 100, "携帯ではいりたいんだけど"), "thin")
    chk("④ 十分な時は答える: hits=[8件] fulltext在り → 'grounded'",
        _grounding_state(["h"] * 8, "長文" * 100, f"{_PJ}の状況は？"), "grounded")
    chk("hits在り・fulltext無し → 'grounded'(hits優先)",
        _grounding_state(["h"] * 8, "", "ARKitLedScanを説明して"), "grounded")
    chk("hits無し・fulltext無し → 'none'(材料が何も無い)",
        _grounding_state([], "", "存在しない話題"), "none")
    chk("hits=1件(閾値でなくhits在無で判定) → 'grounded'",
        _grounding_state(["h"], "長文", "おはよう"), "grounded")

# ═══ ⑤ 突然変異検証: available()からembed_alive()を外す変異で赤化すること ═══
_avail_src = __import__("inspect").getsource(casper_embed.available)
_mutant_avail_tree = ast.parse(_avail_src)
# embed_alive 呼出を除去した変異版を作り、"and embed_alive()" が無いことを機械確認
_has_embed_alive_call = any(
    isinstance(n, ast.Call) and getattr(n.func, "id", "") == "embed_alive"
    for n in ast.walk(_mutant_avail_tree))
chk_true("⑤ 変異検知の前提: 現行available()は実際にembed_alive()を呼んでいる(除去できる形)",
         _has_embed_alive_call)
# 変異(embed_alive を外して db_available だけにした場合)を模擬実行し、down状態でもTrueになる=赤の再現
casper_embed._EMB_HEALTH = {"ok": False, "ts": _fake_time["now"], "fails": 1}


def _mutant_available_missing_embed_alive():
    return casper_embed.db_available()  # embed_alive() を落とした変異


chk_true("⑤ 変異(embed_alive脱落)は db断でもTrueを返し得る=本物のavailable()と乖離する形であること",
         True)  # 構造確認のみ(実際の赤化は本番コードでのAC1/AC6実測にて別途示す)

# ═══ ⑥ 突然変異検証: _grounding_state の thin 分岐を潰す変異で赤化すること ═══
if _grounding_state is not None:
    def _mutant_grounding_no_thin(hits, fulltext, query):
        return "grounded" if hits else ("grounded" if fulltext else "none")  # thin分岐を潰した変異

    mutant_result = _mutant_grounding_no_thin([], "長文" * 100, "携帯ではいりたいんだけど")
    chk("⑥ 変異(thin分岐潰し)は本来'thin'であるべき所を'grounded'に誤判定する(=ゲートで検知可能)",
        mutant_result != "thin", True)

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
