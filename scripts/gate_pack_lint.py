#!/usr/bin/env python3
"""語彙検問(pack_lint.py)の回帰ゲート(cmd_504)。全PASSで exit 0。

pack_lintという道具は在るのに誰も走らせておらぬゆえ、三度(cmd_489→495→504)崩れた。
「崩した者がその場で気づく」ため、gate群(足軽がcmdの終わりに必ず全件走らせる)に
本ゲートを加える(軍師設計・本命)。

守る掟:
 ① pack_lint.py(bokan)がexit=0(engineにpack固有語ゼロ)であること。
 ② pack_lint.py --pack toystudio でもexit=0であること(cmd_489の証明の維持=
    bokanを直してもtoystudioが壊れぬこと)。
 ③ 語彙を1件わざと書き込めば pack_lint.py が exit!=0 で捉えること(=検問が本当に
    機能している証拠。骨抜き実装や、pack.yamlを読まずいつも0を返す実装を防ぐ)。
 ④ 【cmd_520第1便・第3便-bで拡張】casper_supervisor.sh の sig() は test_*.py /
    gate_*.py を auto-reload署名の対象から除外している(編集だけで誤reloadが発火
    する事故対策)。この除外が静かに穴化しないよう、除外対象(test_*.py・gate_*.py)
    が監視対象母集合(scripts直下・test_/gate_除外後の全ファイル)から実際に
    importされていないことを機械検査する。★④-0(cmd_522再手当・出所共有方式):
    門(_monitored_scripts()のglob)とsupervisor正典のsig()が実際に選ぶ母集合を、
    数値リテラルで固定せずその場で突合する(将軍裁定「数を写すな、出所を共有せよ」)。
    第1便はchat_server.py一本のみを検査しており、sig()の影響範囲より狭かった
    =risk_3)から実際に
    importされていないことを機械検査する。importの検出は名前付きimport文だけでなく
    __import__(...)形式・importlib.import_module(...)形式・セミコロン区切りの
    import文(import a; import b)も捉える(risk_1: 第1便の正規表現はこれらを
    見逃していた)。合成のimport文を注入すれば本検査が赤化することも実証する
    (骨抜き実装防止)。
 ⑤ 【cmd_520第3便-b新設・将軍指図・gunshi裁定(d)案で差替済】scripts直下の
    試験専用スクリプトの命名規約検査。当初はimport到達性(④の到達集合)で
    「本番から呼ばれぬ実行専用スクリプト」を推定したが、casper_failover.py・
    replay_corpus.py・symptom_free_status.py等の正規常設ツール16本を誤検知した
    (手動/cron専用の常設物は本番からimportされない)。gunshi裁定によりgit追跡状態
    (git ls-files)へ判定基準を差替: ①名がtest_/gate_に掛かる→可 ②gitに追跡されて
    いる→可(常設物・リポジトリが認めた資産) ③いずれでもない→赤。git addされぬ
    使い捨てスクリプトのみを赤とすることで誤検知ゼロを達成する(_mutation_test_
    520_impl2a.py事案の再発防止・削除だけでは同じ穴が開くため機構で強制)。
 ⑥ 【cmd_520第3便-b新設・①(c)/AC3・鉄則第九条】casper_howto_digest()の退避経路
    (about is not True の早期return・材料ゼロの正直な出口)に「失敗の告白以外の
    文字列」(体験ガイド案内=_TAIKEN_GUIDE_LINE)が混入していないことを静的検査する。
    合成注入(材料ゼロの出口へ_TAIKEN_GUIDE_LINEを混入させる)で本検査が赤化する
    ことも実証する。
 ⑦ 【cmd_522新設・門と本体の乖離検知器】risk_6手当(cmd_520)で13機構を「門と本体が
    共有関数/定数を通る」形へ是正したが、この正しい形を守らせる機構が無かった。
    次に誰かが新しい機構を素の条件のまま(門と本体が別々に判定式を持つ形で)登録
    すれば同じ穴が再び開く。以下3材料をASTで機械的に取得し、両者が「結ばれている」
    ことを検査する:
      材料1: replay_corpus.pyの_EXPECTATION_CHECKSから登録済機構名を取得。
      材料2: 同lambdaのASTからC.<attr>形式の参照名(関数名/定数名)を取得。
      材料3: chat_server.pyの_DIGEST_REGISTRYから機構名→digest関数名の対応を取得。
    「結ばれている」とは、門が参照する名(材料2)とdigest関数本体が参照する名の
    共通部分が非空であることを言う。これは以下三形いずれをも等しく捉える:
      形1: digest本体が門の参照先関数を直接呼び出す(例: calendar_digestが
        _calendar_has_matchを呼ぶ)。
      形2: 門とdigest本体が同じ第三の関数を共有する(例: entity_digest/門ともに
        _entity_resolve_recordを呼ぶ)。
      形3: 門とdigest本体が同じ定数(正規表現等)を直接参照する(例: existence/
        verifyは門・本体ともに_EXIST_Q_RE/_STATE_Q_REという同じ定数を参照)。
    ★casper_howtoは手当6(cmd_512第1便)により意図的な差替(replay側と本番側で
    LLM分類器の代わりに規則側の門を使う設計)があり素朴な形1/2/3判定では捉え
    られない特殊ケースゆえ除外する(除外はこの1件のみ・除外を増やす時は必ず
    軍師QCを通すこと)。

 ⑨ 【cmd_524新設・skills/白名簿の公開安全門】skills/casper-dm/を公開白名簿へ加える
    にあたり(gunshi裁定・案A=伏せ字化)、白名簿が今後も社内情報を漏らさぬことを
    機械で保証する。母集合は main repo(本ファイルの2階層上=projects/casperの
    さらに親)の `git ls-files -- 'skills/**'` の全数(標本でなく全数・鉄則)。
    ★projects/casperは別のネストしたgitリポジトリであるため(git stash教訓
    参照)、この列挙は必ずmain repoをcwdに指定して行う——HERE(本ゲート自身の
    ディレクトリ)を誤ってcwdに使うと母集合が空(別リポジトリゆえ)になる。
    赤化条件(3種、いずれか1つでも該当ファイルがあれば赤):
      a. 内部IP帯(192.168.x.x等の私有アドレス帯)が本文に現れる。
      b. roster実名(★/tmp/cal_users.json から実行時に読み、usernameの集合を
         照合に使う——名の一覧をゲートへ直書きしない=「写すな共有せよ」鉄則)
         が本文に現れる。roster読取自体に失敗した場合はこの検査のみunknown
         (chk_tri)とし、失敗を緑と誤読させない。
      c. `.local` ホスト名パターンが本文に現れる。
    AC7変異(過剰阻止をしない対照・「正しい修正を止める検問は無いより悪い」):
    合成ダミーファイルへ内部IPを1件混入させ本検査が赤化することを示し、かつ
    同時に現行8skill(白名簿記載の既存分)は依然緑であることを示す。
"""
import ast
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACK_LINT = os.path.join(HERE, "pack_lint.py")

sys.path.insert(0, HERE)
import pack_config as _pc
# 本ゲート自身がpack語彙をengineに直書きすればpack_lintに引っ掛かる(自己言及の罠)。
# 注入語はpack.yaml(examples.project_names)から取る(=本ゲートも語の文字を持たない)。
_PJ = (_pc.get("examples", {}).get("project_names") or ["<PJ名>"])[0]

results = []
# ★AC-path-4(cmd_522・cmd_512病の再演防止): 観測不能はresults(PASS/FAIL二値集計)
# とは完全に独立したカテゴリとして扱う。PASS件数にもFAIL件数にも一切算入しない。
unknowns = []


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


def chk_tri(name, verdict, detail=""):
    """三値判定を記録する("green"|"red"|"unknown")。
    ★AC-path-4: "unknown"はresults(bool集計)へは一切追加しない——PASS/FAIL件数の
    どちらにも紛れ込ませない独立カテゴリとして unknowns へ積む(cmd_512でS4が
    永久Unknownなのにoverallがgreenへhttp吸われた病の再演防止)。
    "green"/"red"はresultsへ通常のbool(True/False)として積む(既存集計との整合)。"""
    if verdict not in ("green", "red", "unknown"):
        raise ValueError(f"chk_tri: invalid verdict {verdict!r}")
    icon = {"green": "✅", "red": "❌", "unknown": "❓"}[verdict]
    suffix = f": {detail}" if detail else ""
    print(f"{icon} {name} [{verdict}]{suffix}")
    if verdict == "unknown":
        unknowns.append(name)
    else:
        results.append(verdict == "green")


def _run(pack):
    """pack_lint.pyを絶対パス+絶対cwdで実行しreturncodeを返す(cwd差異で走らぬ事故を防ぐ)。"""
    r = subprocess.run([sys.executable, PACK_LINT, "--pack", pack],
                        cwd=HERE, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ── ① bokan: engineにpack語彙ゼロ ──────────────────────────────────
rc1, out1 = _run("bokan")
chk("① pack_lint.py(bokan) が exit=0(engineにpack語彙ゼロ)", rc1, 0)
if rc1 != 0:
    print("  " + out1.replace("\n", "\n  "))

# ── ② toystudio: bokanを直してもtoystudioが壊れぬこと(cmd_489の証明の維持・AC2) ──
rc2, out2 = _run("toystudio")
chk("② pack_lint.py --pack toystudio が exit=0", rc2, 0)
if rc2 != 0:
    print("  " + out2.replace("\n", "\n  "))

# ── ③ 突然変異: 違反を1件わざと入れれば検問が赤化すること(AC3・実証) ────────
# 本番ファイルには一切触れない。pack_lint.scan()はHERE配下を再帰走査する実装ゆえ、
# HERE直下に一時ファイルを置いて注入する(=本物のスキャン経路を通す)。既存の本番.pyには
# 一切書き込まず、本ゲート終了時に必ず削除する(try/finallyで保証)。
_scratch = os.path.join(HERE, "_gate_pack_lint_mutant_scratch.py")
try:
    with open(_scratch, "w", encoding="utf-8") as f:
        f.write("# gate_pack_lint.py AC3実証専用の一時ファイル(本ゲート終了時に自動削除)\n")
        f.write(f'VIOLATION = "{_PJ}の状況は？"\n')
    rc3, out3 = _run("bokan")
    chk("③ 違反を1件わざと入れると pack_lint.py が exit!=0 で捉える", rc3 != 0, True)
    chk("③' 検知内容に注入ファイル名が含まれる",
        os.path.basename(_scratch) in out3, True)
finally:
    if os.path.exists(_scratch):
        os.remove(_scratch)

# 後始末の確認: 一時ファイルを消した後は再びPASSへ戻ること(本番状態を汚さぬ証拠)
rc4, out4 = _run("bokan")
chk("③'' 一時ファイル削除後は pack_lint.py(bokan) が再び exit=0 へ戻る", rc4, 0)


# ── ④ supervisor sig()除外対象が監視対象母集合のいずれからもimportされていないこと ──
CHAT_SERVER = os.path.join(HERE, "chat_server.py")

# risk_1: 名前付き import/from 文だけでなく、__import__(...)・importlib.import_module(...)・
# セミコロン区切りのimport文(import a; import b)も捉える(第1便の正規表現はこれらを見逃した)。
_IMPORT_RE = re.compile(r"(?:^|;)\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
_DUNDER_IMPORT_RE = re.compile(r"__import__\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)")
_IMPORTLIB_RE = re.compile(r"importlib\.import_module\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)")


def _imported_modules(path):
    """path内でimportされる先頭モジュール名の集合を返す(静的解析・実行しない)。
    名前付きimport文・セミコロン区切りimport文・__import__(...)形式・
    importlib.import_module(...)形式の四種を捉える(risk_1手当)。"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    names = set(_IMPORT_RE.findall(src))
    names |= set(_DUNDER_IMPORT_RE.findall(src))
    names |= set(_IMPORTLIB_RE.findall(src))
    return names


def _excluded_module_names():
    """sig()除外パターン(test_*.py・gate_*.py)に合致するscripts直下モジュール名の集合。"""
    names = set()
    for pat in ("test_*.py", "gate_*.py"):
        for p in glob.glob(os.path.join(HERE, pat)):
            names.add(os.path.splitext(os.path.basename(p))[0])
    return names


def _monitored_scripts():
    """sig()の監視対象母集合(scripts直下・test_/gate_除外後)。risk_3の到達集合算出と
    対象3(命名規約検査)の材料算出の両方で共有する(器を増やさぬ・gunshi指図)。"""
    return sorted(
        p for p in glob.glob(os.path.join(HERE, "*.py"))
        if not (os.path.basename(p).startswith("test_") or os.path.basename(p).startswith("gate_"))
    )


_excluded = _excluded_module_names()
_monitored = _monitored_scripts()

# cmd_522仕上げA(1便目・下限チェック案)は将軍検品で却下された。将軍裁定:
# 「数を写すな、出所を共有せよ」——68でも60でもなく、門が数える母集合と
# supervisorのsig()が実際に選ぶ母集合が同一であることこそが不変条件である。
# 以下はその出所共有方式(cmd_522仕上げA・再手当・subtask_522_impl3)。
# ★数値リテラル(68・60・69等)は一切書かない——常にその場で取得した
# 2つの件数同士を突合する。
SUPERVISOR_CANONICAL = "/mnt/h/multi-agent-shogun-main/scripts/casper_supervisor.sh"

_SIG_FN_RE = re.compile(r"^sig\(\)\{(.*)", re.M | re.S)
_SIG_FIND_RE = re.compile(r"(find\b[^|]*)\|")


def _extract_sig_find_expr(supervisor_path):
    """casper_supervisor.shのsig()本体からfind式を抽出する(出所共有方式)。
    抽出できなければNoneを返す(沈黙して緑を返さない・失敗とゼロを別出口へ)。"""
    try:
        with open(supervisor_path, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return None
    m_fn = _SIG_FN_RE.search(src)
    if not m_fn:
        return None
    m_find = _SIG_FIND_RE.search(m_fn.group(1))
    if not m_find:
        return None
    return m_find.group(1).strip()


def _run_find_expr(find_expr, scripts_dir):
    """抽出したfind式を安全に実行し、対象ファイル数を返す。
    失敗(抽出不能・findで始まらない・実行失敗)時はNoneを返す。
    shell=Trueは使わず、shlexでトークン化してsubprocessへ渡す(risk: shell
    インジェクション対策・将軍指摘の安全注意(2))。$SCRのみ実パスへ置換する。"""
    if find_expr is None:
        return None
    if not find_expr.lstrip().startswith("find"):
        return None
    cleaned = re.sub(r"2>/dev/null", "", find_expr).strip()
    try:
        tokens = shlex.split(cleaned)
    except ValueError:
        return None
    if not tokens or tokens[0] != "find":
        return None
    tokens = [scripts_dir if t == "$SCR" else t for t in tokens]
    try:
        r = subprocess.run(tokens, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return len([x for x in r.stdout.split("\0") if x])


_sig_find_expr = _extract_sig_find_expr(SUPERVISOR_CANONICAL)
chk_true("④-0-a sig()本体からfind式の抽出に成功した(出所共有・抽出前提)",
         _sig_find_expr is not None)

_supervisor_count = _run_find_expr(_sig_find_expr, HERE)
chk_true("④-0-b supervisorのfind式実行に成功した(Noneでない=沈黙して緑を返していない)",
         _supervisor_count is not None)

# AC-④0-2: 門(_monitored_scripts()のglob件数)とsupervisor側(sig()のfind式実行件数)
# が実際に一致することを実測する。数値リテラルではなく、その場で取得した2値同士。
chk("④-0 門の母集合件数とsupervisorのsig()対象件数が一致する(出所共有・数を写さぬ証明)",
    _supervisor_count, len(_monitored))

# AC-④0-3(突然変異): 抽出失敗時に沈黙して緑を返さないことを実証する。
chk_true("④-0-c' 合成的にsig()の形式を壊すとfind式抽出が失敗しNoneを返す(沈黙緑の防止)",
         _extract_sig_find_expr(os.path.join(HERE, "__no_such_supervisor__.sh")) is None)
chk_true("④-0-c'' 抽出失敗(None)を渡すと_run_find_exprもNoneを返す(失敗の伝播)",
         _run_find_expr(None, HERE) is None)

# AC-④0-5(安全性): find式実行前に「findで始まる」ことを検める処理が実装され、
# 任意文字列がshellへ渡らないことを実証する。悪意ある文字列を注入した場合に
# 実行を拒否することを合成で確認する(shell=Trueを使わずshlexトークン化する
# ため、そもそも`;`等はfindコマンドへの単なる引数文字列として渡り実行されない
# 設計だが、「findで始まらない」場合の拒否も別途確認する)。
chk_true("④-0-d 「findで始まらない」合成文字列は実行前に拒否される",
         _run_find_expr('echo pwned; rm -rf /tmp/test', HERE) is None)
_malicious_find = 'find "$SCR" -maxdepth 1 -name \'*.py\' ; rm -rf /tmp/test |'
_malicious_tokens = shlex.split(re.sub(r"2>/dev/null", "", _malicious_find).strip())
chk_true("④-0-d' 悪意ある文字列もshlexトークン化によりfindへの引数として渡るのみで"
         "shell解釈(;によるコマンド連結)は発生しない(shell=True不使用の実証)",
         ";" in _malicious_tokens and _malicious_tokens[0] == "find")

# AC-④0-4(★最重要・出所共有の証明): supervisor側の除外規則を合成で変えた「写し」
# (本番のcasper_supervisor.shには一切触れず、写しに対してのみ行う)を作り、
# 門がこれに自動追従して赤にならないことを実証する。
_scratch_supervisor = os.path.join(HERE, "_gate_pack_lint_supervisor_mutant_scratch.sh")
try:
    with open(_scratch_supervisor, "w", encoding="utf-8") as f:
        f.write(
            "sig(){ find \"$SCR\" -maxdepth 1 -name '*.py' ! -name 'test_*.py' "
            "! -name 'gate_*.py' ! -name 'chat_server.py' -print0 2>/dev/null "
            "| xargs -0 stat -c %Y 2>/dev/null | awk '{s+=$1} END{print s}'; }\n"
        )
    _mutant_find_expr = _extract_sig_find_expr(_scratch_supervisor)
    _mutant_supervisor_count = _run_find_expr(_mutant_find_expr, HERE)
    # 写しはchat_server.pyも追加除外するため、門の全母集合件数より1件少ないはず。
    # 出所共有方式ならこの差分を検出でき、門(_monitored)を書き換えずとも
    # 「除外規則の変化」を自動的に捉えられる(=数を写さず出所を共有した証明)。
    chk_true("④-0-e 写し(chat_server.py追加除外)のsig()対象件数は門の母集合より少ない"
             "(出所共有により除外規則の変化を自動検出)",
             _mutant_supervisor_count is not None
             and _mutant_supervisor_count == len(_monitored) - 1)
finally:
    if os.path.exists(_scratch_supervisor):
        os.remove(_scratch_supervisor)

# risk_3: chat_server.py一本のみでなく監視対象母集合全てにimport検査を適用し、
# 除外名集合との積が全ファイルで空であることを検める。
_leak_by_file = {}
for p in _monitored:
    leak = sorted(_excluded & _imported_modules(p))
    if leak:
        _leak_by_file[os.path.basename(p)] = leak
chk("④ 監視対象母集合のいずれもtest_*.py/gate_*.pyをimportしていない(sig()除外の穴なし・risk_3手当)",
    _leak_by_file, {})
if _leak_by_file:
    print(f"  漏洩: {_leak_by_file}")

# ④' 突然変異: 本番ファイルへの参照を一切変えず、一時コピー上で四形式それぞれのimport文を
# 注入すれば本検査ロジックが赤化することを実証する(骨抜き・常にPASSを返す実装の防止)。
_scratch4 = os.path.join(HERE, "_gate_pack_lint_import_mutant_scratch.py")
_mutant_forms = {
    "名前付きimport": "import gate_synthetic\n",
    "セミコロン区切り": "import os; import gate_synthetic\n",
    "__import__形式": '__import__("gate_synthetic")\n',
    "importlib形式": 'importlib.import_module("gate_synthetic")\n',
}
for _label, _body in _mutant_forms.items():
    try:
        with open(_scratch4, "w", encoding="utf-8") as f:
            f.write("# gate_pack_lint.py AC-risk1実証専用の一時ファイル(終了時に自動削除)\n")
            f.write(_body)
        _mutant_leak = sorted(_excluded & _imported_modules(_scratch4))
        chk(f"④' 合成import({_label})を注入すると本検査が赤化する(検知内容にgate_syntheticを含む)",
            "gate_synthetic" in _mutant_leak, True)
    finally:
        if os.path.exists(_scratch4):
            os.remove(_scratch4)


# ── ⑤ 試験専用スクリプトの命名規約検査(将軍指図・gunshi裁定(d)案・_mutation_test_520_impl2a.py
#     事案の再発防止) ──
# ★旧基準(import到達性で「実行専用」を推定)はcasper_failover.py・replay_corpus.py・
# symptom_free_status.py等の正規常設ツール16本を誤検知した(gunshi実測)。gunshi裁定に
# より判定基準をgit追跡状態へ差替: ①名がtest_/gate_に掛かる→可 ②gitに追跡されている
# (git ls-files)→可(常設物・リポジトリが認めた資産) ③いずれでもない→赤。
# git addされぬ使い捨てスクリプトは規約でなく人の自然な振舞いとして未追跡のままになる
# ため、この基準で誤検知ゼロを達成する(gunshi実測: 監視対象68本中git未追跡=0本)。
# ★n_risk_1(gunshi申し送り): git addし忘れの正規ツールが赤くなるのは意図的な「正しい
# 警告」として扱う(cmd_518 git stash事案=未コミット消失の入口ゆえ)。赤の文言に次の一手
# (git addせよ、またはtest_/gate_の名へ改めよ)を明記する。
# ★n_risk_2: projects/casperはネストした別リポジトリのため、git ls-filesはcwdを
# scripts直下(casper側)に明示指定して呼ぶ。git呼出自体が失敗した場合は沈黙して緑を
# 返さず、赤(Unknown同様の危険側)として扱う(失敗とゼロを別出口に・鉄則)。
_MAIN_GUARD_RE = re.compile(r"^if\s+__name__\s*==\s*['\"]__main__['\"]", re.M)


def _has_main_guard(path):
    with open(path, encoding="utf-8") as f:
        return bool(_MAIN_GUARD_RE.search(f.read()))


def _git_tracked_basenames():
    """scripts直下(HERE)でgit追跡されている*.pyのbasename集合を返す。
    git呼出自体が失敗した場合は None を返す(沈黙して緑を返さぬ・失敗とゼロを別出口に・鉄則)。
    cwd=HEREを明示指定することでprojects/casper(ネストした別リポジトリ)を正しく読む
    (n_risk_2手当)。"""
    r = subprocess.run(["git", "ls-files", "--", "*.py"],
                        cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return {os.path.basename(line.strip()) for line in r.stdout.splitlines() if line.strip()}


def _naming_violations(monitored, tracked):
    """__main__guardを持つスクリプトのうち、test_/gate_接頭辞に掛からず、かつgit未追跡の
    ものの一覧(basename)を返す(gunshi裁定(d)案)。tracked=None(git呼出失敗)の場合は
    全件を危険側(赤候補)として扱う。"""
    violations = []
    for p in monitored:
        base = os.path.basename(p)
        if base.startswith("test_") or base.startswith("gate_"):
            continue  # 既に除外パターンに掛かっている = sig()の穴にならない
        if not _has_main_guard(p):
            continue  # __main__guardを持たない = 実行専用の合図が無い
        if tracked is not None and base in tracked:
            continue  # gitに追跡されている = 常設物(リポジトリが認めた資産)
        violations.append(base)
    return sorted(violations)


_tracked_basenames = _git_tracked_basenames()
chk_true("⑤-0 git ls-filesの呼出に成功した(cwd=scripts直下・n_risk_2手当)",
         _tracked_basenames is not None)

_naming_violations_now = _naming_violations(_monitored, _tracked_basenames)
chk("⑤ 命名規約違反(test_/gate_接頭辞に掛からずgit未追跡の実行専用スクリプト)が0件",
    _naming_violations_now, [])
if _naming_violations_now:
    print(f"  違反: {_naming_violations_now}")
    print("  次の一手: git addせよ、またはtest_/gate_の名へ改めよ(n_risk_1・意図的な正しい警告)")

# ⑤' 突然変異: __main__guard を持つ未追跡(git未追跡)の一時ファイルを
# test_/gate_接頭辞なしの名で置けば、本検査が赤化することを実証する(変異→赤化→復元)。
_scratch5 = os.path.join(HERE, "_gate_pack_lint_naming_mutant_scratch.py")
try:
    with open(_scratch5, "w", encoding="utf-8") as f:
        f.write("# gate_pack_lint.py AC-naming実証専用の一時ファイル(終了時に自動削除)\n")
        f.write("if __name__ == '__main__':\n    pass\n")
    _mutant_violations = _naming_violations(_monitored + [_scratch5], _tracked_basenames)
    chk("⑤' 合成の命名規約違反ファイル(未追跡)を注入すると本検査が赤化する(検知内容に該当ファイル名を含む)",
        os.path.basename(_scratch5) in _mutant_violations, True)
finally:
    if os.path.exists(_scratch5):
        os.remove(_scratch5)

# ⑤'' 後始末の確認: 一時ファイル削除後は再び違反0件へ戻ること(本番状態を汚さぬ証拠)
_naming_violations_after = _naming_violations(_monitored, _tracked_basenames)
chk("⑤'' 一時ファイル削除後は命名規約違反が再び0件へ戻る", _naming_violations_after, [])

# ⑤''' AC-N4(gunshi必須念押し): 正しい名(test_接頭辞)の未追跡合成ファイルを置いた場合は
# ★緑のままであること(過剰阻止せぬ証明・「何を置いても赤くなる門」は新たな害)。
_scratch5b = os.path.join(HERE, "test_gate_pack_lint_naming_mutant_correctname_scratch.py")
try:
    with open(_scratch5b, "w", encoding="utf-8") as f:
        f.write("# gate_pack_lint.py AC-N4実証専用の一時ファイル(終了時に自動削除)\n")
        f.write("if __name__ == '__main__':\n    pass\n")
    _mutant_violations_correctname = _naming_violations(_monitored + [_scratch5b], _tracked_basenames)
    chk("⑤''' AC-N4: 正しい名(test_接頭辞)の未追跡合成ファイルは規約違反にならない(過剰阻止せぬ証明)",
        os.path.basename(_scratch5b) in _mutant_violations_correctname, False)
finally:
    if os.path.exists(_scratch5b):
        os.remove(_scratch5b)


# ── ⑥ ①(c)/AC3: casper_howto_digest()の退避経路純潔検査(鉄則第九条) ─────────
# 退避経路(about is not True の早期return・材料ゼロの正直な出口)に「失敗の告白以外の
# 文字列」(体験ガイド案内=_TAIKEN_GUIDE_LINE)が混入していないことをASTで静的に検める。
# 実害(2026-08-18): 材料の無い出口に案内を添えたため、案内そのものが答えを乗っ取った。


def _howto_digest_exit_purity(src):
    """chat_server.pyのソース文字列を解析し、casper_howto_digest()の中の
    早期return(about is not True)と最終return(material-zero honest exit)を
    それぞれ静的に検める。戻り値: (found:bool, early_return_clean:bool, final_return_clean:bool)。
    「clean」とは、当該return式のAST内に _TAIKEN_GUIDE_LINE への参照が無いことを言う。"""
    tree = ast.parse(src)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "casper_howto_digest":
            func = node
            break
    if func is None:
        return False, None, None

    # ★ast.walk はBFSでありソース順を保証しない(実測で判明・returns[-1]取り違えの罠)。
    # lineno でソートしてソース上の出現順に揃える。
    returns = sorted((n for n in ast.walk(func) if isinstance(n, ast.Return)),
                      key=lambda n: n.lineno)

    def _references_guide_line(ret_node):
        if ret_node.value is None:
            return False
        for n in ast.walk(ret_node.value):
            if isinstance(n, ast.Name) and n.id == "_TAIKEN_GUIDE_LINE":
                return True
        return False

    # 早期return = 「aboutの判定直後に現れるreturn」をlinenoで特定(gunshi裁定・第二案)。
    # ★「returnsのうち最終return以外の全て」(gunshi裁定・第一案)は実測でNGと判明:
    # 関数には early(about is not True)・正典成功時(about is True かつ 正典あり・
    # ★意図的に_TAIKEN_GUIDE_LINEを含む正規分岐)・final(材料ゼロ)の3 returnがあり、
    # 第一案だと正典成功時のreturnまで「早期return」扱いされ、本番コードで
    # early_clean=Falseの誤検知(過剰阻止)が実測で生じた。
    # ★値の形(Constant/BinOp)でも絞り込まない——混入した瞬間にConstantがBinOpへ変わり
    # 検査対象から外れる構造(cmd_511盲点1と同型)を再現するため、位置(lineno順序)かつ
    # 「関数トップレベルの最初のIf文の中のreturn」という構造的定義で早期returnを特定する。
    # ★risk_4手当: ast.walk(stmt)はBFSでありソース順を保証しない(returns算出と同じ罠・
    # 上記コメント参照)。入れ子if構造では浅い側のreturnを先に見つけてしまう恐れがある
    # ため、最初のIf文の中の全returnをlinenoでソートし、ソース上最初のもの(sorted()[0])
    # を早期returnとする(returns算出と同一の設計原則・gunshi裁定案B)。
    early_ret = None
    for stmt in func.body:
        if isinstance(stmt, ast.If):
            _if_returns = sorted((n for n in ast.walk(stmt) if isinstance(n, ast.Return)),
                                  key=lambda n: n.lineno)
            if _if_returns:
                early_ret = _if_returns[0]
            break
    early_clean = (not _references_guide_line(early_ret)) if early_ret is not None else None
    # 最終return(材料ゼロの正直な出口 = 関数末尾のreturn)。
    final_ret = returns[-1] if returns else None
    final_clean = (not _references_guide_line(final_ret)) if final_ret is not None else None

    return True, early_clean, final_clean


_src_chat_server = open(CHAT_SERVER, encoding="utf-8").read()
_found, _early_clean, _final_clean = _howto_digest_exit_purity(_src_chat_server)
chk_true("⑥-0 casper_howto_digest()が検査対象として存在する", _found)
chk("⑥ 早期return(about is not True)の出口に体験ガイド案内が混入していない",
    _early_clean, True)
chk("⑥ 材料ゼロの正直な出口(最終return)に体験ガイド案内が混入していない(鉄則第九条)",
    _final_clean, True)

# ⑥' 突然変異: 本番ファイルには一切触れず、ソース文字列上でのみ最終returnへ
# _TAIKEN_GUIDE_LINE の混入を合成注入し、本検査が赤化することを実証する。
_mutant_src = _src_chat_server.replace(
    'return "\\n\\n## 【Casperの使い方】\\n" + _HOWTO_FALLBACK',
    'return "\\n\\n## 【Casperの使い方】\\n" + _HOWTO_FALLBACK + _TAIKEN_GUIDE_LINE',
    1,
)
chk_true("⑥' 突然変異注入がソース文字列上で実際に発生した(前提の確認)",
         _mutant_src != _src_chat_server)
_mutant_found, _mutant_early_clean, _mutant_final_clean = _howto_digest_exit_purity(_mutant_src)
chk("⑥' 合成注入(材料ゼロ出口へ体験ガイド案内を混入)すると本検査が赤化する",
    _mutant_final_clean, False)

# ⑥'' 突然変異(early_clean側・gunshi是正指図): 早期return('return ""')へ
# _TAIKEN_GUIDE_LINE を混入する。混入すると AST は Constant("") から BinOp
# (Constant("") + Name)へ変わる——値の形で絞り込む旧実装ではこの変異が検査対象から
# 外れ、赤化できなかった穴(cmd_511盲点1と同型)。lineno位置ベースの新実装で
# 赤化することを実証する。
_mutant_early_src = _src_chat_server.replace(
    'return ""   # False(無関係) または None(判定不能=timeout等) → 何も差さぬ',
    'return "" + _TAIKEN_GUIDE_LINE   # 合成混入(early_clean変異試験)',
    1,
)
chk_true("⑥'' 早期return側の突然変異注入がソース文字列上で実際に発生した(前提の確認)",
         _mutant_early_src != _src_chat_server)
_mutant_e_found, _mutant_e_early_clean, _mutant_e_final_clean = _howto_digest_exit_purity(_mutant_early_src)
chk("⑥'' 合成注入(早期returnへ体験ガイド案内を混入・BinOp化)すると本検査が赤化する",
    _mutant_e_early_clean, False)

# 後始末不要(⑥はソース文字列上でのみ変異させており、本番ファイル・一時ファイルとも
# 一切書き込んでいない)。念のため本番chat_server.pyが不変であることを確認する。
_src_chat_server_after = open(CHAT_SERVER, encoding="utf-8").read()
chk_true("⑥''' 本番chat_server.pyは検査中一切変更されていない",
         _src_chat_server_after == _src_chat_server)

# ── ⑦ 門と本体の乖離検知器(cmd_522新設・risk_6型の穴の再発防止) ───────────
REPLAY_CORPUS = os.path.join(HERE, "replay_corpus.py")

# ★案A(gunshi裁定): casper_howtoはreplay/本番でLLM分類器を規則側の門へ意図的に
# 差替(手当6・cmd_512第1便)しており素朴な形1/2/3判定では捉えられない。
# 除外はこの1件のみとし、除外を増やす時は必ず軍師QCを通すこと。
_MECH_EXCLUDE = {"casper_howto"}


def _collect_c_attr_names(node):
    """AST部分木からC.<attr>形式の参照名(関数名/定数名)の集合を返す(材料2)。
    C._foo(...)(関数呼出)もC._BAR_RE.search(...)(定数の属性アクセス経由呼出)も、
    どちらもAttribute(value=Name('C'), attr=X)として現れるため同一の抽出で両方捉える。"""
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "C":
            names.add(n.attr)
    return names


def _collect_body_ref_names(func_node):
    """関数定義本体が参照する全ての名前(Name.id・Attribute.attr)の集合を返す。
    直接呼出(形1)・第三関数の共有呼出(形2)・定数の共有参照(形3)のいずれも
    Name または Attribute として現れるため、この単一の抽出で三形すべてを捉える。"""
    names = set()
    for n in ast.walk(func_node):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return names


def _extract_expectation_checks(src):
    """replay_corpus.pyのソースをASTで読み、_EXPECTATION_CHECKS辞書から
    {機構名: 門が参照する名の集合} を返す(材料1+材料2)。"""
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_EXPECTATION_CHECKS" for t in node.targets):
            d = node.value
            for k, v in zip(d.keys, d.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out[k.value] = _collect_c_attr_names(v)
    return out


def _extract_digest_registry(src):
    """chat_server.pyのソースをASTで読み、_DIGEST_REGISTRYリストから
    {機構名: digest関数名} の対応表を返す(材料3)。
    ★user_profileは_DIGEST_REGISTRYの表に載らず、build_digests()冒頭の
    pieces=[(...)]という初期値ハードコード1件として特別配線されている
    (L8062)。これも同じ構造(名前定数→digest関数呼出)を持つためASTで
    機械的に拾い、材料3の対応表へ同じ扱いで加える(器を増やさず既存の
    宣言箇所から取得する・gunshi実測方針の踏襲)。"""
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_DIGEST_REGISTRY" for t in node.targets):
            for el in node.value.elts:
                if not (isinstance(el, ast.Tuple) and len(el.elts) == 2):
                    continue
                name_node, fn_node = el.elts
                if not (isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)):
                    continue
                if isinstance(fn_node, ast.Lambda):
                    call = fn_node.body
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                        out[name_node.value] = call.func.id
        if isinstance(node, ast.FunctionDef) and node.name == "build_digests":
            for stmt in node.body:
                if not (isinstance(stmt, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "pieces" for t in stmt.targets)):
                    continue
                if not isinstance(stmt.value, ast.List):
                    continue
                for el in stmt.value.elts:
                    if not (isinstance(el, ast.Tuple) and len(el.elts) == 2):
                        continue
                    name_node, call_node = el.elts
                    if not (isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)):
                        continue
                    # _dg("name", <digest呼出>) の内側のCallからdigest関数名を取る
                    for inner in ast.walk(call_node):
                        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                                and inner.func.id.endswith("_digest"):
                            out.setdefault(name_node.value, inner.func.id)
                            break
    return out


def _extract_func_defs(src):
    """ソース中のトップレベル関数定義名→ASTノードの対応表を返す。"""
    tree = ast.parse(src)
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def _expand_gate_names(gate_names, func_defs):
    """門(lambda)がC.<attr>で直接参照する名(材料2の生値)に加え、その参照先が
    chat_server.py内の関数として定義されている場合は、その関数自身が参照する名も
    一段展開して加える(例: 門はC._entity_has_matchのみを直接参照するが、
    _entity_has_match自身の本体は_entity_resolve_recordを呼ぶ——形2の共有関数は
    門とdigest本体それぞれ一段先で一致する構造のため、素の直接参照だけでは
    形2/形3を取りこぼす)。関数でない名(定数)はそのまま素通しする。"""
    expanded = set(gate_names)
    for nm in gate_names:
        fn = func_defs.get(nm)
        if fn is not None:
            expanded |= _collect_body_ref_names(fn)
    return expanded


def _diverged_mechanisms(checks, registry, func_defs, exclude=_MECH_EXCLUDE):
    """各登録済機構(exclude除く)について、門の参照名(材料2・一段展開込み)と
    digest関数本体の参照名の積が空である(=形1/2/3いずれでも結ばれていない)
    機構名の一覧を返す。digest関数が見つからない場合も安全側(赤候補)として
    一覧に含める(失敗とゼロを別出口にする鉄則により、Unknownを緑と紛れさせない)。"""
    diverged = []
    for name, gate_names in checks.items():
        if name in exclude:
            continue
        fn_name = registry.get(name)
        if fn_name is None or fn_name not in func_defs:
            diverged.append(name)
            continue
        body_names = _collect_body_ref_names(func_defs[fn_name])
        expanded_gate_names = _expand_gate_names(gate_names, func_defs)
        if not (expanded_gate_names & body_names):
            diverged.append(name)
    return sorted(diverged)


_src_replay = open(REPLAY_CORPUS, encoding="utf-8").read()
_src_chat_server_7 = open(CHAT_SERVER, encoding="utf-8").read()
_checks = _extract_expectation_checks(_src_replay)
_registry = _extract_digest_registry(_src_chat_server_7)
_func_defs = _extract_func_defs(_src_chat_server_7)

chk_true("⑦-0 _EXPECTATION_CHECKSから登録済機構(casper_howto除く)を検出した",
         len(set(_checks) - _MECH_EXCLUDE) > 0)

_diverged_now = _diverged_mechanisms(_checks, _registry, _func_defs)
chk("⑦ AC7-1: 現状の全登録機構(casper_howto除く)が門と本体で結ばれている(誤検知ゼロ)",
    _diverged_now, [])
if _diverged_now:
    print(f"  乖離: {_diverged_now}")

# ⑦' AC7-2/AC7-3: 合成で素の条件(門も本体も共有せず独立実装)の第14番目の機構を注入し
# 赤化することを実証し、取り除くと緑へ復元することを確認する(本番ファイルには一切触れず
# 材料の辞書上でのみ合成する)。
_checks_mutant = dict(_checks)
_checks_mutant["gate_synthetic_mech"] = {"_gate_synthetic_only_name"}
_registry_mutant = dict(_registry)
_registry_mutant["gate_synthetic_mech"] = "gate_synthetic_digest_fn"
_func_defs_mutant = dict(_func_defs)
_func_defs_mutant["gate_synthetic_digest_fn"] = ast.parse(
    "def gate_synthetic_digest_fn(q):\n    return _body_synthetic_only_name(q)\n"
).body[0]

_diverged_mutant = _diverged_mechanisms(_checks_mutant, _registry_mutant, _func_defs_mutant)
chk("⑦' AC7-2: 素の条件(門も本体も共有せず独立実装)の合成機構を注入すると⑦が赤化する",
    "gate_synthetic_mech" in _diverged_mutant, True)

_diverged_after_removal = _diverged_mechanisms(_checks, _registry, _func_defs)
chk("⑦'' AC7-3: 注入した合成機構を取り除くと緑へ復元する", _diverged_after_removal, [])

# ⑦''' AC7-4(★最重要・三形対応): 形1のみ・形2のみ・形3のみで判ずる各版へ変異させ、
# それぞれ対応する型の誤検知が生じることを実証する(三形すべてを認める必要があることの
# 機械的証明)。実機構(entity=形2代表・existence=形3代表)を材料に、判定式を単一の形へ
# 制限した版を合成し、その形を欠く機構が誤って赤化する(=過剰阻止)ことを示す。

# 形1のみで判ずる版: 「門の参照名がdigest関数名(展開後の呼出名)と文字列一致するか」
# だけを見る(=直接呼出以外を認めない)。entity(形2)・image_asset(形2)・existence(形3)・
# verify(形3)は、この狭い基準では"門の参照先関数名"がdigest本体の中の関数名と一致しない
# ため誤検知(赤)になる。
def _diverged_form1_only(checks, registry, func_defs, exclude=_MECH_EXCLUDE):
    diverged = []
    for name, gate_names in checks.items():
        if name in exclude:
            continue
        fn_name = registry.get(name)
        if fn_name is None or fn_name not in func_defs:
            diverged.append(name)
            continue
        # 形1のみ: digest本体が「門が参照する名」を直接Callしている場合のみ結ばれているとみなす
        direct_calls = {c.func.id for c in ast.walk(func_defs[fn_name])
                         if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        if not (gate_names & direct_calls):
            diverged.append(name)
    return sorted(diverged)


_diverged_f1 = _diverged_form1_only(_checks, _registry, _func_defs)
chk_true("⑦''' AC7-4(形1のみ): entity型(形2)の誤検知が生じる", "entity" in _diverged_f1)
chk_true("⑦''' AC7-4(形1のみ): image_asset型(形2)の誤検知が生じる", "image_asset" in _diverged_f1)

# 形2のみで判ずる版: 「門とdigest本体がAttribute参照でなくCallの引数越しに同じ関数を
# 呼ぶ」ような間接共有だけを認め、定数(Attribute終端・.search()等の直接呼出でない参照)
# を除外する。existence(形3)・verify(形3)はこの基準では定数のみを共有しており、
# Attributeのみの参照(Callの引数にならない)ゆえ誤検知(赤)になる。
def _diverged_form2_only(checks, registry, func_defs, exclude=_MECH_EXCLUDE):
    diverged = []
    for name, gate_names in checks.items():
        if name in exclude:
            continue
        fn_name = registry.get(name)
        if fn_name is None or fn_name not in func_defs:
            diverged.append(name)
            continue
        # 形2のみ: digest本体側でCallのfuncとして現れる名だけを「結ばれている」候補とする
        # (定数がそのままAttribute参照される形=形3を除外する)
        called_names = {c.func.id for c in ast.walk(func_defs[fn_name])
                         if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        called_names |= {c.func.attr for c in ast.walk(func_defs[fn_name])
                          if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
        if not (gate_names & called_names):
            diverged.append(name)
    return sorted(diverged)


_diverged_f2 = _diverged_form2_only(_checks, _registry, _func_defs)
chk_true("⑦''' AC7-4(形2のみ): existence型(形3)の誤検知が生じる", "existence" in _diverged_f2)
chk_true("⑦''' AC7-4(形2のみ): verify型(形3)の誤検知が生じる", "verify" in _diverged_f2)

# 形3のみで判ずる版: 「門とdigest本体が同じAttribute終端(Callされるか否か問わず全ての
# Attribute.attr名)を共有する」基準そのものは実は形1/2も内包してしまうため、代わりに
# 「門の参照名がdigest本体でCallのfuncとして一切現れない場合のみ結ばれているとみなす」
# (=関数として呼ばれる共有は全て否定し、Attribute終端の非Call参照のみを認める)ことで
# 形1・形2を人為的に除外し、大量誤検知(brief記載の実測: 形3のみ→8件)を再現する。
def _diverged_form3_only(checks, registry, func_defs, exclude=_MECH_EXCLUDE):
    diverged = []
    for name, gate_names in checks.items():
        if name in exclude:
            continue
        fn_name = registry.get(name)
        if fn_name is None or fn_name not in func_defs:
            diverged.append(name)
            continue
        func_node = func_defs[fn_name]
        called_names = {c.func.id for c in ast.walk(func_node)
                         if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        called_names |= {c.func.attr for c in ast.walk(func_node)
                          if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
        # 形3のみ: Call経由の共有(形1/2)は無効化し、Callされない生のAttribute/Name参照
        # だけを「結ばれている」根拠として残す
        non_call_names = _collect_body_ref_names(func_node) - called_names
        if not (gate_names & non_call_names):
            diverged.append(name)
    return sorted(diverged)


_diverged_f3 = _diverged_form3_only(_checks, _registry, _func_defs)
chk_true("⑦''' AC7-4(形3のみ): 大量誤検知が生じる(brief実測=8件相当・過半数が赤化)",
         len(_diverged_f3) >= 8)
print(f"  (参考)形3のみ版の誤検知一覧: {_diverged_f3}")


# ── ⑧ SUPERVISOR_CANONICALの道の写し問題(cmd_522最終便・将軍新発見) ──────────
# 将軍指摘: gate_pack_lint.py L188のSUPERVISOR_CANONICALは絶対パスが手で書かれて
# おり、実際に稼働中のsupervisorがそのパスを本当に実行しているかは誰も検めていない。
# repoが移った日、あるいは別の場所から起きたsupervisorが本番を握った日、門は誰も
# 走らせていないファイルを読みながら緑を出し続ける恐れがある(今朝の双子の罠と同じ
# 「一致が保証されない」形)。
# ★二値でなく三値判定(cmd_519のprobe三値化・cmd_512「埋まらねば仮説と書け」踏襲):
#   一致=green / 不一致=red / 観測不能(supervisor停止中・/procが読めない環境等)=unknown。
# ★pid決め打ち禁止(pidは時点の値・将軍がmd5で戒めたのと同型)——/proc全走査で
# その場にいる全プロセスのcmdlineを読み、casper_supervisor.shで終わる引数を
# realpathで正規化して集める(pid番号を一切ハードコードしない)。


def _running_supervisor_paths(proc_root="/proc"):
    """/proc全走査でcasper_supervisor.shを実行中の全プロセスの実パス集合を返す。
    pid決め打ちをせず、その場にいる全プロセスのcmdlineを読んで機械的に集める。
    /proc自体が読めない環境ではNoneを返す(沈黙して空集合=緑材料に見せかけない・
    失敗とゼロを別出口にする鉄則)。個々のpidのcmdlineが読めない(プロセスが競合で
    消えた等)場合はそのpidのみスキップして続行する(全体をNoneにはしない)。"""
    try:
        pids = [d for d in os.listdir(proc_root) if d.isdigit()]
    except OSError:
        return None
    paths = set()
    for pid in pids:
        cmdline_path = os.path.join(proc_root, pid, "cmdline")
        try:
            with open(cmdline_path, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        for arg in raw.split(b"\x00"):
            if not arg:
                continue
            try:
                arg_s = arg.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            if arg_s.endswith("casper_supervisor.sh"):
                paths.add(os.path.realpath(arg_s))
    return paths


def _supervisor_path_verdict_containment(canonical, running_paths):
    """(cmd_523以前の旧判定・AC-uniq-5の対照専用に残す)SUPERVISOR_CANONICALと
    稼働中パス集合を「含有」で突合し、三値("green"|"red"|"unknown")と detail
    文字列を返す。running_pathsがNoneまたは空集合(=supervisorが1つも観測でき
    なかった)場合はunknown。★二匹目が集合に混じっていても正典さえ含まれれば
    緑を返してしまう欠陥判定(cmd_523で唯一性判定へ置き換えられた理由そのもの)。
    現行の判定には使わない——AC-uniq-5の非退行全数対照でのみ呼ぶ。"""
    if not running_paths:
        return "unknown", "稼働中のcasper_supervisor.shが1件も観測できなかった"
    canonical_real = os.path.realpath(canonical)
    if canonical_real in running_paths:
        return "green", f"稼働中パス集合に正典が含まれる: {sorted(running_paths)}"
    return "red", (f"正典 {canonical_real} は稼働中パス集合に含まれない: "
                    f"{sorted(running_paths)}")


def _supervisor_path_verdict(canonical, running_paths):
    """SUPERVISOR_CANONICALと稼働中パス集合を「唯一性」で突合し、三値
    ("green"|"red"|"unknown")と detail文字列を返す(cmd_523・将軍新発見:
    含有判定は二匹目が集合に混じっていても正典が含まれれば緑を返す欠陥が
    あった)。running_pathsがNoneまたは空集合(=supervisorが1つも観測できな
    かった)場合はunknown——観測できなかったことを緑にも赤にも化けさせない
    (AC-path-4/AC-uniq-3の前提)。要素数が1件かつそれが正典と一致する場合の
    みgreen。2件以上(二匹目以上が並走)ならred。1件だが正典と不一致でも
    red(唯一だが別物という形も不一致として扱う)。"""
    if not running_paths:
        return "unknown", "稼働中のcasper_supervisor.shが1件も観測できなかった"
    canonical_real = os.path.realpath(canonical)
    if len(running_paths) != 1:
        return "red", (f"稼働中パス集合に{len(running_paths)}件(唯一でない)が"
                        f"並走している: {sorted(running_paths)}")
    _only_path = next(iter(running_paths))
    if _only_path == canonical_real:
        return "green", f"稼働中パス集合は正典と一致する唯一の1件: {_only_path}"
    return "red", (f"稼働中パス集合の唯一の1件が正典と不一致: "
                    f"{_only_path} != {canonical_real}")


_running_paths_now = _running_supervisor_paths()
_verdict_now, _detail_now = _supervisor_path_verdict(SUPERVISOR_CANONICAL, _running_paths_now)
chk_tri("⑧-AC-path-1/2 SUPERVISOR_CANONICALと/proc走査の稼働中パスの突合(現況)",
        _verdict_now, _detail_now)

# AC-path-3(合成): 不一致ケースを再現する(現況の稼働中パス集合はそのまま、
# canonical側を別の場所を指す合成パスへ差替えて不一致を作る)。
_mutant_canonical = os.path.join(HERE, "_no_such_dir_", "casper_supervisor.sh")
_verdict_mismatch, _detail_mismatch = _supervisor_path_verdict(_mutant_canonical, _running_paths_now)
chk_tri("⑧' AC-path-3(合成・不一致): canonicalを別経路へ差替えるとred判定になる"
        "(観測不能でこの結果が出ていないことが前提)",
        "green" if (_running_paths_now and _verdict_mismatch == "red") else "red",
        f"verdict={_verdict_mismatch!r}")

# AC-path-3(合成): 観測不能ケースを再現する(供給元プロセス停止相当=空集合を注入)。
_verdict_empty, _detail_empty = _supervisor_path_verdict(SUPERVISOR_CANONICAL, set())
chk_tri("⑧'' AC-path-3(合成・観測不能=空集合): 稼働中パス集合が空だとunknown判定になる",
        "green" if _verdict_empty == "unknown" else "red",
        f"verdict={_verdict_empty!r}")

_verdict_none, _detail_none = _supervisor_path_verdict(SUPERVISOR_CANONICAL, None)
chk_tri("⑧''' AC-path-3(合成・観測不能=/proc読取不能相当): "
        "稼働中パス集合がNoneだとunknown判定になる",
        "green" if _verdict_none == "unknown" else "red",
        f"verdict={_verdict_none!r}")

# AC-path-4(★最重要・cmd_512病の再演防止): 観測不能(unknown)がPASS/FAIL集計
# (results)のどちらにも算入されないことを実測する。chk_triは実装上unknownを
# resultsへ一切追加しない設計だが、ここでは「その設計が本当に機能している」こと
# 自体を実測で証明する——unknownsリストに積まれた件数だけresultsの長さから
# 除外されていることを、直接件数比較で示す。
_unknown_count_before = len(unknowns)
_results_len_before = len(results)
chk_tri("⑧-probe AC-path-4検証専用: 観測不能をわざと1件記録する", "unknown", "検証用プローブ")
_unknown_count_after = len(unknowns)
_results_len_after = len(results)
chk_true("⑧-AC-path-4 観測不能を記録してもresults(PASS/FAIL集計)の件数は増えない"
         "(unknownは独立カテゴリ・PASSにもFAILにも算入されない)",
         _results_len_after == _results_len_before and _unknown_count_after == _unknown_count_before + 1)

# ── ⑧の唯一性判定(cmd_523・将軍新発見: 含有判定は二匹目が混じっていても正典が
# 含まれれば緑を返す欠陥があった)。判定ロジック(_supervisor_path_verdict)のみを
# 書き換え、材料の収集器(_running_supervisor_paths)には一切手を触れない
# (gunshi注意事項)。以下は5ケース(0匹/1匹正典一致/1匹不一致/2匹以上/観測不能)
# を合成データで全数検査する。

_canon_real = os.path.realpath(SUPERVISOR_CANONICAL)
_other_path = os.path.realpath(os.path.join(HERE, "_second_supervisor_", "casper_supervisor.sh"))

# AC-uniq-1: 要素数1件かつ正典一致の場合のみgreen。
_v1, _d1 = _supervisor_path_verdict(SUPERVISOR_CANONICAL, {_canon_real})
chk_tri("⑧-AC-uniq-1 唯一性判定: 要素数1件かつ正典一致でgreenを返す",
        _v1, _d1)

# AC-uniq-2: 二匹目のパスを注入すると赤化する。含有判定なら同じ集合がgreenの
# ままであることと対にして示す(片方だけでは「元から赤い集合だった」を排除
# できないため・gunshi指示通り同じ検査内で両方を回す)。
_two_paths = {_canon_real, _other_path}
_v2_containment, _d2_containment = _supervisor_path_verdict_containment(SUPERVISOR_CANONICAL, _two_paths)
_v2_uniq, _d2_uniq = _supervisor_path_verdict(SUPERVISOR_CANONICAL, _two_paths)
chk_true("⑧-AC-uniq-2(i) 同じ二匹合成集合を含有判定へ通すとgreenのまま"
         "(唯一性判定への置換前の挙動を確認・退行の対照)",
         _v2_containment == "green")
chk_true("⑧-AC-uniq-2(ii) 同じ二匹合成集合を唯一性判定へ通すとredになる"
         "(正典は含まれるが二匹目が並走するため)",
         _v2_uniq == "red")

# AC-uniq-3: 要素数0件で観測不能(unknown)を返す(三値判定の維持)。
_v3, _d3 = _supervisor_path_verdict(SUPERVISOR_CANONICAL, set())
chk_tri("⑧-AC-uniq-3 唯一性判定: 要素数0件でunknownを返す(三値判定の維持)",
        _v3, _d3)

# AC-uniq-4(cmd_512病の再演防止・cmd_522と同型): 観測不能が集計(PASS/FAIL件数)
# へ吸われないことを再確認する。
_uniq_unknown_count_before = len(unknowns)
_uniq_results_len_before = len(results)
chk_tri("⑧-uniq-probe AC-uniq-4検証専用: 唯一性判定でunknownをわざと1件記録する",
        _v3, "検証用プローブ(AC-uniq-3のverdictを再利用)")
_uniq_unknown_count_after = len(unknowns)
_uniq_results_len_after = len(results)
chk_true("⑧-AC-uniq-4 唯一性判定のunknownもresults(PASS/FAIL集計)の件数を"
         "増やさない(unknownは独立カテゴリ)",
         _uniq_results_len_after == _uniq_results_len_before
         and _uniq_unknown_count_after == _uniq_unknown_count_before + 1)

# AC-uniq-5(gunshi追加提案・非退行の全数対照): 含有判定と唯一性判定を5ケース
# 全数(0匹/1匹正典一致/1匹不一致/2匹以上/観測不能)で突き合わせ、「差が出るのは
# 二匹目のケースのみ」を機械で示す。観測不能(None)は空集合と同じunknown経路を
# 通るため、0匹ケースの一種として扱う(2ケースをNone/空集合で明示的に走らす)。
_uniq_cases = {
    "0匹(空集合)": set(),
    "0匹(None)": None,
    "1匹_正典一致": {_canon_real},
    "1匹_不一致": {_other_path},
    "2匹以上": {_canon_real, _other_path},
}
_uniq_case_diffs = {}
for _case_name, _case_paths in _uniq_cases.items():
    _c_verdict, _ = _supervisor_path_verdict_containment(SUPERVISOR_CANONICAL, _case_paths)
    _u_verdict, _ = _supervisor_path_verdict(SUPERVISOR_CANONICAL, _case_paths)
    _uniq_case_diffs[_case_name] = (_c_verdict, _u_verdict, _c_verdict != _u_verdict)
_diff_cases = sorted(name for name, (_, _, diff) in _uniq_case_diffs.items() if diff)
chk_true("⑧-AC-uniq-5 含有判定と唯一性判定の5ケース全数対照: 差が出るのは"
         f"「2匹以上」のケースのみ(実測差分ケース={_diff_cases!r})",
         _diff_cases == ["2匹以上"])
print(f"  (参考)⑧-AC-uniq-5 5ケース全数(含有verdict, 唯一性verdict, 差異有無): "
      f"{_uniq_case_diffs}")

# ── ⑧ AC7: _supervisor_path_verdict(唯一性判定)への変異検査(cmd_523) ──
# ★変異は判定関数(_supervisor_path_verdict)のlen判定にのみ当てる。収集器
# (_running_supervisor_paths)には当てない——それは材料を運ぶ側であり、
# 殺せば四ACが同時に赤くなり何を証明したか判らなくなる(gunshi注意事項・
# 「変異は検査される側にのみ当てよ」)。
_gpl_src_path = os.path.join(HERE, "gate_pack_lint.py")
with open(_gpl_src_path, "r", encoding="utf-8") as _f:
    _gpl_src_before = _f.read()
_gpl_md5_before = hashlib.md5(_gpl_src_before.encode("utf-8")).hexdigest()

# 変異前確認: 二匹合成集合は現行実装でred。
_pre_mutation_verdict, _ = _supervisor_path_verdict(SUPERVISOR_CANONICAL, _two_paths)
chk_true("⑧-AC7-pre 変異前確認: 二匹合成集合は唯一性判定でred",
         _pre_mutation_verdict == "red")

# 変異: len(running_paths) != 1 の唯一性ガード節と、後続の単一要素抽出
# (next(iter(...)))を丸ごと「canonicalがsetに含まれるか」という含有判定
# 相当(_supervisor_path_verdict_containmentと同じ穴)へ置き換えたコードを
# exec し、二匹合成集合(len=2)が唯一性チェックを迂回してredを見逃し
# greenへ化ける(=検査が赤化する)ことを確認する。★当初は"!= 1"の右辺の
# 数字だけを書き換える変異(999・>2等)や、ガード節のみを削除し後続の
# next(iter(running_paths))へ処理を委ねる変異を試みたが、いずれもset
# (文字列2件)の反復順序がPYTHONHASHSEEDにより実行プロセスごとに変わる
# ため、mutant_verdictがgreen/redの間で非決定的に揺れ(実測: 同一コードを
# 4回連続実行してgreen/red/red/greenと結果が割れた)、検査自体が偶然
# PASSしたりFAILしたりする不安定な門になってしまった(掟「観測装置は
# 赤くなれると証明するまで観測装置でない」に反する)。★是正:
# next(iter())には一切頼らず、`canonical_real in running_paths`という
# 順序に依存しない包含判定へ関数全体を置き換える変異とした——これは
# ①で置き換えた旧含有判定(_supervisor_path_verdict_containment)の欠陥
# そのものの再現でもあり、意味的にも正しい変異である。ソースファイル
# 自体は書き換えない。★全文execだと本ファイルのモジュールレベル処理
# (/proc走査・pack_lint.py実行等)が丸ごと二重に走ってしまうため、
# _supervisor_path_verdict関数定義のASTノード1つだけを本文から抽出して
# compile・execする(収集器や他の検査には一切触れない・関数のみの局所
# 変異)。
_gpl_ast_before = ast.parse(_gpl_src_before, filename=_gpl_src_path)
_verdict_fn_node = next(
    n for n in _gpl_ast_before.body
    if isinstance(n, ast.FunctionDef) and n.name == "_supervisor_path_verdict"
)
_verdict_fn_src = ast.get_source_segment(_gpl_src_before, _verdict_fn_node)
_mutant_fn_src = (
    'def _supervisor_path_verdict(canonical, running_paths):\n'
    '    if not running_paths:\n'
    '        return "unknown", "稼働中のcasper_supervisor.shが1件も観測できなかった"\n'
    '    canonical_real = os.path.realpath(canonical)\n'
    '    if canonical_real in running_paths:\n'
    '        return "green", f"稼働中パス集合に正典が含まれる: {sorted(running_paths)}"\n'
    '    return "red", f"正典は稼働中パス集合に含まれない: {sorted(running_paths)}"\n'
)
chk_true("⑧-AC7-mutant-injected 変異注入: 唯一性判定(len==1かつ一致)を"
         "含有判定(canonical in running_paths)へ丸ごと置き換えた変異ソースが"
         "元の関数ソースと異なる(置換が実際に効いたことの確認)",
         _mutant_fn_src != _verdict_fn_src)
_mutant_ns = {"os": os}
exec(compile(_mutant_fn_src, _gpl_src_path, "exec"), _mutant_ns)
_mutant_verdict_fn = _mutant_ns.get("_supervisor_path_verdict")
if _mutant_verdict_fn is None:
    chk_true("⑧-AC7-mutant 変異版から_supervisor_path_verdictを取得できた", False)
else:
    _mutant_verdict, _ = _mutant_verdict_fn(SUPERVISOR_CANONICAL, _two_paths)
    chk_true("⑧-AC7 変異(唯一性判定→含有判定への置換)を当てると二匹合成集合が"
             f"redでなくなる(赤化を実測・variant verdict={_mutant_verdict!r})",
             _mutant_verdict != "red")

# 復元確認: ソースファイルは一切書き換えていないため、変異検査後も
# md5が変異前と一致することを直接示す(execは別名前空間・ファイルI/Oなし)。
with open(_gpl_src_path, "r", encoding="utf-8") as _f:
    _gpl_src_after = _f.read()
_gpl_md5_after = hashlib.md5(_gpl_src_after.encode("utf-8")).hexdigest()
chk_true("⑧-AC7-restore 変異検査後もgate_pack_lint.py自身のmd5が変異前と一致"
         "(ソースファイルは書き換えていない・execによる別名前空間注入のみ)",
         _gpl_md5_after == _gpl_md5_before)

# ── ⑨ skills/白名簿の公開安全門(cmd_524新設) ─────────────────────────
MAIN_REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))

# ★CJK文字はPython既定でre的に\w扱いとなり、末尾\bが日本語直後で境界不成立になる
# (実測: "192.168.44.253経由"で\bが効かず検知漏れ)。前後は数字非連続で区切る。
_INTERNAL_IP_RE = re.compile(
    r"(?<!\d)(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})(?!\d)"
)
_LOCAL_HOST_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]+\.local(?![A-Za-z0-9_-])")


def _skills_tracked_files(repo_root=MAIN_REPO_ROOT):
    """main repo(projects/casperとは別リポジトリ)の`git ls-files -- 'skills/**'`
    全数を返す。★HEREをcwdに使うと母集合が空になる(nested repo・git stash教訓)ため、
    必ずrepo_rootをcwdに明示指定する。失敗時はNone(失敗とゼロを別出口に・鉄則)。"""
    r = subprocess.run(["git", "ls-files", "--", "skills/**"],
                        cwd=repo_root, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return sorted(line.strip() for line in r.stdout.splitlines() if line.strip())


# ★cmd_524裁定「casperは伏せるな(製品名であり機微でない)」——rosterにcasper自身の
# サービスアカウント(uid101)が実在するため、素の照合だとcasper自身の言及まで誤検知
# する(過剰阻止)。roster実名検査からこの1件のみ明示的に除外する(除外を増やす時は
# 必ず軍師QCを通すこと・⑦の除外運用に倣う)。
_ROSTER_NAME_EXCLUDE = {"casper"}


def _is_service_form(username):
    """resolve.py._looks_service_like()と同じ「形」判定(メールアドレス形/非ASCII)。
    サービス/システム的なusernameは特定個人の実名ではないため、roster実名検査の
    対象から外す(uid1「tanaka@example.com」・uid48「アプリ管理用」等の誤検知防止・
    過剰阻止をしない=AC7の趣旨をここでも守る)。
    ★副作用として非ASCII名も一律除外される——本関数はサービス判定の副産物として
    「かな/漢字の実名も検査対象から外れる」結果になる。⑨-bがASCII usernameしか
    検めない設計上の理由の一つ(E1是正・cmd_524)。"""
    u = username or ""
    if "@" in u:
        return True
    if not u.isascii():
        return True
    return False


def _load_roster_usernames(cal_users_path="/tmp/cal_users.json"):
    """roster実名の集合を実行時に/tmp/cal_users.jsonから読む(直書き禁止・写すな共有せよ)。
    失敗時はNoneを返す(実名検査をunknownへ落とすための合図)。
    ★守備範囲の限界(E1是正・cmd_524): usernameフィールド(ASCIIローマ字)のみを
    集合化する。rosterにかな/漢字表記の実名は存在しないため、⑨-b実名検査は
    構造上ASCII表記の実名しか検知できない——かな/漢字の実名(例:「きよとも」)は
    この検査の対象外である。緑判定は「ASCII実名なし」の意であり「実名なし」の
    保証ではない。母集合が無い状態でかな表記を手で列挙することも「写すな・
    共有せよ」の掟に反するため行わない。"""
    try:
        with open(cal_users_path, encoding="utf-8") as f:
            d = json.load(f)
        items = d.get("items") or (d if isinstance(d, list) else [])
        names = set()
        for u in items:
            uname = u.get("username")
            if not uname:
                continue
            if str(uname).lower() in _ROSTER_NAME_EXCLUDE:
                continue
            if _is_service_form(str(uname)):
                continue
            names.add(str(uname).lower())
        return names
    except Exception:
        return None


def _scan_public_safety(path, roster_usernames):
    """1ファイルを読み、(内部IP有無, roster実名有無, .localホスト名有無)を返す。
    roster_usernames=Noneの場合、実名検査はスキップ(呼出側でunknown扱いにする)。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError:
        return False, False, False
    has_ip = bool(_INTERNAL_IP_RE.search(body))
    has_local = bool(_LOCAL_HOST_RE.search(body))
    has_name = False
    if roster_usernames:
        lowered = body.lower()
        for uname in roster_usernames:
            # ★CJK文字はPython既定で\w扱いとなり\bが日本語直後で不成立になる
            # (実測: "kiyotomo宛に送って"で\bが効かず検知漏れ)。英数字非連続で区切る。
            if len(uname) >= 3 and re.search(
                    r"(?<![a-z0-9_])" + re.escape(uname) + r"(?![a-z0-9_])", lowered):
                has_name = True
                break
    return has_ip, has_name, has_local


_skills_pop = _skills_tracked_files()
_roster_usernames = _load_roster_usernames()

if _skills_pop is None:
    chk_tri("⑨-a/b/c skills/白名簿 公開安全門(git ls-files失敗のため母集合を取得できず)",
            "unknown", "git ls-files -- 'skills/**' がmain repoで失敗した")
elif _roster_usernames is None:
    chk_tri("⑨-b roster実名検査(/tmp/cal_users.json 読取不可)",
            "unknown", "実ソース未取得ゆえ実名検査は判定不能")
    _ip_violations = []
    for _rel in _skills_pop:
        _abs = os.path.join(MAIN_REPO_ROOT, _rel)
        _has_ip, _, _has_local = _scan_public_safety(_abs, None)
        if _has_ip or _has_local:
            _ip_violations.append(_rel)
    chk_true(f"⑨-a/c skills/追跡ファイル{len(_skills_pop)}件に内部IP/.localホスト名なし",
             not _ip_violations)
    if _ip_violations:
        print(f"  ❌ 検出: {_ip_violations}")
else:
    _violations = {"ip": [], "name": [], "local": []}
    for _rel in _skills_pop:
        _abs = os.path.join(MAIN_REPO_ROOT, _rel)
        _has_ip, _has_name, _has_local = _scan_public_safety(_abs, _roster_usernames)
        if _has_ip:
            _violations["ip"].append(_rel)
        if _has_name:
            _violations["name"].append(_rel)
        if _has_local:
            _violations["local"].append(_rel)

    chk_true(f"⑨-a skills/追跡ファイル{len(_skills_pop)}件(全数)に内部IP帯なし",
             not _violations["ip"])
    if _violations["ip"]:
        print(f"  ❌ 内部IP検出: {_violations['ip']}")

    chk_true(f"⑨-b skills/追跡ファイル{len(_skills_pop)}件(全数)にroster実名(ASCII usernameのみ検査対象・かな/漢字表記は対象外)なし",
             not _violations["name"])
    if _violations["name"]:
        print(f"  ❌ roster実名検出: {_violations['name']}")

    chk_true(f"⑨-c skills/追跡ファイル{len(_skills_pop)}件(全数)に.localホスト名なし",
             not _violations["local"])
    if _violations["local"]:
        print(f"  ❌ .localホスト名検出: {_violations['local']}")

    # AC7変異(過剰阻止をしない対照): 合成ダミーへ内部IPを1件混ぜ赤化を示し、
    # かつ現行8skillは依然緑であることを同時に示す。本番ファイルには一切触れない。
    _mutant_dir = os.path.join(HERE, "_gate_pack_lint_524_mutant_scratch")
    os.makedirs(_mutant_dir, exist_ok=True)
    _mutant_file = os.path.join(_mutant_dir, "dummy.md")
    try:
        with open(_mutant_file, "w", encoding="utf-8") as f:
            f.write("Calendar 192.168.44.253経由で接続する合成ダミー。\n")
        _mutant_has_ip, _, _ = _scan_public_safety(_mutant_file, _roster_usernames)
        chk_true("⑨-AC7 合成ダミーへ内部IPを1件混ぜると本検査が赤化する(過剰阻止していない証拠)",
                 _mutant_has_ip)
    finally:
        if os.path.exists(_mutant_file):
            os.remove(_mutant_file)
        if os.path.isdir(_mutant_dir) and not os.listdir(_mutant_dir):
            os.rmdir(_mutant_dir)

    _existing_8 = [p for p in _skills_pop if not p.startswith("skills/casper-dm/")]
    _existing_8_violations = [p for p in _existing_8
                               if any(p in _violations[k] for k in ("ip", "name", "local"))]
    chk_true(f"⑨-AC7対照 現行の白名簿記載分{len(_existing_8)}件は依然すべて緑"
             "(過剰阻止=正しい修正を止める検問になっていない証拠)",
             not _existing_8_violations)
    if _existing_8_violations:
        print(f"  ❌ 既存分で誤検知: {_existing_8_violations}")

if unknowns:
    print(f"  ❓ 観測不能(unknown・PASS/FAILどちらにも算入せず): {unknowns}")

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
