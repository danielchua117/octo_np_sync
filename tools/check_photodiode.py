"""Live photodiode / input check: prints min/mean/max of every AI channel each 0.5 s.
Put a white then black full-screen (or the patch) under the sensor and pick a resistor
that gives a clear black/white difference (ideally >0.2 V) without saturating near 5 V."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np, nidaqmx, nidaqmx.errors
from nidaqmx.constants import AcquisitionType, TerminalConfiguration
import config as C
from ni_rig import check_device, _explain
print(f"device {C.DEVICE}: {check_device()}")
try:
    with nidaqmx.Task() as t:
        for name, p in C.AI_CHANNELS.items():
            r = C.AI_RANGES_V[name]
            t.ai_channels.add_ai_voltage_chan(f"{C.DEVICE}/{p}", terminal_config=TerminalConfiguration.RSE,
                                              min_val=-r, max_val=r)
        t.timing.cfg_samp_clk_timing(C.AI_RATE_HZ, sample_mode=AcquisitionType.CONTINUOUS)
        names = list(C.AI_CHANNELS)
        print("Ctrl+C to stop")
        try:
            while True:
                x = np.array(t.read(number_of_samples_per_channel=C.AI_RATE_HZ // 2))
                print("  ".join(f"{n}: {r.min():5.2f}/{r.mean():5.2f}/{r.max():5.2f} V"
                                for n, r in zip(names, x, strict=True)))
        except KeyboardInterrupt:
            pass
except nidaqmx.errors.DaqError as e:
    sys.exit(_explain(e))
