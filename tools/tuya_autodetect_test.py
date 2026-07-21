#!/usr/bin/env python3
"""Standalone tester for the Tuya EV charger local auto-detection.

Run this ON THE SAME MACHINE / NETWORK as Home Assistant (same subnet as the
charger). It reproduces exactly what the integration does to relocate a charger
by its device_id after its DHCP IP changes, plus extra diagnostics to explain
*why* auto-detection might fail.

Usage examples
--------------
    # List every Tuya device the machine can see (UDP broadcast listen):
    python3 tools/tuya_autodetect_test.py

    # Test the integration's "find my charger by device_id" logic:
    python3 tools/tuya_autodetect_test.py --device-id bfXXXXXXXXXXXXXXXXXXXX

    # Longer listen window (default 18s here, the integration uses ~6s):
    python3 tools/tuya_autodetect_test.py --scantime 30

    # Cross-subnet / broadcast-blocked networks: active scan of a range
    python3 tools/tuya_autodetect_test.py --force --network 192.168.1.0/24

Exit code is 0 when at least one device is found (or the requested device_id is
located), 1 otherwise — handy for scripting.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any


def _ensure_tinytuya_importable() -> None:
    """Import tinytuya, falling back to the integration's own .venv if needed."""
    try:
        import tinytuya  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    repo_root = Path(__file__).resolve().parent.parent
    for site in sorted(repo_root.glob(".venv/lib/python3.*/site-packages")):
        if (site / "tinytuya").is_dir():
            sys.path.insert(0, str(site))
            break
    try:
        import tinytuya  # noqa: F401
    except ModuleNotFoundError:
        sys.exit(
            "tinytuya is not installed for this interpreter.\n"
            "Install it (`pip install tinytuya`) or run with the interpreter "
            "that Home Assistant uses."
        )


def _local_network_hint() -> str:
    """Best-effort local IP/subnet, to spot 'HA is on another subnet' issues."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is actually sent; this just selects the default-route iface.
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = "unknown"
    finally:
        sock.close()
    return ip


def _tcp_check(host: str, port: int = 6668, timeout: float = 3.0) -> str:
    """Raw TCP reachability of the Tuya control port (no local_key needed)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return "OPEN (accepts connections)"
    except ConnectionRefusedError:
        return "REFUSED — port busy (single local connection already held) or closed"
    except socket.timeout:
        return "TIMEOUT — filtered by a firewall or host down"
    except OSError as err:
        return f"{type(err).__name__}: {err}"
    finally:
        sock.close()


def _fmt_device(dev_id: str, info: dict[str, Any]) -> str:
    ip = str(info.get("ip", "") or "")
    tcp = _tcp_check(ip) if ip else "no ip"
    return (
        f"  device_id (gwId): {dev_id}\n"
        f"      ip:      {info.get('ip', '?')}\n"
        f"      mac:     {info.get('mac', '?')}\n"
        f"      version: {info.get('version', '?')}\n"
        f"      name:    {info.get('name', '')}\n"
        f"      key set: {'yes' if info.get('key') else 'no (not in devices.json)'}\n"
        f"      port 6668: {tcp}"
    )


def _scan(scantime: int, wantids: list[str] | None, forcescan) -> dict[str, dict]:
    """Same call the integration uses: byID=True so keys are the real gwId."""
    from tinytuya import scanner

    devices = scanner.devices(
        verbose=False,
        scantime=scantime,
        color=False,
        poll=False,
        byID=True,
        wantids=wantids,
        forcescan=forcescan,
    )
    return {
        dev_id: info
        for dev_id, info in devices.items()
        if isinstance(info, dict) and info.get("ip")
    }


def _probe_voltage(dev_id: str, host: str, local_key: str, version: str) -> tuple[bool, str]:
    """Read status from a device with the given local_key; extract grid voltage.

    Returns (responded, detail). Only the real charger decrypts the reply with
    the correct local_key, so a successful read confirms identity — exactly what
    the integration does before adopting a new IP.
    """
    import tinytuya

    try:
        device = tinytuya.Device(dev_id=dev_id, address=host, local_key=local_key, version=version)
        device.set_socketTimeout(5)
        payload = device.status()
    except Exception as err:  # noqa: BLE001
        return False, f"error: {type(err).__name__}: {err}"

    if not isinstance(payload, dict) or "Error" in payload or not isinstance(payload.get("dps"), dict):
        return False, f"no valid response ({payload})"

    dps = payload["dps"]
    voltage = None
    metrics_raw = dps.get("102")
    if isinstance(metrics_raw, str):
        try:
            metrics = json.loads(metrics_raw)
            l1 = metrics.get("L1")
            if isinstance(l1, list) and l1:
                voltage = float(l1[0]) / 10.0
        except (ValueError, TypeError):
            pass
    if voltage is not None:
        return True, f"grid voltage L1 = {voltage:.1f} V (dps keys: {sorted(dps)})"
    return True, f"responded, dps keys: {sorted(dps)}"


def _probe_devices(devices: dict[str, dict], local_key: str, protocol: str, override_id: str | None) -> None:
    print("=== Read-verification probe (integration's confirmation step) ===")
    print("Trying each device with the provided local_key...\n")
    any_ok = False
    for dev_id, info in devices.items():
        probe_id = override_id or dev_id
        host = info.get("ip", "")
        version = str(info.get("version") or protocol)
        ok, detail = _probe_voltage(probe_id, host, local_key, version)
        flag = "OK  " if ok else "FAIL"
        any_ok = any_ok or ok
        print(f"  [{flag}] {host} (gwId {dev_id}, v{version}) -> {detail}")
    print()
    if any_ok:
        print("=> The charger answered a live read. Auto-detection via read-verification WILL work.\n")
    else:
        print("=> No device answered a live read. Possible causes:")
        print("   * 'Connection refused' / Err 901: a Tuya device allows only ONE local")
        print("     connection at a time on port 6668. If Home Assistant is running and")
        print("     already connected to the charger, this external probe is refused.")
        print("     Run this on the HA host with HA stopped, or just rely on the UDP scan")
        print("     result above. Inside the integration the probe uses HA's own")
        print("     connection, so it is not affected.")
        print("   * Otherwise: wrong local_key or protocol version.\n")


def _cross_check_config_entries(path: str, devices: dict[str, dict]) -> None:
    """Compare stored tuya_ev_charger config against the live scan."""
    print("=== Cross-check with Home Assistant config entries ===")
    try:
        with open(path, encoding="utf-8") as handle:
            store = json.load(handle)
    except (OSError, ValueError) as err:
        print(f"  Could not read {path}: {err}\n")
        return

    entries = [
        entry
        for entry in store.get("data", {}).get("entries", [])
        if entry.get("domain") == "tuya_ev_charger"
    ]
    if not entries:
        print("  No 'tuya_ev_charger' config entry found in this file.\n")
        return

    live_ids = set(devices)
    for entry in entries:
        data = entry.get("data", {})
        stored_id = str(data.get("device_id", ""))
        stored_host = data.get("host", "")
        stored_mac = data.get("mac", "")
        print(f"  entry '{entry.get('title', '?')}':")
        print(f"      stored device_id: {stored_id}")
        print(f"      stored host:      {stored_host}")
        print(f"      stored mac:       {stored_mac or '(none)'}")

        if stored_id in live_ids:
            live_ip = devices[stored_id].get("ip")
            verdict = "MATCH — rediscovery will work"
            if live_ip and live_ip != stored_host:
                verdict += f" (IP changed {stored_host} -> {live_ip}, will be updated)"
            print(f"      => {verdict}")
        elif "." in stored_id and stored_id.count(".") == 3:
            print("      => PROBLEM: device_id looks like an IP address, not a gwId.")
            print("         This entry was created by the old buggy scan. Re-add the")
            print("         charger (delete + add) so the real gwId is stored, or edit")
            print("         core.config_entries to set device_id to the gwId above.")
        else:
            print("      => PROBLEM: stored device_id is not among the broadcasting")
            print("         devices. Auto-detection cannot match it. Compare with the")
            print("         gwId(s) listed above.")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device-id", help="Locate this specific charger (gwId) like the integration does.")
    parser.add_argument(
        "--local-key",
        help="Probe each discovered device with this local_key and read its grid "
        "voltage — reproduces the integration's read-verification step.",
    )
    parser.add_argument("--protocol", default="3.5", help="Protocol version for the probe (default 3.5).")
    parser.add_argument("--scantime", type=int, default=18, help="UDP listen window in seconds (default 18).")
    parser.add_argument("--force", action="store_true", help="Active scan (unicast) instead of relying on UDP broadcast.")
    parser.add_argument("--network", help="CIDR to force-scan, e.g. 192.168.1.0/24 (implies --force).")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON of discovered devices.")
    parser.add_argument(
        "--config-entries",
        help="Path to Home Assistant's .storage/core.config_entries, to cross-check "
        "the stored device_id/host/mac against what is actually on the network.",
    )
    args = parser.parse_args()

    _ensure_tinytuya_importable()
    import tinytuya

    forcescan: Any = False
    if args.network:
        forcescan = [args.network]
    elif args.force:
        forcescan = True

    print("=== Tuya EV charger auto-detection test ===")
    print(f"tinytuya version : {getattr(tinytuya, 'version', getattr(tinytuya, '__version__', '?'))}")
    print(f"python           : {sys.version.split()[0]} ({sys.executable})")
    print(f"this host IP     : {_local_network_hint()}")
    print(f"scan mode        : {'forcescan ' + str(forcescan) if forcescan else 'UDP broadcast listen'}")
    print(f"scan window      : {args.scantime}s")
    if args.device_id:
        print(f"looking for      : {args.device_id}")
    print("Listening for Tuya broadcasts (this can take the full window)...\n")

    wantids = [args.device_id] if args.device_id else None
    try:
        devices = _scan(args.scantime, wantids, forcescan)
    except Exception as err:  # noqa: BLE001
        print(f"!! scan raised: {type(err).__name__}: {err}")
        return 1

    if args.json:
        print(json.dumps(devices, indent=2, default=str))

    if not devices:
        print("No Tuya devices found.\n")
        print("Likely causes:")
        print("  * This machine is on a different subnet/VLAN than the charger")
        print("    (UDP broadcast does not cross subnets). Try: --force --network <charger-subnet>/24")
        print("  * A firewall blocks UDP ports 6666/6667")
        print("  * The charger is unplugged / not yet on the network")
        print("  * Client isolation (guest Wi-Fi) is enabled on the AP")
        print("  * The listen window was too short — retry with --scantime 30\n")
        if args.config_entries:
            _cross_check_config_entries(args.config_entries, devices)
        return 1

    print(f"Found {len(devices)} Tuya device(s):\n")
    for dev_id, info in devices.items():
        print(_fmt_device(dev_id, info))
        print()

    if args.local_key:
        _probe_devices(devices, args.local_key, args.protocol, args.device_id)

    if args.config_entries:
        _cross_check_config_entries(args.config_entries, devices)

    if args.device_id:
        match = devices.get(args.device_id)
        if match:
            print(f"OK: charger {args.device_id} located at {match.get('ip')} "
                  f"(mac {match.get('mac')}). Auto-detection WOULD work.")
            return 0
        print(f"FAIL: device_id {args.device_id} was NOT among the broadcasts.")
        print("  * Check the value stored in the config entry — an older buggy scan")
        print("    could have saved the IP into the device_id field instead of the gwId.")
        print("  * Compare it to the gwId values listed above.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
