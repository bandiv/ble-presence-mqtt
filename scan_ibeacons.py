#!/usr/bin/env python3
"""List the iBeacons the host can hear (UUID / major / minor / RSSI) to find your phone's beacon.

Usage: sudo python3 scan_ibeacons.py [seconds]
"""
import sys

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

from presence import APPLE, parse_ibeacon

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()
adapter = dbus.Interface(bus.get_object('org.bluez', '/org/bluez/hci0'), 'org.bluez.Adapter1')
seen = {}                                 # (uuid, major, minor) -> [count, best rssi]
paths = {}


def handle(path, props):
    v = props.get('ManufacturerData', {}).get(dbus.UInt16(APPLE))
    key = parse_ibeacon(v) if v is not None else None
    if key:
        paths[path] = key
    key = key or paths.get(path)
    if key and 'RSSI' in props:
        s = seen.setdefault(key, [0, -127])
        s[0] += 1
        s[1] = max(s[1], int(props['RSSI']))


bus.add_signal_receiver(lambda i, c, inv, path=None: handle(str(path), c), signal_name='PropertiesChanged',
                        dbus_interface='org.freedesktop.DBus.Properties', arg0='org.bluez.Device1',
                        path_keyword='path')
bus.add_signal_receiver(lambda p, ifs: handle(str(p), ifs.get('org.bluez.Device1', {})),
                        signal_name='InterfacesAdded', dbus_interface='org.freedesktop.DBus.ObjectManager')
adapter.SetDiscoveryFilter({'Transport': 'le', 'DuplicateData': True})
adapter.StartDiscovery()


def done():
    if not seen:
        print('no iBeacons heard')
    for (uuid, major, minor), (n, best) in sorted(seen.items(), key=lambda kv: -kv[1][1]):
        u = f'{uuid[:8]}-{uuid[8:12]}-{uuid[12:16]}-{uuid[16:20]}-{uuid[20:]}'
        print(f'{u}  major {major:<5}  minor {minor:<5}  sightings {n:<4}  best RSSI {best}')
    loop.quit()


secs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
print(f'listening {secs} s ...')
GLib.timeout_add_seconds(secs, done)
loop = GLib.MainLoop()
loop.run()
