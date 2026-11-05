"""config.py -- every rig setting in one place. Edit this file, not the scripts.
Requires Python 3.11 (see requirements.txt)."""
import os
ROOT = os.path.dirname(os.path.abspath(__file__))   # paths below work from any working directory

# ---------------- NI USB-6421 ----------------
DEVICE = "MAH-OCTO-NP-USB6421"        # USB-6421 name as listed in NI MAX / NI-DAQmx on the rig PC

# Digital outputs (one hardware-timed DO task, written together)
DO_LINES = "port0/line0:2"
BARCODE_BIT, CAM_BIT = 1, 2          # P0.1 barcode -> OneBox SMA1 (sync in), P0.2 marker -> camera Line0
                                      # P0.0 is spare (held LOW). The barcode start pulse IS the onset TTL.
DO_RATE_HZ = 1000                     # 1 ms resolution for the event waveform

# Event waveform (barcode + camera marker start on the SAME sample = stimulus onset)
CAM_MARKER_MS = 100                   # marker to camera (>= 2 frames at 60 fps)
BARCODE_START_MS = 20                 # same format as arduino-barcodes.ino
BARCODE_GAP_MS = 20
BARCODE_BIT_MS = 30
BARCODE_BITS = 32

# Analog inputs (one hardware-timed AI task = the NI master timeline)
AI_RATE_HZ = 10000                    # 0.1 ms resolution; 3 ch = 30 kS/s (limit 250 kS/s)
AI_RANGES_V = {                       # per-channel input range; USB-6421 offers +/-0.2, 1, 5, 10 V
    "photodiode": 5.0,                #   TEPT5600 output is 0..~4.9 V -> +/-5 V gives 2x finer resolution
    "barcode_loop": 10.0,             #   5 V TTL
    "camera_exposure": 10.0,          #   5 V pull-up
}
AI_STORE_V_PER_COUNT = 0.00025        # ni_ai.bin is int16 in 0.25 mV steps (+/-8.19 V full scale)
AI_CHANNELS = {                       # name -> physical channel
    "photodiode": "ai0",              # TEPT5600 on the screen patch
    "barcode_loop": "ai1",            # jumper from P0.1
    "camera_exposure": "ai3",         # camera P7 pin 4 blue OPTO_OUT (Line1) with 2 kOhm pull-up to NI +5V; pin 5 green OPTO_GND to D GND
}
AI_CHUNK_S = 0.1

# ---------------- Camera (Hikrobot MV-CS050-10UM/UC, 6-pin P7 I/O) ----------------
CAMERA_FPS = 60
CAMERA_EXPOSURE_EDGE = "auto"         # "auto" | "falling" | "rising". Line1 opto output with 2k pull-up to 5 V:
                                      # conducting ~0.6-0.7 V with 2k (manual: 0.84 V with 1k), off = ~5 V. "auto" picks the
                                      # edge that starts the SHORT state (the strobe pulse).

# ---------------- PsychoPy / stimulus ----------------
FULLSCREEN = True
MONITOR = "testMonitor"
SCREEN = 0
IMAGES = [os.path.join(ROOT, "stimuli", f) for f in ("stim_A.png", "stim_B.png")]  # placeholders (grating, checkerboard) - replace with yours
N_TRIALS = 40
STIM_FRAMES = 30                      # 0.5 s at 60 Hz
ITI_MODE = "random"                   # "fixed" | "random"
ITI_FIXED_S = 3.0
ITI_MIN_S, ITI_MAX_S = 2.0, 4.0
PHOTODIODE_PATCH_PX = 80              # white square under the photodiode during stimulus
PHOTODIODE_CORNER = "bottom-right"    # "top-left" | "top-right" | "bottom-left" | "bottom-right"

# Single-PC rig: SpikeGLX (OneBox), the camera software and this script share one PC.
# True = raise PsychoPy to HIGH process priority during trials (fewer dropped frames).
# Left False by default so SpikeGLX is never starved of CPU (lost neural data is worse
# than a late frame). Turn on only if you see dropped frames and SpikeGLX stays healthy.
PSYCHOPY_HIGH_PRIORITY = False

# ---------------- Output ----------------
DATA_DIR = os.path.join(ROOT, "data")


def barcode_duration_s():
    return (BARCODE_START_MS + BARCODE_GAP_MS + BARCODE_BITS * BARCODE_BIT_MS) / 1000.0


def event_duration_s():
    return max(barcode_duration_s(), CAM_MARKER_MS / 1000) + 0.005
