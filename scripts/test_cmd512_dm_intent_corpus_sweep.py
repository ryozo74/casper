#!/usr/bin/env python3
"""cmd_512第2便(AC7・本cmd最重要): _DM_INTENT_RE(語彙表)の外から、regexに拠らず
「送信文脈で現れる動詞候補」を三つの外部真実源(vault社内資料・casper_outbox.jsonl実送信DM本文・
conversation_log.jsonlユーザ発話)から機械抽出し、_DM_INTENT_REに無い語を赤として列挙する。

★軍師戦略review(subtask_512_strategy)の設計に従う:
  - 手書きの語彙表は一切作らない。抽出は「語の一覧」でなく「構造(位置)」で行う——
    依頼形(〜して/〜をお願い/〜いたします等)の★直前に現れるサ変動詞語幹、という
    位置的制約だけで候補を絞り込む。casper_outboxの本文は実際に送られたDMであり、
    そこに現れるサ変動詞語幹は定義上、送信語彙の候補である。
  - 手当4(第3便・門の構造是正)より★必ず先に本試験を走らせ、11語のうち何語が赤として
    挙がるかを実測する(是正前の状態を証拠として残す・AC7)。
  - 見つからぬ語は見つからぬと正直に報告する(no silent caps)。真実源に無い語を
    無理に足して「11語全部が赤」を演出すれば、第三の手書き表が生まれ本cmdの目的が死ぬ。

★守秘: conversation_log.jsonl/casper_outbox.jsonlには社員/殿の実発話が含まれる。
  本ファイルはどの関数からも本文そのものをprint/checkのdetailへ出さない——
  出力は「抽出された語(短い動詞語幹)」と判定結果(bool/件数)のみに限る
  (test_send_intent_gate.py AC1の作法を踏襲)。

Usage: python3 test_cmd512_dm_intent_corpus_sweep.py
"""
import glob
import json
import os
import re
import sys

os.environ.setdefault("CASPER_NO_DAEMON", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chat_server as C

_failures = []


def check(name, cond, detail=""):
    status = "OK" if cond else "NG"
    print(f"[{status}] {name} {detail}")
    if not cond:
        _failures.append(name)


# ── AC7対象: 制作現場の日常語11語(_DM_INTENT_REに欠落・タスクブリーフ記載の語彙) ──────
_TARGET_11_WORDS = [
    "送付", "転送", "展開", "回覧", "提出", "申し送り",
    "打診", "差し替え", "上申", "周知", "配信",
]

_VAULT_DIR = os.path.normpath(os.path.join(HERE, "..", "vault"))
_OUTBOX_PATH = os.path.join(HERE, "casper_outbox.jsonl")
_CONVLOG_PATH = os.path.join(HERE, "conversation_log.jsonl")

# ── 構造抽出パターン(★語の一覧でなく位置で拾う) ──────────────────────────────
# サ変動詞語幹: 漢字/カタカナの連続。送り仮名を伴う語(申し送り/差し替え等)を拾うため、
# 「し/り/え/が/す/く/き/い/た/つ」のいずれかを挟んだ二連続も許す(申し+送り、差し+替え等)。
# ★cmd_512第3便是正(impl3の regex 瑕疵): 従来形は送り仮名終端語(「差し替え」等・送り仮名
# ブロックの直後に本体が続かず終わる形)を構造的に表現できなかった——二連続目の本体部分に
# `+`(1文字以上)を要求していたため、「替え」の後に何も続かない語形を素通りしていた。
# 是正: 二連続目の送り仮名+本体を`*`(0回以上の繰返し)で任意回数まで許し、最後に単独の
# 送り仮名 or 本体1文字が来る形も拾えるようにする(申し送り/差し替え双方を表現できる)。
_STEM_RE = r"([一-龥ァ-ヶー]+(?:[しりえがすくきいたつ][一-龥ァ-ヶー]*)*[一-龥ァ-ヶーしりえがすくきいたつ]?)"
# 依頼形直前の語幹を拾う: 「して(ください/いただけ/もらえ)?」「いたします/いたしました」
# 「します/しました」「をお願い(します)?」「を頼みます?」「願います」——いずれも
# _DM_INTENT_RE等の既存語彙表と無関係に、日本語の依頼・送信報告の定型接尾辞そのものを使う
# (新しい語彙表ではなく、文法形式による構造フィルタ)。
_REQUEST_SUFFIX_RE = (
    r"(?:して(?:ください|下さい|いただけ|頂け|もらえ)?"
    r"|いたします|いたしました|致します|致しました"
    r"|します|しました"
    r"|をお願い(?:します|いたします)?"
    r"|を頼み?ます?"
    r"|願います)"
)
_EXTRACT_RE = re.compile(_STEM_RE + _REQUEST_SUFFIX_RE)


def _extract_send_context_stems(text):
    """依頼形直前のサ変動詞語幹を構造で抽出する(語の一覧を使わない・位置的制約のみ)。
    長さ2〜6文字の語幹に絞る(1文字は助詞混入の恐れ・7文字超は複合名詞の巻き込みが多いため)。"""
    out = set()
    for m in _EXTRACT_RE.finditer(text):
        w = m.group(1)
        if 2 <= len(w) <= 6:
            out.add(w)
    return out


def _scan_jsonl(path, field_getter):
    """jsonlの各行から対象フィールドを取り出し、送信文脈語幹を集計する。
    本文そのものはこの関数の外へ返さない(語の集合のみ返す・守秘遵守)。"""
    stems = set()
    n_lines = 0
    n_texts = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            n_lines += 1
            try:
                d = json.loads(line)
            except Exception:
                continue
            text = field_getter(d)
            if not text:
                continue
            n_texts += 1
            stems |= _extract_send_context_stems(text)
    return stems, n_lines, n_texts


def _scan_vault():
    """vault(社内資料)全ファイルを走査し、送信文脈語幹を集計する。"""
    stems = set()
    files = glob.glob(os.path.join(_VAULT_DIR, "**", "*.md"), recursive=True)
    n_files = 0
    for fp in files:
        n_files += 1
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception:
            continue
        stems |= _extract_send_context_stems(text)
    return stems, n_files


def test_ac7_0_三源が読み込める():
    """前提確認: vault・outbox・conversation_logの三源がいずれも存在し読み込めること。"""
    check("AC7-0a: vaultディレクトリが存在する", os.path.isdir(_VAULT_DIR), _VAULT_DIR)
    check("AC7-0b: casper_outbox.jsonlが存在する", os.path.isfile(_OUTBOX_PATH))
    check("AC7-0c: conversation_log.jsonlが存在する", os.path.isfile(_CONVLOG_PATH))


def test_ac7_1_構造抽出が空でない候補を返す():
    """試験自体が壊れていないことの反証照会: 三源から構造抽出した語の総数が0でないこと
    (0ならパターンかファイルパスが壊れている=試験自体の欠陥であり、対象語が無いこととは別)。"""
    vault_stems, n_vault_files = _scan_vault()
    outbox_stems, n_outbox_lines, n_outbox_texts = _scan_jsonl(
        _OUTBOX_PATH, lambda d: d.get("args", {}).get("body", "") if isinstance(d.get("args"), dict) else "")
    convlog_stems, n_convlog_lines, n_convlog_texts = _scan_jsonl(
        _CONVLOG_PATH, lambda d: d.get("content", "") if d.get("role") == "user" else "")

    check(f"AC7-1a: vault({n_vault_files}ファイル)から1語以上の候補が抽出できる",
          len(vault_stems) > 0, f"n_stems={len(vault_stems)}")
    check(f"AC7-1b: casper_outbox({n_outbox_lines}行中{n_outbox_texts}件body有)から"
          "1語以上の候補が抽出できる", len(outbox_stems) > 0, f"n_stems={len(outbox_stems)}")
    check(f"AC7-1c: conversation_log({n_convlog_lines}行中{n_convlog_texts}件user発話)から"
          "1語以上の候補が抽出できる", len(convlog_stems) > 0, f"n_stems={len(convlog_stems)}")

    # ★反証照会: 既知の在庫語(_DM_INTENT_REに既にある語)が構造抽出で拾えることを確認する。
    #   拾えなければ抽出パターン自体が壊れている(対象語が無いこととは別の欠陥)。
    known_in_re_words = {"確認", "報告", "共有", "修正"}
    all_stems = vault_stems | outbox_stems | convlog_stems
    overlap = known_in_re_words & all_stems
    check("AC7-1d反証照会: 既知の在庫語(確認/報告/共有/修正)の一部が構造抽出で拾える"
          "(=抽出パターン自体は機能している証)", len(overlap) > 0, f"overlap={sorted(overlap)}")


def test_ac7_2_regexに無い語を三源併用で赤として列挙する():
    """★AC7本体: 11語のうち何語が「vault∪outbox∪conversation_log から構造抽出され、
    かつ_DM_INTENT_REに一致しない」赤として挙がるかを実測する。
    ★11語全部が赤になることを要求しない(現存真実源では原理的に届かぬ語がある・
    軍師実測5/家老裁可待ちのAC7読み替え案)——本試験は実測件数と内訳を正直に報告する。"""
    vault_stems, _ = _scan_vault()
    outbox_stems, _, _ = _scan_jsonl(
        _OUTBOX_PATH, lambda d: d.get("args", {}).get("body", "") if isinstance(d.get("args"), dict) else "")
    convlog_stems, _, _ = _scan_jsonl(
        _CONVLOG_PATH, lambda d: d.get("content", "") if d.get("role") == "user" else "")

    union_stems = vault_stems | outbox_stems | convlog_stems

    red_words = []       # 真実源に在ってregexに無い語(=AC7が挙げよと求める赤)
    not_found_words = [] # どの真実源にも構造抽出で現れなかった語(正直に報告)
    already_in_re = []   # 既にregexに在る語(想定上11語はいずれもここに入らないはず)

    for w in _TARGET_11_WORDS:
        in_re = bool(C._DM_INTENT_RE.search(w))
        found_in_corpus = w in union_stems
        if in_re:
            already_in_re.append(w)
        elif found_in_corpus:
            red_words.append(w)
        else:
            not_found_words.append(w)

    check("AC7-2-0前提: 11語はいずれも現状_DM_INTENT_REに一致しない"
          "(既にregexに在る語が混入していれば11語リスト自体の誤り)",
          len(already_in_re) == 0, f"already_in_re={already_in_re}")

    check(f"AC7-2本体: 11語中{len(red_words)}語が「真実源に在ってregexに無い」赤として"
          "構造抽出で列挙できた(★1語以上を要求・0語ならno silent capsの意味で試験の存在意義が無い)",
          len(red_words) >= 1, f"red_words={sorted(red_words)}")

    print(f"    [AC7実測結果] 赤(regex外・真実源で発見): {sorted(red_words)} ({len(red_words)}/11)")
    print(f"    [AC7実測結果] 未発見(どの真実源にも構造抽出で現れず): "
          f"{sorted(not_found_words)} ({len(not_found_words)}/11)")

    # ★no silent caps: 見つからなかった語について、生の部分文字列としてなら三源のどこかに
    #   現れているか(=構造条件だけを満たさなかったのか、そもそも語自体が無いのか)を
    #   区別して報告する(件数のみ・本文は出さない)。
    for w in not_found_words:
        raw_hit_vault = any(w in open(fp, encoding="utf-8", errors="ignore").read()
                             for fp in glob.glob(os.path.join(_VAULT_DIR, "**", "*.md"), recursive=True)
                             if os.path.isfile(fp))
        print(f"    [AC7内訳] 「{w}」: 生の部分文字列としてvaultに存在するか={raw_hit_vault}"
              "(存在するが依頼形直前という構造条件を満たさなかった/存在しないのいずれか)")


def test_ac7_3_合否判定は保留する():
    """★家老/将軍への申し送り(タスクブリーフ明記): AC7の合否基準(「11語すべて」か
    「複数語を独立に発見できること」か)は本試験の権限外。ここでは実測結果を記録するのみで、
    合否のassertは行わない(pass/failを断定しない設計そのものが申し送りの一部)。"""
    check("AC7-3: 合否の最終判定は将軍/殿の裁可待ち(本試験は実測結果の記録に専念する)", True)


def main():
    tests = [
        test_ac7_0_三源が読み込める,
        test_ac7_1_構造抽出が空でない候補を返す,
        test_ac7_2_regexに無い語を三源併用で赤として列挙する,
        test_ac7_3_合否判定は保留する,
    ]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        try:
            t()
        except Exception as e:
            check(t.__name__, False, f"EXCEPTION: {e!r}")
    print(f"\n{'='*60}")
    if _failures:
        print(f"FAIL: {len(_failures)}件 -> {_failures}")
        sys.exit(1)
    print("ALL PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
