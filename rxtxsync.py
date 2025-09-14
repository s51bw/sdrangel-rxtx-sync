#!/usr/bin/env python3
"""
Sync R0 SSB demod inputFrequencyOffset -> T1 SSB mod inputFrequencyOffset (with optional offset)
and align R1 device center frequency to R0 device center frequency.

Config options at top of file. Use --offset to add a fixed Hz offset to the shift applied to Tx.

Andrej S51BW 2025-09-14

"""

import requests
import time
import argparse
from datetime import datetime

# ---------------------------
# Config (change defaults here)
# ---------------------------
SDRANGEL_HOST = "localhost"
SDRANGEL_PORT = 8091

# Device set / channel mapping (defaults used by the previous conversation)
R0_DEVICE_SET = 0     # R0 device set index
R0_CHANNEL = 0        # R0 channel index (SSB demodulator)

R1_DEVICE_SET = 1     # R1 device set index (device to align center frequency to R0)

T1_DEVICE_SET = 1     # T1 device set index (Tx channel that hosts SSB modulator)
T1_CHANNEL = 0        # T1 channel index (SSB modulator)

SYNC_DELAY = 0.3      # seconds between sync iterations (300 ms)
# ---------------------------

def http_url(path):
    return f"http://{SDRANGEL_HOST}:{SDRANGEL_PORT}{path}"


def find_center_freq_path(dev_data):
    """
    Return tuple (container_dict, key) where container_dict[key] == centerFrequency.
    """
    candidates = [
        ("limeSdrInputSettings", "centerFrequency"),
        ("limeSdrOutputSettings", "centerFrequency"),
        ("usrpInputSettings", "centerFrequency"),
        ("usrpOutputSettings", "centerFrequency"),
        ("centerFrequency", None),  # top-level numeric centerFrequency
    ]

    for k, subk in candidates:
        if subk is None:
            if k in dev_data:
                return dev_data, k
        else:
            if k in dev_data and isinstance(dev_data[k], dict) and subk in dev_data[k]:
                return dev_data[k], subk

    # Recursive search
    stack = [dev_data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "centerFrequency" in node:
                return node, "centerFrequency"
            for v in node.values():
                if isinstance(v, dict):
                    stack.append(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            stack.append(item)
    return None, None


def get_device_settings(device_set):
    url = http_url(f"/sdrangel/deviceset/{device_set}/device/settings")
    r = requests.get(url)
    r.raise_for_status()
    return r.json()


def get_device_center_frequency(device_set):
    dev_data = get_device_settings(device_set)
    container, key = find_center_freq_path(dev_data)
    if container is None:
        raise KeyError(f"Cannot find centerFrequency in device settings for deviceSet {device_set}")
    return dev_data, container[key]


def set_device_center_frequency_if_changed(device_set, new_center_freq):
    url = http_url(f"/sdrangel/deviceset/{device_set}/device/settings")
    r = requests.get(url)
    r.raise_for_status()
    dev_data = r.json()

    container, key = find_center_freq_path(dev_data)
    if container is None:
        raise KeyError(f"Cannot find centerFrequency in device settings for deviceSet {device_set}")

    current = container.get(key)
    if current != new_center_freq:
        container[key] = new_center_freq
        r2 = requests.patch(url, json=dev_data)
        r2.raise_for_status()
        return True
    return False


def get_rx_ssb_shift(device_set=R0_DEVICE_SET, channel=R0_CHANNEL):
    url = http_url(f"/sdrangel/deviceset/{device_set}/channel/{channel}/settings")
    r = requests.get(url)
    r.raise_for_status()
    data = r.json()
    if "SSBDemodSettings" not in data:
        raise KeyError(f"Rx channel is not an SSB demodulator in deviceSet {device_set} channel {channel}")
    return data["SSBDemodSettings"]["inputFrequencyOffset"]


def set_tx_ssb_shift_if_changed(shift_hz, device_set=T1_DEVICE_SET, channel=T1_CHANNEL):
    url = http_url(f"/sdrangel/deviceset/{device_set}/channel/{channel}/settings")
    r = requests.get(url)
    r.raise_for_status()
    tx_data = r.json()
    if "SSBModSettings" not in tx_data:
        raise KeyError(f"Tx channel is not an SSB modulator in deviceSet {device_set} channel {channel}")
    current = tx_data["SSBModSettings"].get("inputFrequencyOffset")
    if current != shift_hz:
        tx_data["SSBModSettings"]["inputFrequencyOffset"] = shift_hz
        r2 = requests.patch(url, json=tx_data)
        r2.raise_for_status()
        return True
    return False


def mirror_and_align(offset_hz=0, verbose=False):
    shift = get_rx_ssb_shift()
    _, r0_center = get_device_center_frequency(R0_DEVICE_SET)

    applied_shift = int(shift + offset_hz)

    tx_changed = set_tx_ssb_shift_if_changed(applied_shift)
    r1_changed = set_device_center_frequency_if_changed(R1_DEVICE_SET, r0_center)

    if verbose:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{ts}] Rx shift={shift} Hz | offset={offset_hz} Hz | applied={applied_shift} Hz | R0 center={r0_center} Hz")
        if tx_changed:
            print(f"[{ts}] ✅ Applied shift to T1 SSB modulator")
        if r1_changed:
            print(f"[{ts}] ✅ Aligned R1 center frequency to R0")


def parse_args():
    p = argparse.ArgumentParser(
        description="Mirror Rx (R0) SSB shift to Tx (T1) SSB and align R1 center frequency to R0."
    )
    p.add_argument("--offset", type=int, default=0, help="Fixed frequency shift offset in Hz (default: 0).")
    p.add_argument("--host", type=str, default=SDRANGEL_HOST, help="SDRangel host (default: localhost).")
    p.add_argument("--port", type=int, default=SDRANGEL_PORT, help="SDRangel Reverse API port (default: 8091).")
    p.add_argument("--delay", type=float, default=SYNC_DELAY, help="Sync delay in seconds (default: 0.3).")
    p.add_argument("--once", action="store_true", help="Run one iteration and exit.")
    p.add_argument("--verbose", action="store_true", help="Enable verbose logging to stdout.")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    SDRANGEL_HOST = args.host
    SDRANGEL_PORT = args.port
    SYNC_DELAY = args.delay
    offset_hz = args.offset

    try:
        if args.once:
            mirror_and_align(offset_hz=offset_hz, verbose=args.verbose)
        else:
            while True:
                try:
                    mirror_and_align(offset_hz=offset_hz, verbose=args.verbose)
                except requests.exceptions.RequestException as e:
                    print(f"HTTP error: {e}")
                except Exception as e:
                    print(f"Other error: {e}")
                time.sleep(SYNC_DELAY)
    except KeyboardInterrupt:
        print("Stopped by user")
