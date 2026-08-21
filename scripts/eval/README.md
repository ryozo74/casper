# Casper 評価問題集（holdout）

## これは何か
Casper が本当に良くなったかを測る物差し。**実際に社員がつまずいた発話**で作ってある。

`gate` の 305件が全緑でも、実測では三回に一回つまずいていた（2026-08-06 週次分析）。
緑が実態を映していないため、実発話で測る。

## ★鉄則（設計顧問 Fable の警告・破ると意味を失う）
1. **これで学習させない。テストの自動生成にも使わない。**
   学習データと検証データが同源になれば、増えた緑は「暗記した」ことしか証明しない。
2. **正解は人が定める。判定モデル（LLM-as-a-Judge）に決めさせない。**
   誤ラベルが固定されると、以後すべてがその誤りに向かって最適化される。
3. **定期に入れ替える。** 同じ問題を使い続ければ、それも暗記される。

## 変更の作法
`holdout.json` の変更は**殿の裁可を要する**。将軍・家老・足軽の判断で書き換えてはならない。
問題を足したい場合は、実ログから候補を選び正解案を添えて殿へ諮ること。

## 構成
- `holdout.json` — 確定版（v1・2026-08-06 裁可）
- `holdout_draft.json` — 草案（履歴として保持）
- 型: A文脈引き継ぎ / B不在断言 / Cできません / D選択 / E DM代筆 / F資料URL / G起票
- `'`付き（A05/B03/D02）は**退行検査** — 直した結果 逆を壊していないかを見る
- `criteria.json` — 16問それぞれの機械判定条件(must/must_not/needs_card/structural)。軍師の甲乙丙分類が土台。
  変更は将軍・家老・足軽で可(holdout.jsonと違い殿裁可は不要)だが、`change_log`に理由を残すこと。
  点を上げるためだけに条件を緩めることは厳禁。
- `run_holdout.py` — 実投入と三値判定(pass/fail/review)の本体。下記AC6参照。
- `results/<run_id>.json` — 実行結果(型別集計・不合格問の応答全文・holdout.jsonのsha256等)。

## AC6: 実行方法(殿・将軍が自分で走らせる手順)

前提: Casperサーバが起動していること(`curl -s localhost:8770/health` が200を返せばOK)。

```bash
cd projects/casper/scripts/eval

# 第1巡のみ(全16問を1回ずつ・約8分)
python3 run_holdout.py

# 第1巡+第2巡(fail/reviewだった問だけ再投入して揺れ=flakyを見る・既定off・合計15〜20分程度)
python3 run_holdout.py --round2

# 実行IDを指定したい時(省略時は run_<unixtime>)
python3 run_holdout.py --round2 --run-id my_run_20260810

# 全問を特定uidで強制実行したい時(既定は各問のsrc人物から自動解決: tetsuo=30/殿=28/kiyotomo=31/ou=36)
python3 run_holdout.py --uid 28
```

出力は `results/<run_id>.json` に書かれる。`summary.counts` に型別pass/fail/review/flaky/untestable件数、
`summary.score_pass_over_pass_plus_fail` に点数(review・untestable件数は分母(pass+fail)から
除外・隠さず併記)、`fail_or_review_full_answers` に不合格/要人判定問の応答全文が入る。標準出力にも
1問ずつの判定と根拠(`reasons`)、および「対象問題数/未測定数」を明示した集計行がリアルタイムで出る
(将軍裁定・cmd_500第2便: 未測定を分母から静かに落とさず必ず表に出す)。

## ★運用上必須の工程: review問の読了(将軍裁定・cmd_500第2便)

`summary.counts.review` に計上された問は、機械では pass/fail を断ぜられないという意味であり、
「合格でも不合格でもない」で済ませてよいものではない。**実行者(殿・将軍)は必ず
`fail_or_review_full_answers` の該当問の応答全文を読み、人の目で判定すること。**
review件数が0でない限り、その回のholdout実行は「読了するまで未完了」として扱う。
機械が判定しきれない問を放置したまま「score = pass/(pass+fail)」だけを見て良否を語ってはならない。

## untestable区分について(cmd_500第2便で新設)

pass/fail/reviewいずれとも異なる第四区分。holdoutのexpectを実際には測れていない問(現状はG01のみ)
に付く。fail確定にして点を下げることも、緩めてpassにして点を上げることもせず、「測れておらぬ」と
正直に申告する。score算出の分母(pass+fail)から除外されるが、`summary.counts.untestable` と
`summary.counts.total` に必ず数として現れる(黙って消えない)。criteria.jsonの該当項目に
`untestable` フィールド(理由文字列)を設定することで発火する。

### 既知の制約(2026-08-06 cmd_500第2便時点)
- **G01(起票)= untestable**: 実際のExcelアップロード経路(`/api/project_import/preview`)は
  ローカルLLMでの構造化に100〜270秒(cmd_496実測)を要し、`/api/chat`のテキスト説明だけでは
  承認カードまで到達しない(=機械的に毎回fail)。holdoutのexpect(構造化が通り承認カードまで
  到達する)を実際には測れていないため、cmd_500第2便でfail確定から`untestable`区分へ変更した
  (criteria.jsonのG01.untestableフィールド参照)。score算出の分母(pass+fail)からは除外されるが、
  `summary.counts.untestable`に必ず数として現れる。実アップロード経路を実測する専用の長時間
  実行枠は将来課題(cmd_500のscope外)。
- **D01のprev**: holdout.json内でこの問だけ`prev`が「（Casperが①②と列挙した直後）」という
  状況記述(地の文)であり、実際に人が打った文ではない。run_holdout.pyはこれを検知して(先頭が
  全角/半角の開き括弧)そのままprevとして投げず、qのみを単独で投げる(prev_sent=None)。
  D01はcmd_499是正により当初の状況(①②の列挙直後)が再現不能であり、qの『1』が行き止まりに
  ならぬことのみを見る限定検証(将軍裁定・判定ロジックは変更しない。criteria.jsonのD01.note参照)。
- **E01のprev = prev_substitute**: E01も同型の地の文prevだが、cmd_500第2便でD01とは扱いを
  変えた。「（Scoreのステータス説明を提示した直後）」という状況は実際に人へ翻訳可能(D01と違い
  再現不能ではない)と軍師が裁定したため、criteria.jsonのE01に`prev_substitute`
  (`"Scoreのステータスを説明して"`)を設定し、地の文の代わりにこれを実際に投げる。
  結果ファイルの各問レコードには`prev`(holdout.json原文の地の文、参照用)と`prev_sent`
  (実際に投げた文。E01なら`prev_substitute`の値、D01のような地の文かつsubstitute無しなら`null`、
  それ以外は`prev`と同一)を分けて記録している——どちらが実際に送られたかを後から機械的に
  確認できるようにするため。
