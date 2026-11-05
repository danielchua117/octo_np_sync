"""run_experiment.py -- PsychoPy visual stimulus + NI sync (structure from bufferImageStim.py).

On every stimulus onset frame:
  * the stimulus and a white photodiode patch are drawn,
  * win.callOnFlip(rig.fire) starts the barcode (OneBox SMA1 + AI loopback)
    and camera marker on the same NI sample clock tick.
Each trial's barcode value is logged, linking PsychoPy, NI, OneBox and video.

Usage:   python run_experiment.py --subject M01            (hardware)
         python run_experiment.py --subject test --simulate (no NI attached)
Single PC: SpikeGLX (OneBox), MVS (camera), NI and PsychoPy all run on this PC.
Start order: SpikeGLX recording -> this script (wait for '[NI] AI recording')
-> camera acquisition + recording in MVS (free-run 60 fps) -> SPACE to start trials.
This way the camera is streaming before the first stimulus, and AI3 strobe #0 is
video frame #0 (no offset to correct).
"""
import argparse, csv, os, random, time
from psychopy import visual, event, core
import config as C
from ni_rig import NIRig

ap = argparse.ArgumentParser()
ap.add_argument("--subject", default="test")
ap.add_argument("--simulate", action="store_true")
ap.add_argument("--seed", type=int, default=None)
ap.add_argument("--trials", type=int, default=C.N_TRIALS, help="override N_TRIALS (e.g. short dry run)")
ap.add_argument("--no-wait", action="store_true", help="skip the SPACE prompt (automated tests)")
a = ap.parse_args()

session = f"{a.subject}_{time.strftime('%Y%m%d_%H%M%S')}"
sdir = os.path.join(C.DATA_DIR, session)
rng = random.Random(a.seed)

# ---- check timing constraints ----
min_iti = C.ITI_FIXED_S if C.ITI_MODE == "fixed" else C.ITI_MIN_S
assert C.STIM_FRAMES / 60 + min_iti > C.event_duration_s() + 0.1, \
    "ITI too short: next stimulus would start before the previous barcode finishes"

# ---- window & clocks (as in bufferImageStim.py) ----
win = visual.Window(fullscr=C.FULLSCREEN, monitor=C.MONITOR, screen=C.SCREEN,
                    color="black", units="norm", allowGUI=False)
win.recordFrameIntervals = True
refresh = win.getActualFrameRate(nIdentical=20, nMaxFrames=120) or 60.0
clock = core.Clock()

# ---- stimuli: pre-render each condition with BufferImageStim (fast, constant draw time) ----
conditions = {}
for path in C.IMAGES:
    img = visual.ImageStim(win, image=path, size=(1.0, 1.0))
    conditions[os.path.splitext(os.path.basename(path))[0]] = visual.BufferImageStim(
        win, stim=[img], rect=(-1, 1, 1, -1))
win.clearBuffer()

# ---- photodiode patch (pixel units, screen corner) ----
w, h = win.size
s = C.PHOTODIODE_PATCH_PX
sx = (w / 2 - s / 2) * (1 if "right" in C.PHOTODIODE_CORNER else -1)
sy = (h / 2 - s / 2) * (1 if "top" in C.PHOTODIODE_CORNER else -1)
patch = visual.Rect(win, width=s, height=s, pos=(sx, sy), units="pix",
                    fillColor="white", lineColor=None)
patch_off = visual.Rect(win, width=s, height=s, pos=(sx, sy), units="pix",
                        fillColor="black", lineColor=None)
msg = visual.TextStim(win, text="Ready. Press SPACE to start, ESC to quit.", height=0.06)

# ---- NI ----
rig = NIRig(sdir, simulate=a.simulate)
rig.start()
os.makedirs(sdir, exist_ok=True)
logf = open(os.path.join(sdir, "psychopy_trials.csv"), "w", newline="")
log = csv.writer(logf)
log.writerow(["trial", "condition", "barcode", "flip_time_s", "fire_perf_s", "iti_s",
              "stim_frames", "dropped_frames_so_far"])

msg.draw(); patch_off.draw(); win.flip()
if not a.no_wait and "escape" in event.waitKeys(keyList=["space", "escape"]):
    rig.stop(); logf.close(); win.close(); core.quit()

if C.PSYCHOPY_HIGH_PRIORITY:
    core.rush(True)                                     # HIGH (not realtime) priority
code = rng.randrange(1, 2**31)
names = list(conditions)
try:
    for trial in range(a.trials):
        cond = rng.choice(names)
        code = (code + 1) % (2**C.BARCODE_BITS)
        rig.arm(code)                                   # waveform loaded before flip

        # onset frame
        conditions[cond].draw(); patch.draw()
        win.callOnFlip(rig.fire)
        t_on = win.flip()
        for _ in range(C.STIM_FRAMES - 1):
            conditions[cond].draw(); patch.draw(); win.flip()
        patch_off.draw(); win.flip()                    # offset frame (photodiode OFF)

        iti = C.ITI_FIXED_S if C.ITI_MODE == "fixed" else rng.uniform(C.ITI_MIN_S, C.ITI_MAX_S)
        log.writerow([trial, cond, code, f"{t_on:.6f}", f"{rig.fire_perf:.6f}", f"{iti:.3f}",
                      C.STIM_FRAMES, win.nDroppedFrames])
        logf.flush()
        t_end = clock.getTime() + iti
        rig.wait_done()
        while clock.getTime() < t_end:
            patch_off.draw(); win.flip()
            if event.getKeys(["escape"]): raise KeyboardInterrupt
        print(f"trial {trial:3d}  {cond:8s}  barcode {code}")
except KeyboardInterrupt:
    print("aborted by user")
finally:
    core.rush(False)
    rig.wait_done(); rig.stop(); logf.close()
    print(f"dropped frames: {win.nDroppedFrames}  | data in {sdir}")
    if win.nDroppedFrames and not a.simulate:
        print("  note: SpikeGLX, MVS and PsychoPy share this PC. If drops matter, minimise SpikeGLX's "
              "graph windows during the session, or set PSYCHOPY_HIGH_PRIORITY. "
              "Onset timing is unaffected: it is measured by the photodiode.")
    win.close(); core.quit()
