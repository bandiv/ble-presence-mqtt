#!/bin/bash
# Install blepresence on Raspberry Pi OS / Debian. Run from the repo directory: sudo ./install.sh
set -e
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
cd "$(dirname "$0")"

apt-get install -y bluez python3-dbus python3-gi python3-paho-mqtt

install -d /opt/blepresence /etc/blepresence
install -m 0755 blepresence.py /opt/blepresence/blepresence.py
install -m 0755 scan_ibeacons.py /opt/blepresence/scan_ibeacons.py
install -m 0644 presence.py /opt/blepresence/presence.py
install -m 0644 blepresence.service /etc/systemd/system/blepresence.service
[ -f /etc/blepresence/config.json ] || install -m 0600 config.example.json /etc/blepresence/config.json

# Optional: lower the LE scan duty during discovery (units of 0.625 ms): 15 ms every 60 ms = 25 %.
# Matters on boards where Wi-Fi and Bluetooth share one chip (Raspberry Pi 3/4/Zero W).
if [ "${LOW_SCAN_DUTY:-1}" = 1 ]; then
  sed -i 's/^#\?ScanIntervalDiscovery=.*/ScanIntervalDiscovery=96/; s/^#\?ScanWindowDiscovery=.*/ScanWindowDiscovery=24/' /etc/bluetooth/main.conf
fi

rfkill unblock bluetooth 2>/dev/null || true
systemctl enable --now bluetooth
systemctl restart bluetooth
systemctl daemon-reload
systemctl enable blepresence

echo
echo "Installed. Next:"
echo "  1. find your beacon:  sudo python3 /opt/blepresence/scan_ibeacons.py 30"
echo "  2. put its uuid/major/minor and your MQTT settings into /etc/blepresence/config.json"
echo "  3. sudo systemctl start blepresence && journalctl -u blepresence -f"
