#!/bin/sh
# Sets QSODesk up on a Raspberry Pi so it starts at boot.
# Run from the QSOdeck folder:   sh tools/install-pi.sh
# Safe to run again after an update. NOTE: not yet tested on a real Pi.
set -e
cd "$(dirname "$0")/.."
DIR="$(pwd)"
echo "Installing what QSODesk needs (Hamlib for radio control, ALSA for audio)..."
sudo apt-get update -qq
sudo apt-get install -y git libhamlib-utils alsa-utils
sudo usermod -aG dialout,audio "$USER"
echo "Setting QSODesk to start at boot..."
sudo tee /etc/systemd/system/qsodesk.service >/dev/null <<UNIT
[Unit]
Description=QSODesk
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/run.py
Restart=always
# lets QSODesk answer on port 80, so the address needs no :8073
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable qsodesk
sudo systemctl restart qsodesk
echo
echo "Done. On any device on this network, open:  http://qsodesk.local"
echo "(or http://$(hostname -I | cut -d' ' -f1):8073 if that name does not work)"
echo "Reboot once so the radio and sound permissions take effect:  sudo reboot"
