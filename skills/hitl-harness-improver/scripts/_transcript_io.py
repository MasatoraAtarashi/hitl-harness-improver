"""Claude Code の transcript JSONL を読み書きする共通ユーティリティ。

両 skill (agent-process-judge / hitl-harness-improver) から import して使う想定。
プロセス境界をまたぐ pickle / serialization は意識せず、純粋な JSON 操作と
ファイルシステム操作に閉じる。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


def claude_projects_root() -> Path:
    """Claude Code のプロジェクトルート (~/.claude/projects) を返す。"""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    return base / "projects"


def list_session_files(
    *,
    days: int | None = None,
    project_substr: str | None = None,
) -> list[Path]:
    """transcript JSONL の一覧を返す。

    - days を指定すると mtime ベースで past N 日に絞る
    - project_substr を指定すると親ディレクトリ名に部分一致するものに絞る
    """
    root = claude_projects_root()
    if not root.exists():
        return []
    files: list[Path] = []
    cutoff = None
    if days is not None and days > 0:
        cutoff = datetime.now(tz=timezone.utc).timestamp() - days * 86400
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        if project_substr and project_substr not in project_dir.name:
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            if cutoff is not None and jsonl.stat().st_mtime < cutoff:
                continue
            files.append(jsonl)
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def find_session_file(session_id: str) -> Path | None:
    """session_id (ファイル名 stem) から JSONL を引き当てる。"""
    root = claude_projects_root()
    if not root.exists():
        return None
    for jsonl in root.rglob(f"{session_id}.jsonl"):
        return jsonl
    return None


@dataclass
class TranscriptRow:
    """transcript JSONL の 1 行を表す軽量モデル。"""

    raw: dict[str, Any]
    timestamp: str | None
    role: str | None
    content: list[Any]
    model: str | None
    usage: dict[str, Any] | None


def iter_transcript_rows(path: Path) -> Iterator[TranscriptRow]:
    """JSONL を 1 行ずつ TranscriptRow として返す (壊れた行はスキップ)。"""
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                # user-typed メッセージは content が素の文字列のことがある。
                # text item 1 個に正規化して下流から見えるようにする。
                content = [{"type": "text", "text": content}] if content else []
            elif not isinstance(content, list):
                content = []
            yield TranscriptRow(
                raw=obj,
                timestamp=obj.get("timestamp"),
                role=msg.get("role"),
                content=content,
                model=msg.get("model"),
                usage=msg.get("usage") if isinstance(msg.get("usage"), dict) else None,
            )


def iter_tool_uses(
    path: Path, name: str | None = None
) -> Iterator[tuple[TranscriptRow, dict[str, Any]]]:
    """transcript から tool_use エントリだけを取り出す。name で絞れる。"""
    for row in iter_transcript_rows(path):
        for item in row.content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_use":
                continue
            if name is not None and item.get("name") != name:
                continue
            yield row, item


def iter_user_texts(path: Path) -> Iterator[str]:
    """user メッセージ中の自然言語テキストを yield する (system-reminder などは除外)。"""
    skip_prefix = re.compile(
        r"^<(ide_opened_file|ide_selection|local-command-caveat|local-command-stdout|system-reminder)\b",
    )
    for row in iter_transcript_rows(path):
        if row.role != "user":
            continue
        for item in row.content:
            if isinstance(item, str):
                if not skip_prefix.match(item):
                    yield item
            elif isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if not skip_prefix.match(text):
                    yield text


@dataclass
class UsageTotals:
    """transcript 内の usage 集計。"""

    input_tokens: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output_tokens: int = 0
    messages_with_usage: int = 0
    models: dict[str, int] = field(default_factory=dict)
    first_timestamp: str | None = None
    last_timestamp: str | None = None

    def cost_usd(self, prices: dict[str, float] | None = None) -> float:
        """概算費用を返す。デフォルト単価は Anthropic Claude Opus 4.x。"""
        p = prices or {
            "input": 15.0 / 1_000_000,
            "cache_creation": 18.75 / 1_000_000,
            "cache_read": 1.5 / 1_000_000,
            "output": 75.0 / 1_000_000,
        }
        return (
            self.input_tokens * p["input"]
            + self.cache_creation * p["cache_creation"]
            + self.cache_read * p["cache_read"]
            + self.output_tokens * p["output"]
        )


def aggregate_usage(path: Path) -> UsageTotals:
    """transcript 全体の usage を集計する。"""
    totals = UsageTotals()
    for row in iter_transcript_rows(path):
        if row.timestamp:
            if totals.first_timestamp is None or row.timestamp < totals.first_timestamp:
                totals.first_timestamp = row.timestamp
            if totals.last_timestamp is None or row.timestamp > totals.last_timestamp:
                totals.last_timestamp = row.timestamp
        if not row.usage:
            continue
        totals.messages_with_usage += 1
        totals.input_tokens += int(row.usage.get("input_tokens", 0) or 0)
        totals.cache_creation += int(
            row.usage.get("cache_creation_input_tokens", 0) or 0
        )
        totals.cache_read += int(row.usage.get("cache_read_input_tokens", 0) or 0)
        totals.output_tokens += int(row.usage.get("output_tokens", 0) or 0)
        if row.model:
            totals.models[row.model] = totals.models.get(row.model, 0) + 1
    return totals


def duration_seconds(totals: UsageTotals) -> float | None:
    """first/last timestamp の差分秒数 (取得できなければ None)。"""
    if not totals.first_timestamp or not totals.last_timestamp:
        return None
    try:
        first = datetime.fromisoformat(totals.first_timestamp.replace("Z", "+00:00"))
        last = datetime.fromisoformat(totals.last_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (last - first).total_seconds()


def safe_dump(obj: Any) -> str:
    """JSON dump (Path 等を文字列化)。"""

    def default(o: Any) -> str:
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return repr(o)

    return json.dumps(obj, ensure_ascii=False, default=default, indent=2)


def collect_user_texts(rows: Iterable[str], limit: int | None = None) -> list[str]:
    """user_texts iterator から先頭 limit 件を集める (None なら全件)。"""
    out: list[str] = []
    for i, t in enumerate(rows):
        if limit is not None and i >= limit:
            break
        out.append(t)
    return out
