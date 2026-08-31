#!/usr/bin/env python3
r"""禁足席と宛先解決の回帰ゲート(2026-08-31・Fable診断 急所1/2)。全PASSで exit 0。

【病(実測)】殿の御下命「z8a(.119)には座らぬ」は**退避先の選定でしか検問されておらず**、
呼出の瞬間に検める者が一人も居なかった。台帳が正でも、読まぬ者には効かぬ:
 ・`distill_activity.py` は `CASPER_ENDPOINT`——**env台帳に存在せぬ鍵名**を読み、既定が
   .119 に焼き付いていた。日次の distill は必ず禁足席へ 27b を撃っていた
   (実測 08-31 13:36 に .119 へ88.5秒の呼出/同刻 .119 に 27b が17.1GiB在席)。
 ・`gate_aurora_save_smoke.py` も宛先を焼き付けており、**全門を走らせるたび禁足席へ
   27b の実呼出を9発**撃っていた(将軍実測: 本日だけで四度)。
 ・cron や gate の一発物は env を source せぬため、焼き付き既定は必ずいつか実運用へ漏れる。

【殿の御裁可(2026-08-24・env台帳に明記)】
 ・禁足席は**【生成の席】の話**である。
 ・★**埋込(bge-m3・0.66GB)は z8a を借り続けてよい**——27b の19GBとは桁が二つ違う。
 ・ゆえ生成と埋込は**別の家**を持つ。一本の switch で束ねてはならぬ
   (実測: 08-24 21:07 の復帰 switch が埋込を .139 へ引きずり、裁可を無言で上書きした。
    その結果 27b が17GiB常駐する .139 で埋込が 503 を返し続け、健診が145度吠えた)。

守る掟:
 ① 宛先を決める関は**一つ**(casper_endpoint)。engine に .119 の焼き付き既定を残さぬ。
 ② 生成が禁足席を指したら**黙って迂回せず名乗って止まる**(ForbiddenSeat)。
 ③ ★禁足の検問を**埋込に掛けぬ**(裁可の文言そのまま。掛ければ意味検索を殺す=過剰阻止)。
 ④ 真実源は env ファイル**そのもの**(プロセスの環境は起動時の写しであり、退避で腐る)。
 ⑤ 退避の switch は埋込に触れぬ。埋込の家は固定台帳(CASPER_EMBED_HOME)が持つ。
 ⑥ 検問は**完全一致のみ**(正しい呼出まで止める検問は無いより悪い——この陣が三度踏んだ)。
 ★突然変異: 各機構を殺すと赤化することを実証する。
"""
import io
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_endpoint as EP                                # noqa: E402

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


# ── ① 焼き付き既定が engine に残っておらぬ ─────────────────────────────
print("── ① 焼き付き既定 ──")
_FORBIDDEN_LITERAL = "192.168.44." + "119"                  # 自らも文字を持たぬ(自己言及の罠回避)
_EXEMPT = {"casper_endpoint.py",                            # 台帳を読む関そのもの(既定は持たぬ)
           "gate_forbidden_seat.py",                        # 本ゲート
           "symptom_free_status.py",                        # breaker台帳の**鍵名**(宛先ではない)
           "grant_egress.py"}                               # 許可リストの文字列(発射せぬ)
_offenders = []
for fn in sorted(os.listdir(HERE)):
    if not fn.endswith(".py") or fn in _EXEMPT:
        continue
    txt = io.open(os.path.join(HERE, fn), encoding="utf-8").read()
    for i, ln in enumerate(txt.splitlines(), 1):
        if _FORBIDDEN_LITERAL not in ln:
            continue
        if ln.lstrip().startswith("#") or "実測" in ln:      # 注記・実測の記録は宛先ではない
            continue
        _offenders.append(f"{fn}:{i}")
chk(f"① 禁足席を焼き付けた行が engine に無い(見つかった: {_offenders or 'なし'})", not _offenders)
chk("① 宛先を決める関が在る", callable(EP.gen_endpoint) and callable(EP.embed_endpoint))
for _mod, _needle in (("distill_activity.py", "_ep.gen_endpoint()"),
                      ("casper_embed.py", "_ep.embed_endpoint()"),
                      ("gate_aurora_save_smoke.py", "_ep.gen_endpoint()"),
                      ("benchmark.py", "_ep.gen_endpoint()")):
    chk(f"① {_mod} が唯一の関から引く",
        _needle in io.open(os.path.join(HERE, _mod), encoding="utf-8").read())

# ── ②③⑥ 検問の向き ────────────────────────────────────────────────────
print("── ②③⑥ 誰を止め、誰を止めぬか ──")
_tmp = tempfile.mkdtemp(prefix="gate_forbidden_")


def _env(gen, embed=None, forbidden="192.168.44." + "119:11434", embed_home=None):
    p = os.path.join(_tmp, "e_%d.env" % len(os.listdir(_tmp)))
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(f"CASPER_OLLAMA={gen}\nCASPER_FORBIDDEN_SEATS={forbidden}\n")
        if embed:
            f.write(f"CASPER_EMBED_ENDPOINT={embed}\n")
        if embed_home:
            f.write(f"CASPER_EMBED_HOME={embed_home}\n")
    EP.ENV_FILE = p
    EP._CACHE.update({"ts": 0.0, "data": None})


_FS = "http://192.168.44." + "119:11434"
_OK = "http://192.168.44." + "139:11434"

_env(gen=_OK)
chk("② 許された席は素通り", EP.gen_endpoint() == _OK)
_env(gen=_FS)
try:
    EP.gen_endpoint()
    chk("② ★禁足席への生成は名乗って止まる", False)
except EP.ForbiddenSeat as e:
    chk("② ★禁足席への生成は名乗って止まる", "禁足" in str(e))
chk("② ★黙って別席へ迂回せぬ(例外で知らせる=握り潰さぬ)",
    "return" not in io.open(os.path.join(HERE, "casper_endpoint.py"),
                            encoding="utf-8").read().split("raise ForbiddenSeat")[0].split("if strict")[1])
chk("② 現況照会(strict=False)は転ばぬ(報告の用)", EP.gen_endpoint(strict=False) == _FS)

_env(gen=_OK, embed=_FS)
chk("③ ★埋込は禁足の検問を受けぬ(裁可: 埋込は z8a を借り続ける)", EP.embed_endpoint() == _FS)
_env(gen=_FS, embed=_FS)
chk("③ 生成が禁足でも埋込は独立に解決する(巻き添えにせぬ)", EP.embed_endpoint() == _FS)

_env(gen=_OK, forbidden="192.168.44." + "119:99999")
chk("⑥ ★完全一致のみ(port が違えば禁足ではない)", not EP.is_forbidden_gen(_FS))
_env(gen=_OK, forbidden="")
chk("⑥ 禁足の指定が無ければ何も止めぬ", EP.forbidden_seats() == set())

# ── ④ 真実源はファイル ─────────────────────────────────────────────────
print("── ④ 台帳が先、環境は補い ──")
_env(gen=_OK)
os.environ["CASPER_OLLAMA"] = _FS
chk("④ ★台帳の値が環境変数より優先(環境は起動時の写しゆえ退避で腐る)", EP.gen_endpoint() == _OK)
_env(gen="")
chk("④ 台帳に無い鍵は環境変数で補う", EP.gen_endpoint(strict=False) == _FS)
os.environ.pop("CASPER_OLLAMA", None)
_env(gen=_OK, embed_home=_FS)
chk("④ 埋込は EMBED_ENDPOINT 無ければ固定台帳(EMBED_HOME)へ落ちる", EP.embed_endpoint() == _FS)

# ── ⑤ 退避は埋込に触れぬ ───────────────────────────────────────────────
print("── ⑤ 退避と埋込を束ねぬ ──")
_fo = io.open(os.path.join(HERE, "casper_failover.py"), encoding="utf-8").read()
chk("⑤ ★switch が埋込を引きずらぬ(is_embed_too=False)", "_rewrite_env(args.to, is_embed_too=False)" in _fo)
chk("⑤ 引きずらぬ理由が書き残されておる(次の者が戻さぬために)",
    "殿の裁可" in _fo and "無言で上書き" in _fo)
_live = io.open(os.path.join(HERE, "casper_endpoints.env"), encoding="utf-8").read()
chk("⑤ 埋込の家が固定台帳として在る", "CASPER_EMBED_HOME=" in _live)

# ── 実台帳の現況(此処は観測。値そのものは動いてよい) ───────────────────
print("── 実台帳の現況 ──")
EP.ENV_FILE = os.path.join(HERE, "casper_endpoints.env")
EP._CACHE.update({"ts": 0.0, "data": None})
_g, _e = EP.gen_endpoint(strict=False), EP.embed_endpoint()
print(f"   生成={_g} / 埋込={_e} / 禁足={sorted(EP.forbidden_seats())}")
chk("★実台帳: 生成が禁足席を指しておらぬ", not EP.is_forbidden_gen(_g))
chk("★実台帳: 生成と埋込が別の家を持つ(束ねられておらぬ)", _g != _e)

# ── ★突然変異 ──────────────────────────────────────────────────────────
print("\n--- 突然変異検証 ---")
SRC = io.open(os.path.join(HERE, "casper_endpoint.py"), encoding="utf-8").read()
_m = '''    if strict and is_forbidden_gen(url):'''
chk("★変異の錨が在る(ゲートの自己点検)", SRC.count(_m) == 1)
_ns = {"__file__": os.path.join(HERE, "casper_endpoint.py"), "__name__": "ep_mutant"}
exec(compile(SRC.replace(_m, "    if False:"), "casper_endpoint.py", "exec"), _ns)
_ns["ENV_FILE"] = os.path.join(_tmp, "mut.env")
io.open(_ns["ENV_FILE"], "w", encoding="utf-8").write(
    f"CASPER_OLLAMA={_FS}\nCASPER_FORBIDDEN_SEATS=192.168.44.{'119'}:11434\n")
_ns["_CACHE"].update({"ts": 0.0, "data": None})
chk("★変異(検問を殺す): 禁足席へ黙って生成を撃つ(赤化実証)", _ns["gen_endpoint"]() == _FS)

_m2 = '''    url = _get("CASPER_EMBED_ENDPOINT") or _get("CASPER_EMBED_HOME")'''
chk("★変異の錨が在る(埋込・ゲートの自己点検)", SRC.count(_m2) == 1)
_ns2 = {"__file__": os.path.join(HERE, "casper_endpoint.py"), "__name__": "ep_mutant2"}
exec(compile(SRC.replace(_m2, "    url = None"), "casper_endpoint.py", "exec"), _ns2)
_ns2["ENV_FILE"] = os.path.join(_tmp, "mut2.env")
io.open(_ns2["ENV_FILE"], "w", encoding="utf-8").write(
    f"CASPER_OLLAMA={_OK}\nCASPER_EMBED_ENDPOINT={_FS}\n")
_ns2["_CACHE"].update({"ts": 0.0, "data": None})
chk("★変異(埋込の家を無視): 埋込が生成に追随し、裁可が再び踏み潰される(赤化実証)",
    _ns2["embed_endpoint"]() == _OK)

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
