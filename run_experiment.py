"""PsychoPy stimulus task with NI barcode, camera marker, and photodiode patch."""
import argparse, csv, os, random, time
from psychopy import visual, event, core
import config as C
from ni_rig import NIRig

ap = argparse.ArgumentParser()
ap.add_argument("--subject", default="test")
ap.add_argument("--simulate", action="store_true")
ap.add_argument("--seed", type=int, default=None)
ap.add_argument("--trials", type=int, default=C.N_TRIALS)
ap.add_argument("--no-wait", action="store_true")
a = ap.parse_args()

session = f"{a.subject}_{time.strftime('%Y%m%d_%H%M%S')}"
sdir = os.path.join(C.DATA_DIR, session)
os.makedirs(sdir, exist_ok=True)
rng = random.Random(a.seed)
win = visual.Window(fullscr=C.FULLSCREEN, monitor=C.MONITOR, screen=C.SCREEN,
                    color="black", units="norm", allowGUI=False)
rig = None
logf = None
try:
    win.recordFrameIntervals = True
    measured = win.getActualFrameRate(nIdentical=20, nMaxFrames=240)
    refresh_hz = float(measured or C.DISPLAY_REFRESH_HZ)
    if abs(refresh_hz - C.DISPLAY_REFRESH_HZ) > C.DISPLAY_REFRESH_TOLERANCE_HZ:
        print(f"WARNING: configured refresh {C.DISPLAY_REFRESH_HZ:.2f} Hz, measured {refresh_hz:.2f} Hz; using measured rate")
    else:
        print(f"Display refresh: {refresh_hz:.3f} Hz (configured {C.DISPLAY_REFRESH_HZ:.2f} Hz)")
    stim_frames = C.stimulus_frames(refresh_hz)
    actual_stim_s = stim_frames / refresh_hz
    min_iti = C.ITI_FIXED_S if C.ITI_MODE == "fixed" else C.ITI_MIN_S
    if actual_stim_s + min_iti <= C.event_duration_s() + 0.1:
        raise ValueError("ITI too short: the next stimulus could start before the previous barcode finishes")
    print(f"Stimulus: {stim_frames} frames, {actual_stim_s:.6f} s; camera acquisition {C.CAMERA_FPS} fps; MVS preview setting {C.CAMERA_PREVIEW_FPS} fps")

    conditions = {}
    for path in C.IMAGES:
        img = visual.ImageStim(win, image=path, size=(1.0, 1.0))
        conditions[os.path.splitext(os.path.basename(path))[0]] = visual.BufferImageStim(win, stim=[img], rect=(-1, 1, 1, -1))
    win.clearBuffer()
    w, h = win.size
    s = C.PHOTODIODE_PATCH_PX
    sx = (w / 2 - s / 2) * (1 if "right" in C.PHOTODIODE_CORNER else -1)
    sy = (h / 2 - s / 2) * (1 if "top" in C.PHOTODIODE_CORNER else -1)
    patch = visual.Rect(win, width=s, height=s, pos=(sx, sy), units="pix", fillColor="white", lineColor=None)
    patch_off = visual.Rect(win, width=s, height=s, pos=(sx, sy), units="pix", fillColor="black", lineColor=None)
    msg = visual.TextStim(win, text="Ready. Press SPACE to start, ESC to quit.", height=0.06)

    rig = NIRig(sdir, simulate=a.simulate)
    rig.start()
    logf = open(os.path.join(sdir, "psychopy_trials.csv"), "w", newline="")
    log = csv.writer(logf)
    log.writerow(["trial", "condition", "barcode", "flip_time_s", "fire_perf_s", "iti_s", "stim_frames", "refresh_hz", "dropped_frames_so_far"])
    msg.draw(); patch_off.draw(); win.flip()
    if not a.no_wait:
        keys = event.waitKeys(keyList=["space", "escape"])
        if "escape" in keys:
            return
    if C.PSYCHOPY_HIGH_PRIORITY:
        core.rush(True)
    code = rng.randrange(1, 2**31)
    names = list(conditions)
    for trial in range(a.trials):
        cond = rng.choice(names)
        code = (code + 1) % (2**C.BARCODE_BITS)
        rig.arm(code)
        conditions[cond].draw(); patch.draw()
        win.callOnFlip(rig.fire)
        t_on = win.flip()
        for _ in range(stim_frames - 1):
            conditions[cond].draw(); patch.draw(); win.flip()
        patch_off.draw(); win.flip()
        iti = C.ITI_FIXED_S if C.ITI_MODE == "fixed" else rng.uniform(C.ITI_MIN_S, C.ITI_MAX_S)
        log.writerow([trial, cond, code, f"{t_on:.6f}", f"{rig.fire_perf:.6f}", f"{iti:.3f}", stim_frames, f"{refresh_hz:.4f}", win.nDroppedFrames])
        logf.flush()
        end = time.perf_counter() + iti
        rig.wait_done()
        while time.perf_counter() < end:
            patch_off.draw(); win.flip()
            if "escape" in event.getKeys(["escape"]):
                raise KeyboardInterrupt
        print(f"trial {trial:3d} {cond:8s} barcode {code}")
except KeyboardInterrupt:
    print("aborted by user")
finally:
    try:
        core.rush(False)
    except Exception:
        pass
    if rig is not None:
        try: rig.wait_done()
        except Exception as e: print(f"Warning waiting for NI output: {e}")
        try: rig.stop()
        except Exception as e: print(f"Warning stopping NI rig: {e}")
    if logf is not None and not logf.closed:
        logf.close()
    win.close()
