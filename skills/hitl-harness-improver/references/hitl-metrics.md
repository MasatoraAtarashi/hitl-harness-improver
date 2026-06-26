# Human-in-the-Loop の定義と定量評価メトリクス

この skill が扱う「Human-in-the-Loop」が何を指すのか、そして Human-in-the-Loop をどう定量評価しうるのかの全体像を定義する。**ここが正典**であり、後述のシグナル（interrupt / AskUserQuestion / 是正）は「現状この skill が transcript から拾っている観測例」にすぎない。シグナルの集合を固定の定義として扱わない。

## Human-in-the-Loop とは

Human-in-the-Loop = **エージェントの自律ループの中に、人間の判断・介入が入る瞬間**。

本 skill の文脈では、以下のいずれも Human-in-the-Loop イベントとして扱う:

- エージェントが自分から人間に確認を求めた（agent-initiated）
- 人間がエージェントの動作を止めた / 差し戻した（human-initiated）

Human-in-the-Loop は「悪」でも「善」でもなく、**適切な所で適切な頻度で起きているか**が問題。多すぎれば人間のコストとスループット低下、少なすぎれば事故のリスク。だから定量的に測って設計にフィードバックする。

一般的に Human-in-the-Loop は **HITL** と略される。

## Human-in-the-Loop 定量評価メトリクス一覧

Human-in-the-Loop を測る指標は多岐にわたる。下表は cvusk の整理を基にした全体像（出典は末尾）。本 skill が今すぐ全部を計算できるわけではない（次節参照）が、「何を目指して測りうるか」の地図として持っておく。

### 量・頻度

| メトリクス | 目的・説明 |
| --- | --- |
| 介在回数 (Human-in-the-Loop Count) | エージェントが人間の介在を要求した総回数。最も基本的で、他すべての分母。 |
| 必要性率 (Human-in-the-Loop Necessity Rate) | 発生した Human-in-the-Loop のうち本当に介在が必要だった割合。即時無条件承認（ラバースタンプ）を識別し、不要なチェックポイントの廃止候補を検出。 |
| 技術的回避可能率 (Technically Avoidable Human-in-the-Loop Rate) | アーキテクチャ改善や最新モデル採用で回避可能だった Human-in-the-Loop の割合。人間コスト vs AI 改善投資コストの比較根拠。 |

### 時間・タイミング

| メトリクス | 目的・説明 |
| --- | --- |
| 待機時間 (Human-in-the-Loop Wait Time) | リクエスト発行から人間応答までの時間。エージェントのアイドル時間 = スループットへの影響。 |
| 業務時間内起動率 (Business Hours Human-in-the-Loop Rate) | Human-in-the-Loop が人間の業務時間内に発生した割合。時間外は応答遅延 → スケジューリング設計の品質。 |
| バッチング率 (Human-in-the-Loop Batching Rate) | 複数リクエストが一定ウィンドウ内にまとまった割合。人間のコンテキストスイッチ削減。 |
| 割り込み分散度 (Interrupt Fragmentation Score) | Human-in-the-Loop が人間の集中作業をどれだけ分断したか。総数が同じでも散発的だとコストが大きい。 |

### 人間コスト・注意力

| メトリクス | 目的・説明 |
| --- | --- |
| Attention Budget 消費量 | Human-in-the-Loop に人間が費やした時間・認知リソースの総量を工数/金額で可視化。費用対効果。 |
| 判断疲労指標 (Human-in-the-Loop Fatigue Index) | 1 日の Human-in-the-Loop 増加に伴い応答時間が延びる/品質が下がる傾向。バッチングやローテーション設計の閾値根拠。 |
| コンテキスト十分性スコア (Context Sufficiency Score) | Human-in-the-Loop 時の提示情報だけで人間が追加調査なしに判断できた割合。低ければ情報設計に改善余地。 |

### 判断品質・価値

| メトリクス | 目的・説明 |
| --- | --- |
| 判断覆し率 (Human-in-the-Loop Override Rate) | 人間が提案を修正・却下した割合。低ければラバースタンプ（廃止候補）、高ければエージェント側に改善余地。 |
| 反事実的価値 (Human-in-the-Loop Counterfactual Value) | 人間が介在しなかった場合の推定損失額。Human-in-the-Loop の ROI を直接計算し、少数の高価値 Human-in-the-Loop を捕捉。 |
| 通過後エラー率 (Post-Human-in-the-Loop Error Rate) | 人間が承認したのに後工程でエラーが出た率。人間の判断品質そのものを測る。 |
| 応答時間 vs 判断品質カーブ | 応答時間と判断品質の相関。深夜の即時承認 vs 業務時間の熟考承認の品質差を明らかにする。 |

### リスク・安全性

| メトリクス | 目的・説明 |
| --- | --- |
| リスク加重価値 (Risk-Weighted Human-in-the-Loop Value) | blast radius で重み付けした価値。高リスク Human-in-the-Loop の品質管理と低リスク Human-in-the-Loop の自動化判断を分離。 |
| スキップコスト (Human-in-the-Loop Skip Cost) | 自動承認/タイムアウト自動進行の導入時に実際に発生したインシデントのコスト。自律性向上圧へのブレーキ。 |
| 見逃し修復コスト (Recovery Cost per Missed Human-in-the-Loop) | 起動すべきだったのにしなかった場合の修復コスト。Human-in-the-Loop の「保険としての価値」を定量化。 |

### 信頼・自律性進化

| メトリクス | 目的・説明 |
| --- | --- |
| 自律稼働率 (Autonomy Ratio) | 人間の介在なく稼働した時間の割合。システム全体の成熟度の最上位指標。 |
| 卒業率 (Human-in-the-Loop Graduation Rate) | 特定カテゴリの Human-in-the-Loop が不要になり自動化された割合。構造的に人間が必要な領域と自動化可能な領域の分離。 |
| 信頼キャリブレーションスコア (Trust Calibration Score) | 「自信がないから聞く」判断の精度。Human-in-the-Loop トリガーの Precision/Recall として評価。 |

### システム設計フィードバック

| メトリクス | 目的・説明 |
| --- | --- |
| カスケード率 (Human-in-the-Loop Cascade Rate) | 1 つの Human-in-the-Loop が追加 Human-in-the-Loop を誘発した割合。高ければ粒度が細かすぎ or 依存関係分析不足。 |
| 粒度ミスマッチ率 (Human-in-the-Loop Granularity Mismatch Rate) | 「この粒度では判断できない」「もっと早く聞いてほしかった」と FB された率。チェックポイント設計の適切性。 |

### スループット・成果

| メトリクス | 目的・説明 |
| --- | --- |
| タスク完了数 (Agent Task Throughput) | 単位時間に完了したタスク数。Human-in-the-Loop 設計の最終ゴールはこの最大化。 |

### 複合指標

| メトリクス | 目的・説明 |
| --- | --- |
| 効率スコア (Human-in-the-Loop Efficiency Score) | Human-in-the-Loop が防いだ推定損失額 ÷（待機コスト + 人間対応コスト + コンテキストスイッチコスト）。各チェックポイントの存続/廃止を判断する統合 ROI。 |

## この skill で実際に観測できるもの / できないもの

Claude Code の transcript JSONL から取れる情報は限られる。**正直に切り分ける**。

| 区分 | メトリクス | 本 skill での扱い |
| --- | --- | --- |
| ✅ 計算可能 | 介在回数 | AskUserQuestion + interrupt の件数で算出 |
| 〜 近似 | 必要性率 / Override 率 | 「介入/是正されたか」で近似（人間が止めた = 必要だった寄り、止めず通った = 不要だった寄り） |
| 〜 近似 | 信頼キャリブレーション | AskUserQuestion が妥当な所で出ているか（人間が後で覆したか）で部分的に |
| ❌ 観測不能 | 待機時間 / 業務時間内率 / 疲労 / Attention Budget | 人間の応答時刻・工数が JSONL に無い |
| ❌ 観測不能 | 反事実的価値 / スキップコスト / 見逃し修復コスト | 「介在しなかった場合の損失」は推定不能 |
| ❌ 観測不能 | Post-Human-in-the-Loop エラー率 / 粒度ミスマッチ率 | 後工程の結果・人間 FB が紐付かない |

→ 本 skill は当面 **量・頻度**（と Override/Necessity の近似）に閉じる。残りは「測るならどういうデータが要るか」を示す地図として持つ。

## シグナル（観測の例。固定の定義ではない）

上のメトリクスを transcript から拾うための**現状の観測手段の例**。あくまで例であり、Human-in-the-Loop の定義そのものではない。観測手段は今後増やしたり差し替えたりしてよい。

| シグナル | transcript 上の出方 | 主に寄与するメトリクス |
| --- | --- | --- |
| `AskUserQuestion` | assistant の tool_use。`input.questions[].{question, header}` | 介在回数（agent-initiated） |
| `[Request interrupted by user (for tool use)]` | user の text。直前 assistant の tool_use に紐付け | 介在回数（human-initiated）/ Necessity 近似 |
| ツール実行後の人間発言（是正候補） | user の自然言語。是正か否かは LLM が判定 | Override 率 / 必要性率 の近似 |
| 危険シグナル正規表現 | Bash コマンドの `--no-verify` / `git push --force` / `rm -rf` / `chmod 777` / `curl \| sh` 等 | リスク加重の入力（本来確認を要する性質か） |

## 出典

- Human-in-the-Loop 定量評価メトリクスの整理: cvusk「Human-in-the-Loop」 <https://qiita.com/cvusk/items/fe61b526babf45429ba1>
