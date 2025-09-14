# sdrangel-rxtx-sync
Script to synchronize Rx/Tx frequency shifts and center frequencies in SDRangel
=======
# SDRangel Rx/Tx Sync

A Python script to synchronize **SSB demodulator frequency shifts** (Rx) to **SSB modulator frequency shifts** (Tx) and align the **center frequencies** of SDR devices in [SDRangel](https://github.com/f4exb/sdrangel).

Supports **LimeSDR**, **USRP B200**, and other SDRangel-compatible devices.

---

## Features

- Mirrors **Rx SSB demodulator frequency shift** to Tx SSB modulator.
- Aligns **R1 device center frequency** with R0 device center frequency.
- Supports **LimeSDR** and **USRP** device JSON structures.
- Optional **fixed frequency shift offset** for fine adjustments.
- Continuous sync loop with configurable delay (default: 300 ms).
- Verbose logging with timestamps.

---

## Requirements

- Python 3.x
- `requests` library

### Install dependencies
```bash
pip3 install requests
