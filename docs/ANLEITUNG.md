# Guide: Enovates wallbox with Modbus TCP

Tested on: Enovates *BUSLITE*, LCCL 1.10.2.1, controller board **SECO SOM-uQ7-MX6-2 (A75)**
with i.MX6 Solo and 512 MB RAM. The board has an SD card slot and a boot jumper (SD ↔ internal eMMC).
A serial console is **not** required. Everything is done over the network (DHCP).

You need: Linux PC, microSD card (≥ 2 GB), network cable, router with DHCP.

> ⚠️ Work on the opened wallbox only when it is disconnected from mains. If the board runs on the
> desk, it can be powered via the uQ7 carrier without mains voltage. Then all measurements are 0.

---

## 1. Set up root access

The Enovates system on the internal eMMC has no known root password. That is why you start the board
once from SD with a SECO Linux and enable SSH login on the eMMC system.

### 1.1 Download the SD card image

SECO's public image for this board (A75, 512 MB, Yocto *kirkstone*) with SSH enabled:

```sh
wget https://secostorage.blob.core.windows.net/secosoftware-public/imx6/a75/kirkstone_1-05-00/seco_imx6_a75-512M-2x256M_edgehog-things-image_5-10_kirkstone_1-05-00_20240829.wic.bz2
sha256sum seco_imx6_a75-512M-2x256M_edgehog-things-image_5-10_kirkstone_1-05-00_20240829.wic.bz2
# expected: 8e6fe3681ea329a356083910901c56ee2979277035dcd92f449435438916422b
```

> Important: use the **things-image**. The *edgehog-embedded-base-image* also boots, but it has **no SSH server**.
> For boards with 256 MB or 1 GB there are separate images in the same folder
> (`…a75-256M-1x256M…`, `…a75-1G-2x512M…`). The RAM configuration must match, otherwise the board will not boot.

### 1.2 Flash the image to the SD card

```sh
# identify the SD card - CAREFUL, the wrong device will be overwritten!
lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINT
# example: the card is /dev/sdX (replace in the following commands!)

sudo umount /dev/sdX?* 2>/dev/null
bzcat seco_imx6_a75-512M-2x256M_edgehog-things-image_5-10_kirkstone_1-05-00_20240829.wic.bz2 \
  | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync
sync
```

### 1.3 Boot from SD

1. Board disconnected from power, insert the SD card, set the **boot jumper J3** (Between the left Networkport and the SOM Socket).
2. Connect the network cable and switch on. The board gets an IP via DHCP (after about 1 minute).
3. Find the IP, e.g. in the router or with
   `nmap -p22 --open 192.168.X.0/24` (MAC prefix `00:c0:08` = SECO).
4. Log in (root without a password):
   ```sh
   ssh -o PubkeyAuthentication=no root@BOARD-IP
   ```

In the SECO system: `mmcblk1` = SD card, **`mmcblk0` = internal eMMC** with the Enovates system
(partition `mmcblk0p1` = kernel/DTB, **`mmcblk0p2` = root file system**).

### 1.4 Enable SSH on the Enovates system

Run on the board (in the SECO system):

```sh
mkdir -p /mnt/emmc
mount /dev/mmcblk0p2 /mnt/emmc

# backups
cp -p /mnt/emmc/etc/shadow             /mnt/emmc/etc/shadow.orig
cp -p /mnt/emmc/etc/ssh/sshd_config    /mnt/emmc/etc/ssh/sshd_config.orig 2>/dev/null

# root password empty + allow SSH login for root
sed -i 's/^root:[^:]*:/root::/' /mnt/emmc/etc/shadow
cat > /mnt/emmc/etc/ssh/sshd_config <<'EOF'
LoginGraceTime 50

PermitRootLogin yes
PermitEmptyPasswords yes
UseDNS no
EOF

sync
umount /mnt/emmc
poweroff
```

### 1.5 Boot the Enovates system again

1. Power off, set the **boot jumper J3 to eMMC**, remove the SD card (optional).
2. Switch on. After about 2 minutes:
   ```sh
   ssh -o PubkeyAuthentication=no root@WALLBOX-IP
   ```
3. **Recommended:** set a root password right away and disable empty passwords:
   ```sh
   passwd
   sed -i 's/^PermitEmptyPasswords yes/PermitEmptyPasswords no/' /etc/ssh/sshd_config
   ```
   (If you do, leave out `-o PubkeyAuthentication=no` in the following commands, or set up an SSH key.)

The Enovates web UI is at `http://WALLBOX-IP:12800/admin/admin.html`. The admin login is in
`/opt/charger/LCCL/etc/lccl.properties` (`admin.web.secured.username` / `admin.web.secured.password`).

---

## 2. Install the bridge

On the PC, from this project folder:

```sh
tar czf - bridge installer | ssh -o PubkeyAuthentication=no root@WALLBOX-IP \
    'mkdir -p /tmp/ewm && tar xzf - -C /tmp/ewm && sh /tmp/ewm/installer/install.sh'
```

The installer:
- backs up the original once (`/opt/charger/ChargePointControllerLight-1.10.2.1.jar.orig`,
  `/opt/charger/LCCL/etc/lccl.properties.pre-modbus`),
- installs the bridge to `/opt/charger/modbus-bridge/` with autostart (`/etc/init.d/modbus-bridge`),
- sets up the PhoenixRest endpoint in the LCCL config and takes the DPM license key from the
  factory presets of **your** wallbox,
- patches the web UI (new fields in the admin configuration).

Then restart:

```sh
ssh -o PubkeyAuthentication=no root@WALLBOX-IP reboot
```

## 3. Enable in the web UI

1. Open `http://WALLBOX-IP:12800/admin/admin.html`, log in.
2. Reload the page with **Ctrl+Shift+R**. The wallbox sends the page with an old date, so otherwise the
   browser shows the cached version without the new fields.
3. In the configuration block below "Network config":
   - **Modbus TCP (port 502):** *On – external control (evcc)*
   - **Max. charging current per phase:** e.g. 16 A or 32 A (only as high as supply cable and fuse allow!)
4. **Save** → confirm the restart prompt. After about 2 minutes Modbus TCP is active.

Check:
```sh
python3 tools/mbtest.py WALLBOX-IP read 0 22     # register 6 = 1 -> bridge active
ssh -o PubkeyAuthentication=no root@WALLBOX-IP cat /var/log/modbus-bridge.log
```

Switch off: set "Modbus TCP" to *Off* in the web UI → save → restart. The wallbox then behaves as from the
factory again, and port 502 is closed.

## 4. Set up evcc

1. In [evcc/evcc-charger.yaml](../evcc/evcc-charger.yaml) replace `WALLBOX-IP` with the IP of the wallbox:
   ```sh
   sed -i 's/WALLBOX-IP/192.168.1.50/g' evcc/evcc-charger.yaml
   ```
2. evcc UI → Configuration → add charger → **user-defined device** → paste the content from `type: custom` on.
   (Or in `evcc.yaml` under `chargers:`.)
3. Loadpoint: **min. current 8 A** (the Enovates software does not charge below 8 A,
   `chargepoint.min.amp.per.phase=8`), **max. current** = value from the web UI. Phases: 3, no switching.

## 5. Uninstall

```sh
tar czf - installer | ssh -o PubkeyAuthentication=no root@WALLBOX-IP \
    'mkdir -p /tmp/ewm && tar xzf - -C /tmp/ewm && sh /tmp/ewm/installer/uninstall.sh && reboot'
```

Restores the original jar and sets `smart.external.limit.device=none`.
The full config backup from before the installation stays at `lccl.properties.pre-modbus`.

## Troubleshooting

| Problem | Cause / solution |
|---|---|
| New fields not visible in the web UI | Browser cache: **Ctrl+Shift+R** |
| Port 502 closed | "Modbus TCP" is off, or LCCL is not running yet (needs ~1-2 min after boot) |
| Wallbox only charges with 8 A | Bridge not running → fallback current. `/etc/init.d/modbus-bridge status`, log `/var/log/modbus-bridge.log` |
| Charging pause at 6-7 A | LCCL minimum is 8 A → set minCurrent 8 in evcc |
| Max. 16 A even though 32 A requested | "Max. charging current" in the web UI is 16 A |
| `Invalid license for DPM` in `/var/log/lccl/server.0` | DPM key missing → run `install.sh` again |
| Measurements are 0 | No mains voltage on the meter (e.g. board on the desk) |
| `reboot` via `ssh … '… &'` does nothing | Commands chained with `&&` and `&` run in the background and stop when the SSH session ends. Use `nohup sh -c "sleep 2; reboot" &` |
