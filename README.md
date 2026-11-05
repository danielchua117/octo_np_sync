# Sync Rig v7: PsychoPy · NI USB-6421 · Neuropixels OneBox (SMA1) · Hikrobot camera

No Arduino. The **NI USB-6421 is the master clock**. At every stimulus onset frame,
PsychoPy tells the NI to start one hardware-timed event:

| Line | Signal | Goes to |
|---|---|---|
| P0.1 | 32-bit barcode (~1 s, value +1 per trial). Its 20 ms start pulse is the onset TTL | OneBox **SMA1 (rear sync input)** over coax, plus a loopback jumper to AI1 |
| P0.2 | Camera marker, 100 ms | Camera Line0 (opto input) |
| P0.0 | Spare, held LOW | Not connected |

The OneBox has a single sync SMA on the rear panel ([Neuropixels OneBox brochure](https://www.neuropixels.org)). A separate TTL is therefore no longer sent: the rising edge of each barcode's start pulse marks stimulus onset.

The NI also records, on a single AI clock:
- the photodiode (AI0), which gives the real on-screen onset,
- the barcode loopback (AI1),
- the camera exposure pulse for every frame (AI3), which gives a time for every video frame.

The barcode value links all four records: the PsychoPy log, the NI recording, the OneBox recording and the video.

## Python 3.11 setup (Windows stimulus PC)
1. Install **NI-DAQmx** (the driver, from ni.com) and **Python 3.11** (64-bit, from python.org).
2. In this folder, run:
   ```
   py -3.11 -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Run the checks below with the venv active. Each one should end with `ALL ... PASSED`.
   ```
   python tests/test_decoder.py
   python tests/test_offline.py
   python tests/test_ni_rig_mock.py
   ```
4. Dry-run PsychoPy without the NI: `python run_experiment.py --simulate --trials 3`.

The scripts work from any folder. Paths are resolved relative to this package.

## Files
| File | Purpose |
|---|---|
| `config.py` | All settings: device name (`MAH-OCTO-NP-USB6421`), pins, timings, trials, ITI, photodiode patch |
| `run_experiment.py` | PsychoPy task, structured on `bufferImageStim.py` |
| `ni_rig.py` | NI control: continuous AI recording and an armed hardware-timed DO event |
| `barcode_tools.py` | Builds the event waveform; centre-sampling barcode decoder |
| `analysis/align_session.py` | Per-trial table: NI onset, photodiode onset, display latency, camera frame, OneBox time |
| `tools/check_photodiode.py` | Live AI readout |
| `tools/bench_test.py` | Fires test events without PsychoPy |
| `tests/test_offline.py` | Hardware-free end-to-end alignment test, with strong and weak (measured) photodiode levels |
| `tests/test_decoder.py` | Barcode decoder stress test: random/extreme codes, gaps 1 ms–3 s, a recording starting mid-barcode |
| `tests/test_ni_rig_mock.py` | Runs the real NI code path against a fake DAQmx driver: RSE, ranges, loopback decode, failure handling |
| `photodiode_v7.png` | Photodiode circuit |
| `wiring_v7.png` | Wiring diagram |

## Current wiring (v7)
1. **OneBox:** P0.1 → 100 Ω (optional) → coax centre → OneBox rear **SMA1**. The coax shield goes to NI D GND. A loopback jumper also connects P0.1 to AI1.
2. **Camera (MV-CS050-10UM/UC, 6-pin P7 I/O cable MV-ACP-H6p-open-HF):**
   - Yellow (pin 2) → P0.2.
   - Blue (pin 4) → AI3, plus a **2 kΩ pull-up to +5V** (required).
   - Green (pin 5) → D GND.
   - Orange, purple, gray and the drain wire are not connected.
3. **Photodiode (TEPT5600):** collector → +5V; emitter → AI0, plus 2.2–10 kΩ to AI GND.
4. **Spare channels:** P0.0 and AI2.

### Breadboard junctions (each "group" = one numbered 5-hole strip a–e)
| Group | What goes in it |
|---|---|
| 10a–e | blue (camera pin 4) · wire to AI3 · one leg of 2 kΩ |
| 14a–e | other leg of 2 kΩ · wire to NI +5V |
| 20a–e | wire from P0.1 · loopback wire to AI1 · one leg of 100 Ω (or the coax centre directly) |
| 24a–e | other leg of 100 Ω · coax centre |
| − rail | wire to NI D GND · green (camera pin 5) · coax shield · photodiode load resistor |

Some notes:
- Long breadboards often have the − rail split halfway along. If yours does, bridge the gap with a jumper.
- Put a solid pin on each stranded wire end before pushing it into the breadboard.

### Grounding on a single PC
The NI and the OneBox are both plugged into the **same PC**, so their grounds are already joined through the PC's USB ports. The coax shield still has to connect at both ends:
- SMA1 measures the voltage between its centre pin and its shell. The barcode's return current must flow back to the NI through a short path running alongside the signal.
- The coax shield is that path. Without it, the return current flows through the two USB cables and the PC, which is longer and noisier.
- Joining the shield at both ends makes a small loop: NI → USB → PC → USB → OneBox → coax shield → NI. Keep it small by running the NI's USB cable, the OneBox's USB cable and the coax together, side by side.
  - Use short cables and plug both into the same PC (rear ports).
  - If 50 Hz hum ever shows up in the neural data, shorten and bundle these cables. Don't disconnect the shield; that would break the signal.

### Full terminal list
| NI terminal | To |
|---|---|
| P0.1 | 100 Ω (optional) → coax centre → OneBox SMA1; jumper → AI1 |
| D GND | coax shield (OneBox SMA1 shell); camera P7 pin 5 (green, OPTO_GND = Line0−/Line1−) |
| P0.2 | camera P7 pin 2 (yellow, OPTO_IN = Line0+). No series resistor |
| +5V (AO/DIO pin 24) | TEPT5600 collector (C); 2 kΩ pull-up to AI3 |
| AI0 | TEPT5600 emitter (E); 2.2–10 kΩ to AI GND (this rig: 10 kΩ) |
| AI3 | camera P7 pin 4 (blue, OPTO_OUT = Line1+) |
| AI GND | photodiode load resistor |
| CHSGND | rig chassis / Faraday cage |

**Photodiode orientation:** don't rely on leg length. Wire it so that AI0 reads about 0 V when the sensor is covered. If it reads about 0.7 V in the dark and barely responds to light, it is reversed.

**Measured on this rig** (10 kΩ, dark room, sensor flat on the screen): black ≈ 0.013 V, white ≈ 0.240 V. The analysis uses adaptive thresholds with hysteresis, and `tests/test_offline.py` checks it at exactly these levels. AI0 uses the ±5 V range, and data is stored in 0.25 mV steps.

### OneBox / SpikeGLX settings (check against the SpikeGLX/OneBox documentation)
- Set the OneBox sync source to **external (SMA1 input)**. The brochure lists "External SYNC input or internal SYNC source (configurable between 0.03 and 500 Hz)".
- SpikeGLX normally expects a periodic sync square wave on this input and may report sync-period warnings. The barcode pulses are not periodic. The edges are still recorded in the sync bit of each stream, and that is all the alignment needs. Confirm this with `tools/bench_test.py` before the first animal.
- Input voltage range and input impedance are not stated in the brochure.
  - Check the barcode is a valid HIGH at the SMA: with the OneBox powered and connected, run the bench test and look for clean edges in SpikeGLX.
  - If the input turns out to be 50 Ω terminated, remove the 100 Ω resistor (otherwise the signal drops to about 1.7 V).
- Optional: the front-panel SDR breakout board has 12 auxiliary ADC channels (BNC). A spare copy of the photodiode signal could go there, but it is not required.
- Export: extract rising/falling edges of the SMA1 sync bit (for example with CatGT) to `onebox_barcode_edges.csv` (`time_s,value`).

## Single-PC setup (SpikeGLX + MVS + NI + PsychoPy on one computer)
The OneBox still has **its own sample clock**, separate from the NI's. Sharing a PC doesn't change this; the PC clock is not a sample-accurate reference for either device. Alignment therefore still uses the barcodes: SMA1 on the OneBox, and the AI1 loopback on the NI.

Sharing one PC does need some care:

1. **SpikeGLX must not use the USB-6421.** SpikeGLX can record NI-DAQ cards as an extra "NI" stream. In SpikeGLX's configuration, leave the NI-DAQ stream **disabled**, or point it at a different device. If both programs try to use `MAH-OCTO-NP-USB6421`, this script stops with a DAQmx -50103 "device in use" message, which names the likely culprit. An open NI MAX test panel causes the same error, so close it before running.
2. **USB bandwidth.** The OneBox (USB 3.0) and the camera (up to about 300 MB/s at full resolution) are both heavy users. The NI needs very little.
   - Put the OneBox and the camera on **different USB 3 controllers**. Check in Windows Device Manager → View → Devices by connection.
   - Avoid hubs for the OneBox and the camera.
3. **Disk.** Raw full-resolution video at 60 fps is about 300 MB/s. Neuropixels data is much smaller, but continuous.
   - Record SpikeGLX and MVS to a fast SSD; ideally give the video its own drive.
   - Or use ROI/binning or compressed video in MVS.
4. **CPU and GPU.** SpikeGLX's live graphs, MVS's live view and PsychoPy all draw to the screen.
   - Run the stimulus on its own monitor, set as `SCREEN` in `config.py`.
   - Minimise SpikeGLX's graph windows during the session.
   - If PsychoPy reports dropped frames, you can set `PSYCHOPY_HIGH_PRIORITY = True` in `config.py`. It's off by default, so SpikeGLX is never starved of CPU.
   - Onset timing is unaffected either way, because the photodiode measures when each stimulus actually appeared.
5. **The OneBox's own code** runs as before: SpikeGLX or your acquisition script, with SMA1 as the external sync input. This package never controls the OneBox. It only sends barcodes into SMA1 and reads them back.

## Camera wiring (Hikrobot MV-CS050-10UM/UC + MV-ACP-H6p-open-HF cable)
The camera is powered over USB 3.0. The 6-pin P7 cable is used **only for timing signals**, and only 3 of its 6 wires are connected.
- Wire colours are from the MV-ACP-H6p-open-HF cable datasheet.
- Signals are from the 6-pin P7 table in Hikrobot's [USB3.0 Area Scan Camera User Manual](https://www.hikrobotics.com/en2/source/vision/document/2023/3/3/UD31198B_USB3.0%20Area%20Scan%20Camera%20User%20Manual_V2.3.1_20221214.pdf).

| P7 pin | Wire colour | Signal | Connect to |
|---|---|---|---|
| 1 | Orange | DC_PWR | **Not connected**. Insulate it; the camera is USB-powered |
| 2 | Yellow | OPTO_IN (Line 0+) | NI **P0.2** (camera marker) |
| 3 | Purple | GPIO (Line 2) | Not connected |
| 4 | Blue | OPTO_OUT (Line 1+) | Junction to NI **AI3**, plus **2 kΩ pull-up to NI +5V** |
| 5 | Green | OPTO_GND (Line 0− and Line 1−) | NI **D GND** (breadboard ground rail) |
| 6 | Gray | GND (power ground, Line 2−) | **Not connected**. This keeps the camera isolated from the NI |

The bare shield/drain wire, if present, is not connected.

Before soldering, confirm each colour with a continuity check: unplug the cable, then test from each socket number on the P7 plug to its bare wire end.

### Electrical notes (USB3 camera manual)
- **Line 0 input.** It reads LOW at 0–1 V and HIGH at 3.3–24 V. Avoid 1–3.3 V. Maximum current is 25 mA; breakdown is 30 V. The rising delay is 1.8–4.6 µs.
  - The NI's 5 V drives it directly, with no series resistor.
  - **Check:** with the marker HIGH, measure yellow to green. It must read **≥ 3.5 V**. If it's lower, add an NPN or MOSFET buffer powered from +5V.
- **Line 1 output.** It's an open-collector opto output: maximum 25 mA, 40 Ω output impedance.
  - With a 5 V supply and the 2 kΩ pull-up used here, LOW ≈ **0.6–0.7 V at about 2 mA**. The manual lists 0.84 V at 4.1 mA for 1 kΩ; 680 Ω also works (about 6 mA).
  - The switch to LOW is fast (3–6 µs delay, 1.9 µs fall). The return to HIGH is slow (15–60 µs delay), so the **falling edge is the exposure-start time**. `CAMERA_EXPOSURE_EDGE = "auto"` handles this.
- **Shared OPTO_GND.** Line 0− and Line 1− share pin 5 on this camera, so a single ground wire is enough.

### Do I need the I/O cable?
- **Streaming alone does not need it.** The USB 3.0 cable powers the camera and carries the video.
- **The cable is for timing.** Without it, video timing relies on PC/USB timestamps, with roughly 1–10 ms of jitter plus drift. With pins 4 and 5 wired, every frame gets an NI timestamp to within about 0.1 ms, on the same clock as the stimulus and the OneBox.
  - Pin 2 is optional. It marks the barcode frames in the video, as a self-check.
- **The current code expects the strobe on AI3.**

## Camera settings (MVS client)
Save these to User Set 1 and set it as the default.

| Menu | Setting | Value |
|---|---|---|
| Acquisition Control | Trigger Mode | **Off** (free-run) |
| | Acquisition Frame Rate Control Enable | **On** |
| | Acquisition Frame Rate | **60** (check that Resulting Frame Rate reads 60) |
| | Exposure Time | **≤ 10 000 µs** (must be < 16 600 µs at 60 fps) |
| Digital IO Control, Line Selector = Line1 | Line Mode | **Strobe** |
| | Line Source | **Exposure Start Active** |
| | Strobe Enable | **On** |
| | Strobe Line Duration | **1000 µs** |
| | Strobe Line Delay | **0 µs** |
| | Line Inverter | **Off** |
| Digital IO Control, Line Selector = Line0 | Line Mode | **Input** (marker only, not a trigger) |
| Chunk Data Control (if available) | Chunk Mode Active | **On**; enable Frame Counter, Timestamp and the line-status chunk |

**Bandwidth.** At the full 2448 × 2048 Mono 8 resolution, 60 fps is about 300 MB/s. That's the camera's maximum and close to the practical limit of USB 3.0. To keep 60 fps with no dropped frames:
- plug the camera into its own USB 3.0 port, on a separate controller from the NI if possible;
- or reduce the resolution with ROI or 2 × 2 binning.

Check Resulting Frame Rate and the dropped-frame counter in MVS.

**Start order.** Start camera acquisition after the script prints `[NI] AI recording` but before you press SPACE (see Running a session). The camera is then streaming before the first stimulus, and every frame, from frame 0, gets an NI time on AI3. Start MVS recording at the same moment as acquisition, so that video frame N = AI3 strobe N. Keep the camera running until the script has finished.

## Running a session
1. On this same PC, start the OneBox recording (SpikeGLX or your OneBox code), with SMA1 set as the external sync input and its NI-DAQ stream disabled.
2. `python run_experiment.py --subject M01`. Wait for `[NI] AI recording`.
3. Start camera acquisition and recording together (MVS, 60 fps). The video is now streaming before any stimulus, and every frame is captured on AI3.
4. Press SPACE to begin the trials. ESC aborts cleanly.
5. Stop in this order: the PsychoPy script, then the camera, then the OneBox.
6. Export the OneBox SMA1 sync-bit edges to CSV (`time_s,value`), then run:
   `python analysis/align_session.py data/<session> --onebox onebox_barcode_edges.csv`

Without hardware, add `--simulate`. This runs PsychoPy only; nothing is recorded.

Other options:
- `--trials N` overrides `N_TRIALS`.
- `--no-wait` skips the SPACE prompt, for automated tests.

If the NI recording fails mid-session (for example, the USB cable is unplugged), the script prints an error and stops at the next trial instead of carrying on without a recording.

## Timing rules
- One event lasts about 1.03 s (20 + 20 + 32×30 ms). **The minimum time from one onset to the next must be longer than this**; the script checks it at start-up. For faster trial rates, reduce `BARCODE_BIT_MS` (for example 10 ms gives about 0.36 s). The decoder recognises each barcode by its 20 ms start pulse, so barcodes can follow each other closely.
- The DO start happens in software right after the flip, typically within a few ms. This delay doesn't affect accuracy: the analysis uses the measured AI loopback and photodiode times. `display_latency_ms` = photodiode onset − barcode onset.
- The camera marker lasts 100 ms (6 frames at 60 fps), so at least 5 frames always show it.

## Output (data/<session>/)
| File | Contents |
|---|---|
| `ni_ai.bin` | int16, interleaved channels; volts = count × `v_per_count` (0.25 mV) |
| `ni_ai.json` | Metadata: rate, channel order, `v_per_count`, input ranges |
| `psychopy_trials.csv` | Trial, condition, barcode, flip time |
| `trials_aligned.csv` | Analysis output; one row per trial |

Storage: 3 channels (AI0, AI1, AI3) at 10 kHz ≈ 216 MB per hour.
