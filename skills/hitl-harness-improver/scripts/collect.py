#!/usr/bin/env python3
"""hitl-harness-improver: 複数セッション横断で Human-in-the-Loop シグナルを抽出して JSON で吐く。

役割分担の原則 (重要):
  - Python は「履歴を取ってきて、完全に無駄なものを除外して渡すだけ」。
    何が危険か / 是正か / 改善すべきか といった **判断は一切しない**。
  - 判断はすべて LLM (skill 本体) がやる。固定の危険パターンや是正の正規表現で
    Python 側が決め打ちすると汎用性が無い。

Python が出すのは「事実」だけ:
  - Human-in-the-Loop イベント: AskUserQuestion / interrupt / ツール実行後の人間発言
  - それぞれに紐づく素材 (質問文、止められた直前ツール、発言テキスト)
  - 件数・頻度などの集計 (これは事実であって判断ではない)

LLM はこの JSON を読んで、危険か / 是正か / どう直すか を判断する。

Usage:
    collect.py                       # 過去 7 日
    collect.py --days 30
    collect.py --project anime-generation --days 30
    collect.py --all
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _transcript_io import (  # noqa: E402
    iter_transcript_rows,
    list_session_files,
    safe_dump,
)


# タイムスタンプのパース (待機時間の算出に使う)
def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


# ---- キャップ (出力サイズの上限。LLM に渡す量を抑えるだけ。判断ではない) ----------

MAX_SESSIONS = 300
SAMPLE_ASK = 60
SAMPLE_INTERRUPT = 60
SAMPLE_POST_TOOL = 100
HEAD_LEN = 200
TEXT_LEN = 300

INTERRUPT_MARKERS = (
    "[Request interrupted by user for tool use]",
    "[Request interrupted by user]",
)

# 完全に無駄 = 人間が打った発言ではない短文の相槌。是正候補から除くだけ。
# (是正かどうかの判断はしない。明らかなノイズの除外のみ)
SHORT_NOISE = {
    "y",
    "yes",
    "はい",
    "うん",
    "ok",
    "okay",
    "sure",
    "yep",
    "yeah",
    "c",
    "n",
    "go",
    "do it",
    "doit",
    "go ahead",
    "proceed",
    "進めて",
    "やって",
    "それで",
    "それでいい",
    "お願いします",
    "おねがいします",
    "いいよ",
    "いいです",
    "大丈夫",
    "ありがとう",
    "ありがとうございます",
    "thanks",
    "thx",
}

_SKIP_PREFIX = re.compile(
    r"^<(ide_opened_file|ide_selection|local-command-caveat|"
    r"local-command-stdout|system-reminder|command-name|command-message)\b"
)

_INJECTED_PREFIXES = (
    "Base directory for this skill:",
    "Continue from where you left off",
    "Caveat:",
    "[Request interrupted",
)


def _looks_injected(text: str) -> bool:
    t = text.lstrip()
    return any(t.startswith(p) for p in _INJECTED_PREFIXES)


def _bash_signature(cmd: str) -> str:
    """コマンドを頻度集計用に丸めるだけ (危険判定はしない)。"""
    if not cmd:
        return "Bash()"
    head = cmd.splitlines()[0].strip()
    tokens = head.split()
    if not tokens:
        return "Bash()"
    cmd0 = tokens[0]
    if (
        cmd0 in {"npm", "pnpm", "yarn", "uv", "make", "git", "gh", "kubectl", "docker"}
        and len(tokens) >= 2
    ):
        cmd1 = tokens[1]
        if cmd1 == "run" and len(tokens) >= 3:
            return f"Bash({cmd0} run {tokens[2].split(':')[0]}*)"
        return f"Bash({cmd0} {cmd1}*)"
    return f"Bash({cmd0}*)"


def _tool_brief(item: dict) -> dict:
    """tool_use を {name, signature, command_head} に圧縮する (判断なし)。"""
    name = item.get("name") or "unknown"
    inp = item.get("input") or {}
    if name == "Bash":
        cmd = inp.get("command", "")
        head = cmd.splitlines()[0][:HEAD_LEN] if isinstance(cmd, str) and cmd else ""
        return {
            "name": name,
            "signature": _bash_signature(cmd if isinstance(cmd, str) else ""),
            "command_head": head,
        }
    return {"name": name, "signature": f"{name}()", "command_head": ""}


def _user_text_items(content: list) -> list[str]:
    texts: list[str] = []
    for item in content:
        if isinstance(item, str):
            if not _SKIP_PREFIX.match(item):
                texts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            t = item.get("text", "")
            if isinstance(t, str) and t and not _SKIP_PREFIX.match(t):
                texts.append(t)
    return texts


def _is_short_noise(text: str) -> bool:
    stripped = text.strip().lower().rstrip("。.!！")
    return len(text.strip()) <= 20 and stripped in SHORT_NOISE


class Accumulator:
    def __init__(self) -> None:
        self.total_tool_uses = 0
        self.first_ts: str | None = None
        self.last_ts: str | None = None

        self.sessions_total: set[str] = set()
        self.sessions_with_interrupt: set[str] = set()
        self.sessions_with_post_tool_msg: set[str] = set()

        self.ask_count = 0
        self.ask_samples: list[dict] = []

        # 介入は 2 種に分ける:
        #   tool_use = ツール実行を止めた (強い「本来確認すべき」シグナル)
        #   turn     = ターン/生成を止めた (人間が割って入った。casual な中断も含む弱いシグナル)
        self.interrupt_tool_use = 0
        self.interrupt_turn = 0
        self.interrupt_by_sig: Counter[str] = (
            Counter()
        )  # tool_use 中断で止められたツールのみ
        self.interrupt_samples: list[dict] = []

        self.post_tool_count = 0
        self.post_tool_samples: list[dict] = []

        self.command_sig: Counter[str] = Counter()

        # 時間・タイミング系 (事実。タイムスタンプから算出)
        self.ask_wait_seconds: list[float] = []  # AskUserQuestion 発行→回答までの待機秒

    def note_timestamp(self, ts: str) -> None:
        if self.first_ts is None or ts < self.first_ts:
            self.first_ts = ts
        if self.last_ts is None or ts > self.last_ts:
            self.last_ts = ts

    def add_ask_wait(self, seconds: float) -> None:
        if seconds >= 0:
            self.ask_wait_seconds.append(seconds)

    def add_ask(self, session: str, item: dict) -> None:
        self.ask_count += 1
        if len(self.ask_samples) < SAMPLE_ASK:
            questions = (item.get("input") or {}).get("questions") or []
            q0 = questions[0] if questions and isinstance(questions[0], dict) else {}
            self.ask_samples.append(
                {
                    "session": session,
                    "header": q0.get("header"),
                    "question": (q0.get("question") or "")[:TEXT_LEN],
                    "num_questions": len(questions),
                }
            )

    def add_interrupt(
        self, session: str, marker: str, pending_tools: list[dict]
    ) -> None:
        self.sessions_with_interrupt.add(session)
        is_tool_use = "for tool use" in marker
        if is_tool_use:
            self.interrupt_tool_use += 1
            # 止められた直前ツールは tool_use 中断のときだけ意味がある
            for t in pending_tools:
                self.interrupt_by_sig[t["signature"]] += 1
        else:
            self.interrupt_turn += 1
        if len(self.interrupt_samples) < SAMPLE_INTERRUPT:
            self.interrupt_samples.append(
                {
                    "session": session,
                    "type": "tool_use" if is_tool_use else "turn",
                    "interrupted_tools": pending_tools[:3] if is_tool_use else [],
                }
            )

    def add_post_tool_message(
        self, session: str, text: str, preceding_tool: dict
    ) -> None:
        self.post_tool_count += 1
        self.sessions_with_post_tool_msg.add(session)
        if len(self.post_tool_samples) < SAMPLE_POST_TOOL:
            self.post_tool_samples.append(
                {
                    "session": session,
                    "text": text,
                    "preceding_tool": preceding_tool,
                }
            )

    def to_payload(self, scope: dict) -> dict:
        n_sessions = len(self.sessions_total) or 1
        n_interrupted = len(self.sessions_with_interrupt) or 1
        total_interrupt = self.interrupt_tool_use + self.interrupt_turn
        # 介在回数 = エージェント自発確認 + ツール実行を止めた介入 (強いシグナルのみ合算)
        hitl_count = self.ask_count + self.interrupt_tool_use
        return {
            "summary": {
                "scope": scope,
                "session_count": len(self.sessions_total),
                "total_tool_uses": self.total_tool_uses,
                "period_first": self.first_ts,
                "period_last": self.last_ts,
                "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            },
            # --- Human-in-the-Loop の程度 (事実の集計。判断ではない。LLM がここから診断する) ---
            "hitl_degree": {
                "_desc": "Human-in-the-Loop の事実集計 (量・頻度 / 時間・タイミング / 自律性)。Qiita メトリクスのうち transcript から計算できる軸。判断はせず数値だけ。",
                # 量・頻度
                "hitl_count": hitl_count,
                "agent_initiated_asks": self.ask_count,
                "human_interrupt_tool_use": self.interrupt_tool_use,  # ツール実行を止めた (強)
                "human_interrupt_turn": self.interrupt_turn,  # ターンを止めた (弱・casual 含む)
                "post_tool_human_messages": self.post_tool_count,
                # 時間・タイミング
                "ask_wait_seconds_median": _percentile(self.ask_wait_seconds, 0.5),
                "ask_wait_seconds_max": round(max(self.ask_wait_seconds), 1)
                if self.ask_wait_seconds
                else None,
                "ask_wait_samples": len(self.ask_wait_seconds),
                # 分散度 (定量): 介入が何セッションに散らばったか / 介入のあったセッションあたり平均
                "interrupted_sessions": len(self.sessions_with_interrupt),
                "interrupts_per_interrupted_session": round(
                    total_interrupt / n_interrupted, 1
                ),
                # 自律性 (% で表示する想定。0-1 の比率)
                "sessions_total": len(self.sessions_total),
                "sessions_fully_autonomous": len(self.sessions_total)
                - len(self.sessions_with_interrupt),
                "autonomy_rate_pct": round(
                    (1 - len(self.sessions_with_interrupt) / n_sessions) * 100, 1
                ),
            },
            # --- Human-in-the-Loop イベントの素材 (LLM が読んで判断する) ---
            "agent_asks": {
                "_desc": "AskUserQuestion。エージェントが自発的に聞いた所。LLM が「冗長か/妥当か」を判断する。",
                "count": self.ask_count,
                "samples": self.ask_samples,
                "dropped": max(0, self.ask_count - len(self.ask_samples)),
            },
            "human_interrupts": {
                "_desc": "人間が手で止めた所。tool_use=ツール実行を止めた(強いシグナル) / turn=ターンを止めた(人間が割り込み。casual 含む弱)。by_interrupted_signature は tool_use のみ。",
                "tool_use_count": self.interrupt_tool_use,
                "turn_count": self.interrupt_turn,
                "total": total_interrupt,
                "by_interrupted_signature": dict(self.interrupt_by_sig.most_common(25)),
                "samples": self.interrupt_samples,
                "dropped": max(0, total_interrupt - len(self.interrupt_samples)),
            },
            "post_tool_human_messages": {
                "_desc": "ツール実行後の人間の発言 (相槌は除外済み)。是正か/新規指示か は LLM が文脈で判断する。",
                "count": self.post_tool_count,
                "samples": self.post_tool_samples,
                "dropped": max(0, self.post_tool_count - len(self.post_tool_samples)),
            },
            # --- 参考: コマンド頻度 (auto-approve 候補の母数。危険判定はしない) ---
            "command_signature_counts": dict(self.command_sig.most_common(30)),
        }


def _scan_session(path: Path, acc: Accumulator) -> None:
    pending_tools: list[dict] = []
    ask_pending: dict[str, datetime] = {}  # AskUserQuestion tool_use_id -> 発行時刻
    session = path.stem
    acc.sessions_total.add(session)

    for row in iter_transcript_rows(path):
        row_ts = _parse_ts(row.timestamp)
        if row.timestamp:
            acc.note_timestamp(row.timestamp)

        if row.role == "assistant":
            tool_briefs: list[dict] = []
            for item in row.content:
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                acc.total_tool_uses += 1
                name = item.get("name")
                if name == "AskUserQuestion":
                    acc.add_ask(session, item)
                    if item.get("id") and row_ts:
                        ask_pending[item["id"]] = row_ts
                    continue
                brief = _tool_brief(item)
                tool_briefs.append(brief)
                if name == "Bash":
                    acc.command_sig[brief["signature"]] += 1
            if tool_briefs:
                pending_tools = tool_briefs

        elif row.role == "user":
            # AskUserQuestion の回答 (tool_result) を見て待機時間を算出
            for item in row.content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    tuid = item.get("tool_use_id")
                    if tuid in ask_pending and row_ts:
                        acc.add_ask_wait(
                            (row_ts - ask_pending.pop(tuid)).total_seconds()
                        )
            texts = _user_text_items(row.content)
            if not texts:
                continue
            joined = " ".join(texts).strip()
            marker = next((m for m in INTERRUPT_MARKERS if m in joined), None)
            if marker:
                acc.add_interrupt(session, marker, pending_tools)
                continue
            if row.raw.get("isMeta") or _looks_injected(joined):
                continue
            if pending_tools and not _is_short_noise(joined):
                acc.add_post_tool_message(session, joined[:TEXT_LEN], pending_tools[-1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--project", default=None)
    parser.add_argument("--all", action="store_true", help="ignore --days and scan all")
    parser.add_argument("--max-sessions", type=int, default=MAX_SESSIONS)
    args = parser.parse_args()

    days = None if args.all else args.days
    files = list_session_files(days=days, project_substr=args.project)
    capped = files[: args.max_sessions]

    acc = Accumulator()
    for path in capped:
        try:
            _scan_session(path, acc)
        except Exception:  # noqa: BLE001  壊れた 1 セッションで全体を止めない
            continue

    scope = {
        "days": "all" if args.all else args.days,
        "project": args.project,
        "sessions_scanned": len(capped),
        "sessions_matched": len(files),
        "sessions_dropped_by_cap": max(0, len(files) - len(capped)),
    }
    sys.stdout.write(safe_dump(acc.to_payload(scope)))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
