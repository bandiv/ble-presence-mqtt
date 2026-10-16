"""Tests for iBeacon parsing and the presence rule. Run: python -m unittest"""
import unittest

from presence import Tracker, normalise_uuid, parse_ibeacon

UUID = '11111111222233334444555555555555'


def ibeacon(uuid=UUID, major=100, minor=7, tx=-59):
    return bytes([0x02, 0x15]) + bytes.fromhex(uuid) + major.to_bytes(2, 'big') + minor.to_bytes(2, 'big') + bytes([tx & 0xFF])


class ParseTest(unittest.TestCase):
    def test_ibeacon(self):
        self.assertEqual(parse_ibeacon(ibeacon()), (UUID, 100, 7))

    def test_major_minor_are_big_endian(self):
        self.assertEqual(parse_ibeacon(ibeacon(major=0x0064, minor=0x3039))[1:], (100, 12345))

    def test_not_ibeacon(self):
        self.assertIsNone(parse_ibeacon(bytes([0x10, 0x05, 1, 2, 3])))   # e.g. Apple "nearby" frames
        self.assertIsNone(parse_ibeacon(ibeacon()[:20]))

    def test_normalise_uuid(self):
        self.assertEqual(normalise_uuid('11111111-2222-3333-4444-555555555555'), UUID)


class TrackerTest(unittest.TestCase):
    def run_seq(self, t, events, until, step=1):
        """events: {time: rssi}; returns list of (time, new_state)."""
        out, now = [], 0
        while now <= until:
            if now in events:
                t.sighting(now, events[now])
            new = t.evaluate(now)
            if new:
                out.append((now, new))
            now += step
        return out

    def test_arrive_needs_two_strong(self):
        t = Tracker(0)
        self.assertEqual(self.run_seq(t, {5: -70, 10: -90, 20: -72}, 25), [(20, 'ON')])

    def test_weak_only_never_arrives(self):
        t = Tracker(0)
        ev = {s: -92 for s in range(0, 300, 10)}
        self.assertEqual(self.run_seq(t, ev, 300), [])

    def test_stays_in_while_seen_weakly(self):
        t = Tracker(0)
        ev = {1: -70, 3: -70}
        ev.update({s: -95 for s in range(10, 600, 30)})          # just outside the door
        self.assertEqual(self.run_seq(t, ev, 600), [(3, 'ON')])

    def test_away_after_silence(self):
        t = Tracker(0, away_after_s=120)
        self.assertEqual(self.run_seq(t, {1: -70, 3: -70, 50: -95}, 300), [(3, 'ON'), (170, 'OFF')])

    def test_restart_resumes_without_flapping(self):
        t = Tracker(0)
        t.resume('ON')
        self.assertEqual(self.run_seq(t, {s: -95 for s in range(0, 200, 20)}, 200), [])
        t2 = Tracker(0)
        t2.resume('ON')
        self.assertEqual(self.run_seq(t2, {}, 130), [(120, 'OFF')])   # away only after the full timeout

    def test_stats(self):
        t = Tracker(0)
        t.sighting(10, -70)
        t.sighting(20, -90)
        s = t.stats(30)
        self.assertEqual((s['rssi'], s['sightings_last_min'], s['strong_sightings_last_min'],
                          s['last_strong_s_ago'], s['last_seen_s_ago']), (-90, 2, 1, 20, 10))


if __name__ == '__main__':
    unittest.main()
