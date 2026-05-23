#!/usr/bin/env python3
"""
Shine It Asset Agent — One-command launcher.

Usage (after unzip):
    python3 start.py

What it does:
  1. Creates .env (prompts for FAL_KEY if missing)
  2. Installs pip dependencies (if missing)
  3. Lets you pick a level
  4. Runs Phase 0 (setup wizard)
  5. Optionally runs the full pipeline end-to-end (Phase 1 → 7)
"""
from __future__ import annotations
import os
import re
import sys
import subprocess
from pathlib import Path

HERE     = Path(__file__).resolve().parent
ENV      = HERE / ".env"
REQ      = HERE / "pipeline" / "requirements.txt"
PIPELINE = HERE / "pipeline" / "shine_it_pipeline.py"

C_RESET = "\033[0m"; C_BOLD = "\033[1m"
C_DIM   = "\033[2m"; C_GREEN = "\033[32m"
C_BLUE  = "\033[34m"; C_YELLOW = "\033[33m"


def banner():
    print(f"{C_BLUE}╔══════════════════════════════════════════════════════════════════╗")
    print(f"║  {C_BOLD}Shine It Asset Agent — Launcher{C_RESET}{C_BLUE}                                ║")
    print(f"╚══════════════════════════════════════════════════════════════════╝{C_RESET}")


def step(n: int, total: int, title: str):
    print(f"\n{C_BOLD}[{n}/{total}] {title}{C_RESET}")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" {C_DIM}[{default}]{C_RESET}" if default else ""
    raw = input(f"  {prompt}{suffix}: ").strip()
    return raw or default


def yesno(prompt: str, default_yes: bool = True) -> bool:
    suffix = "(Y/n)" if default_yes else "(y/N)"
    raw = input(f"  {prompt} {suffix}: ").strip().lower()
    if not raw:
        return default_yes
    return raw in ("y", "yes")


def setup_env():
    if ENV.exists():
        print(f"  {C_GREEN}✓{C_RESET} .env có sẵn")
        return
    print(f"  Lấy FAL_KEY tại: {C_BLUE}https://fal.ai/dashboard/keys{C_RESET}")
    key = ask("Nhập FAL_KEY", default="")
    if not key:
        print(f"  {C_YELLOW}⚠{C_RESET} Tạo .env với placeholder — bạn cần edit lại trước khi chạy phase 1+")
        key = "your-fal-key-here"
    ENV.write_text(f"FAL_KEY={key}\n")
    print(f"  {C_GREEN}✓{C_RESET} Đã tạo .env")


def install_deps():
    try:
        import fal_client  # noqa
        import rembg       # noqa
        import PIL         # noqa
        print(f"  {C_GREEN}✓{C_RESET} Tất cả dependencies đã được cài")
        return
    except ImportError:
        pass
    print(f"  Đang cài (lần đầu mất 1-2 phút)...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(REQ)],
        check=True,
    )
    print(f"  {C_GREEN}✓{C_RESET} Done")


def pick_level() -> int:
    levels = sorted(
        d for d in (HERE / "projects").glob("level_*") if d.is_dir()
    )
    if not levels:
        sys.exit(f"  {C_YELLOW}Không tìm thấy level nào trong projects/{C_RESET}")
    print("  Levels có sẵn:")
    for i, d in enumerate(levels):
        print(f"    [{i+1}] {d.name}")
    while True:
        sel = ask("Chọn level", default="1")
        try:
            chosen = levels[int(sel) - 1]
            m = re.match(r"level_(\d+)", chosen.name)
            if m:
                return int(m.group(1))
        except (ValueError, IndexError):
            pass
        print(f"  {C_YELLOW}Số không hợp lệ. Thử lại.{C_RESET}")


def run_phase(level: int, phase: str, extra: list[str] | None = None,
              auto_yes: bool = False) -> int:
    cmd = [sys.executable, str(PIPELINE),
           "--level", str(level), "--phase", phase]
    if auto_yes:
        cmd.append("--yes")
    if extra:
        cmd.extend(extra)
    print(f"\n{C_DIM}$ {' '.join(cmd)}{C_RESET}")
    return subprocess.call(cmd)


def main():
    banner()

    # ── 1. .env ─────────────────────────────────────────────────────────────
    step(1, 4, "Setup .env")
    setup_env()

    # ── 2. deps ─────────────────────────────────────────────────────────────
    step(2, 4, "Kiểm tra dependencies")
    install_deps()

    # ── 3. pick level ───────────────────────────────────────────────────────
    step(3, 4, "Chọn project")
    level = pick_level()

    # ── 4. main menu ─────────────────────────────────────────────────────────
    step(4, 4, f"Workflow cho level {level}")
    print()
    print(f"  {C_BOLD}A.{C_RESET} 🆕 {C_BOLD}Full pipeline cho level mới{C_RESET}")
    print(f"     {C_DIM}Phase 0 (setup) → 0.5 (concept) → 1 (anchor) → 2 (lint) → 3 (smart) → 3b → 5 → 6{C_RESET}")
    print(f"     {C_DIM}~$1.31 · ~12 phút · có pause tại complex states để duyệt{C_RESET}")
    print()
    print(f"  {C_BOLD}B.{C_RESET} 🔧 {C_BOLD}Regen từng phase{C_RESET} (level đã có assets, chỉ sửa 1 chỗ)")
    print()
    print(f"  {C_BOLD}C.{C_RESET} 👀 {C_BOLD}Mở review HTML + feedback server{C_RESET} (assets đã có sẵn)")
    print()
    print(f"  {C_BOLD}D.{C_RESET} 💡 {C_BOLD}Concept Board only{C_RESET} ($0.05, không commit production)")
    print()
    print(f"  {C_BOLD}E.{C_RESET} 📋 In hướng dẫn các lệnh thủ công")
    print()
    choice = ask("Chọn (A/B/C/D/E)", default="A").upper()

    if choice == "A":
        _run_full_pipeline(level)
    elif choice == "B":
        _run_single_phase(level)
    elif choice == "C":
        run_phase(level, "6", extra=["--serve"])
    elif choice == "D":
        run_phase(level, "0.5")
    else:
        _print_manual_commands(level)


# ─────────────────────────────────────────────────────────────────────────────
# Workflow A: Full pipeline với gate ở từng phase
# ─────────────────────────────────────────────────────────────────────────────

def _run_full_pipeline(level: int):
    """End-to-end run with confirm gates between phases."""
    # Phase 0: setup wizard
    print(f"\n{C_BLUE}━━ Phase 0 — Setup wizard ━━{C_RESET}")
    rc = run_phase(level, "0")
    if rc != 0: sys.exit(rc)

    # Phase 0.5: concept board (optional, recommended for first time)
    print(f"\n{C_BLUE}━━ Phase 0.5 — Concept Board (~$0.05) ━━{C_RESET}")
    print(f"  {C_DIM}Generate 1 ảnh grid preview tất cả states để duyệt nhanh trước khi spend $1.{C_RESET}")
    if yesno("Tạo concept board trước?", default_yes=True):
        rc = run_phase(level, "0.5", auto_yes=True)
        if rc != 0:
            print(f"  {C_YELLOW}Phase 0.5 fail. Tiếp tục?{C_RESET}")
            if not yesno("Tiếp tục Phase 1?", default_yes=True):
                sys.exit(rc)
        else:
            staging = HERE / "projects"
            # find concept_board.png path
            for lvl_dir in (HERE / "projects").glob(f"level_{level:02d}_*"):
                cb = lvl_dir / "staging" / "concept_board.png"
                if cb.exists():
                    print(f"  {C_GREEN}✓{C_RESET} Concept board: open {cb}")
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", str(cb)])
                    break
            if not yesno("Concept ok, tiếp tục với production assets?", default_yes=True):
                print(f"  {C_YELLOW}Dừng. Sửa items_config.json nếu cần rồi chạy lại.{C_RESET}")
                sys.exit(0)

    # Phase 1: anchor + auto-select
    print(f"\n{C_BLUE}━━ Phase 1 — Anchor candidates (~$0.08) ━━{C_RESET}")
    rc = run_phase(level, "1")
    if rc != 0: sys.exit(rc)

    # Phase 2: validate + lint + dependency map (free)
    print(f"\n{C_BLUE}━━ Phase 2 — Validate + lint (free) ━━{C_RESET}")
    rc = run_phase(level, "2")
    if rc != 0:
        if not yesno("Có warnings/errors. Tiếp tục?", default_yes=False):
            sys.exit(rc)

    # Phase 3: chain — pipeline tự hỏi mode (batch/waterfall/smart) tại runtime
    print(f"\n{C_BLUE}━━ Phase 3 — Chain states (~$0.18) ━━{C_RESET}")
    print(f"  {C_DIM}Pipeline sẽ hỏi mode: batch (gen hết) / waterfall (approve từng cái) / smart (pause complex).{C_RESET}")
    rc = run_phase(level, "3")
    if rc != 0: sys.exit(rc)

    # Phase 3b: subparts (batch — usually fewer issues here)
    print(f"\n{C_BLUE}━━ Phase 3b — Subparts (~$0.69) ━━{C_RESET}")
    if yesno("Chạy Phase 3b?", default_yes=True):
        rc = run_phase(level, "3b", auto_yes=True)
        if rc != 0: sys.exit(rc)

    # Phase 5: postprocess (rembg)
    print(f"\n{C_BLUE}━━ Phase 5 — Postprocess (rembg, free) ━━{C_RESET}")
    rc = run_phase(level, "5")
    if rc != 0: sys.exit(rc)

    # Phase 6: review + serve
    print(f"\n{C_GREEN}✓ Pipeline xong!{C_RESET}")
    if yesno("Mở review HTML + feedback server (Phase 6 --serve)?", default_yes=True):
        run_phase(level, "6", extra=["--serve"])


# ─────────────────────────────────────────────────────────────────────────────
# Workflow B: Pick a single phase to regen
# ─────────────────────────────────────────────────────────────────────────────

def _run_single_phase(level: int):
    """Run just one phase (for partial regen)."""
    phases = [
        ("0",   "Setup wizard"),
        ("0.5", "Concept Board ($0.05)"),
        ("1",   "Anchor candidates ($0.08)"),
        ("2",   "Validate + lint (free)"),
        ("3",   "Chain states ($0.18, sẽ hỏi mode: batch/waterfall/smart)"),
        ("3b",  "Subparts ($0.69)"),
        ("5",   "Postprocess (rembg, free)"),
        ("6",   "Review HTML + serve"),
        ("7",   "Promote final → approved"),
    ]
    print()
    for i, (ph, name) in enumerate(phases):
        print(f"  [{i+1}] Phase {ph:4} — {name}")
    sel = ask("Chọn phase", default="6")
    try:
        ph = phases[int(sel)-1][0]
    except (ValueError, IndexError):
        print(f"  {C_YELLOW}Không hợp lệ.{C_RESET}")
        return

    extra = []
    # Phase 3: pipeline tự hỏi mode (đừng force ở đây)
    if ph == "6":
        extra = ["--serve"]
    run_phase(level, ph, extra=extra)


def _print_manual_commands(level: int):
    print("\n  " + C_BOLD + "Các lệnh thủ công:" + C_RESET)
    cmds = [
        ("0",   "Setup wizard (interactive)"),
        ("0.5", "Concept Board ($0.05)"),
        ("1",   "Anchor candidates"),
        ("2",   "Validate + lint"),
        ("3 --mode smart", "Chain states (pause tại complex states)"),
        ("3 --mode waterfall", "Chain states (pause MỖI state)"),
        ("3 --yes", "Chain states (batch, no pauses)"),
        ("3b",  "Subparts"),
        ("5",   "Postprocess"),
        ("6 --serve", "Review + auto-start feedback server"),
        ("7",   "Promote final → approved"),
    ]
    for ph, note in cmds:
        print(f"    {C_DIM}python3 pipeline/shine_it_pipeline.py --level {level} --phase {ph:20}{C_RESET}  # {note}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {C_YELLOW}Aborted.{C_RESET}")
        sys.exit(130)
