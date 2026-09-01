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
    が監視対象68本(scripts直下・test_/gate_除外後の全母集合。第1便はchat_server.py
    一本のみを検査しており、sig()の影響範囲より狭かった=risk_3)から実際に
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

★絶対パスで呼ぶ(cwdの違いで走らぬ恐れがある・軍師の教訓)。
③・④・⑤・⑥の突然変異試験はscratchpad上の一時コピーに対してのみ行い、
本番ファイルには一切触れない。
"""
import ast
import glob
import os
import re
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


def chk(name, got, exp):
    ok = got == exp
    results.append(ok)
    print(("✅" if ok else "❌") + f" {name}: got={got!r}" + ("" if ok else f" exp={exp!r}"))


def chk_true(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


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


# ── ④ supervisor sig()除外対象が監視対象68本のいずれからもimportされていないこと ──
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
chk_true("④-0 監視対象母集合が68本である(risk_3の実測基準)", len(_monitored) == 68)

# risk_3: chat_server.py一本のみでなく監視対象68本全てにimport検査を適用し、
# 除外名集合との積が全ファイルで空であることを検める。
_leak_by_file = {}
for p in _monitored:
    leak = sorted(_excluded & _imported_modules(p))
    if leak:
        _leak_by_file[os.path.basename(p)] = leak
chk("④ 監視対象68本のいずれもtest_*.py/gate_*.pyをimportしていない(sig()除外の穴なし・risk_3手当)",
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

n_ok, n = sum(results), len(results)
print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
