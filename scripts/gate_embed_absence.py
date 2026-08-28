#!/usr/bin/env python3
r"""「混雑を不在と名乗るな」の回帰ゲート(殿御下命2026-08-29・Fable処方「丁」)。全PASSで exit 0。

実害(2026-08-26): `.139` の `/api/generate` と `/api/embed` が90秒超で無応答になった。
`ping` 0.6ms・`/api/tags` 27ms は即答——**行列を通らぬ口だけが生きている**型である。
このとき再索引は `skipped(bge-m3不在・字面索引のみ最新)` と刻んだ。
だが **在庫は在った**(/api/tags に bge-m3 を確認済)。sqlite 30091行 / chunks 30098行、
意味ベクトルが静かに欠け始めていた。混雑・無応答・不在が**同じ一つの出口**へ流れていた。

守る掟(鉄則「失敗とゼロを別の出口へ」の具体):
 ① 在庫照会は**三値**(在る/無い/訊けなんだ)。「訊けなんだ」を「無い」へ倒さぬ。
 ② **不在を名乗るのは在庫照会が『無い』と答えた時のみ**。それ以外で不在と名乗らぬ。
 ③ 混雑(503/429)・時間切れ・不通・空返しは、それぞれ別の名で刻む。
 ④ 従前の契約(embed_batch → vectors|None)は壊さぬ。理由が要る呼び手だけが _ex を使う。
 ★突然変異: 出口をひとつに畳むと赤化することを実証する。
"""
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import casper_embed as E                                  # noqa: E402

results = []


def chk(name, cond):
    results.append(bool(cond))
    print(("✅" if cond else "❌") + f" {name}")


class _Resp:
    """urlopen の身代わり(context manager)。★本番と同じ形の JSON を返す。"""

    def __init__(self, payload):
        self._p = payload

    def read(self):
        import json as _j
        return _j.dumps(self._p).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def install(tags=None, tags_exc=None, embed=None, embed_exc=None):
    """/api/tags と /api/embed の応答を差し替える。★辞書でなく**URLで**振り分ける
    (添字で置くと検体が届かぬ事故を過去に踏んだゆえ)。"""
    def fake(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if "/api/tags" in url:
            if tags_exc is not None:
                raise tags_exc
            return _Resp(tags)
        if "/api/embed" in url:
            if embed_exc is not None:
                raise embed_exc
            return _Resp(embed)
        raise AssertionError("検体が届いておらぬ URL: " + url)
    E.urllib.request.urlopen = fake


_REAL_URLOPEN = urllib.request.urlopen
IN_STOCK = {"models": [{"name": E.MODEL}, {"name": "qwen3.6:27b"}]}
NO_STOCK = {"models": [{"name": "qwen3.6:27b"}]}
OK_EMBED = {"embeddings": [[0.1, 0.2, 0.3]]}
E503 = urllib.error.HTTPError("http://x/api/embed", 503, "Service Unavailable", {}, None)
E404 = urllib.error.HTTPError("http://x/api/embed", 404, "Not Found", {}, None)
TIMEOUT = TimeoutError("timed out")
REFUSED = urllib.error.URLError(ConnectionRefusedError("Connection refused"))

try:
    # ── ① 在庫照会は三値 ─────────────────────────────────────────────
    print("── ① 在庫照会 ──")
    install(tags=IN_STOCK)
    chk("① 在れば True", E.model_in_stock() is True)
    install(tags=NO_STOCK)
    chk("① 無ければ False", E.model_in_stock() is False)
    install(tags_exc=REFUSED)
    chk("① ★訊けなんだ時は None(False へ倒さぬ)", E.model_in_stock() is None)
    install(tags={"models": [{"name": E.MODEL.split(":")[0] + ":latest"}]})
    chk("① タグ違いの同名は在庫と見なす", E.model_in_stock() is True)

    # ── ② 不在を名乗るのは在庫が『無い』時のみ ───────────────────────
    print("── ② 出口の名づけ ──")
    install(tags=NO_STOCK, embed_exc=E404)
    chk("② 在庫が無い → model_absent", E.embed_batch_ex(["x"])[1] == "model_absent")
    install(tags=IN_STOCK, embed_exc=E503)
    chk("② ★在庫が在って 503 → busy(不在と名乗らぬ)", E.embed_batch_ex(["x"])[1] == "busy")
    install(tags=IN_STOCK, embed_exc=TIMEOUT)
    chk("② ★在庫が在って時間切れ → timeout(不在と名乗らぬ)", E.embed_batch_ex(["x"])[1] == "timeout")
    install(tags_exc=REFUSED, embed_exc=REFUSED)
    chk("② ★在庫すら訊けぬ → unreachable(不在と名乗らぬ)", E.embed_batch_ex(["x"])[1] == "unreachable")
    install(tags=IN_STOCK, embed_exc=E404)
    chk("② 在庫が在るのに404 → error(不在と名乗らぬ)", E.embed_batch_ex(["x"])[1] == "error")
    install(tags=IN_STOCK, embed={"embeddings": []})
    chk("② ★空返しも失敗(『在るのに0件』と名乗らせぬ)", E.embed_batch_ex(["x"])[1] == "empty")
    install(tags=IN_STOCK, embed=OK_EMBED)
    _v, _r = E.embed_batch_ex(["x"])
    chk("② 成った時は ok とベクトル", _r == "ok" and _v == [[0.1, 0.2, 0.3]])

    # ── ③ 名乗りが取り違えられておらぬ ───────────────────────────────
    print("── ③ 刻まれる文言 ──")
    chk("③ 不在の文言にだけ『bge-m3不在』が入る",
        "bge-m3不在" in E.EMBED_SKIP_MSG["model_absent"]
        and not any("bge-m3不在" in v for k, v in E.EMBED_SKIP_MSG.items() if k != "model_absent"))
    chk("③ 混雑は混雑と名乗り、在庫が在ることも申す",
        "混雑" in E.EMBED_SKIP_MSG["busy"] and "在庫は在り" in E.EMBED_SKIP_MSG["busy"])
    chk("③ 不通は『届かず』と名乗る", "届かず" in E.EMBED_SKIP_MSG["unreachable"])
    chk("③ どの出口も『字面索引のみ最新』は伝える(退避先を隠さぬ)",
        all("字面索引のみ最新" in v for v in E.EMBED_SKIP_MSG.values()))
    chk("③ 出口は6つ(畳まれておらぬ)", len(set(E.EMBED_SKIP_MSG.values())) == 6)

    # ── ④ 従前の契約を壊さぬ ─────────────────────────────────────────
    print("── ④ 後方互換 ──")
    install(tags=IN_STOCK, embed=OK_EMBED)
    chk("④ embed_batch は従前どおりベクトルのみ返す", E.embed_batch(["x"]) == [[0.1, 0.2, 0.3]])
    install(tags=IN_STOCK, embed_exc=E503)
    chk("④ 失敗時も従前どおり None", E.embed_batch(["x"]) is None)

    # ── ⑤ 結線(再索引が理由を運ぶ) ───────────────────────────────────
    print("── ⑤ 結線 ──")
    SRC = open(os.path.join(HERE, "casper_embed.py"), encoding="utf-8").read()
    chk("⑤ 再索引が embed_batch_ex を使う", "vecs, _why = embed_batch_ex(gt)" in SRC)
    chk("⑤ 再索引が理由から文言を引く", 'EMBED_SKIP_MSG.get(_why' in SRC)
    chk("⑤ 再索引の戻りに理由そのものも残す", '"reason": _why' in SRC)
    chk("⑤ 『bge-m3不在』の直書きが再索引から消えている",
        SRC.count('"skipped(bge-m3不在・字面索引のみ最新)"') == 1)   # EMBED_SKIP_MSG の1箇所のみ
    chk("⑤ 全量埋込の中断表示も理由を出す", 'print("  バッチ埋め込み失敗: " + EMBED_SKIP_MSG.get(_why' in SRC)

    # ── ★突然変異 ────────────────────────────────────────────────────
    print("\n--- 突然変異検証 ---")
    _real_diag = E._diagnose_embed_failure
    E._diagnose_embed_failure = lambda exc=None: "model_absent"     # 出口をひとつに畳む
    install(tags=IN_STOCK, embed_exc=E503)
    chk("★変異(出口を畳む): 在庫が在る混雑を『不在』と名乗ってしまう(赤化実証)",
        E.embed_batch_ex(["x"])[1] == "model_absent")
    E._diagnose_embed_failure = _real_diag

    _real_stock = E.model_in_stock
    E.model_in_stock = lambda timeout=3: False                       # 「訊けなんだ」を「無い」へ倒す
    install(tags_exc=REFUSED, embed_exc=REFUSED)
    chk("★変異(三値を二値へ潰す): 届かぬだけの機を『不在』と断じてしまう(赤化実証)",
        E.embed_batch_ex(["x"])[1] == "model_absent")
    E.model_in_stock = _real_stock
finally:
    E.urllib.request.urlopen = _REAL_URLOPEN

n_ok, n = sum(1 for r in results if r), len(results)
print(("\n✅ 全PASS: " if n_ok == n else "\n❌ FAIL あり: ") + f"{n_ok}/{n}")
sys.exit(0 if n_ok == n else 1)
