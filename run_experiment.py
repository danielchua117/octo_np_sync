"""PsychoPy experiment with periodic NI barcodes and external acquisition prompts.

The NI AI recording starts first so camera exposure strobes are recorded from the
beginning of camera acquisition. The operator then starts MVS, waits the configured
lead, starts SpikeGLX, confirms recording is active, and starts trials. Barcodes
start on the first stimulus flip and continue on an absolute 5 s schedule,
independently of stimulus timing.
"""
import argparse
import csv
import os
import random
import threading
import time
from datetime import datetime

from psychopy import visual, event, core
import config as C
from ni_rig import NIRig


def wait_for_key(prompt, key):
    print(prompt, flush=True)
    while True:
        keys = event.waitKeys(keyList=[key, "escape"])
        if "escape" in keys:
            raise KeyboardInterrupt
        if key in keys:
            return


def barcode_worker(rig, base_code, start_event, stop_event, session_dir, worker_state):
    """Send barcodes on an absolute monotonic schedule; record skipped slots.

    If the worker wakes so late that a scheduled event would be crowded by the
    previous event, it skips whole slots and advances the code. This leaves an
    observable code gap instead of sending a catch-up burst.
    """
    path = os.path.join(session_dir, "barcode_events.csv")
    columns = ["slot", "barcode", "target_elapsed_s", "actual_elapsed_s",
               "lateness_ms", "status", "error"]
    try:
        start_event.wait()
        if stop_event.is_set():
            return
        epoch = time.perf_counter()
        slot = 0
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            fh.flush()
            while not stop_event.is_set():
                target = epoch + slot * C.BARCODE_PERIOD_S
                if stop_event.wait(max(0.0, target - time.perf_counter())):
                    break
                now = time.perf_counter()
                # Avoid back-to-back output if the process was suspended/stalled.
                latest_safe_slot = int(max(0.0, now - epoch) // C.BARCODE_PERIOD_S)
                if latest_safe_slot > slot and now - target >= C.BARCODE_PERIOD_S - C.event_duration_s():
                    for skipped in range(slot, latest_safe_slot):
                        skipped_code = (base_code + skipped) % (2 ** C.BARCODE_BITS)
                        writer.writerow({"slot": skipped, "barcode": skipped_code,
                                         "target_elapsed_s": skipped * C.BARCODE_PERIOD_S,
                                         "actual_elapsed_s": "", "lateness_ms": "",
                                         "status": "skipped_late", "error": ""})
                    slot = latest_safe_slot
                    target = epoch + slot * C.BARCODE_PERIOD_S
                code = (base_code + slot) % (2 ** C.BARCODE_BITS)
                try:
                    rig.arm(code)
                    rig.fire()
                    actual = getattr(rig, "fire_perf", time.perf_counter())
                    writer.writerow({"slot": slot, "barcode": code,
                                     "target_elapsed_s": slot * C.BARCODE_PERIOD_S,
                                     "actual_elapsed_s": actual - epoch,
                                     "lateness_ms": (actual - target) * 1000,
                                     "status": "fired", "error": ""})
                    fh.flush()
                    rig.wait_done(timeout=max(3.0, C.event_duration_s() + 1.0))
                except Exception as exc:
                    worker_state["error"] = repr(exc)
                    writer.writerow({"slot": slot, "barcode": code,
                                     "target_elapsed_s": slot * C.BARCODE_PERIOD_S,
                                     "actual_elapsed_s": "", "lateness_ms": "",
                                     "status": "error", "error": repr(exc)})
                    fh.flush()
                    stop_event.set()
                    break
                slot += 1
    except Exception as exc:
        worker_state["error"] = repr(exc)
        stop_event.set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", default="test")
    ap.add_argument("--simulate", action="store_true", help="PsychoPy only; no NI recording or output")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--trials", type=int, default=C.N_TRIALS)
    ap.add_argument("--no-wait", action="store_true", help="skip acquisition prompts; for software testing only")
    ap.add_argument("--camera-spikeglx-delay", type=float, default=C.CAMERA_TO_SPIKEGLX_DELAY_S)
    ap.add_argument("--spikeglx-settle", type=float, default=C.SPIKEGLX_SETTLE_S)
    a = ap.parse_args()
    if a.camera_spikeglx_delay < 0 or a.spikeglx_settle < 0:
        ap.error("startup delays must be non-negative")

    session = f"{a.subject}_{time.strftime('%Y%m%d_%H%M%S')}"
    sdir = os.path.join(C.DATA_DIR, session)
    os.makedirs(sdir, exist_ok=True)
    rng = random.Random(a.seed)
    win = visual.Window(fullscr=C.FULLSCREEN, monitor=C.MONITOR, screen=C.SCREEN,
                        color="black", units="norm", allowGUI=False)
    rig = None
    trial_file = None
    scheduler_thread = None
    start_event = threading.Event()
    stop_event = threading.Event()
    worker_state = {}
    try:
        win.recordFrameIntervals = True
        refresh = float(win.getActualFrameRate(nIdentical=20, nMaxFrames=240) or C.DISPLAY_REFRESH_HZ)
        if abs(refresh - C.DISPLAY_REFRESH_HZ) > C.DISPLAY_REFRESH_TOLERANCE_HZ:
            print(f"WARNING: configured refresh {C.DISPLAY_REFRESH_HZ:.2f} Hz, measured {refresh:.2f} Hz")
        stim_frames = C.stimulus_frames(refresh)
        print(f"Display {refresh:.3f} Hz; stimulus {stim_frames} frames ({stim_frames/refresh:.4f} s); "
              f"barcodes every {C.BARCODE_PERIOD_S:.3f} s; camera acquisition target {C.CAMERA_FPS} fps")

        conditions = {}
        for path in C.IMAGES:
            image = visual.ImageStim(win, image=path, size=(1.0, 1.0))
            conditions[os.path.splitext(os.path.basename(path))[0]] = visual.BufferImageStim(
                win, stim=[image], rect=(-1, 1, 1, -1))
        win.clearBuffer()
        width, height = win.size
        size = C.PHOTODIODE_PATCH_PX
        px = (width / 2 - size / 2) * (1 if "right" in C.PHOTODIODE_CORNER else -1)
        py = (height / 2 - size / 2) * (1 if "top" in C.PHOTODIODE_CORNER else -1)
        patch_on = visual.Rect(win, width=size, height=size, pos=(px, py), units="pix",
                               fillColor="white", lineColor=None)
        patch_off = visual.Rect(win, width=size, height=size, pos=(px, py), units="pix",
                                fillColor="black", lineColor=None)
        msg = visual.TextStim(win, text="Ready for acquisition setup.", height=0.06)

        rig = NIRig(sdir, simulate=a.simulate)
        rig.start()  # Starts NI AI first, so every camera frame can be timestamped.
        trial_path = os.path.join(sdir, "psychopy_trials.csv")
        trial_file = open(trial_path, "w", newline="", encoding="utf-8")
        trial_log = csv.DictWriter(trial_file, fieldnames=["trial", "condition", "flip_time_s",
            "stim_frames", "refresh_hz", "dropped_frames_so_far"])
        trial_log.writeheader()
        trial_file.flush()

        msg.text = "Start camera acquisition/recording in MVS, then press C."
        msg.draw(); patch_off.draw(); win.flip()
        if not a.no_wait and not a.simulate:
            wait_for_key("Start the camera recording now. Press C in this window once acquisition is running.", "c")
            time.sleep(a.camera_spikeglx_delay)
            msg.text = "Start SpikeGLX recording. Press S only after its recording is active."
            msg.draw(); patch_off.draw(); win.flip()
            wait_for_key("Start SpikeGLX now; confirm its recording is active, then press S.", "s")
            if a.spikeglx_settle:
                print(f"Waiting {a.spikeglx_settle:.1f} s for acquisition initialization...", flush=True)
                time.sleep(a.spikeglx_settle)
        elif a.simulate:
            print("SIMULATE: skipping camera/SpikeGLX prompts; no hardware barcodes will be emitted.")

        if C.PSYCHOPY_HIGH_PRIORITY and not a.simulate:
            core.rush(True)
        base_code = rng.randrange(1, 2 ** 31)
        scheduler_thread = threading.Thread(target=barcode_worker,
            args=(rig, base_code, start_event, stop_event, sdir, worker_state), daemon=True)
        scheduler_thread.start()
        names = list(conditions)
        for trial in range(a.trials):
            condition = rng.choice(names)
            conditions[condition].draw(); patch_on.draw()
            if trial == 0 and not a.simulate:
                # Anchor the periodic scheduler to the first stimulus-onset flip.
                win.callOnFlip(start_event.set)
            flip_time = win.flip()
            for _ in range(stim_frames - 1):
                conditions[condition].draw(); patch_on.draw(); win.flip()
            patch_off.draw(); win.flip()
            trial_log.writerow({"trial": trial, "condition": condition, "flip_time_s": f"{flip_time:.6f}",
                                "stim_frames": stim_frames, "refresh_hz": f"{refresh:.4f}",
                                "dropped_frames_so_far": win.nDroppedFrames})
            trial_file.flush()
            iti = C.ITI_FIXED_S if C.ITI_MODE == "fixed" else rng.uniform(C.ITI_MIN_S, C.ITI_MAX_S)
            end_at = time.perf_counter() + iti
            while time.perf_counter() < end_at:
                patch_off.draw(); win.flip()
                if "escape" in event.getKeys(["escape"]):
                    raise KeyboardInterrupt
            print(f"trial {trial:3d} {condition}", flush=True)

        # Complete the trial run, then stop future barcode slots. The current
        # finite barcode waveform is allowed to finish before the NI task closes.
        stop_event.set()
        if scheduler_thread:
            scheduler_thread.join(timeout=C.event_duration_s() + 3.0)
        if worker_state.get("error"):
            raise RuntimeError(f"Periodic barcode output failed: {worker_state['error']}")
    except KeyboardInterrupt:
        print("Aborted by operator.", flush=True)
    finally:
        stop_event.set()
        if scheduler_thread and scheduler_thread.is_alive():
            scheduler_thread.join(timeout=C.event_duration_s() + 3.0)
        try:
            core.rush(False)
        except Exception:
            pass
        if rig is not None:
            try:
                rig.wait_done(timeout=max(3.0, C.event_duration_s() + 1.0))
            except Exception as exc:
                print(f"Warning waiting for NI output completion: {exc}", flush=True)
            try:
                rig.stop()
            except Exception as exc:
                print(f"Warning stopping NI rig: {exc}", flush=True)
        if trial_file is not None and not trial_file.closed:
            trial_file.close()
        print(f"Data in {sdir}; periodic event log: {os.path.join(sdir, 'barcode_events.csv')}", flush=True)
        win.close()


if __name__ == "__main__":
    main()
