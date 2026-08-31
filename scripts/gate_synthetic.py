#!/usr/bin/env python3
r"""門・試験が本番の台帳を汚さぬための小さな盾(2026-08-31)。

【実測で判った病】門が合成の失敗を**本番の黒匣**(queue/casper_incident.jsonl)へ書き込んでいた。
本日だけで `gate_embed_absence` が .119 宛の偽incidentを8件×6回=48件、`gate_embed_truth` が
host `http://h:1` を10件。★この陣には既に掟が在った——「合成試験は CASPER_SYNTHETIC=1 を名乗れ」
——にも関わらず、門自身がそれを破っていた。緑の門が、帳簿に嘘を積んでいた。

使い方(門の冒頭で一度):
    import gate_synthetic; gate_synthetic.shield()          # 名札＋台帳の隔離
    import gate_synthetic; gate_synthetic.shield(isolate=False)   # 名札のみ(実呼出を測る門)

★isolate=True は「本番の台帳に一行も書かせぬ」(試験は本番に触れぬ)。
★isolate=False は「本物の宛先を叩くが、合成と名乗る」——実疎通を測る門はこちら。
  消費者(casper_health 等)は synthetic 行を分母から外すゆえ、名乗れば数は濁らぬ。
"""
import os
import tempfile


def shield(isolate=True):
    """合成の名札を立て(必須)、望まれれば台帳の置き場を一時領域へ逸らす。"""
    os.environ["CASPER_SYNTHETIC"] = "1"
    if not isolate:
        return None
    try:
        import casper_llm_client as _LLC
    except Exception:
        return None
    # ★INCIDENT_LOG 等は import 時に確定する定数ゆえ、QUEUE_DIR だけ差し替えても効かぬ
    #   (「塞いだつもりで塞げておらぬ」を実測で踏んだ——書いた後に必ず数えて確かめよ)。
    d = tempfile.mkdtemp(prefix="gate_synthetic_ledger_")
    _LLC.QUEUE_DIR = d
    _LLC.INCIDENT_LOG = os.path.join(d, "casper_incident.jsonl")
    _LLC.INFLIGHT_DIR = os.path.join(d, "ollama_inflight")
    _LLC.INFLIGHT_ORPHAN_LOG = os.path.join(d, "ollama_inflight_orphan.jsonl")
    return d
