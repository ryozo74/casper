#!/usr/bin/env python3
"""cmd_508病五(道具台帳): 社内ツール名の単一ソース。

★軍師の訂正(実査済・戦略review point_b): 道具台帳なる単一の真実源はこれまで存在せず、
ツール名は chat_server.py 内に文字列リテラルで散在していた
(実測: send_message 30箇所・aurora_create 19・aurora_append 13・update_task 8・
get_messages 8・vimeo_search 3・upload_asset 3・aurora_search 3…)。

本cmdのscopeは「台帳を作る」ところまで(散在リテラルの一斉置換は本番の広範囲に触れるため
危険・cmd_508では狙わない・軍師進言)。既存リテラルは残したまま、新規/改修箇所
(_INTERNAL_TOOL_SCOPE_RE等)からのみ本台帳を参照させる漸進形とする。

TOOLS の各エントリ:
  name        : ツール名(qwen tool-call / MCP のfunction name と一致)
  source      : "chat_server_static"(chat_server.py静的定義) / "mcp"(casper_mcp動的取得)
  self_scoped_search: True なら「このツールが既に自前の検索scopeを持つ」
                       (=外部Web検索がこの語で誤発火してはならぬ・_INTERNAL_TOOL_SCOPE_RE用)
  aliases     : 日本語含む呼称の別名(検索誤発火判定・将来の語彙拡張用)
"""

TOOLS = [
    {"name": "search_vault", "source": "chat_server_static",
     "self_scoped_search": False, "aliases": []},
    {"name": "calendar_lookup", "source": "chat_server_static",
     "self_scoped_search": False, "aliases": []},
    {"name": "vimeo_search", "source": "chat_server_static",
     "self_scoped_search": True, "aliases": ["vimeo", "ヴィメオ", "ビメオ"]},
    {"name": "vimeo_set_password", "source": "chat_server_static",
     "self_scoped_search": False, "aliases": []},
    {"name": "aurora_search", "source": "chat_server_static",
     "self_scoped_search": False, "aliases": ["aurora", "オーロラ"]},
    {"name": "aurora_get", "source": "chat_server_static",
     "self_scoped_search": False, "aliases": []},
    {"name": "aurora_create", "source": "chat_server_static",
     "self_scoped_search": False, "aliases": []},
    {"name": "aurora_append", "source": "chat_server_static",
     "self_scoped_search": False, "aliases": []},
    {"name": "send_message", "source": "mcp",
     "self_scoped_search": False, "aliases": []},
    {"name": "get_messages", "source": "mcp",
     "self_scoped_search": False, "aliases": []},
    {"name": "update_task", "source": "mcp",
     "self_scoped_search": False, "aliases": []},
    {"name": "upload_asset", "source": "mcp",
     "self_scoped_search": False, "aliases": []},
    {"name": "add_reference_material", "source": "mcp",
     "self_scoped_search": False, "aliases": []},
]

_BY_NAME = {t["name"]: t for t in TOOLS}


def get(name):
    """ツール名からエントリを引く。未登録なら None(台帳が未完でも例外にせぬfail-soft)。"""
    return _BY_NAME.get(name)


def self_scoped_search_names():
    """自前の検索scopeを持つツール名の集合(外部Web検索の誤発火除外に使う)。"""
    return {t["name"] for t in TOOLS if t.get("self_scoped_search")}


def self_scoped_search_vocab():
    """self_scoped_search=True なツールの名詞群(ツール名+aliases)を平らな集合で返す。
    _INTERNAL_TOOL_SCOPE_RE の語彙導出に使う——手書き語彙をやめ台帳から生成する。"""
    vocab = set()
    for t in TOOLS:
        if not t.get("self_scoped_search"):
            continue
        vocab.add(t["name"])
        vocab.update(t.get("aliases") or [])
    return vocab
