#!/usr/bin/env python3
"""cmd_488 回帰ゲート: アップローダ経路(uploader/resolve→submit)のバイナリ往復がsha256一致すること。
真因: chat.html takeFile()が非画像(pptx/docx/xlsx/pdf等)でdata_b64を作らず送信していなかった(①)。
      さらに submit→feed_save が参照する ASSET_FILES と resolve の保存先 ASSETS_DIR が別ディレクトリ・
      別ファイル名で、複製がなければ feed_save は本文抽出できなかった(②)。両方を是正済。
★掟: 文字列比較や「読めるか」判定ではなく sha256 の完全一致で判定する(feedback_casper_fix_iron_rules)。

cmd_488やり直し(subtask_488_impl2): 経路X(/api/aurora/upload・kiyotomo殿が実際に通った経路)の
守りを追加。真因はchat.html auroraUpload()のf.text()がpptx等バイナリをUTF-8decode(不可逆)して
いたこと。是正はfail-closed(バイナリはAuroraへ送らせず/送られても文字化け痕跡でサーバが拒否)。
経路Yとは別経路・別関数(check_route_x_aurora_binary_reject)であり、経路Y側の既存チェックは無変更。

cmd_488やり直し(subtask_488_impl3・将軍差し戻し是正): 突然変異検証がchat_server.py本体を
書き換える設計だったため(supervisorのmtime自動リロードを誘発し稼働中本番を実際に変異状態で
再起動させる事故が実発生)、/tmp配下のオーバーライドファイル経由の実行時注入方式に変更。
本番ファイルは終始無変更(diffなし)。加えてflockで多重起動を排他(併走事故の再発防止)。

cmd_488やり直し(subtask_488_impl4・将軍差し戻し是正): 経路Xの突然変異検証(clean control/
mutation check)が実際に/api/aurora/uploadのok:true応答を経てcasper_aurora.create()まで
到達し、本番Auroraへ資料を実書込し続けていた(削除API無く単調増加・31件確認済)。
是正: chat_server.py側に dry_run パラメータを追加し、ガード判定(拒否されるか否か)の直後・
casper_aurora.create()到達の直前で止める設計に変更(HTTPレスポンスのok/dry_run値のみで
検査が完結)。ゲートは既定でdry_run=Trueを送るため、本番Auroraへは一切書き込まない。
加えて_route_x_guard_enabled()にts(タイムスタンプ)必須化・5分超過で安全側(有効)へ倒す判定と、
無効化成立時のstderr警告ログを追加(ガードが人知れず外れたまま残ることを防ぐ)。

実行: python3 gate_upload_binary_roundtrip.py (chat_server.py 未起動時はスキップ・exit 0)
"""
import base64
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 【殿御下命2026-08-29・丙】loopback だけでは最早内部機構を名乗れぬ。合鍵を提げて呼ぶ。
# ★合鍵は casper_secrets.host_secret() が無ければ作る——ゆえハーネスが黙って匿名へ落ちることは無い。
try:
    import casper_secrets as _casper_secrets
    HOST_SECRET = _casper_secrets.host_secret()
except Exception:
    HOST_SECRET = ""
BASE = "http://127.0.0.1:8770"


def re_search_sid(line):
    m = re.search(r"casper_sid\s+([0-9a-f]{16})", line)
    return m.group(1) if m else None


def _server_reachable(retries=3, delay=2):
    import time
    for i in range(retries):
        try:
            with urllib.request.urlopen(BASE + "/api/me", timeout=3):
                return True
        except urllib.error.HTTPError:
            return True   # 404/401 でも「到達」とみなす(サーバは生きている)
        except Exception:
            if i < retries - 1:
                time.sleep(delay)   # 自動リロード直後の再起動窓を吸収
    return False


def _sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _curl_json(path, payload, cookies):
    body = json.dumps(payload)
    r = subprocess.run(
        ["curl", "-s", "-c", cookies, "-b", cookies, "-X", "POST", BASE + path,
         "-H", "Content-Type: application/json", "--data-binary", "@-"],
        input=body.encode("utf-8"), capture_output=True, timeout=60)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"_raw": r.stdout.decode("utf-8", "replace")}


def _make_pptx(path):
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
          '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
            '</Relationships>')
    pres = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
    slide = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
             'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
             '<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>'
             '<a:t>gate回帰テスト用スライド 日本語本文確認 文字化けせぬこと</a:t>'
             '</a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("ppt/presentation.xml", pres)
        z.writestr("ppt/slides/slide1.xml", slide)


GUARD_LINE = '                self._json({"ok": False, "error": "本文にデコード不能な文字が多数含まれるため保存を中止しました(バイナリ形式の可能性)"}); return\n'
CHAT_SERVER_PY = os.path.join(HERE, "chat_server.py")

# subtask_488_impl3: 本番chat_server.pyは書き換えない(scripts/配下のmtime変更はsupervisorの
# 自動リロードを誘発し、稼働中サーバを実際に再起動させてしまうため)。代わりに/tmp配下の
# オーバーライドファイル(scripts/外・supervisor監視対象外)をchat_server.py側が実行時に読む
# 注入方式に切り替える(_route_x_guard_enabled()@chat_server.py参照)。
ROUTE_X_GUARD_OVERRIDE_FILE = "/tmp/casper_gate_route_x_guard_override.json"
GATE_LOCK_FILE = "/tmp/casper_gate_upload_binary_roundtrip.lock"


def _set_route_x_guard(disabled, ts=None):
    """ts省略時は現在時刻(通常経路)。impl4是正③の検証用に古いtsを注入できるようts引数を持つ。"""
    import time
    with open(ROUTE_X_GUARD_OVERRIDE_FILE, "w", encoding="utf-8") as f:
        json.dump({"disabled": bool(disabled), "ts": ts if ts is not None else time.time()}, f)


def _clear_route_x_guard_override():
    try:
        os.remove(ROUTE_X_GUARD_OVERRIDE_FILE)
    except FileNotFoundError:
        pass


def _aurora_upload(filename, title, content, dry_run=True):
    """経路X(/api/aurora/upload)を実際にHTTPで叩く。loopback+X-Actor-User-Idは
    identify()がloopback originのみ信頼するため、gate専用の偽uidで認証を得る(本番uidとは無関係)。
    dry_run=True(既定・cmd_488 subtask_488_impl4): ガード判定より後段のcasper_aurora.create()に
    到達させず、本番Auroraへ実書込しない。ガードで拒否されるケース(ok:False)は dry_run の有無に
    関わらず同じ挙動(ガード判定はdry_runチェックより前で行われるため)。"""
    payload = json.dumps({"filename": filename, "title": title, "content": content, "dry_run": dry_run})
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", BASE + "/api/aurora/upload",
         "-H", "Content-Type: application/json",
         "-H", "X-Actor-User-Id: 9999999",
         "-H", "X-Casper-Host-Secret: " + HOST_SECRET,
         "--data-binary", "@-"],
        input=payload.encode("utf-8"), capture_output=True, timeout=30)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"_raw": r.stdout.decode("utf-8", "replace")}


def check_route_x_aurora_binary_reject():
    """経路X回帰チェック: kiyotomo殿が実際に通った/api/aurora/uploadで、
    f.text()相当のUTF-8デコードで潰れたバイナリ本文が黙って保存されず拒否されること。
    かつ正常テキストは引き続き保存できること(fail-closedが過剰検知していないこと)。
    突然変異検証(ガード無効化相当)は本番chat_server.pyを一切書き換えない。
    /tmp配下のオーバーライドファイル(_route_x_guard_enabled()@chat_server.pyが実行時に読む)を
    gate側から書き換えるだけであり、supervisorのmtime監視(scripts/配下限定)には触れない。

    cmd_488 subtask_488_impl4是正: ok:true になり得る全呼出(clean control/mutation check/restore後の
    確認はいずれもok:Falseだが)は dry_run=True で叩く。dry_runはガード判定(拒否されるか否か)の
    "後"・casper_aurora.create()の"前"で止まるため、本番Auroraへは一切書き込まれない。
    ガードで拒否される場合の応答(ok:False)はdry_runの影響を受けない(ガード判定が先に走るため)。
    """
    results = []

    def chk(label, ok):
        results.append(ok)
        print(("✅" if ok else "❌") + f" [経路X] {label}")

    if not os.path.exists(CHAT_SERVER_PY):
        print("⚠ chat_server.py が見当たらず経路Xチェックをスキップ")
        return results
    original = open(CHAT_SERVER_PY, encoding="utf-8").read()
    if GUARD_LINE not in original:
        print("❌ ガード行が見当たらず(是正が退行した疑い) — 経路Xチェックをスキップしてもガード実体喪失はFAIL扱い")
        results.append(False)
        return results

    tmpdir = tempfile.mkdtemp(prefix="gate_route_x_")
    src = os.path.join(tmpdir, "route_x_test.pptx")
    _make_pptx(src)
    corrupted = open(src, "rb").read().decode("utf-8", errors="replace")
    chk("突然変異前提: バイナリのUTF-8decodeにU+FFFDが含まれる(テストデータ健全性確認)",
        corrupted.count("�") >= 3)

    _clear_route_x_guard_override()  # 通常状態(ガード有効)から開始することを保証
    res_bad = _aurora_upload("route_x_test.pptx", "gate route-X reject check", corrupted)
    chk("バイナリ(f.text()相当)は保存されず拒否される", res_bad.get("ok") is False)

    res_ok = _aurora_upload("route_x_clean.md", "gate route-X clean control",
                             "正常なテキスト本文。文字化けなし。")
    chk("正常テキストは引き続き保存できる(過剰検知でない・dry_runで実書込なし)",
        res_ok.get("ok") is True and res_ok.get("dry_run") is True)

    # 突然変異検証: /tmpオーバーライドファイルでガードを無効化→同じ攻撃が今度は通ることを確認→復元
    # (本番ファイルには一切触れないため、supervisor再起動もmd5変化も発生しない)
    try:
        _set_route_x_guard(disabled=True)
        res_mutated = _aurora_upload("route_x_test.pptx", "gate route-X mutation check", corrupted)
        # ガードを外すと拒否されなくなる(dry_run:trueに到達する)=退行を検知できる証拠。実書込は無い。
        mutation_makes_it_red = res_mutated.get("ok") is True and res_mutated.get("dry_run") is True
        chk("突然変異(ガード無効化)で挙動が変わる=このチェックに実効性がある証拠(dry_runで実書込なし)",
            mutation_makes_it_red)

        # 是正③: オーバーライドファイルに古いtsを仕込むと、disabled:trueのままでも安全側(有効)へ倒れる
        _set_route_x_guard(disabled=True, ts=__import__("time").time() - 600)  # 10分前(5分の期限超過)
        res_stale = _aurora_upload("route_x_test.pptx", "gate route-X stale override check", corrupted)
        chk("オーバーライドが古い(10分前)場合、disabled:trueでもガードは安全側(有効)へ倒れ拒否する",
            res_stale.get("ok") is False)
    finally:
        _clear_route_x_guard_override()
        # 復元後、ガードが実際に効いていることを再確認する(オーバーライド解除が有効化されたことの実証)
        res_restored = _aurora_upload("route_x_test.pptx", "gate route-X restore check", corrupted)
        chk("オーバーライド解除後、ガードが再び効く(復元確認)", res_restored.get("ok") is False)

    restored = open(CHAT_SERVER_PY, encoding="utf-8").read()
    chk("chat_server.pyは終始無変更(diff空)", restored == original)

    return results


def main():
    if not _server_reachable():
        print(f"⚠ chat_server({BASE})に到達できず。バイナリ往復ゲートをスキップする(CIを壊さぬためexit 0)。")
        return 0

    vault_files = None
    try:
        sys.path.insert(0, HERE)
        import pack_paths
        vault_files = os.path.join(pack_paths.VAULT, "50_asset_shadows", "files")
    except Exception as e:
        print(f"❌ pack_paths.VAULT を解決できず: {e}")
        return 1

    tmpdir = tempfile.mkdtemp(prefix="gate_upload_rt_")
    cookies = os.path.join(tmpdir, "cookies.txt")

    # セッション確立(casper_sid Cookieは/api/chatのみ発行するため、実ブラウザ同様に先に叩く。
    # /api/chat はndjsonストリーミング応答ゆえ --max-time で打ち切ってもCookieは既に届いている)
    subprocess.run(["curl", "-s", "-c", cookies, "-b", cookies, "-X", "POST", BASE + "/api/chat",
                    "-H", "Content-Type: application/json",
                    "-d", '{"messages":[{"role":"user","content":"gate warmup"}]}',
                    "-o", "/dev/null", "--max-time", "15"], capture_output=True)

    src = os.path.join(tmpdir, "gate_test.pptx")
    _make_pptx(src)
    orig_sha = _sha256(src)

    results = []

    def chk(label, ok):
        results.append(ok)
        print(("✅" if ok else "❌") + f" {label}")

    fn = "gate_upload_rt_check.pptx"
    b64 = base64.b64encode(open(src, "rb").read()).decode("ascii")
    res = _curl_json("/api/uploader/resolve", {"filename": fn, "data_b64": b64, "hint": ""}, cookies)
    chk("resolve が応答した(エラーなし)", "error" not in res)

    sub = _curl_json("/api/uploader/submit", {"intent": "daily", "filename": fn, "note": "gate roundtrip"}, cookies)
    if "error" in sub:
        print(f"  (submit error detail: {sub})")
    chk("submit が written:true を返した", sub.get("ok") is True and sub.get("written") is True)

    dest = os.path.join(vault_files, fn)
    dest_exists = os.path.exists(dest)
    chk(f"保存先ファイルが存在する ({dest})", dest_exists)

    if dest_exists:
        dest_sha = _sha256(dest)
        match = (dest_sha == orig_sha)
        chk(f"sha256完全一致 (orig={orig_sha[:12]}... dest={dest_sha[:12]}...)", match)
    else:
        results.append(False)
        print("❌ sha256比較不可(保存先ファイル無し)")

    # 後始末(本番サーバ上に検証用成果物を残さぬ): vault側の保存物・note、resolveが書いたASSETS_DIR側の実体
    try:
        if dest_exists:
            os.remove(dest)
        note = os.path.join(os.path.dirname(vault_files), "asset_gate_upload_rt_check.md")
        if os.path.exists(note):
            os.remove(note)
        _sid = None
        with open(cookies) as _cf:
            for _line in _cf:
                _m = re_search_sid(_line)
                if _m:
                    _sid = _m
        if _sid:
            _upl = os.path.join(HERE, "assets", f"upl_{_sid}.pptx")
            if os.path.exists(_upl):
                os.remove(_upl)
    except Exception:
        pass

    results += check_route_x_aurora_binary_reject()

    n_ok, n = sum(results), len(results)
    print(f"\n{'✅ 全PASS' if n_ok == n else '❌ FAIL あり'}: {n_ok}/{n}")
    return 0 if n_ok == n else 1


def _run_with_lock():
    """flockで多重起動を排他(subtask_488_impl3: 併走事故の再発防止)。
    ロックが取れなければ(既に走行中なら)即座に降りる(CIを壊さぬためexit 0・スキップメッセージのみ)。"""
    lock_fd = os.open(GATE_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("⚠ 別プロセスがgate_upload_binary_roundtrip.pyを実行中のため、このプロセスは即座に降りる"
              "(併走排他・CIを壊さぬためexit 0)。")
        os.close(lock_fd)
        return 0
    try:
        return main()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(_run_with_lock())
