#!/usr/bin/env python3
"""
Sync R0 SSB demod inputFrequencyOffset -> T1 SSB mod inputFrequencyOffset (with optional offset)
and align R1 device center frequency to R0 device center frequency.

Config options at top of file. Use --offset to add a fixed Hz offset to the shift applied to Tx.

Andrej S51BW 2025-09-14

rxtxsync.py - robust version

- Mirrors R0 SSB demodulator inputFrequencyOffset -> T1 SSB modulator inputFrequencyOffset
  (with an optional fixed offset).
- Aligns R1 device center frequency with R0 device center frequency.
- Handles LimeSDR / USRP and other device JSON shapes by discovering the right keys.
- If the SDRangel server is unreachable, retries every 10 seconds until it comes back.
"""

import requests
import argparse
import time
from datetime import datetime

# ---------------------------
# Defaults (tweak here if needed)
# ---------------------------
SDRANGEL_HOST = "localhost"
SDRANGEL_PORT = 8091

R0_DEVICE_SET = 0     # Rx device set index (R0)
R0_CHANNEL = 0        # Rx channel index (SSB demodulator)

R1_DEVICE_SET = 1     # Device set to align center frequency with R0

T1_DEVICE_SET = 1     # Tx device set (hosts SSB modulator)
T1_CHANNEL = 0        # Tx channel index (SSB modulator)

SYNC_DELAY = 0.3      # seconds between normal sync iterations
RECONNECT_DELAY = 10  # seconds to wait when server is unreachable

REQUEST_TIMEOUT = 5   # seconds for HTTP requests
# ---------------------------


def http_url(path):
    return f"http://{SDRANGEL_HOST}:{SDRANGEL_PORT}{path}"


def find_center_freq_path(dev_data):
    """
    Return (container_dict, key) where container_dict[key] == centerFrequency.
    Checks common keys first, then does a recursive search.
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

    # Generic recursive search for a 'centerFrequency' key in nested dicts
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
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def set_device_settings(device_set, dev_data):
    url = http_url(f"/sdrangel/deviceset/{device_set}/device/settings")
    r = requests.patch(url, json=dev_data, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def set_device_center_frequency_if_changed(device_set, new_center_freq, verbose=False):
    """
    Fetch device settings, discover where centerFrequency is stored, update it and PATCH
    only if the value changed.
    """
    dev_data = get_device_settings(device_set)
    container, key = find_center_freq_path(dev_data)
    if container is None:
        raise KeyError(f"Cannot find centerFrequency in device settings for deviceSet {device_set}")

    current = container.get(key)
    if current != new_center_freq:
        container[key] = new_center_freq
        set_device_settings(device_set, dev_data)
        if verbose:
            print(f"[{now_ts()}] Updated deviceSet {device_set} centerFrequency: {current} -> {new_center_freq}")
        return True
    return False


def get_channel_settings(device_set, channel):
    url = http_url(f"/sdrangel/deviceset/{device_set}/channel/{channel}/settings")
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def set_channel_settings(device_set, channel, channel_data):
    url = http_url(f"/sdrangel/deviceset/{device_set}/channel/{channel}/settings")
    r = requests.patch(url, json=channel_data, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def find_channel_settings_key(channel_json, role='demod', kind_hint='ssb'):
    """
    Find the key in channel JSON that corresponds to the settings we need.
    role: 'demod' or 'mod' (searches for keys ending with 'DemodSettings' or 'ModSettings')
    kind_hint: e.g. 'ssb' to prefer SSB-specific keys.
    Returns the exact key name (case preserved) or None.
    """
    role_part = role.lower()
    # Collect candidate keys
    candidates = []
    for k in channel_json.keys():
        lk = k.lower()
        if lk.endswith('settings') and role_part in lk:
            candidates.append(k)
    # Prefer those containing kind_hint
    if kind_hint:
        for k in candidates:
            if kind_hint.lower() in k.lower():
                return k
    if candidates:
        return candidates[0]
    # If nothing found, try any '*Settings' key (last resort)
    for k in channel_json.keys():
        if k.lower().endswith('settings'):
            return k
    return None


def get_rx_ssb_shift(device_set=R0_DEVICE_SET, channel=R0_CHANNEL):
    """Return the Rx SSB demodulator inputFrequencyOffset (Hz)."""
    data = get_channel_settings(device_set, channel)
    demod_key = find_channel_settings_key(data, role='demod', kind_hint='ssb')
    if demod_key is None:
        raise KeyError(f"Cannot find SSB demodulator settings in deviceSet {device_set} channel {channel}")
    settings = data[demod_key]
    if "inputFrequencyOffset" not in settings:
        raise KeyError("inputFrequencyOffset not present in demod settings")
    return settings["inputFrequencyOffset"]


def set_tx_ssb_shift_if_changed(shift_hz, device_set=T1_DEVICE_SET, channel=T1_CHANNEL, verbose=False):
    """
    Fetch full channel JSON for Tx, update SSB modulator 'inputFrequencyOffset' and PATCH if changed.
    """
    data = get_channel_settings(device_set, channel)
    mod_key = find_channel_settings_key(data, role='mod', kind_hint='ssb')
    if mod_key is None:
        raise KeyError(f"Cannot find SSB modulator settings in deviceSet {device_set} channel {channel}")
    settings = data[mod_key]
    current = settings.get("inputFrequencyOffset")
    if current != shift_hz:
        settings["inputFrequencyOffset"] = shift_hz
        # send whole object back
        set_channel_settings(device_set, channel, data)
        if verbose:
            print(f"[{now_ts()}] Updated T{device_set}:{channel} SSBMod inputFrequencyOffset: {current} -> {shift_hz}")
        return True
    return False


def now_ts():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def mirror_and_align_once(offset_hz=0, verbose=False):
    """
    Do one iteration: read Rx shift, set Tx shift (with offset), align R1 center freq to R0 center freq.
    """
    # get Rx shift
    shift = get_rx_ssb_shift(R0_DEVICE_SET, R0_CHANNEL)
    applied_shift = int(shift + offset_hz)

    # get R0 center frequency
    dev0 = get_device_settings(R0_DEVICE_SET)
    cont0, key0 = find_center_freq_path(dev0)
    if cont0 is None:
        raise KeyError(f"Cannot find centerFrequency for deviceSet {R0_DEVICE_SET}")
    r0_center = cont0.get(key0)

    # apply TX shift if changed
    tx_changed = set_tx_ssb_shift_if_changed(applied_shift, T1_DEVICE_SET, T1_CHANNEL, verbose=verbose)

    # align R1 center frequency if changed
    r1_changed = set_device_center_frequency_if_changed(R1_DEVICE_SET, r0_center, verbose=verbose)

    if verbose:
        ts = now_ts()
        print(f"[{ts}] Rx shift={shift} Hz | offset={offset_hz} Hz | applied={applied_shift} Hz | R0 center={r0_center} Hz")
        if tx_changed:
            print(f"[{ts}] ✅ Applied shift to T{T1_DEVICE_SET}:{T1_CHANNEL}")
        if r1_changed:
            print(f"[{ts}] ✅ Aligned R{R1_DEVICE_SET} center frequency to R0")


def main_loop(offset_hz=0, delay=SYNC_DELAY, once=False, verbose=False):
    """
    Main loop with reconnect handling:
    - On connection/timeout errors, sleep RECONNECT_DELAY seconds and retry indefinitely.
    - On other exceptions, print error and retry after RECONNECT_DELAY.
    """
    while True:
        try:
            mirror_and_align_once(offset_hz=offset_hz, verbose=verbose)
            if once:
                return
            time.sleep(delay)
        except (requests.ConnectionError, requests.Timeout) as e:
            # server likely down / unreachable
            print(f"[ERROR] SDRangel unreachable: {e}. Retrying in {RECONNECT_DELAY} s...")
            time.sleep(RECONNECT_DELAY)
        except requests.HTTPError as e:
            # unexpected HTTP response (400/500). Print error and retry after delay.
            try:
                status = e.response.status_code
                text = e.response.text
            except Exception:
                status = None
                text = None
            print(f"[ERROR] HTTP error: {status} - {text}. Retrying in {RECONNECT_DELAY} s...")
            time.sleep(RECONNECT_DELAY)
        except KeyboardInterrupt:
            print("Stopped by user")
            return
        except Exception as e:
            # generic catch-all: print and retry after delay
            print(f"[ERROR] Unexpected error: {e}. Retrying in {RECONNECT_DELAY} s...")
            time.sleep(RECONNECT_DELAY)

def parse_args_and_run():
    global SDRANGEL_HOST, SDRANGEL_PORT, SYNC_DELAY  # must come first

    p = argparse.ArgumentParser(description="Mirror R0 SSB shift -> T1 SSB and align R1 center frequency.")
    p.add_argument("--offset", type=int, default=0, help="Fixed Hz offset to add to Rx shift before applying to Tx.")
    p.add_argument("--host", type=str, default=SDRANGEL_HOST, help="SDRangel host (default: localhost).")
    p.add_argument("--port", type=int, default=SDRANGEL_PORT, help="SDRangel Reverse API port (default: 8091).")
    p.add_argument("--delay", type=float, default=SYNC_DELAY, help="Sync delay in seconds (default: 0.3).")
    p.add_argument("--once", action="store_true", help="Run one iteration and exit.")
    p.add_argument("--verbose", action="store_true", help="Verbose logging to stdout.")
    args = p.parse_args()

    SDRANGEL_HOST = args.host
    SDRANGEL_PORT = args.port
    SYNC_DELAY = args.delay

    if args.verbose:
        print(f"[{now_ts()}] Starting rxtxsync (host={SDRANGEL_HOST}, port={SDRANGEL_PORT})")

    main_loop(offset_hz=args.offset, delay=SYNC_DELAY, once=args.once, verbose=args.verbose)



if __name__ == "__main__":
    parse_args_and_run()
