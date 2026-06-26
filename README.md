# hitl-harness-improver

Claude Code の過去セッションを横断走査し、**実際に起きた Human-in-the-Loop を計測・診断し、現状の `~/.claude/settings.json` を踏まえてハーネス改善まで提案する** Claude Code plugin（1 skill で診断→改善まで）。

設定ファイルを静的に読んで理想形を提案するアプローチ（例: ECC harness-optimizer）との違いは、**現に人間が手で介入した実測点**を起点にすること。

## 何を見るか

transcript JSONL に**実在する** Human-in-the-Loop イベントを抽出する。

| 種類 | シグナル | 意味 |
| --- | --- | --- |
| エージェント起点 | `AskUserQuestion` | 自分で止まって聞いた = 健全 |
| 人間起点・介入（ツール停止） | `[Request interrupted by user for tool use]` | 聞かずに進んだのを手で止めた（強いシグナル） |
| 人間起点・介入（ターン中断） | `[Request interrupted by user]` | 生成を途中で止めた（casual 含む弱） |
| 人間起点・是正 | ツール実行後の「やり直し/否定/要らない」 | 必要な事をやれていない |

これらから Qiita メトリクス（介在回数 / 待機時間 / 自律稼働率 等。[`hitl-metrics.md`](skills/hitl-harness-improver/references/hitl-metrics.md)）と 4 分割診断を出し、現状設定と突き合わせて改善提案する。

## 役割分担

「全部読む」をしない（tokoroten/prompt-review の規律に倣う）。**Python は事実を集めるだけ、判断は LLM**:

- **collect.py (Python)**: スコープを `--days` / `--project` とキャップで絞り、HIL イベントの素材と事実集計（回数・待機時間・自律稼働率）を渡すだけ。危険か/是正かの**判断はしない**。
- **skill (LLM)**: 数値を診断し、危険か/是正かを文脈判断し、現状 settings.json と突き合わせて改善提案を書く。

## インストール (Claude Code plugin)

```text
/plugin marketplace add MasatoraAtarashi/hitl-harness-improver
/plugin install hitl-harness-improver@masatora-marketplace
```

## インストール (手動 clone + symlink)

```bash
ghq get git@github.com:MasatoraAtarashi/hitl-harness-improver.git
ln -sfn ~/ghq/github.com/MasatoraAtarashi/hitl-harness-improver/skills/hitl-harness-improver \
        ~/.claude/skills/hitl-harness-improver
```

## 使い方

```text
/hitl-harness-improver         # 過去 7 日
/hitl-harness-improver 30      # 過去 30 日
/hitl-harness-improver anime-generation 30
/hitl-harness-improver all     # 全期間
```

直接スクリプトを叩く場合:

```bash
python3 ~/.claude/skills/hitl-harness-improver/scripts/collect.py --days 7
python3 ~/.claude/skills/hitl-harness-improver/scripts/collect.py --project anime-generation --days 30
python3 ~/.claude/skills/hitl-harness-improver/scripts/collect.py --all
```

JSON が標準出力に出る。skill 経由で呼ぶと、Claude が JSON を読んで `reports/hitl-harness-improver-YYYY-MM-DD.md` を書く。

## 構成

```
.
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json
    ├── skills/hitl-harness-improver/
    │   ├── SKILL.md                       # 4 ステップの実行フロー
    │   ├── scripts/
    │   │   ├── collect.py                 # 3 種シグナル抽出 + 一次判定
    │   │   └── _transcript_io.py          # 共通ユーティリティ (skill 同梱)
    │   └── references/
    │       ├── hitl-metrics.md            # HITL の定義 + Qiita 定量メトリクス一覧
│       └── report-template.md         # 診断 + 改善提案レポートのフォーマット
└── reports/                           # 生成レポートの出力先 (gitignore)
```

## 自動編集しない理由

誤った `deny` は開発を止め、誤った `allow` は危険操作を素通しさせ、誤った CLAUDE.md 追記は挙動を歪める。可視化と候補提示までで止め、採用は人間が判断する。

## 参考・クレジット

この skill の構造は [tokoroten/prompt-review](https://github.com/tokoroten/prompt-review) を強く参考にしています。具体的に以下を踏襲しました。

- `collect.py` による **Python-first の抽出**（jsonl から必要なものを取り、一次判定まで Python で行い、LLM には絞り込み済みの JSON だけ渡す）
- スコープを `--days` / `--project` フラグ + 各種キャップで絞り、「全部読む」をしない設計
- `SKILL.md` / `scripts/` / `references/` の 3 層構成と、**レポートはファイルに書いてパスを通知する**出力ルール

prompt-review 自体は「プロンプターの理解度・スキル・AI 依存度の診断」が目的で、本 skill とは出力先（人 vs ハーネス設定）が異なりますが、収集スクリプトの作法は大いに学ばせてもらいました。

## 関連 skill

- [MasatoraAtarashi/agent-process-judge](https://github.com/MasatoraAtarashi/agent-process-judge) — **単一セッション**の完了申告を Stop hook 起点で別 subagent に事実確認させる別 skill。
