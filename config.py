"""Central settings for the octopus synchronization rig."""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# NI USB-6421
DEVICE = "MAH-OCTO-NP-USB6421"
DO_LINES = "port0/line0:2"
BARCODE_BIT, CAM_BIT = 1, 2
DO_RATE_HZ = 1000
CAM_MARKER_MS = 100
BARCODE_START_MS = 20
BARCODE_GAP_MS = 20
BARCODE_BIT_MS = 30
BARCODE_BITS = 32

AI_RATE_HZ = 10000
AI_RANGES_V = {"photodiode": 5.0, "barcode_loop": 10.0, "camera_exposure": 10.0}
AI_STORE_V_PER_COUNT = 0.00025
AI_CHANNELS = {"photodiode": "ai0", "barcode_loop": "ai1", "camera_exposure": "ai3"}
AI_CHUNK_S = 0.1

# Display refresh is distinct from camera acquisition and MVS preview rates.
DISPLAY_REFRESH_HZ = 319.98
DISPLAY_REFRESH_TOLERANCE_HZ = 3.2  # warn if measured rate differs by more than 1%
STIM_DURATION_S = 0.5
CAMERA_FPS = 60                 # camera acquisition / exposure-strobe rate
CAMERA_PREVIEW_FPS = 30         # MVS preview only; does not set acquisition rate
CAMERA_EXPOSURE_EDGE = "auto"   # auto | falling | rising

FULLSCREEN = True
MONITOR = "testMonitor"
SCREEN = 0
IMAGES = [os.path.join(ROOT, "stimuli", f) for f in ("stim_A.png", "stim_B.png")]
N_TRIALS = 40
ITI_MODE = "random"
ITI_FIXED_S = 3.0
ITI_MIN_S, ITI_MAX_S = 2.0, 4.0
PHOTODIODE_PATCH_PX = 80
PHOTODIODE_CORNER = "bottom-right"
PSYCHOPY_HIGH_PRIORITY = False
DATA_DIR = os.path.join(ROOT, "data")


def barcode_duration_s():
    return (BARCODE_START_MS + BARCODE_GAP_MS + BARCODE_BITS * BARCODE_BIT_MS) / 1000.0


def event_duration_s():
    return max(barcode_duration_s(), CAM_MARKER_MS / 1000) + 0.005


def stimulus_frames(refresh_hz=DISPLAY_REFRESH_HZ):
    """Integer display frames closest to the requested stimulus duration."""
    return max(1, round(STIM_DURATION_S * refresh_hz))
