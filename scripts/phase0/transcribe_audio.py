#!/usr/bin/env python3
"""使用本地 MLX Whisper 转写短音频，不上传原始文件。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def format_timestamp(seconds: float) -> str:
    """把秒数格式化为稳定的时分秒定位。"""

    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_value, milliseconds_value = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds_value:02d}.{milliseconds_value:03d}"


def render_markdown(result: dict[str, Any], model: str, source_sha256: str) -> str:
    """将 Whisper 结果渲染为带模型与时间定位的 Markdown。"""

    lines = [
        "# 本地音频转写",
        "",
        f"> 模型：`{model}`  ",
        f"> 原件 SHA-256：`{source_sha256}`  ",
        "> 状态：模型候选转写，未经人工校对，不得作为正式原话证据",
        "",
    ]
    segments = result.get("segments") or []
    for segment in segments:
        start = format_timestamp(float(segment.get("start", 0.0)))
        end = format_timestamp(float(segment.get("end", 0.0)))
        text = str(segment.get("text", "")).strip()
        if text:
            lines.append(f"- `{start} - {end}` {text}")
    if not segments:
        text = str(result.get("text", "")).strip()
        lines.append(text or "_未得到可用转写。_")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    """解析本地转写参数。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--model", default="mlx-community/whisper-small-mlx")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--initial-prompt", default=None)
    return parser.parse_args()


def main() -> int:
    """下载模型权重后在本机转写，并保存 JSON 与 Markdown。"""

    args = parse_args()
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(args.audio),
        path_or_hf_repo=args.model,
        language=args.language,
        initial_prompt=args.initial_prompt,
        verbose=False,
        word_timestamps=True,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output.with_suffix(".md").write_text(
        render_markdown(result, args.model, args.sha256),
        encoding="utf-8",
    )
    print(json.dumps({"text_length": len(result.get("text", "")), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
