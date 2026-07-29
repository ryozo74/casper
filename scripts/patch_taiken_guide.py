#!/usr/bin/env python3
"""Casper 体験ガイド(Aurora doc 2978eda6)を更新する。既存の意匠・class語彙をそのまま使う。
 ① 01「まず開く」のスマホ注記を、通知の前提(ログイン/ホーム画面追加)へ繋げる
 ② 03「できること」に 📦 Dropbox転送(フォルダ丸ごと・アカウント不要)を追加
 ③ 新章「04 携帯で通知を受け取る」= 殿御所望のプッシュ通知設定指南
 ④ 既存の「04 安心して触ってよい理由」を 05 へ繰り下げ
"""
import re
import sys

src = sys.argv[1]
dst = sys.argv[2]
h = open(src, encoding="utf-8").read()
orig = h

# ── ① スマホ注記を差し替え(通知の章へ導く) ───────────────────────────
old_note = ('<div class="note">証明書の警告が出たら「詳細 → アクセスする」で進む（社内サーバのため安全）。'
            'ホーム画面に追加＆通知ONで先回りお知らせが届く。</div>')
new_note = ('<div class="note">証明書の警告が出たら「詳細 → アクセスする」で進む（社内サーバのため安全）。'
            '<b>ホーム画面に追加</b>してそこから開くと、先回りの通知を受け取れる → <b>04章</b>参照。</div>')
assert old_note in h, "スマホ注記が見つからぬ"
h = h.replace(old_note, new_note)

# ── ② 03「できること」に Dropbox転送カードを足す ─────────────────────
#    ✅タスクを操作する カードの直後(= caps グリッドの末尾)に挿す
anchor = '''      <div class="ex">このSHOTを review に上げて</div>
    </div>'''
assert anchor in h, "タスク操作カードの末尾が見つからぬ"
dbx_card = anchor + '''

    <div class="cap card">
      <div class="ct"><span class="ce">📦</span>ファイル／フォルダを外へ渡す</div>
      <p>添付やドラッグ＆ドロップで Dropbox へ上げ、<b>パスワード付きの1リンク</b>を発行する。
        <b>フォルダを丸ごと投げても可</b>（中身をまとめて1リンク・zipで届く）。
        受け取る相手は <b>Dropboxアカウント不要</b>——リンクとパスワードだけで落とせる。
        リンクの下に「☑ 匿名検査済」と出れば、社外の相手にパスワードが要る状態を機構が確かめた印。</p>
      <ol>
        <li>ファイル／フォルダを画面へドロップ</li>
        <li>「📦 Dropboxで送る」を押す</li>
        <li>出たリンクとパスワードを相手へ（コピー用ボタンあり）</li>
      </ol>
    </div>'''
h = h.replace(anchor, dbx_card)

# ── ③ 新章「04 携帯で通知を受け取る」を挿し、既存04を05へ ─────────────
old_h2_safe = '<h2><span class="n">04</span> 安心して触ってよい理由</h2>'
assert old_h2_safe in h, "04章の見出しが見つからぬ"

push_section = '''<!-- ============ 通知設定 ============ -->
  <h2><span class="n">04</span> 携帯で通知を受け取る</h2>
  <div class="h-sub">先回りの知らせを携帯へ届ける設定。<b>一度だけ</b>で済む。順に踏めば3分ほど。</div>

  <div class="caps">

    <div class="cap card">
      <div class="ct"><span class="ce">📲</span>手順 ①　携帯で開いて「ホーム画面に追加」</div>
      <p>通知は <b>https（8443）</b>で開いた時だけ使える。さらに <b>iPhone はホーム画面へ追加し、
        そのアイコンから開く</b>ことが必須（Safariのタブから開いたままでは通知が使えぬ・iOS 16.4以降）。</p>
      <ol>
        <li>携帯のブラウザで <b>https://192.168.44.45:8443</b> を開く</li>
        <li>証明書の警告 →「詳細」→「アクセスする」で進む（社内サーバゆえ安全）</li>
        <li>iPhone: 共有ボタン <b>⤴</b> →「ホーム画面に追加」／Android: メニュー →「ホーム画面に追加」</li>
        <li><b>ホーム画面のアイコンから開き直す</b>（ここが要）</li>
      </ol>
    </div>

    <div class="cap card">
      <div class="ct"><span class="ce">👤</span>手順 ②　先にログインする</div>
      <p>通知は「誰に届けるか」を要するため、<b>その端末でログイン済であること</b>が前提。
        右上に「ゲスト」と出ていたら未ログイン。押すとログイン画面へ進める。</p>
      <ol>
        <li>右上の <b>👤</b> の表示を見る</li>
        <li>「ゲスト」なら押してログイン（社内アカウント）</li>
        <li>自分の名前が出れば準備完了</li>
      </ol>
    </div>

    <div class="cap card">
      <div class="ct"><span class="ce">🔔</span>手順 ③　通知をONにする</div>
      <p>画面上の <b>🔔 通知</b> を押し、端末が尋ねてくる確認に<b>「許可」</b>と答える。
        表示が <b>🔔 ON ✓</b> に変われば完了。以後この端末へ届く。</p>
      <ol>
        <li>上部の <b>🔔 通知</b> を押す</li>
        <li>「通知を許可しますか？」→ <b>許可</b></li>
        <li>表示が <b>🔔 ON ✓</b> になったのを確かめる</li>
      </ol>
    </div>

    <div class="cap card">
      <div class="ct"><span class="ce">⚙️</span>手順 ④　受け取る種類を選ぶ</div>
      <p>ONにした後もう一度 <b>🔔</b> を押すと、種類の一覧が出る。要らぬものは切ってよい。
        <b>切り替えた時点で保存</b>される。</p>
      <ol>
        <li>☀️ <b>朝のブリーフ</b> — 今日の要点をまとめて朝に</li>
        <li>🔴 <b>納期超過</b> — 期限を過ぎたタスクが出た時</li>
        <li>⏳ <b>FB／確認の停滞</b> — 確認待ちが動かぬ時</li>
        <li>💬 <b>新着DM</b> — 自分宛のDMが届いた時</li>
        <li>✅ <b>約束の完了</b> — 追っていた件が片付いた時</li>
      </ol>
    </div>

  </div>

  <div class="safe card">
    <div class="ct"><span>🧰</span>うまくいかぬ時（🔔の表示で分かる）</div>
    <ul>
      <li><b>「🔔 要ログイン」</b>と出る → その端末でまだログインしておらぬ。右上 👤 からログインし、もう一度 🔔 を押す。</li>
      <li><b>「🔔 通知(ブロック中)」</b>と出る → 過去に「許可しない」を選んでおる。端末の設定 → 通知（またはブラウザのサイト設定）で許可し直す。</li>
      <li><b>「🔔 通知(非対応)」</b>と出る／押しても何も起きぬ → <b>http（8770）で開いておらぬか</b>、
        <b>iPhoneでホーム画面のアイコンから開いておらぬか</b>を確かめる。この二つが最も多い。</li>
      <li><b>ONにしたのに届かぬ</b> → <b>社内ネットワークに居るか</b>を確かめる。社外や別のWi-Fiからは届かぬ。
        社内Wi-Fiでも、来客用など別の網に繋がっておると届かぬことがある。</li>
      <li><b>それでも直らぬ</b> → Casper に「通知が届かぬ」と話しかけてくだされ。状態を調べて次の手を示す。</li>
    </ul>
  </div>

  <!-- ============ 安心 ============ -->
  ''' + old_h2_safe.replace('<span class="n">04</span>', '<span class="n">05</span>')

h = h.replace('<!-- ============ 安心 ============ -->\n  ' + old_h2_safe, push_section)
if push_section not in h:                      # コメント位置が違う版への保険
    h = h.replace(old_h2_safe, push_section)

assert '<span class="n">05</span> 安心して触ってよい理由' in h, "05への繰り下げに失敗"
assert '携帯で通知を受け取る' in h, "通知章の挿入に失敗"
assert h != orig, "何も変わっておらぬ"
open(dst, "w", encoding="utf-8").write(h)
print("更新前 %d字 → 更新後 %d字 (+%d)" % (len(orig), len(h), len(h) - len(orig)))
print("章:", re.findall(r'<h2><span class="n">(\d+)</span>\s*([^<]+)</h2>', h))
