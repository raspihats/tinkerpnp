"""The job runner (docs/api.md, section 15): places every enabled, not yet placed placement of a job's boards.

Board frame -> machine frame: the job says where each board's origin is on the machine (x, y, z = board top surface)
and its rotation; a placement at (px, py, r) on the board goes to
    x = bx + px·cos(br) − py·sin(br),  y = by + px·sin(br) + py·cos(br),  rotation = r + br.
(Fiducials will correct bx, by, br per board: step D2. Bottom-side boards: step D3.)

Each placement is one request on the motion thread (feed, pick, place), run from a job thread, so between
placements the machine can be jogged while the job is paused. Pause, step and stop act between placements;
machine.stop aborts at once.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .errors import MachineError, Stopped
from .pnp import pick, place

log = logging.getLogger("tinkerpnp.job")


def board_to_machine(board_location: dict, p: dict) -> dict:
    br = math.radians(board_location.get("rotation", 0.0))
    px, py = p.get("x", 0.0), p.get("y", 0.0)
    return {"x": board_location.get("x", 0.0) + px * math.cos(br) - py * math.sin(br),
            "y": board_location.get("y", 0.0) + px * math.sin(br) + py * math.cos(br),
            "z": board_location.get("z", 0.0),
            "rotation": p.get("rotation", 0.0) + board_location.get("rotation", 0.0)}


@dataclass
class Step:
    board_index: int
    board: str
    placement: str
    part: str | None
    location: dict                       # machine frame
    done: bool = False


@dataclass
class JobRunner:
    machine: Any
    job: dict
    steps: list[Step] = field(default_factory=list)
    state: str = "loaded"
    _thread: threading.Thread | None = None
    _pause: threading.Event = field(default_factory=threading.Event)
    _stop: threading.Event = field(default_factory=threading.Event)
    _step_once: bool = False
    started: float = 0.0
    dry_run: bool = False

    # ── planning ───────────────────────────────────────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, machine: Any, data: Any, job_id: str) -> "JobRunner":
        job = data.get("jobs", job_id)
        steps, problems = [], []
        for bi, jb in enumerate(job.get("boards", [])):
            if not jb.get("enabled", True):
                continue
            if jb.get("side", "top") != "top":
                problems.append(f"board {bi + 1} ({jb['board']}): bottom side isn't supported yet")
                continue
            board = data.find("boards", jb["board"])
            if board is None:
                problems.append(f"board {bi + 1}: no board {jb['board']!r}")
                continue
            placed = set(jb.get("placed", []))
            for p in board.get("placements", []):
                if p.get("type", "placement") != "placement" or not p.get("enabled", True) or p["id"] in placed:
                    continue
                steps.append(Step(bi, board["id"], p["id"], p.get("part"),
                                  board_to_machine(jb.get("location", {}), p.get("location", {}))))
        runner = cls(machine, job, steps)
        for msg in problems:
            runner.event("warning", message=msg)
        missing = sorted({s.part or "(no part)" for s in steps if not runner.feeder_for(s.part, check_empty=False)})
        if missing:
            runner.event("warning", message="no feeder for: " + ", ".join(missing))
        runner.publish()
        return runner

    def feeder_for(self, part: str | None, check_empty: bool = True) -> Any:
        for f in self.machine.of_kind("feeder"):
            s = f.settings
            if s.part == part and s.enabled and (not check_empty or f.remaining() != 0):
                return f
        return None

    def nozzle(self) -> Any:
        sel = self.machine.state.status().get("selected_tool") or ""
        nozzles = self.machine.of_kind("nozzle")
        return next((n for n in nozzles if n.name == sel), nozzles[0] if nozzles else None)

    def plan(self) -> list[dict]:
        return [{"board": s.board, "board_index": s.board_index, "placement": s.placement, "part": s.part,
                 "location": {k: round(v, 4) for k, v in s.location.items()}, "done": s.done} for s in self.steps]

    # ── status and events ──────────────────────────────────────────────────────────────────────────────────────
    def publish(self, current: str | None = None, message: str | None = None) -> None:
        done = sum(1 for s in self.steps if s.done)
        self.machine.job.update(state=self.state, job_id=self.job["id"], name=self.job.get("name") or self.job["id"],
                                progress={"done": done, "total": len(self.steps)}, current_placement=current,
                                elapsed=round(time.monotonic() - self.started, 1) if self.started else 0.0,
                                message=message)

    def event(self, type: str, **params: Any) -> None:
        self.machine.emit("notify_job_event", {"type": type, "job": self.job["id"], **params})

    # ── control (any thread) ───────────────────────────────────────────────────────────────────────────────────
    def start(self, dry_run: bool = False) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise MachineError("busy", "the job is already running")
        self.machine.planner.require_ready()
        self.dry_run = dry_run
        self._stop.clear()
        self._pause.clear()
        self.started = time.monotonic()
        self.state = "running"
        self.publish()
        self._thread = threading.Thread(target=self._run, name="job", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self.state == "running":
            self._pause.set()

    def resume(self, step: bool = False) -> None:
        if self.state not in ("paused", "loaded", "error"):
            raise MachineError("invalid_params", f"the job is {self.state}")
        self._step_once = step
        if self._thread is None or not self._thread.is_alive():
            self.start(self.dry_run)
            if step:
                self._step_once = True
            return
        self._pause.clear()
        self.state = "running"
        self.publish()

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()

    # ── the loop (job thread) ──────────────────────────────────────────────────────────────────────────────────
    def _wait_while_paused(self, step: Step | None) -> None:
        while self._pause.is_set() and not self._stop.is_set():
            if self.state != "paused":
                self.state = "paused"
                self.publish(step.placement if step else None)
                self.event("paused", placement=step.placement if step else None)
            time.sleep(0.05)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                step = next((s for s in self.steps if not s.done), None)   # job.skip marks steps done
                if step is None:
                    break
                self._wait_while_paused(step)
                if self._stop.is_set() or step.done:
                    continue
                self.state = "running"
                self.publish(step.placement)
                try:
                    self.machine.submit(self._place_one, step).result()
                except MachineError as exc:
                    kind = "feeder_empty" if exc.name == "feeder_error" else "error"
                    self.event(kind, placement=step.placement, part=step.part, message=exc.message)
                    if exc.name == "stopped":
                        self.state = "stopped"
                        self.publish(step.placement, exc.message)
                        return
                    # pause on the error: fix it (load the feeder, ...) and resume to retry, or skip the placement
                    self.state = "paused"
                    self._pause.set()
                    self.publish(step.placement, exc.message)
                    self.event("paused", placement=step.placement, message=exc.message)
                    self._wait_while_paused(step)
                    continue
                step.done = True
                self.publish(step.placement)
                self.event("placement_done", placement=step.placement, board=step.board, part=step.part)
                if self._step_once:
                    self._step_once = False
                    self._pause.set()
            if self._stop.is_set():
                self.state = "stopped"
                self.publish(None, "stopped")
                self.event("stopped")
            else:
                self.state = "finished"
                self.publish(None, "all placements done")
                self.event("finished")
        except Exception as exc:  # a bug: don't leave the job "running"
            log.exception("job")
            self.state = "error"
            self.publish(None, str(exc))
            self.event("error", message=str(exc))

    def _place_one(self, step: Step) -> None:
        """On the motion thread: feed, pick, place, record."""
        machine = self.machine
        nozzle = self.nozzle()
        if nozzle is None:
            raise MachineError("invalid_params", "this machine has no nozzle")
        feeder = self.feeder_for(step.part)
        if feeder is None:
            raise MachineError("feeder_error", f"no feeder with parts for {step.part} ({step.placement})",
                               part=step.part)
        part = machine.state_store.find("parts", step.part) if hasattr(machine.state_store, "find") else None
        if self.dry_run:
            planner = machine.planner
            loc = feeder.pick_location()
            planner.move_to(nozzle, {"x": loc["x"], "y": loc["y"]})
            planner.move_to(nozzle, {"x": step.location["x"], "y": step.location["y"]})
            return
        retries = (part or {}).get("pick_retry_count", 0)
        for attempt in range(retries + 1):
            try:
                pick(machine, nozzle, feeder, part)
                break
            except MachineError as exc:
                if exc.name == "stopped" or attempt == retries:
                    raise
                self.event("pick_failed", placement=step.placement, message=exc.message, attempt=attempt + 1)
        place(machine, nozzle, step.location, part)
        self._record(step)

    def _record(self, step: Step) -> None:
        store = self.machine.state_store
        if not hasattr(store, "get"):
            return
        job = store.get("jobs", self.job["id"])
        boards = job.get("boards", [])
        placed = boards[step.board_index].setdefault("placed", [])
        if step.placement not in placed:
            placed.append(step.placement)
        updated = store.update("jobs", self.job["id"], {"boards": boards})
        self.job = updated
        self.machine.emit("notify_data_changed", {"collection": "jobs", "id": self.job["id"],
                                                  "revision": updated["revision"], "change": "updated"})
