"""iBeacon parsing and the presence decision. No Bluetooth/MQTT imports, so it is unit-testable anywhere."""
from collections import deque

APPLE = 0x004C


def parse_ibeacon(data):
    """Apple manufacturer-data value (bytes after the 0x004C company id) -> (uuid_hex, major, minor) or None."""
    b = bytes(data)
    if len(b) < 22 or b[0] != 0x02 or b[1] != 0x15:
        return None
    return b[2:18].hex(), int.from_bytes(b[18:20], 'big'), int.from_bytes(b[20:22], 'big')


def normalise_uuid(uuid):
    return uuid.replace('-', '').lower()


class Tracker:
    """Presence of one beacon in one room, with hysteresis.

    in   = `arrive_sightings` sightings >= `rssi_min` within `arrive_window_s` (strong = clearly in the room)
    stay = while the beacon is seen at any strength (just outside the door is often as weak as further away)
    away = no sighting at any strength for `away_after_s`

    State is None until decided. After a restart, call resume() with the last published state.
    Times are monotonic seconds supplied by the caller.
    """

    def __init__(self, now, rssi_min=-83, arrive_sightings=2, arrive_window_s=30, away_after_s=120):
        self.rssi_min = rssi_min
        self.arrive_sightings = arrive_sightings
        self.arrive_window_s = arrive_window_s
        self.away_after_s = away_after_s
        self.state = None
        self.strong = deque(maxlen=200)
        self.all = deque(maxlen=200)       # (t, rssi)
        self.last_strong = now
        self.last_any = now                # a restart reports 'away' only after away_after_s

    def resume(self, state):
        if self.state is None and state in ('ON', 'OFF'):
            self.state = state

    def sighting(self, now, rssi):
        self.all.append((now, rssi))
        self.last_any = now
        if rssi >= self.rssi_min:
            self.strong.append(now)
            self.last_strong = now

    def evaluate(self, now):
        """Returns the new state if it changed, else None."""
        recent = sum(1 for t in self.strong if now - t <= self.arrive_window_s)
        new = self.state
        if self.state != 'ON' and recent >= self.arrive_sightings:
            new = 'ON'
        elif self.state != 'OFF' and now - self.last_any >= self.away_after_s:
            new = 'OFF'
        if new != self.state:
            self.state = new
            return new
        return None

    def stats(self, now):
        last = self.all[-1] if self.all else None
        minute = [r for t, r in self.all if now - t <= 60]
        return {
            'rssi': last[1] if last and now - last[0] <= 60 else None,
            'sightings_last_min': len(minute),
            'strong_sightings_last_min': sum(1 for r in minute if r >= self.rssi_min),
            'last_strong_s_ago': round(now - self.last_strong) if self.strong else None,
            'last_seen_s_ago': round(now - self.last_any) if self.all else None,
        }
