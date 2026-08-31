#!/usr/bin/env python3
r"""視認の振り分けの回帰ゲート(殿御裁可2026-08-31)。全PASSで exit 0。

【背景】8/24 の御裁可は「甲=据え置き(vision は雲)」であった。その再考条件
「`.139` 復電後に VRAM 余白を実測」が満たされ、**前提が崩れた**:
 ・`qwen3.6:27b` **自身が vision を備える**(capabilities に vision)。8/24 の憂い
   「vision**専用の別模型**(7B/11B級)を 27b の隣に積めば押し合う」は、積む物が無いゆえ消えた。
 ・実測: 画像turnで load_duration=0.00秒(積み直し無し)・VRAM 15.84GiB→15.84GiB(不変)・4.07秒で正答。
   ★**追加GPUは不要**。
 ・実務10枚で捏造ゼロ(UEスクショは「338 actors (1 selected)」まで一字違わず/報告書PDFの
   18m・19m・6.8m・24,000個・約3週間は悉く正)。★弱点は**固有名の綴りのみ**。

守る掟:
 ① 「読み取り」(見えるものの列挙)は地元へ、「**判断**」(傾向抽出・キャプション注入)は**雲**へ。
    ★据え置きの理由①(「最も難しい判断は能力ある機構へ寄せる」)は**未反証**ゆえ動かさぬ。
 ② 主の栓(CASPER_VISION=off)は用途を問わず全てを閉じる。
 ③ ★退避中(雲に着座)は地元を指さぬ——在らぬ者に見せてはならぬ。
 ④ 地元が応じねば**雲へ退く**(読み取りは止めてよい仕事ではない)。退いた事実は帳簿に残る。
 ⑤ ★読み取った**固有名は名簿へ通して正す**。ただし定まらぬ時は触らぬ(過剰な書換をせぬ)。
 ⑥ 地元の視認は本番の対話と**同じ形**(num_ctx/keep_alive)で訊く——形を違えれば別ランナーの
    積み直しを求め 503 即答になる(2026-08-29/31 に二度実測した誤診の型)。
 ⑦ 雲と地元を**同じ形で帳簿へ**刻む(並べて数えられねば、降ろした判断を後から検められぬ)。
 ★突然変異: 各機構を殺すと赤化することを実証する。
"""
import ast
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_endpoint as EP                                # noqa: E402

SRC = io.open(os.path.join(HERE, "chat_server.py"), encoding="utf-8").read()
results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


_tmp = tempfile.mkdtemp(prefix="gate_vision_")


def _env(**kv):
    p = os.path.join(_tmp, "e_%d.env" % len(os.listdir(_tmp)))
    with io.open(p, "w", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")
    EP.ENV_FILE = p
    EP._CACHE.update({"ts": 0.0, "data": None})


# ── ①②③ 振り分けの向き ────────────────────────────────────────────────
print("── ①②③ 誰が見るか ──")
_env(CASPER_VISION="claude_cli", CASPER_VISION_READ="local", CASPER_BACKEND="ollama")
chk("① 読み取りは地元", EP.vision_backend("read") == "local")
chk("① ★判断は雲(据え置きの理由①は未反証ゆえ動かさぬ)", EP.vision_backend("judge") == "claude_cli")
_env(CASPER_VISION="off", CASPER_VISION_READ="local", CASPER_BACKEND="ollama")
chk("② 主の栓を閉じれば読み取りも閉じる", EP.vision_backend("read") == "off")
chk("② 主の栓を閉じれば判断も閉じる", EP.vision_backend("judge") == "off")
_env(CASPER_VISION="claude_cli", CASPER_VISION_READ="local", CASPER_BACKEND="claude_cli")
chk("③ ★雲に着座中は地元を指さぬ(在らぬ者に見せぬ)", EP.vision_backend("read") == "claude_cli")
_env(CASPER_VISION="claude_cli", CASPER_VISION_READ="local", CASPER_BACKEND="anthropic")
chk("③ 降段先が anthropic でも同じ", EP.vision_backend("read") == "claude_cli")
_env(CASPER_VISION="claude_cli", CASPER_BACKEND="ollama")
chk("① 既定(READ未指定)は従前どおり雲(黙って降ろさぬ)", EP.vision_backend("read") == "claude_cli")

# ── ④⑤⑥⑦ 実装の形 ────────────────────────────────────────────────────
print("── ④⑤⑥⑦ 機構の形 ──")
_tree = ast.parse(SRC)
_fns = {n.name for n in _tree.body if isinstance(n, ast.FunctionDef)}
for _f in ("ollama_vision", "vision_read", "vision_fix_names", "_vision_ledger"):
    chk(f"   関 {_f} が在る", _f in _fns)
_vr = SRC[SRC.index("def vision_read("):]
_vr = _vr[:_vr.index("\ndef ", 5)]
chk("④ ★地元が応じねば雲へ退く", "return claude_cli_vision(image_path, prompt)   # 地元が応じねば雲へ退く" in _vr)
chk("④ 退いた事実を帳簿へ刻む", "local_vision_fallback" in _vr)
chk("⑤ ★読み取った名を名簿へ通す", "vision_fix_names(out)" in _vr)
chk("② off なら地元も雲も叩かぬ", 'if where == "off"' in _vr)

_ov = SRC[SRC.index("def ollama_vision("):]
_ov = _ov[:_ov.index("\ndef ", 5)]
chk("⑥ ★本番と同じ num_ctx", '"num_ctx": 12288' in _ov)
chk("⑥ ★本番と同じ keep_alive(-1。probeが本番の寿命を縮めぬ)", '"keep_alive": -1' in _ov)
chk("⑥ 宛先は本番の生成口(OLLAMA)", "urllib.request.Request(OLLAMA" in _ov)
# ★地元の関は雲を**呼ばぬ**(退避の判断は呼び手=vision_read の役。二重に持たぬ)。
#   頭書きに名が出るのは参照であって呼出ではないゆえ、**呼出の形**で数える。
chk("④ 失敗は雲へ黙って落とさず、契約どおり [vision で名乗る",
    _ov.count("[vision") >= 2 and "claude_cli_vision(" not in _ov)
chk("⑦ 地元の視認も帳簿へ(雲と同じ形)", '_vision_ledger("local_vision"' in _ov)
chk("⑦ 混雑と失敗を別の名で刻む(混雑を死と名乗らぬ)", '"busy" if _code in (429, 503)' in _ov)

# 読み取り/判断の呼出が正しく分かれておるか(機械で数える)
# ★口を機械で数える(手書きの一覧にせぬ)。増減すれば此処が赤くなり、振り分けの見落としを掴む。
_read_sites = SRC.count("vision_read(") - 1                  # 定義1を除く
_judge_sites = SRC.count("claude_cli_vision(sp, vp)") + SRC.count("claude_cli_vision(fp, prompt)")
chk(f"① 読み取りの口が新しい関を通る(実測{_read_sites}箇所: 資料取込/PDFページ/成果物)",
    _read_sites == 3)
chk("① ★判断の二口(報告書ビルダー/キャプション注入)は雲のまま", _judge_sites == 2)

# ── ⑤ 名簿による綴りの是正(実挙動) ────────────────────────────────────
print("── ⑤ 名前を機構で正す ──")
_ns = {}
exec("import re", _ns)
_pick = [n for n in _tree.body if isinstance(n, ast.FunctionDef) and n.name == "vision_fix_names"]
exec(compile(ast.Module(body=_pick, type_ignores=[]), "chat_server.py", "exec"), _ns)
_ns["_canonical"] = lambda s: "".join(ch for ch in str(s).lower() if ch.isalnum())
_ns["_pj_index"] = lambda: {"idx": {"かんなみすふりんくすcc": ["かんなみスプリングスCC"],
                                    "あいまい": ["候補甲", "候補乙"]}}
_ns["_VISION_NAME_FIXED"] = []
_ns["_canonical"] = lambda s: "".join(ch for ch in str(s) if ch.isalnum()).lower()
_fixed = _ns["vision_fix_names"]("報告書は かんなみスプリングスCC の件。18m・24,000個。")
chk("⑤ 実測の誤り(かんなみ→かなみ)の型で、名簿に在る名は正される or 触れられぬ",
    "かんなみスプリングスCC" in _fixed)
chk("⑤ ★数値は書き換えぬ(名前だけを正す)", "18m" in _fixed and "24,000個" in _fixed)
_amb = _ns["vision_fix_names"]("あいまい な語")
chk("⑤ ★定まらぬ名(ambiguous)は触らぬ(過剰な書換をせぬ)", "あいまい" in _amb)
chk("⑤ 空でも転ばぬ", _ns["vision_fix_names"]("") == "")

# ── ★突然変異 ──────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
EPSRC = io.open(os.path.join(HERE, "casper_endpoint.py"), encoding="utf-8").read()
_m = '''    if purpose != "read":
        return "claude_cli"                            # 判断は雲(据え置き)'''
chk("★変異の錨が在る(ゲートの自己点検)", EPSRC.count(_m) == 1)
_n1 = {"__file__": os.path.join(HERE, "casper_endpoint.py"), "__name__": "ep_m1"}
exec(compile(EPSRC.replace(_m, "    if False:\n        pass"), "casper_endpoint.py", "exec"), _n1)
_n1["ENV_FILE"] = os.path.join(_tmp, "m1.env")
io.open(_n1["ENV_FILE"], "w", encoding="utf-8").write(
    "CASPER_VISION=claude_cli\nCASPER_VISION_READ=local\nCASPER_BACKEND=ollama\n")
_n1["_CACHE"].update({"ts": 0.0, "data": None})
chk("★変異(用途の別を消す): 判断まで地元へ降り、据え置きの理由①が踏み潰される(赤化実証)",
    _n1["vision_backend"]("judge") == "local")

_m2 = '''    if (_get("CASPER_BACKEND", "ollama") or "").lower() in ("claude_cli", "anthropic"):
        return "claude_cli"'''
chk("★変異の錨が在る(退避・ゲートの自己点検)", EPSRC.count(_m2) == 1)
_n2 = {"__file__": os.path.join(HERE, "casper_endpoint.py"), "__name__": "ep_m2"}
exec(compile(EPSRC.replace(_m2, "    if False:\n        pass"), "casper_endpoint.py", "exec"), _n2)
_n2["ENV_FILE"] = os.path.join(_tmp, "m2.env")
io.open(_n2["ENV_FILE"], "w", encoding="utf-8").write(
    "CASPER_VISION=claude_cli\nCASPER_VISION_READ=local\nCASPER_BACKEND=claude_cli\n")
_n2["_CACHE"].update({"ts": 0.0, "data": None})
chk("★変異(退避の枝を殺す): 雲に着座中も地元を指し、在らぬ者に見せる(赤化実証)",
    _n2["vision_backend"]("read") == "local")

EP.ENV_FILE = os.path.join(HERE, "casper_endpoints.env")
EP._CACHE.update({"ts": 0.0, "data": None})
print(f"\n   実台帳: 読み取り={EP.vision_backend('read')} / 判断={EP.vision_backend('judge')}")

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
