#!/usr/bin/env python3
r"""推論機の宛先を決める**唯一の関**(2026-08-31・Fable診断 急所1/2)。

【なぜ要るか——実測で判った病】
 ・`distill_activity.py` は `CASPER_ENDPOINT`(env台帳に**存在せぬ鍵名**)を読み、既定が
   .119 に焼き付いていた。ゆえ日次の distill は**必ず禁足席へ 27b を撃って**いた
   (実測: 08-31 13:36 に .119 へ 88.5秒の呼出／同刻 .119 に 27b が17.1GiB在席)。
 ・cron や gate の一発物は env ファイルを source せぬため、悉く焼き付き既定へ落ちる。
 ・★禁足(CASPER_FORBIDDEN_SEATS)は**退避先の選定でしか**検問されておらず、
   呼出の瞬間に検める者が一人も居なかった。台帳が正でも、読まぬ者には効かぬ。

【殿の御裁可(2026-08-24・env台帳に明記)】
 ・禁足席(.119=z8a)は**【生成の席】の話**。機構が勝手に戻れば殿の作業を圧迫する。
 ・★**埋込(bge-m3)は z8a を借り続けてよい**。0.66GB と 19GB では桁が二つ違い、圧迫せぬ。
 ・ゆえ生成と埋込は**別の家**を持つ。一本の switch で束ねてはならぬ
   (実測: 08-24 21:07 の復帰 switch が埋込を .139 へ引きずり、裁可を無言で上書きした)。

【真実源は env ファイル**そのもの**】
 プロセスの環境変数は**起動時の写し**であり、退避機構が台帳を書き換えても古いまま残る
 (「bash常駐は起動時のコードを死ぬまで回す」——同じ型)。ゆえ此処では
 **ファイルを毎度読む**(短命キャッシュつき)。ファイルに無い鍵のみ環境変数で補う。
"""
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, "casper_endpoints.env")
_CACHE = {"ts": 0.0, "data": None}
_TTL = 5.0                       # 短命(退避直後の書換にすぐ追随する)


class ForbiddenSeat(RuntimeError):
    """禁足席へ生成を撃とうとした(殿御下命の違背)。黙って迂回せず、名乗って止める。"""


def _read_file():
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]
    cur = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                cur[k.strip()] = v.strip()
    except Exception:
        cur = {}                 # 読めねば環境変数へ退く(此処で転べば全経路が止まる)
    _CACHE["ts"], _CACHE["data"] = now, cur
    return cur


def _get(key, default=None):
    """★台帳(ファイル)が先、環境変数は補い。理由は頭書きの通り(環境は起動時の写し)。"""
    v = (_read_file().get(key) or "").strip()
    if v:
        return v
    return (os.environ.get(key) or "").strip() or default


def hostport(url):
    return (url or "").rstrip("/").split("://", 1)[-1]


def forbidden_seats():
    """座ってはならぬ**生成**の席(host:port)。空なら禁足なし。"""
    raw = _get("CASPER_FORBIDDEN_SEATS", "") or ""
    return {s.strip() for s in raw.split(",") if s.strip()}


def is_forbidden_gen(url):
    """★完全一致のみ。正しい呼出まで止める検問は無いより悪い(この陣が三度踏んだ)。"""
    return hostport(url) in forbidden_seats()


def gen_endpoint(strict=True):
    """生成の宛先。★禁足席なら黙って迂回せず ForbiddenSeat を投げる(名乗って止める)。

    strict=False は観測・報告の用(現況を知りたいだけの者が例外で転ばぬように)。
    """
    url = _get("CASPER_OLLAMA") or _get("CASPER_HOME_OLLAMA")
    if not url:
        raise RuntimeError("生成の宛先が台帳にも環境にも無い(casper_endpoints.env を確かめられよ)")
    url = url.rstrip("/")
    if strict and is_forbidden_gen(url):
        raise ForbiddenSeat(
            f"生成の宛先 {hostport(url)} は禁足席にござる(殿御下命2026-08-24)。"
            "台帳 CASPER_OLLAMA を確かめられよ——機構は黙って別席へ迂回せぬ。")
    return url


def embed_endpoint():
    """埋込の宛先。★生成とは**別の家**を持つ(殿御裁可: 埋込は z8a を借り続けてよい)。

    順: CASPER_EMBED_ENDPOINT(現在の実効) > CASPER_EMBED_HOME(固定台帳) > 生成の宛先。
    ★禁足の検問は掛けぬ——禁足は生成の席の話である(裁可の文言そのまま)。
    """
    url = _get("CASPER_EMBED_ENDPOINT") or _get("CASPER_EMBED_HOME")
    if url:
        return url.rstrip("/")
    return gen_endpoint(strict=False)


def embed_model():
    return _get("CASPER_EMBED_MODEL", "bge-m3")


def gen_model():
    return _get("CASPER_MODEL", "qwen3.6:27b")


def vision_backend(purpose="read"):
    """画像を誰に見せるか。purpose="read"(読み取り) / "judge"(判断)。

    【殿御裁可 2026-08-31】8/24の「甲=据え置き」を、**用途で分けて**改める。再考条件
    (「.139復電後にVRAM余白を実測」)が満たされ、前提が崩れたゆえ:
      ・`qwen3.6:27b` **自身が vision を備える**(capabilities に vision)。別模型を積む必要が無く、
        実測でも画像turnは load_duration=0.00秒(積み直し無し)・VRAM 15.84GiB→15.84GiB(不変)。
        ★追加GPUは不要——8/24の「押し合い」の前提(vision専用の別模型を同居させる)が消えた。
      ・実務10枚で捏造ゼロ。報告書PDFの数値(18m/19m/6.8m/24,000個/約3週間/8-21–10-8)は悉く正、
        UEスクショは「338 actors (1 selected)」まで一字違わず。
      ・★弱点は**固有名の綴りのみ**(かんなみ→かなみ/墨田→壱田)。これは名簿で機構的に正す。

    ★"judge"(傾向の抽出・高度資料のキャプション注入)は**雲に据え置く**。据え置きの理由①
      「最も難しい判断は能力ある機構へ寄せ、qwen には"読み取れた事実"を渡す」は**未反証**であり、
      今回測ったのは「見えるものを列挙する」仕事のみ。測っておらぬ物を降ろさぬ。
    ★未試験の種類(手書き/低画質写真)も雲のまま(検体が蔵に無く、測れなかった)。

    返り: "local" | "claude_cli" | "off"
    """
    master = (_get("CASPER_VISION", "claude_cli") or "").lower()
    if master == "off":
        return "off"                                   # 主の栓が閉じておれば用途を問わず閉じる
    if purpose != "read":
        return "claude_cli"                            # 判断は雲(据え置き)
    v = (_get("CASPER_VISION_READ", "claude_cli") or "").lower()
    if v != "local":
        return "claude_cli"
    # ★退避中(雲に着座)は地元の模型が居らぬ。地元を指せば「在らぬ者に見せる」ことになる。
    if (_get("CASPER_BACKEND", "ollama") or "").lower() in ("claude_cli", "anthropic"):
        return "claude_cli"
    return "local"


if __name__ == "__main__":
    print(f"生成: {gen_endpoint(strict=False)}  (model={gen_model()})")
    print(f"埋込: {embed_endpoint()}  (model={embed_model()})")
    print(f"禁足席(生成): {sorted(forbidden_seats()) or '(なし)'}")
    print(f"視認(読み取り): {vision_backend('read')} / (判断): {vision_backend('judge')}")
    g = gen_endpoint(strict=False)
    print(f"★生成の宛先は禁足席か: {'はい(違背)' if is_forbidden_gen(g) else 'いいえ'}")
