"""Periodic-barcode NI synchronization with a PsychoPy looming stimulus.

The original run_experiment.py remains unchanged. This is a separate pilot
entry point for the standalone looming-disk protocol.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import random
import threading
import time
from psychopy import core, event, logging, monitors, visual

import config as C
from ni_rig import NIRig


SCREEN_RES_PIX = (1920, 1080)
MONITOR_WIDTH_CM = 34.4
VIEWING_DISTANCE_CM = 14.0
TARGET_REFRESH_HZ = 60.0
DEFAULT_START_ANGLE_DEG = 2.0
LV_CONDITIONS = (
    ("Level 1 - very fast loom", 0.5),
    ("Level 2 - fast loom", 1.0),
    ("Level 3 - medium loom", 2.0),
    ("Level 4 - slow loom", 3.0),
    ("Level 5 - slower loom", 4.0),
    ("Level 6 - very slow loom", 6.0),
)


def ask_int(prompt, default=None, min_value=None, max_value=None):
    while True:
        raw = input(prompt).strip()
        try:
            value = default if raw == "" and default is not None else int(raw)
        except ValueError:
            print("Enter a whole number.")
            continue
        if min_value is not None and value < min_value:
            print(f"Value must be at least {min_value}.")
            continue
        if max_value is not None and value > max_value:
            print(f"Value must be at most {max_value}.")
            continue
        return value


def ask_float(prompt, default=None, min_value=None):
    while True:
        raw = input(prompt).strip()
        try:
            value = default if raw == "" and default is not None else float(raw)
        except ValueError:
            print("Enter a number.")
            continue
        if not math.isfinite(value) or (min_value is not None and value < min_value):
            print(f"Enter a finite number >= {min_value}.")
            continue
        return value


def choose_condition(start_angle_deg):
    print("\nLoom conditions:")
    for i, (label, collision_s) in enumerate(LV_CONDITIONS, start=1):
        lv_ratio = math.tan(math.radians(start_angle_deg) / 2) * collision_s
        print(f"  {i}. {label} — t_collision={collision_s:.2f} s, l/v={lv_ratio:.6f} s")
    choice = ask_int("Select condition: ", min_value=1, max_value=len(LV_CONDITIONS))
    label, collision_s = LV_CONDITIONS[choice - 1]
    return label, collision_s, math.tan(math.radians(start_angle_deg) / 2) * collision_s


def loom_radius_cm(t, lv_ratio, collision_s, max_radius_cm):
    remaining = max(collision_s - t, 1e-6)
    return min(VIEWING_DISTANCE_CM * lv_ratio / remaining, max_radius_cm)


def make_window(screen_index):
    monitor_height_cm = MONITOR_WIDTH_CM * SCREEN_RES_PIX[1] / SCREEN_RES_PIX[0]
    monitor = monitors.Monitor("octopus_loom_monitor")
    monitor.setWidth(MONITOR_WIDTH_CM)
    monitor.setDistance(VIEWING_DISTANCE_CM)
    monitor.setSizePix(SCREEN_RES_PIX)
    win = visual.Window(
        size=SCREEN_RES_PIX,
        fullscr=True,
        screen=screen_index,
        monitor=monitor,
        units="cm",
        color=[1, 1, 1],
        colorSpace="rgb",
        allowGUI=False,
        waitBlanking=True,
    )
    win.mouseVisible = False

    width_px, height_px = win.size
    px_per_cm = width_px / MONITOR_WIDTH_CM
    patch_size = C.PHOTODIODE_PATCH_PX
    corner = C.PHOTODIODE_CORNER.lower()
    patch_x = (width_px / 2 - patch_size / 2) * (1 if "right" in corner else -1)
    patch_y = (height_px / 2 - patch_size / 2) * (1 if "top" in corner else -1)
    patch_black = visual.Rect(
        win, width=patch_size, height=patch_size, pos=(patch_x, patch_y),
        units="pix", fillColor=[-1, -1, -1], lineColor=None,
    )
    patch_white = visual.Rect(
        win, width=patch_size, height=patch_size, pos=(patch_x, patch_y),
        units="pix", fillColor=[1, 1, 1], lineColor=None,
    )
    disk = visual.GratingStim(
        win=win, tex=None, mask="circle", units="cm", size=(0.001, 0.001),
        pos=(0, 0), color=[-1, -1, -1], colorSpace="rgb", interpolate=True,
    )
    max_radius = math.hypot(MONITOR_WIDTH_CM, monitor_height_cm)
    return win, disk, patch_black, patch_white, px_per_cm, max_radius


def wait_key(key, prompt):
    print(prompt, flush=True)
    while True:
        keys = event.waitKeys(keyList=[key, "escape"])
        if "escape" in keys:
            raise KeyboardInterrupt
        if key in keys:
            return


def barcode_worker(rig, base_code, start_event, stop_event, session_dir, state):
    path = os.path.join(session_dir, "barcode_events.csv")
    columns = [
        "slot", "barcode", "target_elapsed_s", "actual_elapsed_s",
        "lateness_ms", "status", "error", "schedule_epoch_perf_s",
    ]
    try:
        start_event.wait()
        if stop_event.is_set():
            return
        epoch = state["epoch_perf_s"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            fh.flush()
            slot = 0
            while not stop_event.is_set():
                target_elapsed = slot * C.BARCODE_PERIOD_S
                if stop_event.wait(max(0.0, epoch + target_elapsed - time.perf_counter())):
                    break
                now = time.perf_counter()
                safe_slot = int(max(0.0, now - epoch) // C.BARCODE_PERIOD_S)
                if safe_slot > slot and now - (epoch + target_elapsed) >= (
                    C.BARCODE_PERIOD_S - C.event_duration_s()
                ):
                    for skipped in range(slot, safe_slot):
                        writer.writerow({
                            "slot": skipped,
                            "barcode": (base_code + skipped) % (2 ** C.BARCODE_BITS),
                            "target_elapsed_s": skipped * C.BARCODE_PERIOD_S,
                            "actual_elapsed_s": "",
                            "lateness_ms": "",
                            "status": "skipped_late",
                            "error": "",
                            "schedule_epoch_perf_s": epoch,
                        })
                    slot = safe_slot
                    target_elapsed = slot * C.BARCODE_PERIOD_S
                code = (base_code + slot) % (2 ** C.BARCODE_BITS)
                try:
                    rig.arm(code)
                    rig.fire()
                    actual_perf = rig.fire_perf or time.perf_counter()
                    actual_elapsed = actual_perf - epoch
                    writer.writerow({
                        "slot": slot,
                        "barcode": code,
                        "target_elapsed_s": target_elapsed,
                        "actual_elapsed_s": actual_elapsed,
                        "lateness_ms": (actual_elapsed - target_elapsed) * 1000,
                        "status": "fired",
                        "error": "",
                        "schedule_epoch_perf_s": epoch,
                    })
                    fh.flush()
                    rig.wait_done(timeout=max(3.0, C.event_duration_s() + 1.0))
                except Exception as exc:
                    state["error"] = repr(exc)
                    writer.writerow({
                        "slot": slot,
                        "barcode": code,
                        "target_elapsed_s": target_elapsed,
                        "actual_elapsed_s": "",
                        "lateness_ms": "",
                        "status": "error",
                        "error": repr(exc),
                        "schedule_epoch_perf_s": epoch,
                    })
                    fh.flush()
                    stop_event.set()
                    break
                slot += 1
    except Exception as exc:
        state["error"] = repr(exc)
        stop_event.set()


def wait_white(win, patch_black, duration_s):
    timer = core.Clock()
    while timer.getTime() < duration_s:
        patch_black.draw()
        win.flip()
        if "escape" in event.getKeys(keyList=["escape"]):
            raise KeyboardInterrupt


def run_loom(
    win, disk, patch_white, trial, condition, collision_s, lv_ratio,
    max_radius, start_scheduler=None,
):
    onset_perf = []
    onset_flip_time = None
    timer = core.Clock()
    frame = 0
    last_radius = 0.0
    while timer.getTime() < collision_s:
        elapsed = timer.getTime()
        last_radius = loom_radius_cm(elapsed, lv_ratio, collision_s, max_radius)
        diameter = 2 * last_radius
        disk.size = (diameter, diameter)
        disk.draw()
        patch_white.draw()
        if frame == 0 and start_scheduler is not None:
            win.callOnFlip(start_scheduler)
        flip_timestamp = win.flip()
        if frame == 0:
            onset_perf.append(time.perf_counter())
            onset_flip_time = flip_timestamp
        frame += 1
        if "escape" in event.getKeys(keyList=["escape"]):
            raise KeyboardInterrupt

    return {
        "trial": trial,
        "condition": condition,
        "flip_time_s": float(onset_flip_time if onset_flip_time is not None else timer.getTime()),
        "flip_perf_s": onset_perf[0] if onset_perf else time.perf_counter(),
        "stim_frames": frame,
        "t_collision_s": collision_s,
        "lv_ratio_s": lv_ratio,
        "final_radius_cm": last_radius,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", default="loom_pilot")
    parser.add_argument("--simulate", action="store_true", help="PsychoPy only; does not record NI or send hardware barcodes")
    parser.add_argument("--camera-spikeglx-delay", type=float, default=C.CAMERA_TO_SPIKEGLX_DELAY_S)
    parser.add_argument("--spikeglx-settle", type=float, default=C.SPIKEGLX_SETTLE_S)
    args = parser.parse_args()
    if args.camera_spikeglx_delay < 0 or args.spikeglx_settle < 0:
        parser.error("startup delays must be non-negative")

    logging.console.setLevel(logging.INFO)
    print("Looming pilot with NI periodic-barcode and photodiode synchronization")
    screen_index = ask_int("Fullscreen screen index [0]: ", default=0, min_value=0)
    start_angle = ask_float(
        f"Starting angular diameter in degrees [{DEFAULT_START_ANGLE_DEG}]: ",
        default=DEFAULT_START_ANGLE_DEG,
        min_value=0.01,
    )
    condition, collision_s, lv_ratio = choose_condition(start_angle)
    trials_n = ask_int("Number of loom trials [1-3 recommended]: ", min_value=1)
    iti_s = ask_float("White ITI between trials in seconds [0]: ", default=0.0, min_value=0.0)
    pre_white_s = ask_float("Pre-loom white duration in seconds [5]: ", default=5.0, min_value=0.0)
    hold_s = ask_float("Final disk hold in seconds [5]: ", default=5.0, min_value=0.0)
    post_white_s = ask_float("Post-loom white duration in seconds [5]: ", default=5.0, min_value=0.0)
    label = input("Session label [pilot]: ").strip() or "pilot"

    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label).strip("_")
    safe_subject = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in args.subject
    ).strip("_")
    if not safe_label or not safe_subject:
        parser.error("subject and session label must contain letters or numbers")
    session = f"{safe_subject}_{safe_label}_{time.strftime('%Y%m%d_%H%M%S')}"
    session_dir = os.path.join(C.DATA_DIR, session)
    os.makedirs(session_dir, exist_ok=False)
    trials_path = os.path.join(session_dir, "psychopy_trials.csv")
    rig = None
    win = None
    start_event = threading.Event()
    stop_event = threading.Event()
    scheduler_state = {}
    scheduler_thread = None

    try:
        win, disk, patch_black, patch_white, px_per_cm, max_radius = make_window(screen_index)
        measured_hz = win.getActualFrameRate(
            nIdentical=20, nMaxFrames=240, nWarmUpFrames=10, threshold=1
        )
        print(
            f"Display refresh: {measured_hz:.2f} Hz"
            if measured_hz is not None else "Display refresh could not be measured."
        )
        if measured_hz is not None and abs(measured_hz - TARGET_REFRESH_HZ) > 2.0:
            print(f"WARNING: expected about {TARGET_REFRESH_HZ:.1f} Hz for this pilot.")

        trial_fields = [
            "trial", "condition", "flip_time_s", "flip_perf_s", "stim_frames",
            "refresh_hz", "t_collision_s", "lv_ratio_s", "final_radius_cm",
        ]
        with open(trials_path, "w", newline="", encoding="utf-8") as trial_file:
            writer = csv.DictWriter(trial_file, fieldnames=trial_fields)
            writer.writeheader()
            trial_file.flush()

            rig = NIRig(session_dir, simulate=args.simulate)
            rig.start()
            print(
                f"Display geometry: {SCREEN_RES_PIX[0]}x{SCREEN_RES_PIX[1]} px, "
                f"{MONITOR_WIDTH_CM:.2f} cm wide, {VIEWING_DISTANCE_CM:.1f} cm viewing distance; "
                f"{px_per_cm:.2f} px/cm"
            )
            print(f"Selected {condition}: collision time {collision_s:.2f} s; l/v {lv_ratio:.6f} s")

            if not args.simulate:
                win.flip()
                wait_key("c", "Start MVS camera acquisition/recording, then press C here.")
                time.sleep(args.camera_spikeglx_delay)
                wait_key("s", "Start SpikeGLX recording and confirm it is active, then press S.")
                if args.spikeglx_settle:
                    print(f"Waiting {args.spikeglx_settle:.1f} s for acquisition initialization.")
                    time.sleep(args.spikeglx_settle)
            else:
                print("SIMULATE: NI hardware and external acquisition prompts are disabled.")

            if args.simulate:
                wait_key("space", "Press SPACE to start the visual-only simulation.")
            else:
                wait_key("space", "Acquisitions are ready. Press SPACE to start the looming trials.")

            base_code = random.SystemRandom().randrange(1, 2 ** 31)
            scheduler_thread = threading.Thread(
                target=barcode_worker,
                args=(rig, base_code, start_event, stop_event, session_dir, scheduler_state),
                daemon=True,
            )
            scheduler_thread.start()
            epoch_callback_done = threading.Event()

            def start_periodic_schedule():
                scheduler_state["epoch_perf_s"] = time.perf_counter()
                epoch_callback_done.set()
                start_event.set()

            for trial in range(1, trials_n + 1):
                if trial > 1 and iti_s:
                    wait_white(win, patch_black, iti_s)
                wait_white(win, patch_black, pre_white_s)

                row = run_loom(
                    win, disk, patch_white, trial, condition, collision_s,
                    lv_ratio, max_radius,
                    start_scheduler=start_periodic_schedule if trial == 1 and not args.simulate else None,
                )
                if trial == 1 and not args.simulate:
                    if not epoch_callback_done.wait(timeout=2.0):
                        raise RuntimeError("Could not anchor periodic schedule to first loom flip")

                disk.size = (2 * row["final_radius_cm"], 2 * row["final_radius_cm"])
                hold_timer = core.Clock()
                while hold_timer.getTime() < hold_s:
                    disk.draw()
                    patch_white.draw()
                    win.flip()
                    if "escape" in event.getKeys(keyList=["escape"]):
                        raise KeyboardInterrupt
                win.flip()
                wait_white(win, patch_black, post_white_s)
                row["refresh_hz"] = measured_hz if measured_hz is not None else ""
                writer.writerow(row)
                trial_file.flush()
                print(f"Completed loom trial {trial}/{trials_n}.")

        stop_event.set()
        if scheduler_thread:
            scheduler_thread.join(timeout=C.event_duration_s() + 3.0)
        if scheduler_state.get("error"):
            raise RuntimeError(f"Periodic barcode output failed: {scheduler_state['error']}")
    except KeyboardInterrupt:
        print("Aborted by operator.")
    finally:
        stop_event.set()
        if scheduler_thread and scheduler_thread.is_alive():
            scheduler_thread.join(timeout=C.event_duration_s() + 3.0)
        if rig is not None:
            try:
                rig.wait_done(timeout=max(3.0, C.event_duration_s() + 1.0))
            except Exception as exc:
                print(f"Warning waiting for NI output completion: {exc}", flush=True)
            try:
                rig.stop()
            except Exception as exc:
                print(f"Warning stopping NI rig: {exc}", flush=True)
        if win is not None:
            win.close()
        print(f"Session data: {session_dir}")
        print(f"Trial log: {trials_path}")
        print(f"Barcode log: {os.path.join(session_dir, 'barcode_events.csv')}")
        print("After SpikeGLX recording is stopped, run analysis/align_session.py and tools/validate_sync_session.py.")


if __name__ == "__main__":
    main()
