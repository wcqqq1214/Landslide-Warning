"""Run the landslide-warning workflow from one auditable entry point."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Stage:
    name: str
    script: str
    description: str


STAGES = (
    Stage("features", "code/features.py", "生成统一特征表和切线角参数"),
    Stage("onset", "code/onset_analysis.py", "生成未来 onset 标签和事件盘点"),
    Stage("shap", "code/shap_select.py", "训练解释模型并输出 SHAP 分析"),
    Stage("convlstm", "code/convlstm.py", "训练 ConvLSTM 位移区间预测模型"),
    Stage("ngboost", "code/ngboost_warn.py", "训练 NGBoost 预警概率模型"),
    Stage("fusion", "code/warning_fusion.py", "融合 V0、切线角和概率旁证"),
    Stage("sensitivity", "code/sensitivity_analysis.py", "执行预设参数敏感性分析"),
    Stage(
        "tangent-review",
        "code/tangent_stage_review.py",
        "生成等速阶段专家复核材料",
    ),
)
STAGE_BY_NAME = {stage.name: stage for stage in STAGES}
Runner = Callable[..., subprocess.CompletedProcess]


def select_stages(
    selected: Sequence[str] | None = None,
    skipped: Sequence[str] | None = None,
) -> list[Stage]:
    """Return requested stages in the canonical workflow order."""
    selected_names = set(selected or STAGE_BY_NAME)
    skipped_names = set(skipped or ())
    return [
        stage
        for stage in STAGES
        if stage.name in selected_names and stage.name not in skipped_names
    ]


def run_pipeline(
    stages: Sequence[Stage],
    *,
    dry_run: bool = False,
    runner: Runner = subprocess.run,
) -> None:
    """Run each stage in an isolated Python process and stop on first failure."""
    if not stages:
        print("[pipeline] 没有需要执行的阶段")
        return

    total_start = time.perf_counter()
    completed: list[tuple[str, float]] = []
    print(
        "[pipeline] 执行顺序: " + " -> ".join(stage.name for stage in stages),
        flush=True,
    )

    for index, stage in enumerate(stages, start=1):
        command = [sys.executable, str(ROOT / stage.script)]
        print(
            f"\n[pipeline] [{index}/{len(stages)}] {stage.name}: {stage.description}",
            flush=True,
        )
        print("[pipeline] 命令: " + " ".join(command), flush=True)
        if dry_run:
            continue

        stage_start = time.perf_counter()
        try:
            runner(command, cwd=ROOT, check=True)
        except subprocess.CalledProcessError:
            elapsed = time.perf_counter() - stage_start
            total_elapsed = time.perf_counter() - total_start
            print(f"[pipeline] 失败: {stage.name} ({elapsed:.1f}s)", file=sys.stderr)
            print(
                f"[pipeline] 已完成 {len(completed)}/{len(stages)} 个阶段，"
                f"总耗时 {total_elapsed:.1f}s",
                file=sys.stderr,
            )
            raise
        elapsed = time.perf_counter() - stage_start
        completed.append((stage.name, elapsed))
        print(f"[pipeline] 完成: {stage.name} ({elapsed:.1f}s)")

    if dry_run:
        print("\n[pipeline] dry-run 完成，未执行任何脚本")
        return

    total_elapsed = time.perf_counter() - total_start
    timing = ", ".join(f"{name}={elapsed:.1f}s" for name, elapsed in completed)
    print(f"\n[pipeline] 全部完成 ({total_elapsed:.1f}s): {timing}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行滑坡预测与预警研究管线")
    parser.add_argument(
        "--stage",
        action="append",
        choices=STAGE_BY_NAME,
        help="只执行指定阶段，可重复使用；按标准流程顺序运行",
    )
    parser.add_argument(
        "--skip",
        action="append",
        choices=STAGE_BY_NAME,
        default=[],
        help="跳过指定阶段，可重复使用",
    )
    parser.add_argument("--dry-run", action="store_true", help="只显示执行顺序和命令")
    parser.add_argument("--list", action="store_true", help="列出可用阶段后退出")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = subprocess.run,
) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        for stage in STAGES:
            print(f"{stage.name:16s} {stage.description}")
        return 0

    stages = select_stages(args.stage, args.skip)
    try:
        run_pipeline(stages, dry_run=args.dry_run, runner=runner)
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
