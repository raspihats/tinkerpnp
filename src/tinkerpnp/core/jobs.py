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
from .pnp import discard, load_tip, pick, place, pump_off
from .vision import fit_board, locate_fiducial

log = logging.getLogger("tinkerpnp.job")


def board_to_machine(board_location: dict, p: dict) -> dict:
    aff = board_location.get("affine")
    if aff:                                  # fitted to 3+ fiducials: scale and shear included
        px, py = p.get("x", 0.0), p.get("y", 0.0)
        return {"x": aff[0][0] * px + aff[0][1] * py + aff[0][2], "y": aff[1][0] * px + aff[1][1] * py + aff[1][2],
                "z": board_location.get("z", 0.0),
                "rotation": p.get("rotation", 0.0) + board_location.get("rotation", 0.0)}
    br = math.radians(board_location.get("rotation", 0.0))
    px, py = p.get("x", 0.0), p.get("y", 0.0)
    return {"x": board_location.get("x", 0.0) + px * math.cos(br) - py * math.sin(br),
            "y": board_location.get("y", 0.0) + px * math.sin(br) + py * math.cos(br),
            "z": board_location.get("z", 0.0),
            "rotation": p.get("rotation", 0.0) + board_location.get("rotation", 0.0)}


class CycleError(Exception):
    """A placement of a cycle failed (the error, and which placement)."""

    def __init__(self, step: "Step", error: MachineError):
        super().__init__(error.message)
        self.step, self.error = step, error


@dataclass
class Step:
    board_index: int
    board: str
    placement: str
    part: str | None
    location: dict                       # machine frame
    done: bool = False
    error_handling: str = "default"      # the placement's (OpenPnP: default / alert / defer)
    rank: int = 0                        # placement rank: lower first
    attempts: int = 0
    errored: str | None = None           # defer: given up after the retries (the job went on)


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
    data: Any = None
    checked: dict[int, dict] = field(default_factory=dict)    # board index -> fiducial fit

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
            side = jb.get("side", "top")
            for p in board.get("placements", []):
                if p.get("type", "placement") != "placement" or not p.get("enabled", True) or p["id"] in placed:
                    continue
                if p.get("side", "top") != side:     # as OpenPnP: only the placements on the side facing up
                    continue
                steps.append(Step(bi, board["id"], p["id"], p.get("part"),
                                  board_to_machine(jb.get("location", {}), p.get("location", {})),
                                  error_handling=p.get("error_handling", "default"), rank=int(p.get("rank", 0))))
        runner = cls(machine, job, steps, data=data)
        runner.order_steps()
        for msg in problems:
            runner.event("warning", message=msg)
        missing = sorted({s.part or "(no part)" for s in steps if not runner.feeder_for(s.part, check_empty=False)})
        if missing:
            runner.event("warning", message="no feeder for: " + ", ".join(missing))
        runner.publish()
        return runner

    def feeder_for(self, part: str | None, check_empty: bool = True, near: dict | None = None) -> Any:
        """As OpenPnP: of the usable feeders with the part (enabled, not failed, not empty), the highest priority,
        then the nearest."""
        rank = {"high": 0, "normal": 1, "low": 2}
        found = [f for f in self.machine.of_kind("feeder")
                 if f.settings.part == part and f.usable() and (not check_empty or f.remaining() != 0)]
        if not found:
            return None

        def distance(f: Any) -> float:
            if near is None:
                return 0.0
            try:
                loc = f.pick_location()
            except MachineError:
                return math.inf
            return math.hypot(loc.get("x", 0) - near.get("x", 0), loc.get("y", 0) - near.get("y", 0))
        return min(found, key=lambda f: (rank.get(f.settings.priority, 1), distance(f)))

    def _part(self, part_id: str | None) -> dict | None:
        return self.data.find("parts", part_id) if self.data is not None and part_id else None

    def nozzle_for(self, part_id: str | None, exclude: set | None = None) -> Any:
        """As OpenPnP's planner: a nozzle whose loaded tip the part's package allows (any tip if the package names
        none). Not the tool selected in the UI: the job doesn't depend on what someone clicked last."""
        return self.tip_plan(part_id, exclude)[0]

    def allowed_tips(self, part_id: str | None) -> set[str]:
        part = self._part(part_id)
        package = self.data.find("packages", part["package"]) if part and part.get("package") and self.data else None
        return set((package or {}).get("compatible_nozzle_tips") or [])

    def tip_plan(self, part_id: str | None, exclude: set | None = None) -> tuple[Any, str | None]:
        """(nozzle, tip to load or None). As OpenPnP's planner: a nozzle whose loaded tip fits first; else one that
        can load a fitting tip (its compatible_tips, the tip not on another nozzle) - a tip change."""
        allowed = self.allowed_tips(part_id)
        nozzles = [n for n in self.machine.of_kind("nozzle") if not (exclude and n.id in exclude)]
        for n in nozzles:
            if not allowed or n.tip_id() in allowed:
                return n, None
        on_nozzles = {n.tip_id() for n in self.machine.of_kind("nozzle")}
        for n in nozzles:
            choices = [t for t in (n.settings.compatible_tips or sorted(allowed)) if t in allowed]
            free = [t for t in choices if t not in on_nozzles and self.machine.objects.get(f"nozzle_tip/{t}")]
            if free:
                return n, free[0]
        return None, None

    # ── order (as OpenPnP's job planner) ───────────────────────────────────────────────────────────────────────
    def order_steps(self) -> None:
        """Placement rank first (lower first), then by the job's order: nozzle_tips (OpenPnP's default: grouped by the
        nozzle tip they need, shortest path within a group), part, part_height (low parts first) or unsorted."""
        order = self.job.get("order", "nozzle_tips")
        if order == "unsorted":
            self.steps.sort(key=lambda s: s.rank)
            return

        def group(s: Step) -> tuple:
            if order == "part":
                return (s.part or "",)
            if order == "part_height":
                return (float((self._part(s.part) or {}).get("height") or 0.0),)
            n, change = self.tip_plan(s.part)
            return ((change or n.tip_id() or "") if n is not None else "~",)
        groups: dict[tuple, list[Step]] = {}
        for s in sorted(self.steps, key=lambda s: s.rank):
            groups.setdefault((s.rank, *group(s)), []).append(s)
        ordered: list[Step] = []
        here = None
        for key in sorted(groups, key=lambda k: tuple(str(x) if not isinstance(x, (int, float)) else x for x in k)):
            left = groups[key]
            while left:                          # shortest path: nearest neighbour from where the last one was
                nxt = left[0] if here is None else min(left, key=lambda s: math.hypot(
                    s.location["x"] - here["x"], s.location["y"] - here["y"]))
                left.remove(nxt)
                ordered.append(nxt)
                here = nxt.location
        self.steps[:] = ordered

    def plan_cycle(self) -> list[tuple["Step", Any]]:
        """The next placements for one cycle, as OpenPnP: one per nozzle that can take one (pick all, place all)."""
        open_ = sorted((s for s in self.steps if not s.done and not s.errored), key=lambda s: s.attempts)
        cycle: list[tuple[Step, Any]] = []
        used: set[str] = set()
        for s in open_:
            n = self.nozzle_for(s.part, exclude=used)
            if n is None:
                if not cycle:                       # nothing can take the first one: its error is reported
                    return [(s, None)]
                continue
            cycle.append((s, n))
            used.add(n.id)
            if self.dry_run or len(used) == len(self.machine.of_kind("nozzle")):
                break
        return cycle

    def preflight(self) -> list[str]:
        """As OpenPnP's job setup check: what makes the job unable to run. Empty: it can start."""
        problems = []
        seen: set[tuple[int, str]] = set()
        for s in self.steps:
            if s.done:
                continue
            if (s.board_index, s.placement) in seen:
                problems.append(f"board {s.board_index + 1}: placement {s.placement} appears twice")
            seen.add((s.board_index, s.placement))
            if not s.part:
                problems.append(f"{s.placement}: no part")
                continue
            part = self._part(s.part)
            if self.data is not None and part is None:
                problems.append(f"{s.placement}: part {s.part} isn't in the parts list")
            elif part and part.get("package") and self.data.find("packages", part["package"]) is None:
                problems.append(f"{s.placement}: package {part['package']} of {s.part} isn't in the packages list")
            if not self.feeder_for(s.part, check_empty=False):
                problems.append(f"{s.placement}: no enabled feeder for {s.part}")
            if self.nozzle_for(s.part) is None:
                problems.append(f"{s.placement}: no nozzle has or can load a tip that {s.part}'s package allows")
        return sorted(set(problems), key=problems.index)

    def plan(self) -> list[dict]:
        return [{"board": s.board, "board_index": s.board_index, "placement": s.placement, "part": s.part,
                 "location": {k: round(v, 4) for k, v in s.location.items()}, "done": s.done,
                 "attempts": s.attempts, "errored": s.errored} for s in self.steps]

    # ── status and events ──────────────────────────────────────────────────────────────────────────────────────
    def publish(self, current: str | None = None, message: str | None = None) -> None:
        done = sum(1 for s in self.steps if s.done)
        errored = sum(1 for s in self.steps if s.errored)
        progress = {"done": done, "total": len(self.steps), **({"errored": errored} if errored else {})}
        self.machine.job.update(state=self.state, job_id=self.job["id"], name=self.job.get("name") or self.job["id"],
                                progress=progress, current_placement=current,
                                elapsed=round(time.monotonic() - self.started, 1) if self.started else 0.0,
                                message=message)

    def event(self, type: str, **params: Any) -> None:
        self.machine.emit("notify_job_event", {"type": type, "job": self.job["id"], **params})

    # ── control (any thread) ───────────────────────────────────────────────────────────────────────────────────
    def start(self, dry_run: bool = False) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise MachineError("busy", "the job is already running")
        self.machine.planner.require_ready()
        problems = self.preflight()
        if problems:
            raise MachineError("validation_error", f"the job can't start: {problems[0]}"
                               + (f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""),
                               errors=[{"path": "job", "message": p} for p in problems])
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

    def _next_step(self) -> Step | None:
        """The next placement: not done, not given up; deferred ones (attempted before) after the fresh ones."""
        open_ = [s for s in self.steps if not s.done and not s.errored]
        return min(open_, key=lambda s: s.attempts) if open_ else None

    def _defer(self, step: Step, exc: MachineError) -> bool:
        """As OpenPnP's Defer error handling: True if the placement is put back (or given up) and the job goes on."""
        mode = step.error_handling if step.error_handling != "default" else self.job.get("error_handling", "alert")
        if mode != "defer":
            return False
        step.attempts += 1
        if step.attempts > int(self.job.get("max_placement_retries", 5)):
            step.errored = exc.message
            self.event("placement_errored", placement=step.placement, board=step.board, message=exc.message,
                       attempts=step.attempts)
        else:
            self.event("deferred", placement=step.placement, message=exc.message, attempt=step.attempts)
        self.publish(step.placement, exc.message)
        return True

    def _discard_all(self) -> None:
        """On the motion thread: parts left on nozzles go to the discard location (OpenPnP: at start and end)."""
        for n in self.machine.of_kind("nozzle"):
            if n.status().get("part"):
                discard(self.machine, n)

    def _cleanup(self) -> None:
        """As OpenPnP's job Cleanup (end or stop): safe Z, discard what's still on the nozzles, park."""
        def run() -> None:
            if not self.dry_run:
                self._discard_all()
            for head in self.machine.of_kind("head"):
                pump_off(self.machine, head, task_end=True)   # pump_control: task_duration
                self.machine.planner.move_to_safe_z(head)
                park = self.machine.config.machine.park
                if park is not None and park.head == head.id:
                    self.machine.planner.park(head)
        try:
            self.machine.submit(run).result()
        except Exception as exc:          # e.g. machine.stop: nothing more to do
            self.event("warning", message=f"cleanup after the job: {getattr(exc, 'message', exc)}")

    def _run(self) -> None:
        try:
            if not self.dry_run:
                try:
                    self.machine.submit(self._discard_all).result()          # as OpenPnP: start with empty nozzles
                except MachineError as exc:
                    self.state = "error"
                    self.publish(None, exc.message)
                    self.event("error", message=exc.message)
                    return
            while not self._stop.is_set():
                cycle = self.plan_cycle()                                   # job.skip marks steps done
                if not cycle:
                    break
                step = cycle[0][0]
                self._wait_while_paused(step)
                if self._stop.is_set() or step.done:
                    continue
                cycle = self.plan_cycle()                                   # again: skips while paused
                if not cycle:
                    break
                step = cycle[0][0]
                self.state = "running"
                self.publish(", ".join(s.placement for s, _ in cycle))
                try:
                    self.machine.submit(self._run_cycle, cycle).result()
                except (CycleError, MachineError) as ce:      # a placement failed, or machine.stop cancelled it
                    step, exc = (ce.step, ce.error) if isinstance(ce, CycleError) else (cycle[0][0], ce)
                    kind = "feeder_empty" if exc.name == "feeder_error" else "error"
                    self.event(kind, placement=step.placement, part=step.part, message=exc.message)
                    if exc.name == "stopped":           # machine.stop: abort at once, no cleanup moves
                        self.state = "stopped"
                        self.publish(step.placement, exc.message)
                        return
                    if self._defer(step, exc):
                        continue
                    # pause on the error: fix it (load the feeder, ...) and resume to retry, or skip the placement
                    self.state = "paused"
                    self._pause.set()
                    self.publish(step.placement, exc.message)
                    self.event("paused", placement=step.placement, message=exc.message)
                    self._wait_while_paused(step)
                    continue
                for s, _ in cycle:
                    s.done = True
                    self.publish(s.placement)
                    self.event("placement_done", placement=s.placement, board=s.board, part=s.part)
                if self._step_once:
                    self._step_once = False
                    self._pause.set()
            self._cleanup()
            if self._stop.is_set():
                self.state = "stopped"
                self.publish(None, "stopped")
                self.event("stopped")
            else:
                self.state = "finished"
                errored = [s.placement for s in self.steps if s.errored]
                self.publish(None, f"done; {len(errored)} not placed after retries: {', '.join(errored)}" if errored
                             else "all placements done")
                self.event("finished")
        except Exception as exc:  # a bug: don't leave the job "running"
            log.exception("job")
            self.state = "error"
            self.publish(None, str(exc))
            self.event("error", message=str(exc))

    def _run_cycle(self, cycle: list[tuple[Step, Any]]) -> None:
        """On the motion thread, as OpenPnP's cycle: fiducials of the boards first, then pick a part on every nozzle,
        then place them all. On an error the parts picked in this cycle are discarded (they're retried later) and the
        placement that failed is reported (CycleError)."""
        for step, nozzle in cycle:
            if nozzle is None:
                raise CycleError(step, MachineError("invalid_params",
                                                    f"no nozzle has a tip for {step.part} ({step.placement})"))
            jb = self.job["boards"][step.board_index]
            if jb.get("check_fiducials") and step.board_index not in self.checked:
                try:
                    self.check_board(step.board_index)
                except MachineError as exc:
                    raise CycleError(step, exc) from None
        picked: list[tuple[Step, Any]] = []
        current = cycle[0][0]
        try:
            for step, nozzle in cycle:                    # tip changes first (nozzles are still empty)
                current = step
                allowed = self.allowed_tips(step.part)
                if allowed and nozzle.tip_id() not in allowed and not self.dry_run:
                    _, tip = self.tip_plan(step.part, exclude={n.id for n in self.machine.of_kind("nozzle")
                                                               if n is not nozzle})
                    if tip is None:
                        raise MachineError("invalid_params", f"{nozzle.name} can't load a tip for {step.part}")
                    self.event("tip_change", nozzle=nozzle.id, tip=tip, placement=step.placement)
                    load_tip(self.machine, nozzle, tip)
            for step, nozzle in cycle:
                current = step
                if self.dry_run:
                    self._dry_run(step, nozzle)
                    continue
                self._pick_for(step, nozzle)
                picked.append((step, nozzle))
            for step, nozzle in picked:
                current = step
                place(self.machine, nozzle, step.location, self._part(step.part))
                picked = [p for p in picked if p[0] is not step]
                self._record(step)
        except MachineError as exc:
            if exc.name != "stopped":
                for _, nozzle in picked:             # back to the start with empty nozzles
                    try:
                        discard(self.machine, nozzle)
                    except MachineError:
                        log.exception("discard after an error")
            raise CycleError(current, exc) from None

    def _dry_run(self, step: Step, nozzle: Any) -> None:
        planner = self.machine.planner
        feeder = self.feeder_for(step.part, near=planner.location(nozzle))
        if feeder is None:
            raise MachineError("feeder_error", f"no feeder with parts for {step.part} ({step.placement})",
                               part=step.part)
        loc = feeder.pick_location()
        planner.move_to(nozzle, {"x": loc["x"], "y": loc["y"]})
        planner.move_to(nozzle, {"x": step.location["x"], "y": step.location["y"]})

    def _pick_for(self, step: Step, nozzle: Any) -> None:
        """Feed and pick the step's part with the nozzle: the part's pick retries; a feeder that is out (failed
        after its retries, or empty) is replaced by the next one with the part (as OpenPnP)."""
        machine = self.machine
        feeder = self.feeder_for(step.part, near=machine.planner.location(nozzle))
        if feeder is None:
            raise MachineError("feeder_error", f"no feeder with parts for {step.part} ({step.placement})",
                               part=step.part)
        part = self._part(step.part)
        retries = (part or {}).get("pick_retry_count", 0)
        attempt = 0
        while True:
            try:
                pick(machine, nozzle, feeder, part)
                return
            except MachineError as exc:
                if exc.name == "stopped":
                    raise
                if feeder.failed() or (exc.name == "feeder_error" and feeder.remaining() == 0):
                    self.event("feeder_failed", placement=step.placement, feeder=feeder.id, message=exc.message)
                    feeder = self.feeder_for(step.part, near=machine.planner.location(nozzle))
                    if feeder is None:
                        raise
                    continue
                attempt += 1
                if attempt > retries:
                    raise
                self.event("pick_failed", placement=step.placement, message=exc.message, attempt=attempt)

    # ── fiducials ──────────────────────────────────────────────────────────────────────────────────────────────
    def check_board(self, board_index: int, max_error: float = 0.3) -> dict:
        """On the motion thread: measure the board's fiducials, fit its real location, re-plan its placements."""
        fit = measure_board(self.machine, self.data, self.job, board_index, max_error)
        self.checked[board_index] = fit
        board = self.data.get("boards", self.job["boards"][board_index]["board"])
        by_id = {p["id"]: p for p in board.get("placements", [])}
        for s in self.steps:
            if s.board_index == board_index:
                s.location = board_to_machine(fit, by_id[s.placement].get("location", {}))
        self.event("fiducials", board_index=board_index, board=board["id"], fit=fit)
        return fit

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


def head_camera(machine: Any) -> Any:
    for cam in machine.of_kind("camera"):
        if cam.head is not None and cam.settings.looking == "down":
            return cam
    raise MachineError("invalid_params", "fiducials need a head-mounted, down-looking camera")


def measure_board(machine: Any, data: Any, job: dict, board_index: int, max_error: float = 0.3) -> dict:
    """Locate a job board's fiducials and fit its real location (x, y, rotation; z kept). On the motion thread."""
    jb = job["boards"][board_index]
    board = data.get("boards", jb["board"])
    fids = [p for p in board.get("placements", []) if p.get("type") == "fiducial" and p.get("enabled", True)]
    if len(fids) < 2:
        raise MachineError("vision_failed", f"board {board['id']} has {len(fids)} fiducial(s), needs 2 or more")
    camera = head_camera(machine)
    location = jb.get("location", {})
    board_pts, machine_pts, found = [], [], []
    for f in fids:
        expected = board_to_machine(location, f.get("location", {}))
        diameter = _fiducial_diameter(data, f)
        got = locate_fiducial(machine, camera, expected, diameter)
        board_pts.append((f["location"].get("x", 0.0), f["location"].get("y", 0.0)))
        machine_pts.append((got["x"], got["y"]))
        found.append({"id": f["id"], **got,
                      "off": round(math.hypot(got["x"] - expected["x"], got["y"] - expected["y"]), 4)})
    fit = fit_board(board_pts, machine_pts)
    # OpenPnP's tolerances: scale within 5 %, shear within 5 %, origin within 5 mm of where the job has it
    scales = [fit.get("scale_x", fit["scale"]), fit.get("scale_y", fit["scale"])]
    origin_off = math.hypot(fit["x"] - location.get("x", 0.0), fit["y"] - location.get("y", 0.0))
    bad = (fit["error_mm"] > max_error or any(abs(sc - 1) > 0.05 for sc in scales)
           or abs(fit.get("shear", 0.0)) > 0.05 or origin_off > 5.0)
    if bad:
        raise MachineError("vision_failed", f"fiducials don't fit the board (error {fit['error_mm']} mm, scale "
                                            f"{fit['scale']}, shear {fit.get('shear', 0)}, origin {origin_off:.2f} mm "
                                            "off): wrong fiducial or board?", result={"found": found})
    fit["z"] = location.get("z", 0.0)
    fit["fiducials"] = found
    return fit


def _fiducial_diameter(data: Any, placement: dict) -> float:
    part = data.find("parts", placement.get("part")) if placement.get("part") else None
    package = data.find("packages", part.get("package")) if part and part.get("package") else None
    pads = (package or {}).get("footprint", {}).get("pads", [])
    if pads and pads[0].get("width"):
        return float(pads[0]["width"])
    return 1.0
