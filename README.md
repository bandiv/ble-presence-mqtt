# ble-presence-mqtt

**Room presence for Home Assistant from your phone's BLE beacon**, using a Raspberry Pi (or any Linux box with BlueZ) in the room.

The Home Assistant companion app can broadcast an **iBeacon** (Settings → Companion app → Manage sensors → *BLE transmitter*). This service listens for it and publishes an occupancy sensor such as `binary_sensor.office_presence_my_phone_in_office` to MQTT, with Home Assistant discovery. It's meant as a reliable "is this person in or around this room / outbuilding" signal for heating, lights and similar automations.

```
phone (companion app iBeacon) ──BLE── blepresence (Pi, BlueZ) ──MQTT── broker ── Home Assistant (discovery)
```

## Why this works when the obvious approach doesn't

- **Phones rotate their Bluetooth address** every few minutes, so tracking by MAC address fails. This service matches the **iBeacon payload** (UUID + major + minor) and ignores the address.
- **RSSI alone can't tell "just outside the door" from "further away".** In our calibration, just outside the door read −88…−98 dBm, the same range as the garden. So the rule has **hysteresis**:
  - **Arrive:** 2 sightings ≥ `rssi_min` (−83 dBm) within 30 s, which means clearly in the room. Walking past outside doesn't trigger it.
  - **Stay:** you stay "in" as long as the beacon is seen at **any** strength. Stepping outside the door or making a coffee doesn't flip the state.
  - **Away:** no sighting at all for `away_after_s` (120 s). Somewhere out of range, such as the main house, reads as away.
- **Restart-safe:** on start the service resumes its last retained MQTT state, so a restart never produces a false "away".
- **Shares the radio nicely.** BlueZ merges discovery filters from all clients, so this service always opens its own discovery session. Another BlueZ client with a narrow filter (e.g. [sbm70-mqtt](https://github.com/bandiv/sbm70-mqtt)) can't hide the beacons from it.

Put the delay that means "away long enough to act" in your automations (e.g. away for 10 min → heating off). The sensor itself only says in/out.

## Calibration example

This is from one Raspberry Pi 3 in a small detached office, with the scan duty at 25% and a phone advertising in the app's default *low power* mode (~1 Hz):

| Phone location | Sightings | RSSI |
|---|---|---|
| At the desk / around the room | one every ~4 s (raw); ~6/min via D-Bus | −62 … −80 |
| Just outside the door | ~5/min | −88 … −98 |
| Garden | one every 20–70 s, sometimes none for minutes | −85 … −99 |
| Main house (~35 m) | none | – |

Your building will be different. Calibrate by walking around with `scan_ibeacons.py` or `journalctl -u blepresence -f` running, then adjust `thresholds` in the config.

## Install

Tested on a Raspberry Pi 3 B with Raspberry Pi OS (Debian 13 "Trixie"), BlueZ 5.82 and Python 3.13.

```sh
git clone https://github.com/bandiv/ble-presence-mqtt && cd ble-presence-mqtt
sudo ./install.sh                                    # apt packages, /opt/blepresence, systemd unit
sudo python3 /opt/blepresence/scan_ibeacons.py 30    # find your phone's beacon (UUID / major / minor)
sudoedit /etc/blepresence/config.json                # beacon(s), room name, MQTT broker
sudo systemctl start blepresence
journalctl -u blepresence -f
```

Two things to know about finding your beacon:

- **Use the UUID the scan shows, not the one in Home Assistant.** The `id` attribute of the companion app's *BLE transmitter* sensor can be stale. In our case it showed a different UUID from the one actually broadcast, while major/minor matched.
- **If you reset the transmitter's UUID in the app, update the config.**

## Configuration

`/etc/blepresence/config.json` (see [`config.example.json`](config.example.json)):

| Key | Meaning |
|---|---|
| `room` | used in entity names: "*My phone* in *office*" |
| `beacons[]` | `id` (topic/entity id), `name`, `uuid`, `major`, `minor`; several phones are fine |
| `thresholds` | `rssi_min` (arrive), `arrive_sightings`, `arrive_window_s`, `away_after_s` |
| `mqtt` | `host`, `port`, `username`, `password`, `client_id`, `base_topic` (default `ble_presence`), `discovery_prefix` |
| `adapter` | default `hci0` |

## What Home Assistant gets

For each beacon, under one device "*Room* presence":

- `binary_sensor.<room>_presence_<name>_in_<room>`: occupancy. Its attributes are `rssi`, `sightings_last_min`, `strong_sightings_last_min`, `last_strong_s_ago`, `last_seen_s_ago` and `changed_at`.
- `sensor.<room>_presence_<name>_<room>_rssi`: diagnostic signal strength, updated every 30 s.

Both become **unavailable** when the service is down (MQTT last will). Make your automations treat *unavailable* as "unknown", not "away".

MQTT topics: `<base>/status` (online/offline), `<base>/<id>/state` (`ON`/`OFF`, retained), `<base>/<id>/attributes` (JSON, retained).

## Tips

- **Raspberry Pi Wi-Fi and Bluetooth share one chip** (Pi 3/4/Zero W). Continuous discovery at BlueZ's default 100% duty cycle raised Wi-Fi ping on a Pi 3 from ~11 ms to ~62 ms and dropped SSH sessions. `install.sh` therefore sets the discovery scan to 25% (`ScanIntervalDiscovery=96`, `ScanWindowDiscovery=24` in `/etc/bluetooth/main.conf`), which brought ping back to ~14–20 ms with no loss of detection. Set `LOW_SCAN_DUTY=0` to skip this.
- **BlueZ only emits an update when the RSSI changes.** That's why D-Bus shows fewer sightings than `btmon`. That's fine for presence, but don't make `away_after_s` too short.
- **More sightings per second:** switch the app's *Advertise mode* to *Balanced* (~4 Hz). This speeds up arrival and tolerates shielding (phone in a pocket) better, at a small battery cost. Raising the *transmit power* increases range, so the "outside" zone grows too.
- **btmon quirks when debugging:**
  - btmon prints iBeacon major/minor byte-swapped (major 100 / minor 12345 shows as `Version: 25600.14640`) and the UUID byte-reversed.
  - With `-T` it shortens long event lines to `LE Meta...`.
  - Don't pipe btmon into `mawk` for live logging; it buffers and can stall. Write to a file and analyse afterwards.
- **Read-only root (overlayroot):** install into the lower layer (e.g. `overlayroot-chroot`), or the service is gone after a reboot.
- **Privacy:** the beacon ID is static, so any BLE scanner nearby can recognise your phone by it. That's inherent to the companion app's transmitter, not to this service. Switch the transmitter off when you don't need it.

## Tests

```sh
python -m unittest      # iBeacon parsing + presence rule, no Bluetooth needed
```

## License

MIT
