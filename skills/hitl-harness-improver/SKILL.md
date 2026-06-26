---
name: hitl-harness-improver
description: >
  Claude Code の過去セッションを横断走査し、実際に起きた Human-in-the-Loop を
  計測・診断し、現状の ~/.claude/settings.json を踏まえてハーネス改善まで提案する skill。
  「人間が手でツール実行を止めた」「エージェントが自発的に確認した」「人間が事後に是正した」
  という transcript に実在するイベントを Python で抽出し、介在回数・待機時間・自律稼働率
  などの定量メトリクスと 4 分割診断を出し、それと現状設定を突き合わせて
  permissions.allow/deny/ask・Stop hook・CLAUDE.md の改善候補を出す。設定の自動編集はしない。
  ユーザーが「最近の運用を振り返りたい」「Human-in-the-Loop の頻度や程度を見たい」
  「ハーネス/権限設定を見直したい」「どこで人間が介入/是正しているか知りたい」「自律性を測りたい」
  と言った時、定期実行 (週次/月次) で運用を見直したい時、`/hitl-harness-improver` で呼ばれた時に使う。
allowed-tools: Read, Write, Glob, Grep, Bash
context: fork
---

# hitl-harness-improver

## 何をするか

人間とエージェントの協働がうまくいっているかは、設定ファイルを眺めても分からない。**実際に人間がどこで・どれだけ介入したか**を測って初めて分かる。

この skill は 1 つで以下まで通す:

1. 過去 N 日のセッションから「Human-in-the-Loop が実際に発生した瞬間」を抽出
2. [`references/hitl-metrics.md`](references/hitl-metrics.md) の定量メトリクス + 4 分割で **診断**
3. **現状の `~/.claude/settings.json`（permissions / hooks）を読み**、診断と突き合わせて **改善提案**（既にある設定は再提案しない）

> 設定ファイルを静的に読むだけのアプローチ（例: ECC harness-optimizer）との違いは、**現に人間が手で介入した実測点**を起点にすること。設定の自動編集はせず、提案までで止める。

## いつ使うか

- `/hitl-harness-improver` で明示的に呼び出されたとき
- 週次 / 月次でハーネスを振り返って調整したいとき

## 引数

`$ARGUMENTS` を解析する:

- 引数なし → 過去 7 日分の全プロジェクト
- 数値 (例: `30`) → 過去 30 日分
- `all` / `0` → 全期間
- 文字列 (例: `anime-generation`) → プロジェクト名 substr フィルタ
- 文字列 + 数値 → プロジェクト名 + 期間

## Human-in-the-Loop イベントの種類 (観測している素材)

transcript に**実在する**イベントを数える。誰が起点かで意味が違う。

| Human-in-the-Loop の種類 | 抽出シグナル | 意味 |
| --- | --- | --- |
| **エージェント起点** | `AskUserQuestion` | エージェントが自分で止まって聞いた = 健全 |
| **人間起点 (介入)** | `[Request interrupted by user (for tool use)]` | 聞かずに進んだのを人間が手で止めた |
| **人間起点 (是正)** | ツール実行後の人間の発言が「やり直し/否定/要らない」 | 必要な事をやれていない = 意図/文脈の取り違え |

Human-in-the-Loop の定義と定量評価メトリクスの全体像は [`references/hitl-metrics.md`](references/hitl-metrics.md)（Qiita メトリクス一覧）を参照。上の 3 種は観測の例であって固定の定義ではない。

## 役割分担 (重要)

**Python は履歴を取って無駄を除いて渡すだけ。判断は一切しない**（固定の危険パターンや是正の正規表現で Python に決め打ちさせない。汎用性が無くなる）。

- **collect.py (Python・判断なしのコレクター)**: スコープ flag とキャップで絞り、Human-in-the-Loop イベントの素材と**事実の集計**（回数・待機時間・業務時間内率・自律稼働率）を渡すだけ。
- **この skill (LLM・診断)**: 数値を解釈して協働の状態を言語化し、`post_tool_human_messages` が**本物の是正か**、`human_interrupts` で**何が問題で止めたか**を文脈判断する。

## 実行フロー

### Step 1: データ収集

```bash
OUTFILE="/tmp/hitl-harness-improver_$(date +%Y%m%d%H%M%S).json"
python3 "<skill-base>/scripts/collect.py" [OPTIONS] > "$OUTFILE"
```

オプション: `--days <N>` (デフォルト 7) / `--project <substr>` / `--all` / `--max-sessions <N>`

出力 JSON のキー（すべて事実。判断は入っていない）:

- `summary` — スコープ / セッション数 / 期間
- `hitl_degree` — 量・頻度（`hitl_count` = 自発確認 + ツール停止、`agent_initiated_asks`、`human_interrupt_tool_use`(ツール停止=強)、`human_interrupt_turn`(ターン中断=弱・casual)）、時間（`ask_wait_seconds_median/max`）、分散（`interrupted_sessions` / `interrupts_per_interrupted_session`）、自律（`autonomy_rate_pct`）
- `agent_asks` — AskUserQuestion の件数と質問サンプル
- `human_interrupts` — `tool_use_count`(強) / `turn_count`(弱) / `by_interrupted_signature`(tool_use のみ) / サンプル
- `post_tool_human_messages` — ツール実行後の人間発言（相槌は除外済み。**是正かは LLM が判断**）
- `command_signature_counts` — 頻出コマンド（参考）

### Step 2: メトリクス算出 + 4 分割分析（LLM）

**(a) メトリクス表**: [`references/hitl-metrics.md`](references/hitl-metrics.md) の Qiita 一覧のうち transcript で出せるものを [`references/report-template.md`](references/report-template.md) の列（メトリクス / 説明 / 値 / 算出 / 根拠・備考）で全部埋める。

- **単位を揃える**: 割合は `%`（`0.69` でなく `69%`）。数えられるもの（必要性率 / 技術的回避可能率 / Override / 分散度）は数値、判断が要るものだけ 高/中/低。
- **長い根拠は「根拠・備考」列へ**（値セルに詰めない）。
- **介入は 2 種を分けて扱う**: `human_interrupt_tool_use`(ツール停止=本来確認すべきの強シグナル) と `human_interrupt_turn`(ターン中断=casual 含む弱)。③ の根拠は tool_use 側だけ使う。
- 実測 = `hitl_degree` の数値。LLM 推測 = `agent_asks` / `human_interrupts`(tool_use) / `post_tool_human_messages` から見積もり、根拠を添える。
- 載せない: 人間の工数・損失額・後工程結果が要るもの。

**(b) 4 分割分析**（診断の本体）: 観測した介在と自律実行を、いいやつ 2・ダメなやつ 2 に振り分ける。各見出しに具体例 + LLM の判断。

1. ✅ **適切に人間が介在した場所**（agent が妥当に聞いた / 人間が止めて正解）
2. ⚠️ **不必要に人間が介在した場所**（確認過多。本来自動化できた）
3. ❌ **すべきだったのに素通りで実行された場所**（ツール停止がその証拠。危険）
4. ✅ **適切に自律できた場所**（介在不要で正しく自走。自律稼働率で裏付け）

固定ルールで決め打ちしない。`by_interrupted_signature`(何を止めたか)・`post_tool_human_messages`(是正)を **LLM が文脈判断**（read 系の停止は危険でなく確認過多寄り、のような判断も LLM）。

### Step 3: 現状設定を読む

`Read` で `~/.claude/settings.json` を読み、`permissions.allow` / `permissions.deny` / `permissions.ask` / `hooks` を把握する。**`env` の API キー等の秘密情報はレポートに出さない**（permissions と hooks の名前だけ使う）。

### Step 4: ハーネス改善提案（診断 × 現状設定）

診断（Step 2）と現状設定（Step 3）を突き合わせ、[`references/report-template.md`](references/report-template.md) の改善提案セクションを書く。**既に設定済みのものは再提案しない**（「既に deny 済み」と明記）。

- **permissions.allow 追加**: ②確認過多 / ③で止められた read 系のうち、`allow` に**未登録**で安全なもの（例: `grep` `gh api`）。既に allow 済みは除外。
- **permissions.deny / ask 追加**: ③の危険な素通り。既に deny 済み（`git push --force` 等）なら「設定済み」と書き、未カバーのものだけ提案。「毎回は嫌だがたまに止めたい」は `ask`（例: 広域 `git add`）。
- **Stop hook**: deny で拾いにくい操作の事後検知（必要時のみ）。
- **CLAUDE.md / 初期指示**: 繰り返す是正（スコープの先走り / 前提の取り違え 等）を最初から防ぐ追記。

各提案に「対象」「根拠（診断のどの数値/事例から）」「現状（既設定か否か）」「採用判断のポイント」を付ける。自動編集はしない。

### Step 5: レポート出力

[`references/report-template.md`](references/report-template.md) の「① メトリクス表 → ② 4 分割分析 → ③ 改善提案」で書く。

1. `mkdir -p reports`
2. Write で `reports/hitl-harness-improver-YYYY-MM-DD.md` に全文を書く
3. チャットには **3〜5 行サマリ + パス** だけ返す

```
🪢 hitl-harness-improver: <期間> / <セッション数>件 を診断しました。
- 介在 <N>（自発確認 <N> / ツール停止 <N>）/ 自律稼働率 <X>%
- 改善案: allow に <X> 追加 / CLAUDE.md に <Y> 追記 など <N> 件
レポート: reports/hitl-harness-improver-YYYY-MM-DD.md
```

> 設定の自動編集はしない。提案までで止め、採用は人間が手で settings.json / CLAUDE.md に反映する。
