"""ni_rig.py -- NI USB-6421 control: continuous AI recording + on-demand event output.

AI task (continuous, hardware clock) = master timeline. Records photodiode,
barcode loopback and camera exposure pulses to data/<session>/ni_ai.bin
(int16, interleaved, C.AI_STORE_V_PER_COUNT volts per count) + ni_ai.json (metadata).

DO task (finite, hardware clock) holds one event waveform. arm(code) loads it
before the flip; fire() is called by PsychoPy's win.callOnFlip so the
barcode (OneBox SMA1) and camera marker start together right after the stimulus flip.
The software start latency (~ms) does not matter for analysis: the TRUE onset
times are measured on the AI loopback and photodiode channels.

Single-PC rig: SpikeGLX (OneBox) runs on this same PC. The OneBox still has its
own sample clock, so alignment is done with the barcodes, not the PC clock.
SpikeGLX can also drive NI-DAQ cards; it must NOT be set to use this USB-6421,
or the two programs will fight over it (DAQmx error -50103).
"""
import json, os, threading, time
import numpy as np
import config as C
from barcode_tools import event_waveform


# DAQmx error codes we translate into plain-language advice
_ERR_HELP = {
    -50103: ("The USB-6421 '{dev}' is already in use by another program. On this single-PC rig "
             "check: (1) SpikeGLX - its NI-DAQ stream must be disabled or set to a different "
             "device; (2) an open NI MAX test panel; (3) another copy of this script or "
             "tools/check_photodiode.py. Close it and try again."),
    -200220: "Device '{dev}' not found. Check the name in NI MAX and DEVICE in config.py.",
    -201003: "Device '{dev}' cannot be accessed - is the USB cable plugged in and the device powered?",
}


def _explain(e):
    code = getattr(e, "error_code", None)
    msg = _ERR_HELP.get(code)
    return (msg.format(dev=C.DEVICE) + f"  [DAQmx {code}]") if msg else f"NI-DAQmx error: {e}"


def check_device():
    """Raise a clear error if C.DEVICE is not present on this PC; return its product type."""
    import nidaqmx.system
    devs = {d.name: d for d in nidaqmx.system.System.local().devices}
    if C.DEVICE not in devs:
        raise RuntimeError(f"NI device '{C.DEVICE}' not found on this PC. Devices present: "
                           f"{sorted(devs) or 'none'}. Set DEVICE in config.py to the name shown in NI MAX.")
    return getattr(devs[C.DEVICE], "product_type", "?")


class NIRig:
    def __init__(self, session_dir, simulate=False):
        self.dir, self.sim = session_dir, simulate
        os.makedirs(session_dir, exist_ok=True)
        self._stop = threading.Event()
        self._armed = None
        self.n_ai = 0
        self.error = None                                # set if the AI thread fails
        self.ai = self.do = None

    # ---------- lifecycle ----------
    def start(self):
        meta = dict(device=C.DEVICE, rate_hz=C.AI_RATE_HZ, channels=list(C.AI_CHANNELS),
                    physical=list(C.AI_CHANNELS.values()), dtype="int16",
                    v_per_count=C.AI_STORE_V_PER_COUNT, ranges_v=dict(C.AI_RANGES_V),
                    started_wall=time.strftime("%Y-%m-%d %H:%M:%S"), simulate=self.sim)
        with open(os.path.join(self.dir, "ni_ai.json"), "w") as fh:
            json.dump(meta, fh, indent=2)
        self._f = open(os.path.join(self.dir, "ni_ai.bin"), "wb")
        if self.sim:
            print("[NI] SIMULATE mode: no hardware, no AI data written"); return
        import nidaqmx
        import nidaqmx.errors
        try:
            ptype = check_device()
            self._open_tasks(nidaqmx)
        except nidaqmx.errors.DaqError as e:
            self._close_tasks(); self._f.close()
            raise RuntimeError(_explain(e)) from e
        except Exception:
            self._close_tasks(); self._f.close()
            raise
        print(f"[NI] {C.DEVICE} ({ptype}): AI recording {len(C.AI_CHANNELS)} ch @ {C.AI_RATE_HZ} Hz -> {self.dir}")

    def _open_tasks(self, nidaqmx):
        from nidaqmx.constants import (AcquisitionType, LineGrouping,
                                       TerminalConfiguration, TaskMode)
        from nidaqmx.stream_readers import AnalogMultiChannelReader
        from nidaqmx.stream_writers import DigitalSingleChannelWriter
        # AI
        self.ai = nidaqmx.Task("sync_ai")
        for name, phys in C.AI_CHANNELS.items():
            r = C.AI_RANGES_V[name]
            self.ai.ai_channels.add_ai_voltage_chan(
                f"{C.DEVICE}/{phys}", terminal_config=TerminalConfiguration.RSE,
                min_val=-r, max_val=r)
        n = int(C.AI_RATE_HZ * C.AI_CHUNK_S)
        self.ai.timing.cfg_samp_clk_timing(C.AI_RATE_HZ, sample_mode=AcquisitionType.CONTINUOUS,
                                           samps_per_chan=n * 50)
        self._reader = AnalogMultiChannelReader(self.ai.in_stream)
        self._buf = np.zeros((len(C.AI_CHANNELS), n))
        # DO
        self._wf_len = len(event_waveform(0))
        self.do = nidaqmx.Task("sync_do")
        self.do.do_channels.add_do_chan(f"{C.DEVICE}/{C.DO_LINES}",
                                        line_grouping=LineGrouping.CHAN_FOR_ALL_LINES)
        self.do.timing.cfg_samp_clk_timing(C.DO_RATE_HZ, sample_mode=AcquisitionType.FINITE,
                                           samps_per_chan=self._wf_len)
        self._writer = DigitalSingleChannelWriter(self.do.out_stream, auto_start=False)
        self.do.control(TaskMode.TASK_COMMIT)            # lower start latency
        self.ai.start()
        self._th = threading.Thread(target=self._ai_loop, daemon=True); self._th.start()

    def _close_tasks(self):
        for name in ("do", "ai"):
            t = getattr(self, name, None)
            if t is None:
                continue
            try: t.stop()
            except Exception: pass
            try: t.close()
            except Exception: pass
            setattr(self, name, None)

    def _ai_loop(self):
        n = self._buf.shape[1]
        k = 1.0 / C.AI_STORE_V_PER_COUNT
        try:
            while not self._stop.is_set():
                self._reader.read_many_sample(self._buf, number_of_samples_per_channel=n, timeout=5)
                counts = np.clip(np.round(self._buf.T * k), -32768, 32767).astype("<i2")
                self._f.write(counts.tobytes()); self.n_ai += n
        except Exception as e:                           # e.g. buffer overflow, USB unplugged
            self.error = e
            print(f"[NI] *** AI recording FAILED: {e!r} -- data after this point is missing ***")

    def stop(self):
        self._stop.set()
        if not self.sim:
            self._th.join(timeout=3)
            self._close_tasks()
        self._f.close()
        print("[NI] stopped; AI samples per channel:", self.n_ai)
        if self.error is not None:
            print("[NI] WARNING: AI recording stopped early because of:", repr(self.error))

    # ---------- events ----------
    def arm(self, code):
        """Load the waveform for this barcode (call BEFORE the stimulus flip)."""
        if self.error is not None:
            raise RuntimeError(f"NI AI recording has failed: {self.error!r}")
        self._armed = code
        if self.sim: return
        self.do.stop()                                   # finished previous event
        self._writer.write_many_sample_port_uint32(event_waveform(code))

    def fire(self):
        """Call via win.callOnFlip(rig.fire). Starts barcode + camera marker."""
        self.fire_perf = time.perf_counter()
        if not self.sim:
            self.do.start()

    def wait_done(self, timeout=3.0):
        if not self.sim:
            self.do.wait_until_done(timeout=timeout)
