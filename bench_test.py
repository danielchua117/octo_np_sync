"""Bench test without PsychoPy: records AI and fires N events (barcode + camera
marker) every 3 s. Run with OneBox and camera recording, then analyse with
analysis/align_session.py to confirm every device sees every barcode."""
import sys, os, time, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config as C
from ni_rig import NIRig
n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
sdir = os.path.join(C.DATA_DIR, "bench_" + time.strftime("%Y%m%d_%H%M%S"))
rig = NIRig(sdir); rig.start(); time.sleep(1)
code = random.randrange(1, 2**31)
for i in range(n):
    code += 1; rig.arm(code); rig.fire(); rig.wait_done()
    print(f"event {i}: barcode {code}"); time.sleep(3 - C.event_duration_s())
time.sleep(1); rig.stop(); print("data in", sdir)
