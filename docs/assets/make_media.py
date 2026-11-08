"""Regenerate docs/assets/demo.gif and social-preview.png from real CLI output.

Run from the repo root with any Python that has Pillow and `evalgate` on PATH
(or in .venv):  python docs/assets/make_media.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
EXAMPLE = ROOT / "examples" / "quickstart"
COMMAND = "evalgate compare baseline candidate --policy evalgate.yaml"

BG, FG, DIM, BORDER = "#0d1117", "#c9d1d9", "#8b949e", "#484f58"
GREEN, RED, YELLOW, BLUE = "#3fb950", "#f85149", "#d29922", "#58a6ff"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # shortcut: Windows/macOS/Linux font paths only, add yours if none is found.
    names = (
        ["consolab.ttf", "DejaVuSansMono-Bold.ttf"]
        if bold
        else ["consola.ttf", "DejaVuSansMono.ttf"]
    )
    for name in names:
        for base in ("C:/Windows/Fonts", "/Library/Fonts", "/usr/share/fonts/truetype/dejavu"):
            if Path(base, name).exists():
                return ImageFont.truetype(str(Path(base, name)), size)
    sys.exit(f"no monospace font found ({names})")


def run_demo() -> list[str]:
    exe = shutil.which("evalgate") or str(ROOT / ".venv" / "Scripts" / "evalgate.exe")
    proc = subprocess.run(
        [exe, *COMMAND.split()[1:]],
        cwd=EXAMPLE,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "COLUMNS": "200", "NO_COLOR": "1"},
    )
    assert proc.returncode == 1, f"demo should FAIL (exit 1), got {proc.returncode}: {proc.stderr}"
    return proc.stdout.splitlines()


def color(line: str) -> str:
    if line.startswith("decision:"):
        return RED if "FAIL" in line else GREEN
    if line.startswith("FAIL"):
        return RED
    if line.startswith("ok "):
        return GREEN
    if line.startswith("warning"):
        return YELLOW
    if line.startswith(("metric ", "regressed", "check flips", "newly failing")):
        return BLUE
    return FG


def make_gif(lines: list[str]) -> None:
    f, fb = font(15), font(15, bold=True)
    cw = f.getbbox("M")[2]
    lh = 20
    width = max(len(s) for s in lines + ["$ " + COMMAND]) * cw + 48
    height = (len(lines) + 4) * lh + 60

    def frame(typed: str, shown: int, exit_line: bool) -> Image.Image:
        img = Image.new("RGB", (width, height), BG)
        d = ImageDraw.Draw(img)
        # Title bar + border so the window stands out on GitHub's dark background.
        d.rectangle((0, 0, width, 36), fill="#21262d")
        d.line((0, 36, width, 36), fill=BORDER, width=1)
        for i, c in enumerate((RED, YELLOW, GREEN)):
            d.ellipse((16 + i * 20, 12, 28 + i * 20, 24), fill=c)
        title = "evalgate · examples/quickstart"
        d.text(((width - len(title) * cw) // 2, 10), title, font=f, fill=DIM)
        d.rectangle((0, 0, width - 1, height - 1), outline=BORDER, width=2)
        y = 52
        d.text((24, y), "$ ", font=fb, fill=GREEN)
        d.text((24 + 2 * cw, y), typed, font=f, fill=FG)
        for line in lines[:shown]:
            y += lh
            d.text((24, y), line, font=fb if line.startswith("decision") else f, fill=color(line))
        if exit_line:
            d.text((24, y + 2 * lh), "$ echo $?", font=f, fill=FG)
            d.text(
                (24, y + 3 * lh), "1   # CI job fails, candidate does not ship", font=f, fill=DIM
            )
        return img

    frames, durations = [], []
    for n in range(0, len(COMMAND) + 1, 2):
        frames.append(frame(COMMAND[:n], 0, False))
        durations.append(40)
    frames.append(frame(COMMAND, 0, False))
    durations.append(500)
    for n in range(1, len(lines) + 1):
        frames.append(frame(COMMAND, n, False))
        durations.append(70)
    frames.append(frame(COMMAND, len(lines), True))
    durations.append(6000)
    frames[0].save(
        OUT / "demo.gif",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )


def make_social() -> None:
    img = Image.new("RGB", (1280, 640), BG)
    d = ImageDraw.Draw(img)
    d.text((80, 90), "EvalGate", font=font(96, bold=True), fill="#ffffff")
    d.text((84, 210), "CI release gate for LLM apps.", font=font(40), fill=FG)
    d.text((84, 262), "Baseline vs candidate evals -> PASS / FAIL.", font=font(40), fill=FG)
    rows = [
        ("ok  ", "quality  +8.12%", GREEN),
        ("ok  ", "latency  -1.79%", GREEN),
        ("FAIL", "safety_valid newly failing: abuse-001", RED),
        ("", "decision: FAIL   exit 1", RED),
    ]
    d.rounded_rectangle((80, 360, 1200, 570), radius=16, fill="#161b22", outline="#30363d", width=2)
    for i, (tag, text, c) in enumerate(rows):
        y = 385 + i * 44
        d.text((110, y), tag, font=font(30, bold=True), fill=c)
        d.text(
            (220 if tag else 110, y), text, font=font(30, bold=not tag), fill=c if not tag else FG
        )
    d.text(
        (80, 590), "pip install evalgate-ai  ·  offline  ·  no API keys", font=font(24), fill=DIM
    )
    img.save(OUT / "social-preview.png", optimize=True)


if __name__ == "__main__":
    make_gif(run_demo())
    make_social()
    print("wrote", OUT / "demo.gif", OUT / "social-preview.png")
