#!/usr/bin/env python3
"""Room presence from BLE iBeacons (e.g. the Home Assistant companion app's
BLE transmitter) -> MQTT with Home Assistant discovery.

Phones rotate their Bluetooth address, so beacons are matched on the iBeacon
payload (UUID + major + minor), never on the address. See README.

Usage: blepresence.py [/etc/blepresence/config.json]
"""
import json
import logging
import sys
import time
from datetime import datetime, timezone

import dbus
import dbus.mainloop.glib
import paho.mqtt.client as mqtt
from gi.repository import GLib

from presence import APPLE, Tracker, normalise_uuid, parse_ibeacon

EVAL_EVERY_S = 2
ATTR_EVERY_S = 30

log = logging.getLogger('blepresence')


class Presence:
    def __init__(self, cfg):
        self.cfg = cfg
        mq = cfg.get('mqtt', {})
        self.base = mq.get('base_topic', 'ble_presence')
        self.prefix = mq.get('discovery_prefix', 'homeassistant')
        self.room = cfg.get('room', 'office')
        self.device_id = cfg.get('device_id', f'ble_presence_{self.room}')
        th = cfg.get('thresholds', {})
        now = time.monotonic()
        self.beacons = {}                 # id -> dict(name, key, tracker, changed_at)
        for b in cfg['beacons']:
            self.beacons[b['id']] = {
                'name': b.get('name', b['id']),
                'key': (normalise_uuid(b['uuid']), int(b['major']), int(b['minor'])),
                'tracker': Tracker(now, **th),
                'changed_at': None,
            }
        self.by_key = {v['key']: k for k, v in self.beacons.items()}
        self.paths = {}                   # D-Bus device path -> beacon id (addresses rotate)

        self.bus = dbus.SystemBus()
        adapter_path = f"/org/bluez/{cfg.get('adapter', 'hci0')}"
        self.adapter = dbus.Interface(self.bus.get_object('org.bluez', adapter_path), 'org.bluez.Adapter1')
        self.bus.add_signal_receiver(self.on_props, signal_name='PropertiesChanged',
                                     dbus_interface='org.freedesktop.DBus.Properties',
                                     arg0='org.bluez.Device1', path_keyword='path')
        self.bus.add_signal_receiver(self.on_added, signal_name='InterfacesAdded',
                                     dbus_interface='org.freedesktop.DBus.ObjectManager')
        self.bus.add_signal_receiver(self.on_removed, signal_name='InterfacesRemoved',
                                     dbus_interface='org.freedesktop.DBus.ObjectManager')

        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=mq.get('client_id', 'blepresence'))
        if mq.get('username'):
            self.mqtt.username_pw_set(mq['username'], mq.get('password'))
        self.mqtt.will_set(f'{self.base}/status', 'offline', qos=1, retain=True)
        self.mqtt.on_connect = self.on_mqtt_connect
        self.mqtt.on_message = self.on_mqtt_message
        self.mqtt.reconnect_delay_set(1, 60)
        self.mqtt.connect_async(mq.get('host', 'localhost'), int(mq.get('port', 1883)), keepalive=60)
        self.mqtt.loop_start()

    # ---------- MQTT ----------
    def on_mqtt_connect(self, client, userdata, flags, reason, props):
        log.info('MQTT connected (%s)', reason)
        client.publish(f'{self.base}/status', 'online', qos=1, retain=True)
        dev = {'identifiers': [self.device_id], 'name': f'{self.room.capitalize()} presence',
               'model': 'BLE iBeacon presence'}
        avail = {'availability_topic': f'{self.base}/status'}
        for bid, b in self.beacons.items():
            st = f'{self.base}/{bid}'
            uid = f'{self.device_id}_{bid}'
            cfg = {'name': f"{b['name']} in {self.room}", 'unique_id': uid, 'device_class': 'occupancy',
                   'state_topic': f'{st}/state', 'json_attributes_topic': f'{st}/attributes', 'device': dev, **avail}
            client.publish(f'{self.prefix}/binary_sensor/{uid}/config', json.dumps(cfg), qos=1, retain=True)
            cfg = {'name': f"{b['name']} {self.room} RSSI", 'unique_id': f'{uid}_rssi',
                   'device_class': 'signal_strength', 'unit_of_measurement': 'dBm', 'state_class': 'measurement',
                   'entity_category': 'diagnostic', 'state_topic': f'{st}/attributes',
                   'value_template': '{{ value_json.rssi }}', 'device': dev, **avail}
            client.publish(f'{self.prefix}/sensor/{uid}_rssi/config', json.dumps(cfg), qos=1, retain=True)
            if b['tracker'].state:
                client.publish(f'{st}/state', b['tracker'].state, qos=1, retain=True)
            else:
                client.subscribe(f'{st}/state', qos=1)   # adopt the retained state after a restart

    def on_mqtt_message(self, client, userdata, msg):
        bid = msg.topic[len(self.base) + 1:].split('/')[0]
        b = self.beacons.get(bid)
        if b and b['tracker'].state is None:
            b['tracker'].resume(msg.payload.decode())
            log.info('%s: resumed retained state %s', bid, b['tracker'].state)
        client.unsubscribe(msg.topic)

    def publish_attrs(self, bid, b):
        attrs = b['tracker'].stats(time.monotonic())
        attrs['changed_at'] = b['changed_at']
        self.mqtt.publish(f'{self.base}/{bid}/attributes', json.dumps(attrs), qos=0, retain=True)

    # ---------- BlueZ ----------
    def start_discovery(self):
        # Always open our own discovery session: BlueZ merges the filters of all clients, and the
        # adapter's 'Discovering' flag is shared, so another client's narrow filter would hide beacons.
        try:
            self.adapter.SetDiscoveryFilter({'Transport': 'le', 'DuplicateData': True})
            self.adapter.StartDiscovery()
            log.info('discovery started')
        except dbus.DBusException as e:
            if e.get_dbus_name() != 'org.bluez.Error.InProgress':   # = our session is already running
                log.warning('start discovery: %s', e.get_dbus_message())

    def match(self, props):
        v = props.get('ManufacturerData', {}).get(dbus.UInt16(APPLE))
        return self.by_key.get(parse_ibeacon(v)) if v is not None else None

    def seed(self):
        om = dbus.Interface(self.bus.get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager')
        for path, ifs in om.GetManagedObjects().items():
            bid = self.match(ifs.get('org.bluez.Device1', {}))
            if bid:
                self.paths[str(path)] = bid

    def on_added(self, path, ifs):
        if 'org.bluez.Device1' in ifs:
            self.sighting(str(path), ifs['org.bluez.Device1'])

    def on_removed(self, path, ifs):
        self.paths.pop(str(path), None)

    def on_props(self, iface, changed, invalidated, path=None):
        self.sighting(str(path), changed)

    def sighting(self, path, props):
        bid = self.match(props)
        if bid:
            self.paths[path] = bid
        else:
            bid = self.paths.get(path)
        if bid and 'RSSI' in props:
            self.beacons[bid]['tracker'].sighting(time.monotonic(), int(props['RSSI']))

    # ---------- timers ----------
    def evaluate(self):
        now = time.monotonic()
        for bid, b in self.beacons.items():
            t = b['tracker']
            new = t.evaluate(now)
            if new:
                s = t.stats(now)
                log.info('%s: -> %s (last strong %ss ago, last seen %ss ago)', bid, new,
                         s['last_strong_s_ago'], s['last_seen_s_ago'])
                b['changed_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
                self.mqtt.publish(f'{self.base}/{bid}/state', new, qos=1, retain=True)
                self.publish_attrs(bid, b)
        return True

    def attrs_tick(self):
        for bid, b in self.beacons.items():
            self.publish_attrs(bid, b)
        return True

    def watchdog(self):
        self.start_discovery()            # recovers from bluetoothd restarts / adapter resets
        return True


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(levelname)s %(message)s')
    path = sys.argv[1] if len(sys.argv) > 1 else '/etc/blepresence/config.json'
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    p = Presence(cfg)
    p.seed()
    p.start_discovery()
    GLib.timeout_add_seconds(EVAL_EVERY_S, p.evaluate)
    GLib.timeout_add_seconds(ATTR_EVERY_S, p.attrs_tick)
    GLib.timeout_add_seconds(60, p.watchdog)
    GLib.MainLoop().run()


if __name__ == '__main__':
    main()
