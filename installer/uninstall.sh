#!/bin/sh
# Removes the Modbus TCP bridge and restores the original Enovates software.
#   sh installer/uninstall.sh
set -e
LCCL=/opt/charger/LCCL
JAR=$LCCL/lib/ChargePointControllerLight-1.10.2.1.jar
JAR_ORIG=/opt/charger/ChargePointControllerLight-1.10.2.1.jar.orig
PROPS=$LCCL/etc/lccl.properties

[ "$(id -u)" = "0" ] || { echo "please run as root"; exit 1; }

/etc/init.d/modbus-bridge stop 2>/dev/null || true
rm -f /etc/init.d/modbus-bridge /etc/rc?.d/S19modbus-bridge /etc/rc?.d/K80modbus-bridge
rm -rf /opt/charger/modbus-bridge
echo "==> bridge removed"

if [ -f "$JAR_ORIG" ]; then
    cp -p "$JAR_ORIG" "$JAR"; echo "==> original jar restored"
fi

# external limit off; the remaining keys are harmless while the device is "none"
sed -i 's/^smart\.external\.limit\.device=.*/smart.external.limit.device=none/' "$PROPS"
echo "==> smart.external.limit.device=none"
echo "    Full config backup (before installation): $LCCL/etc/lccl.properties.pre-modbus"
sync
echo "==> Done. Reboot required: reboot"
