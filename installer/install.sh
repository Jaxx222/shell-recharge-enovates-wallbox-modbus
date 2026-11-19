#!/bin/sh
# Installs the Modbus TCP bridge on an Enovates wallbox (LCCL 1.10.2.1).
# Run on the wallbox as root, from the copied project folder:
#   cd /tmp/enovates-wallbox-modbus && sh installer/install.sh
# Can be run multiple times. Backups are created only the first time.
set -e

HERE=$(cd "$(dirname "$0")/.." && pwd)
LCCL=/opt/charger/LCCL
JAR=$LCCL/lib/ChargePointControllerLight-1.10.2.1.jar
JAR_ORIG=/opt/charger/ChargePointControllerLight-1.10.2.1.jar.orig
PROPS=$LCCL/etc/lccl.properties
PROPS_BAK=$LCCL/etc/lccl.properties.pre-modbus
BRIDGE_DIR=/opt/charger/modbus-bridge

say() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "please run as root"
[ -f "$JAR" ] || die "$JAR not found - is this an Enovates wallbox with LCCL 1.10.2.1?"
[ -f "$PROPS" ] || die "$PROPS not found"
command -v python >/dev/null || die "python (2.7) not found"

# set_prop KEY VALUE  - sets/replaces a key in lccl.properties (":" escaped Java-style)
set_prop() {
    key=$1; val=$(echo "$2" | sed 's/:/\\:/g')
    if grep -q "^$key=" "$PROPS"; then
        # use | as the sed delimiter, escape \ and & in the value
        esc=$(echo "$val" | sed 's/[\\&|]/\\&/g')
        sed -i "s|^$key=.*|$key=$esc|" "$PROPS"
    else
        echo "$key=$val" >> "$PROPS"
    fi
}

# 1) Backups (only once)
if [ ! -f "$JAR_ORIG" ]; then
    cp -p "$JAR" "$JAR_ORIG"; say "backup: $JAR_ORIG"
fi
if [ ! -f "$PROPS_BAK" ]; then
    cp -p "$PROPS" "$PROPS_BAK"; say "backup: $PROPS_BAK"
fi

# 2) Install the bridge + autostart (S19 = before LCCL S20)
mkdir -p "$BRIDGE_DIR"
cp "$HERE/bridge/modbus_bridge.py" "$BRIDGE_DIR/modbus_bridge.py"
chmod 755 "$BRIDGE_DIR/modbus_bridge.py"
python -m py_compile "$BRIDGE_DIR/modbus_bridge.py" && rm -f "$BRIDGE_DIR/modbus_bridge.pyc"
cp "$HERE/bridge/modbus-bridge.init" /etc/init.d/modbus-bridge
chmod 755 /etc/init.d/modbus-bridge
for r in 2 3 4 5; do ln -sf ../init.d/modbus-bridge /etc/rc$r.d/S19modbus-bridge; done
for r in 0 1 6; do ln -sf ../init.d/modbus-bridge /etc/rc$r.d/K80modbus-bridge; done
say "bridge installed: $BRIDGE_DIR, autostart /etc/init.d/modbus-bridge"

# 3) Configure LCCL for the "PhoenixRest" external limit device.
#    The device itself (smart.external.limit.device) stays unchanged; it is switched in the web UI.
#    The DPM license key is read from LCCL's factory presets on THIS wallbox.
KEY=$(python - "$JAR_ORIG" <<'EOF'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
for n in z.namelist():
    if n.startswith("com/enovates/lccl/util/configpresets/") and n.endswith(".properties"):
        for line in z.read(n).splitlines():
            if line.startswith("com.enovates.phoenixrestExternalLimitDevice="):
                print(line.split("=", 1)[1].strip()); sys.exit(0)
sys.exit(1)
EOF
) || die "DPM license key not found in the factory presets"
set_prop com.enovates.phoenixrestExternalLimitDevice "$KEY"
set_prop smart.external.limit.PhoenixRest.rest.url.endpoint "http://127.0.0.1:8081/"
grep -q "^smart.external.limit.device=" "$PROPS" || set_prop smart.external.limit.device none
say "LCCL config updated (PhoenixRest endpoint + DPM license from preset)"

# 4) Patch the web UI (always built from the original jar)
python "$HERE/installer/patch_webui.py" "$JAR_ORIG" /tmp/lccl-patched.jar
mv /tmp/lccl-patched.jar "$JAR"
sync
say "web UI patched: admin -> configuration -> 'Modbus TCP' and 'Max. charging current'"

echo
say "Done. Reboot required: reboot"
say "After that: open http://<wallbox-ip>:12800/admin/admin.html, reload with Ctrl+Shift+R,"
say "set 'Modbus TCP' to On, save and confirm the restart."
