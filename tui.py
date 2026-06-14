"""
Textual TUI for SoundMaster.

Layout:
  ┌──────────────────────────────────────────────┐
  │  SoundMaster  [■ SENDING]  [SLOW]  [dB(A)]   │
  ├──────────────────────────────────────────────┤
  │                   52.8 dB                    │
  ├──────────────────────────────────────────────┤
  │  Letzte 60 Sekunden                          │
  │  (plotext graph)                             │
  ├──────────────────────────────────────────────┤
  │  Min: 41.2  Max: 68.5  Avg: 52.3  Count: 58 │
  └──────────────────────────────────────────────┘
  [q] Beenden  [p] Pause
"""

import queue
import time
from collections import deque

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, Static, Digits
from textual.widget import Widget
from textual.containers import Vertical, Horizontal
from textual_plotext import PlotextPlot

import db as database
from db import Measurement

HISTORY_SECONDS = 60
UPDATE_INTERVAL = 1.0


class ValueDisplay(Widget):
    def compose(self) -> ComposeResult:
        with Horizontal(id="value-inner"):
            yield Digits("--.-", id="value-digits")
            yield Label("dB", id="value-unit")


class StatusBar(Static):
    status_text = reactive("● Verbinde...")

    def render(self) -> str:
        return self.status_text


class StatsBar(Static):
    stats_text = reactive("Min: --.-  Max: --.-  Avg: --.-")

    def render(self) -> str:
        return self.stats_text


class GraphWidget(PlotextPlot):
    def on_mount(self) -> None:
        self._update_plot([], [])

    def update_data(self, times: list[float], values: list[float]) -> None:
        self._update_plot(times, values)

    def _update_plot(self, times: list[float], values: list[float]) -> None:
        plt = self.plt
        plt.clear_figure()
        plt.theme("dark")
        plt.xlabel("Zeit (s)")
        plt.ylabel("dB")
        plt.title("Letzte 60 Sekunden")

        if times and values:
            now = time.time()
            rel_times = [t - now for t in times]
            plt.plot(rel_times, values, color="green", marker="braille")
            plt.ylim(max(0, min(values) - 5), max(values) + 5)
            plt.xlim(-HISTORY_SECONDS, 0)
        else:
            plt.plot([], [])
            plt.ylim(0, 100)
            plt.xlim(-HISTORY_SECONDS, 0)

        self.refresh()


class SoundMasterApp(App):
    CSS = """
    Screen {
        background: $surface;
    }
    #value-display {
        height: 9;
        align: center middle;
        border: solid $primary;
        margin: 0 1;
    }
    #value-inner {
        width: auto;
        height: auto;
    }
    #value-digits {
        width: auto;
        color: $success;
        text-style: bold;
    }
    #value-digits.warning {
        color: $error;
    }
    #value-unit {
        color: $success;
        text-style: bold;
        padding-top: 2;
        padding-left: 1;
    }
    #value-unit.warning {
        color: $error;
    }
    #status-bar {
        height: 1;
        margin: 0 1;
        color: $text-muted;
    }
    #stats-bar {
        height: 1;
        margin: 0 1;
        color: $text;
    }
    #graph-widget {
        height: 1fr;
        margin: 0 1;
        border: solid $primary-darken-2;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Beenden"),
        Binding("p", "pause", "Pause"),
    ]

    def __init__(self, data_queue: queue.Queue, api_port: int) -> None:
        super().__init__()
        self._q = data_queue
        self._api_port = api_port
        self._paused = False
        self._history: deque[Measurement] = deque()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(id="status-bar")
        yield ValueDisplay(id="value-display")
        yield GraphWidget(id="graph-widget")
        yield StatsBar(id="stats-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"SoundMaster Logger  [API ::{self._api_port}]"
        self.set_interval(UPDATE_INTERVAL, self._tick)

    def _tick(self) -> None:
        # Drain queue
        new_measurements = []
        try:
            while True:
                m = self._q.get_nowait()
                new_measurements.append(m)
        except queue.Empty:
            pass

        if not self._paused and new_measurements:
            cutoff = time.time() - HISTORY_SECONDS
            for m in new_measurements:
                self._history.append(m)
            # Trim old entries
            while self._history and self._history[0].ts < cutoff:
                self._history.popleft()

        self._refresh_display()

    def _refresh_display(self) -> None:
        status_widget = self.query_one("#status-bar", StatusBar)
        value_widget = self.query_one("#value-display", ValueDisplay)
        graph_widget = self.query_one("#graph-widget", GraphWidget)
        stats_widget = self.query_one("#stats-bar", StatsBar)

        paused_indicator = "  [PAUSE]" if self._paused else ""

        digits_widget = value_widget.query_one("#value-digits", Digits)
        unit_label = value_widget.query_one("#value-unit", Label)

        if not self._history:
            status_widget.status_text = f"● Warte auf Gerät (SENDING-Modus aktivieren?){paused_indicator}"
            digits_widget.update("--.-")
            digits_widget.remove_class("warning")
            unit_label.remove_class("warning")
            stats_widget.stats_text = "Min: --.-  Max: --.-  Avg: --.-"
            graph_widget.update_data([], [])
            return

        latest = self._history[-1]
        sending = "■ SENDING" if not latest.overflow and not latest.underflow else "● SIGNAL"
        over = " [OVER]" if latest.overflow else ""
        under = " [UNDER]" if latest.underflow else ""
        status_widget.status_text = (
            f"[{sending}]  [{latest.response}]  [dB({latest.weighting})]"
            f"  Range: {latest.range_min}–{latest.range_max} dB"
            f"{over}{under}{paused_indicator}"
        )

        warning = latest.overflow or latest.underflow
        digits_widget.update(f"{latest.level_db:.1f}")
        if warning:
            digits_widget.add_class("warning")
            unit_label.add_class("warning")
        else:
            digits_widget.remove_class("warning")
            unit_label.remove_class("warning")

        times = [m.ts for m in self._history]
        values = [m.level_db for m in self._history]
        graph_widget.update_data(times, values)

        if values:
            stats_widget.stats_text = (
                f"Min: {min(values):.1f}  "
                f"Max: {max(values):.1f}  "
                f"Avg: {sum(values)/len(values):.1f}  "
                f"Count: {len(values)}"
            )

    def action_pause(self) -> None:
        self._paused = not self._paused

    def action_quit(self) -> None:
        self.exit()
