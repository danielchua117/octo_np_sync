"""NI USB-6421 continuous AI recorder and finite event output."""
import json, os, threading, time
import numpy as np
import config as C
from barcode_tools import event_waveform

_ERR_HELP = {
    -50103: ("The USB-6421 '{dev}' is already in use. Disable SpikeGLX NI-DAQ streaming for this device "
             "and close NI MAX test panels/other scripts."),
    -200220: "Device '{dev}' not found. Check NI MAX and DEVICE in config.py.",
    -201003: "Device '{dev}' cannot be accessed; check USB and power.",
}


def _explain(e):
    code = getattr(e, "error_code", None)
    msg = _ERR_HELP.get(code)
    return (msg.format(dev=C.DEVICE) + f" [DAQmx {code}]") if msg else f"NI-DAQmx error: {e}"


def check_device():
    import nidaqmx.system
    devs = {d.name: d for d in nidaqmx.system.System.local().devices}
    if C.DEVICE not in devs:
        raise RuntimeError(f"NI device {C.DEVICE!r} not found; devices present: {sorted(devs)}")
    return getattr(devs[C.DEVICE], "product_type", "?")


class NIRig:
    def __init__(self, session_dir, simulate=False):
        self.dir, self.sim = session_dir, simulate
        os.makedirs(session_dir, exist_ok=True)
        self._stop = threading.Event()
        self.n_ai = 0
        self.error = None
        self.ai = self.do = None
        self._th = None
        self.fire_perf = None

    def start(self):
        meta = dict(device=C.DEVICE, rate_hz=C.AI_RATE_HZ, channels=list(C.AI_CHANNELS),
                    physical=list(C.AI_CHANNELS.values()), dtype="int16",
                    v_per_count=C.AI_STORE_V_PER_COUNT, ranges_v=dict(C.AI_RANGES_V),
                    started_wall=time.strftime("%Y-%m-%d %H:%M:%S"), simulate=self.sim)
        with open(os.path.join(self.dir, "ni_ai.json"), "w") as f:
            json.dump(meta, f, indent=2)
        self._f = open(os.path.join(self.dir, "ni_ai.bin"), "wb")
        if self.sim:
            print("[NI] SIMULATE mode: no hardware AI data", flush=True)
            return
        import nidaqmx, nidaqmx.errors
        try:
            ptype = check_device()
            self._open_tasks(nidaqmx)
        except nidaqmx.errors.DaqError as e:
            self._close_tasks(); self._f.close()
            raise RuntimeError(_explain(e)) from e
        except Exception:
            self._close_tasks(); self._f.close()
            raise
        print(f"[NI] {C.DEVICE} ({ptype}): AI recording {len(C.AI_CHANNELS)} ch @ {C.AI_RATE_HZ} Hz -> {self.dir}", flush=True)

    def _open_tasks(self, nidaqmx):
        from nidaqmx.constants import AcquisitionType, LineGrouping, TerminalConfiguration, TaskMode
        from nidaqmx.stream_readers import AnalogMultiChannelReader
        from nidaqmx.stream_writers import DigitalSingleChannelWriter
        self.ai = nidaqmx.Task("sync_ai")
        for name, phys in C.AI_CHANNELS.items():
            r = C.AI_RANGES_V[name]
            self.ai.ai_channels.add_ai_voltage_chan(f"{C.DEVICE}/{phys}",
                terminal_config=TerminalConfiguration.RSE, min_val=-r, max_val=r)
        n = int(C.AI_RATE_HZ * C.AI_CHUNK_S)
        self.ai.timing.cfg_samp_clk_timing(C.AI_RATE_HZ, sample_mode=AcquisitionType.CONTINUOUS,
                                           samps_per_chan=n * 50)
        self._reader = AnalogMultiChannelReader(self.ai.in_stream)
        self._buf = np.zeros((len(C.AI_CHANNELS), n))
        self._wf_len = len(event_waveform(0))
        self.do = nidaqmx.Task("sync_do")
        self.do.do_channels.add_do_chan(f"{C.DEVICE}/{C.DO_LINES}",
                                         line_grouping=LineGrouping.CHAN_FOR_ALL_LINES)
        self.do.timing.cfg_samp_clk_timing(C.DO_RATE_HZ, sample_mode=AcquisitionType.FINITE,
                                           samps_per_chan=self._wf_len)
        self._writer = DigitalSingleChannelWriter(self.do.out_stream, auto_start=False)
        self.do.control(TaskMode.TASK_COMMIT)
        self.ai.start()
        self._th = threading.Thread(target=self._ai_loop, daemon=True)
        self._th.start()

    def _close_tasks(self):
        for name in ("do", "ai"):
            task = getattr(self, name, None)
            if task is not None:
                try: task.stop()
                except Exception: pass
                try: task.close()
                except Exception: pass
                setattr(self, name, None)

    def _ai_loop(self):
        n = self._buf.shape[1]
        scale = 1.0 / C.AI_STORE_V_PER_COUNT
        try:
            while not self._stop.is_set():
                self._reader.read_many_sample(self._buf, number_of_samples_per_channel=n, timeout=5)
                counts = np.clip(np.round(self._buf.T * scale), -32768, 32767).astype("<i2")
                self._f.write(counts.tobytes())
                self.n_ai += n
        except Exception as e:
            if not self._stop.is_set():
                self.error = e
                print(f"[NI] *** AI recording FAILED: {e!r} ***", flush=True)

    def stop(self):
        self._stop.set()
        if not self.sim:
            if self.ai is not None:
                try: self.ai.stop()
                except Exception: pass
            if self._th is not None:
                self._th.join(timeout=10)
                if self._th.is_alive():
                    raise RuntimeError("AI reader thread did not stop; refusing to close its output file")
            self._close_tasks()
        self._f.close()
        print("[NI] stopped; AI samples per channel:", self.n_ai, flush=True)
        if self.error is not None:
            print("[NI] WARNING: AI recording stopped early:", repr(self.error), flush=True)

    def arm(self, code):
        if self.error is not None:
            raise RuntimeError(f"NI AI recording has failed: {self.error!r}")
        if self.sim:
            return
        self.do.stop()
        self._writer.write_many_sample_port_uint32(event_waveform(code))

    def fire(self):
        """Start the currently armed finite barcode/marker waveform."""
        self.fire_perf = time.perf_counter()
        if not self.sim:
            self.do.start()

    def fire_event(self, code, timeout=3.0):
        """Arm, start, and wait for one event; used by periodic barcode worker."""
        self.arm(code)
        self.fire()
        self.wait_done(timeout=timeout)
        return self.fire_perf

    def wait_done(self, timeout=3.0):
        if not self.sim:
            self.do.wait_until_done(timeout=timeout)
