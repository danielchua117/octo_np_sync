"""Exercise the REAL ni_rig.NIRig hardware code path against a fake NI-DAQmx driver.

The fake driver records every API call, produces a hardware-clocked AI stream in
real time, and loops the DO waveform back onto AI1 (like the P0.1 -> AI1 jumper),
so we can check: task setup (RSE, ranges, rates), arm/fire/wait/stop, the binary
file format, barcode decoding of the recorded loopback, and the AI-failure path.
Run:  python tests/test_ni_rig_mock.py
"""
import enum, json, os, shutil, sys, threading, time, types
import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import config as C

# ---------------------------------------------------------------- fake nidaqmx
CALLS = []


class _E(enum.Enum):
    pass


constants = types.ModuleType("nidaqmx.constants")
constants.AcquisitionType = enum.Enum("AcquisitionType", "CONTINUOUS FINITE")
constants.LineGrouping = enum.Enum("LineGrouping", "CHAN_FOR_ALL_LINES CHAN_PER_LINE")
constants.TerminalConfiguration = enum.Enum("TerminalConfiguration", "DEFAULT RSE NRSE DIFF")
constants.TaskMode = enum.Enum("TaskMode", "TASK_COMMIT TASK_START")


class FakeDevice:
    """Shared 'hardware': AI sample counter + pending DO waveform for loopback."""
    def __init__(self):
        self.lock = threading.Lock()
        self.ai_sample = 0          # samples per channel acquired so far
        self.ai_t0 = None
        self.do_wave = None         # armed waveform
        self.do_start_sample = None
        self.events = []            # (start_sample, waveform)
        self.fail_after_reads = None


DEV = FakeDevice()


class _Chans:
    def __init__(self, task): self.task = task
    def add_ai_voltage_chan(self, phys, terminal_config=None, min_val=-5.0, max_val=5.0, **kw):
        CALLS.append(("add_ai_voltage_chan", phys, terminal_config, min_val, max_val))
        self.task.ai_phys.append(phys)
    def add_do_chan(self, lines, line_grouping=None, **kw):
        CALLS.append(("add_do_chan", lines, line_grouping))


class _Timing:
    def __init__(self, task): self.task = task
    def cfg_samp_clk_timing(self, rate, sample_mode=None, samps_per_chan=1000, **kw):
        CALLS.append(("cfg_samp_clk_timing", self.task.name, rate, sample_mode, samps_per_chan))
        self.task.rate, self.task.mode, self.task.n = rate, sample_mode, samps_per_chan


class Task:
    def __init__(self, name=""):
        self.name, self.ai_phys, self.running = name, [], False
        self.ai_channels = self.do_channels = _Chans(self)
        self.timing = _Timing(self)
        self.in_stream = self.out_stream = self
        self.buf = None
    def control(self, action): CALLS.append(("control", self.name, action))
    def start(self):
        CALLS.append(("start", self.name)); self.running = True
        if self.name == "sync_ai":
            DEV.ai_t0 = time.perf_counter()
        elif self.name == "sync_do":
            with DEV.lock:
                assert DEV.do_wave is not None, "DO started without a written waveform"
                # DO start lands on the next AI sample (shared device timebase)
                s = int((time.perf_counter() - DEV.ai_t0) * C.AI_RATE_HZ)
                DEV.events.append((s, DEV.do_wave.copy())); DEV.do_wave = None
            self.done_at = time.perf_counter() + self.n / self.rate
    def wait_until_done(self, timeout=10.0):
        assert self.running, "wait_until_done on a task that was never started"
        rem = self.done_at - time.perf_counter()
        assert rem < timeout, "DO did not finish within timeout"
        time.sleep(max(0, rem))
    def stop(self): CALLS.append(("stop", self.name)); self.running = False
    def close(self): CALLS.append(("close", self.name))


class AnalogMultiChannelReader:
    def __init__(self, stream): self.task = stream; self.reads = 0
    def read_many_sample(self, data, number_of_samples_per_channel, timeout=10.0):
        self.reads += 1
        if DEV.fail_after_reads is not None and self.reads > DEV.fail_after_reads:
            raise RuntimeError("DaqError -200279: buffer overflow (simulated)")
        n = number_of_samples_per_channel
        s0 = DEV.ai_sample
        # wait like real hardware until these samples exist
        while (time.perf_counter() - DEV.ai_t0) * C.AI_RATE_HZ < s0 + n:
            time.sleep(0.005)
        idx = np.arange(s0, s0 + n)
        data[:] = 0.0
        data[0] = 0.013 + np.random.default_rng(s0).normal(0, 0.002, n)   # photodiode black level
        data[2] = 5.0                                                        # camera pull-up idle
        with DEV.lock:
            for start, w in DEV.events:
                up = np.repeat(w, C.AI_RATE_HZ // C.DO_RATE_HZ)
                k = idx - start
                m = (k >= 0) & (k < len(up))
                data[1, m] = 5.0 * ((up[k[m]] >> C.BARCODE_BIT) & 1)
        DEV.ai_sample += n


class DigitalSingleChannelWriter:
    def __init__(self, task_out_stream, auto_start=False):
        assert auto_start is False
        self.task = task_out_stream
    def write_many_sample_port_uint32(self, data, timeout=10.0):
        assert data.dtype == np.uint32 and data.ndim == 1
        assert len(data) == self.task.n, "waveform length != DO samps_per_chan"
        assert data[-1] == 0, "lines must idle LOW after the event"
        with DEV.lock:
            DEV.do_wave = data


class DaqError(Exception):
    def __init__(self, message, error_code, task_name=""):
        super().__init__(message); self.error_code = error_code


errors = types.ModuleType("nidaqmx.errors"); errors.DaqError = DaqError
DEVICES = [C.DEVICE, "SomeOtherDev"]
RESERVE_ON_START = {"on": False}


class _Dev:
    def __init__(self, name): self.name, self.product_type = name, "USB-6421"


class System:
    @staticmethod
    def local():
        o = types.SimpleNamespace(); o.devices = [_Dev(n) for n in DEVICES]; return o


system = types.ModuleType("nidaqmx.system"); system.System = System
_orig_start = Task.start


def _start(self):
    if RESERVE_ON_START["on"] and self.name == "sync_ai":
        raise DaqError("The specified resource is reserved.", -50103)
    _orig_start(self)


Task.start = _start
fake = types.ModuleType("nidaqmx"); fake.Task = Task; fake.constants = constants
fake.errors, fake.system = errors, system
sr = types.ModuleType("nidaqmx.stream_readers"); sr.AnalogMultiChannelReader = AnalogMultiChannelReader
sw = types.ModuleType("nidaqmx.stream_writers"); sw.DigitalSingleChannelWriter = DigitalSingleChannelWriter
sys.modules.update({"nidaqmx": fake, "nidaqmx.constants": constants,
                    "nidaqmx.errors": errors, "nidaqmx.system": system,
                    "nidaqmx.stream_readers": sr, "nidaqmx.stream_writers": sw})

# ---------------------------------------------------------------- test 1: normal run
from ni_rig import NIRig
from barcode_tools import edges_from_analog, decode

sd = os.path.join(C.DATA_DIR, "_mocktest"); shutil.rmtree(sd, ignore_errors=True)
rig = NIRig(sd); rig.start(); time.sleep(0.3)
codes = [123456, 7, 2**31 + 5]
for c in codes:
    rig.arm(c); rig.fire(); rig.wait_done(); time.sleep(0.15)
time.sleep(0.3); rig.stop()

ai_calls = [c for c in CALLS if c[0] == "add_ai_voltage_chan"]
assert [c[1] for c in ai_calls] == [f"{C.DEVICE}/{p}" for p in C.AI_CHANNELS.values()]
assert all(c[2] == constants.TerminalConfiguration.RSE for c in ai_calls), "AI must be RSE"
assert [c[4] for c in ai_calls] == [C.AI_RANGES_V[k] for k in C.AI_CHANNELS], "per-channel ranges"
assert ("add_do_chan", f"{C.DEVICE}/{C.DO_LINES}", constants.LineGrouping.CHAN_FOR_ALL_LINES) in CALLS
assert ("control", "sync_do", constants.TaskMode.TASK_COMMIT) in CALLS
assert ("close", "sync_ai") in CALLS and ("close", "sync_do") in CALLS

meta = json.load(open(os.path.join(sd, "ni_ai.json")))
raw = np.fromfile(os.path.join(sd, "ni_ai.bin"), dtype="<i2")
nch = len(meta["channels"]); assert raw.size == rig.n_ai * nch
x = raw.reshape(-1, nch).T * meta["v_per_count"]
assert abs(np.median(x[2]) - 5.0) < 1e-3 and abs(np.median(x[0]) - 0.013) < 1e-3
fs = meta["rate_hz"]
bt, bv = edges_from_analog(x[1], fs)
t, got = decode(bt, bv, t_end=x.shape[1] / fs)
assert list(got) == codes, f"loopback decode {list(got)} != {codes}"
assert rig.error is None
print(f"PASS [NIRig mock run]: {rig.n_ai} samples/ch, RSE + ranges {meta['ranges_v']}, "
      f"decoded loopback barcodes {list(got)}")

# ---------------------------------------------------------------- test 2: AI failure is reported
DEV.__init__(); CALLS.clear()
sd2 = os.path.join(C.DATA_DIR, "_mocktest_fail"); shutil.rmtree(sd2, ignore_errors=True)
DEV.fail_after_reads = 2
rig = NIRig(sd2); rig.start(); time.sleep(0.6)
assert rig.error is not None, "AI failure was not captured"
try:
    rig.arm(1); raise AssertionError("arm() should refuse to run after AI failure")
except RuntimeError:
    pass
rig.stop()
print("PASS [NIRig AI failure]: error captured, arm() refuses, stop() warns")

# ---------------------------------------------------------------- test 3: wrong device name
DEV.__init__(); DEVICES[:] = ["Dev1"]
try:
    NIRig(sd2).start(); raise AssertionError("missing device not detected")
except RuntimeError as e:
    assert C.DEVICE in str(e) and "Dev1" in str(e), e
    print("PASS [device check]:", str(e)[:110], "...")
DEVICES[:] = [C.DEVICE]

# ---------------------------------------------------------------- test 4: device busy (SpikeGLX / NI MAX)
DEV.__init__(); RESERVE_ON_START["on"] = True
try:
    NIRig(sd2).start(); raise AssertionError("reserved device not reported")
except RuntimeError as e:
    assert "SpikeGLX" in str(e) and "-50103" in str(e), e
    assert ("close", "sync_ai") in CALLS and ("close", "sync_do") in CALLS, "tasks not released"
    print("PASS [device busy]: tasks released; message names SpikeGLX / NI MAX")
RESERVE_ON_START["on"] = False
shutil.rmtree(sd, ignore_errors=True); shutil.rmtree(sd2, ignore_errors=True)
print("ALL MOCK TESTS PASSED")
