#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focus Timer — Pomodoro timer with session tracking and daily stats."""

import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

try:
    import msvcrt
    def kbhit():
        return msvcrt.kbhit()
    def getch():
        return msvcrt.getch()
except ImportError:
    # Unix fallback
    import tty, termios, select
    def kbhit():
        return select.select([sys.stdin], [], [], 0)[0] != []
    def getch():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1).encode()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

try:
    from rich import box
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    try:
        from rich.console import Group
    except ImportError:
        from rich.console import RenderGroup as Group  # older rich
except ImportError:
    print("Missing dependency. Run: pip install rich")
    raise SystemExit(1)

WORK_MINS = 25
SHORT_BREAK_MINS = 5
LONG_BREAK_MINS = 15
POMODOROS_PER_LONG = 4

DATA_FILE = Path.home() / ".focus_timer" / "sessions.json"
console = Console()


# ── Data layer ─────────────────────────────────────────────────────────────

def load_sessions() -> list:
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE) as f:
        return json.load(f).get("sessions", [])


def save_session(task: str, stype: str, duration_secs: int, completed: bool):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    sessions = load_sessions()
    sessions.append({
        "date": str(date.today()),
        "time": datetime.now().strftime("%H:%M"),
        "task": task,
        "type": stype,
        "duration": duration_secs,
        "completed": completed,
    })
    with open(DATA_FILE, "w") as f:
        json.dump({"sessions": sessions}, f, indent=2)


def get_stats() -> dict:
    sessions = load_sessions()
    today = str(date.today())
    work_done = [s for s in sessions if s["type"] == "work" and s["completed"]]
    today_work = [s for s in work_done if s["date"] == today]

    all_work_dates = sorted(set(s["date"] for s in work_done), reverse=True)
    streak, check = 0, date.today()
    for d in all_work_dates:
        if str(check) == d:
            streak += 1
            check = date.fromordinal(check.toordinal() - 1)
        else:
            break

    return {
        "today_count": len(today_work),
        "today_secs": sum(s["duration"] for s in today_work),
        "streak": streak,
        "total_count": len(work_done),
        "recent": [s for s in sessions if s["type"] == "work"][-8:][::-1],
    }


def fmt_dur(secs: int) -> str:
    h, m = divmod(secs // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def fmt_time(secs: int) -> str:
    return f"{secs // 60:02d}:{secs % 60:02d}"


# ── Timer ──────────────────────────────────────────────────────────────────

class Timer:
    def __init__(self):
        self.stype = "work"
        self.task = ""
        self.remaining = WORK_MINS * 60
        self.total = WORK_MINS * 60
        self.state = "idle"   # idle | running | paused | stats
        self.session_num = get_stats()["today_count"]
        self._tick_ref = time.monotonic()
        self._prev_state = "idle"

    def _start(self, stype: str, task: str = ""):
        durations = {
            "work": WORK_MINS * 60,
            "short_break": SHORT_BREAK_MINS * 60,
            "long_break": LONG_BREAK_MINS * 60,
        }
        self.stype, self.task = stype, task
        self.total = self.remaining = durations[stype]
        self.state = "running"
        self._tick_ref = time.monotonic()

    def _tick(self):
        if self.state != "running":
            return
        now = time.monotonic()
        if now - self._tick_ref >= 1.0:
            self.remaining = max(0, self.remaining - 1)
            self._tick_ref += 1.0

    def _on_complete(self) -> str:
        save_session(self.task, self.stype, self.total, completed=True)
        if self.stype == "work":
            self.session_num += 1
            return "long_break" if self.session_num % POMODOROS_PER_LONG == 0 else "short_break"
        return "work"

    def _poll_key(self) -> str | None:
        if not kbhit():
            return None
        ch = getch()
        return {
            b' ': 'space', b'q': 'quit', b'Q': 'quit',
            b'n': 'new',   b'N': 'new',
            b's': 'stats', b'S': 'stats',
            b'b': 'back',  b'B': 'back',
            b'\x03': 'quit',
        }.get(ch)

    # ── Panels ────────────────────────────────────────────────────────────

    def render(self):
        if self.state == "stats":
            return self._stats_panel()
        if self.state == "idle":
            return self._idle_panel()
        return self._timer_panel()

    def _idle_panel(self) -> Panel:
        s = get_stats()
        body = Text("\n")
        body.append("  Ready to focus?\n", style="bold white")
        body.append(f"\n  Today: ", style="dim")
        body.append(f"{s['today_count']} pomodoros", style="cyan")
        if s["today_secs"] > 0:
            body.append(f"  ·  {fmt_dur(s['today_secs'])} focused", style="cyan")
        if s["streak"] > 1:
            body.append(f"  ·  {s['streak']}-day streak", style="bold yellow")
        body.append("\n")
        return Panel(
            body,
            title="[bold blue] Focus Timer [/bold blue]",
            subtitle="[dim]  n  new session    s  stats    q  quit  [/dim]",
            border_style="blue",
            padding=(0, 1),
        )

    def _timer_panel(self) -> Panel:
        pct = 1.0 - self.remaining / self.total if self.total else 0
        w = 30
        bar = "█" * int(w * pct) + "░" * (w - int(w * pct))
        labels = {"work": "FOCUS", "short_break": "SHORT BREAK", "long_break": "LONG BREAK"}
        colors = {"work": "red", "short_break": "green", "long_break": "cyan"}
        color = colors[self.stype] if self.state == "running" else "yellow"

        body = Text()
        body.append(f"\n  {labels[self.stype]}", style=f"bold {color}")
        if self.state == "paused":
            body.append("   ⏸ PAUSED", style="bold yellow")
        body.append(f"\n\n  {fmt_time(self.remaining)}\n", style=f"bold {color}")
        body.append(f"\n  [{bar}]\n", style=color)
        body.append(f"\n  {self.task}\n" if self.task else "\n", style="white")

        return Panel(
            body,
            title="[bold] Focus Timer [/bold]",
            subtitle="[dim]  space  pause    n  new    s  stats    q  quit  [/dim]",
            border_style=color,
            padding=(0, 1),
        )

    def _stats_panel(self) -> Panel:
        s = get_stats()

        grid = Table.grid(expand=True, padding=(0, 3))
        for _ in range(4):
            grid.add_column(justify="center")

        def cell(val, lbl) -> Text:
            t = Text(justify="center")
            t.append(f"{val}\n", style="bold cyan")
            t.append(lbl, style="dim")
            return t

        grid.add_row(
            cell(s["today_count"], "today"),
            cell(fmt_dur(s["today_secs"]), "focus today"),
            cell(f"{s['streak']}d", "streak"),
            cell(str(s["total_count"]), "all time"),
        )

        tbl = Table(
            box=box.SIMPLE, show_header=True, header_style="bold dim",
            expand=True, padding=(0, 1),
        )
        tbl.add_column("Date", style="dim", width=10)
        tbl.add_column("Time", style="dim", width=6)
        tbl.add_column("Task")
        tbl.add_column("Dur", justify="right", style="cyan", width=6)
        tbl.add_column("", width=2)

        for sess in s["recent"]:
            tbl.add_row(
                sess["date"],
                sess.get("time", ""),
                sess["task"] or "(no task)",
                fmt_dur(sess["duration"]),
                "[green]✓[/green]" if sess["completed"] else "[dim]·[/dim]",
                style="" if sess["completed"] else "dim",
            )

        return Panel(
            Group(grid, tbl),
            title="[bold] Stats [/bold]",
            subtitle="[dim]  b  back  [/dim]",
            border_style="cyan",
            padding=(1, 2),
        )

    # ── Main loop ─────────────────────────────────────────────────────────

    def _live_loop(self) -> str:
        """Drive the live display; returns an action string to handle outside."""
        with Live(self.render(), console=console, refresh_per_second=8) as live:
            while True:
                self._tick()

                if self.state == "running" and self.remaining == 0:
                    next_stype = self._on_complete()
                    return f"complete:{next_stype}"

                key = self._poll_key()

                if key == "quit":
                    check = self._prev_state if self.state == "stats" else self.state
                    if check in ("running", "paused") and self.remaining < self.total:
                        save_session(self.task, self.stype,
                                     self.total - self.remaining, completed=False)
                    return "quit"

                elif key == "space":
                    if self.state == "running":
                        self.state = "paused"
                    elif self.state == "paused":
                        self.state = "running"
                        self._tick_ref = time.monotonic()

                elif key == "new":
                    check = self._prev_state if self.state == "stats" else self.state
                    if check in ("running", "paused") and self.remaining < self.total:
                        save_session(self.task, self.stype,
                                     self.total - self.remaining, completed=False)
                    return "new"

                elif key == "stats":
                    self._prev_state = self.state
                    self.state = "stats"

                elif key == "back" and self.state == "stats":
                    self.state = self._prev_state

                live.update(self.render())
                time.sleep(0.1)

    def _prompt_task(self) -> str:
        console.print("\n  [bold]What are you working on?[/bold] [dim](enter to skip)[/dim]")
        console.print("  [dim]>[/dim] ", end="")
        return input().strip()

    def run(self):
        while True:
            action = self._live_loop()

            if action == "quit":
                break

            elif action == "new":
                task = self._prompt_task()
                self._start("work", task)

            elif action.startswith("complete:"):
                next_stype = action.split(":")[1]
                if next_stype == "work":
                    console.print("\n  [green]✓ Break done![/green]  Let's focus.")
                    task = self._prompt_task()
                    self._start("work", task)
                else:
                    label = "long" if next_stype == "long_break" else "short"
                    console.print(
                        f"\n  [green bold]✓ Pomodoro #{self.session_num} done![/green bold]"
                        f"  Starting {label} break…"
                    )
                    time.sleep(1.0)
                    self._start(next_stype)

        console.print("\n[dim]See you next time. Stay focused![/dim]\n")


if __name__ == "__main__":
    t = Timer()
    try:
        t.run()
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted. See you next time![/dim]\n")
