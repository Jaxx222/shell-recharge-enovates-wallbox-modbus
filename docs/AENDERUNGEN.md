# Changes to the wallbox

Overview of all changes this project makes to the Enovates system, and why.

## Background / motivation

The wallbox was first connected to evcc via **OCPP 1.6J** (`ocpp.endpoint.location=ws://<evcc>:8887/`).
That was unreliable and the wallbox showed **"Out of Order"**:

- evcc answers the `BootNotification` with `Pending` until evcc has created the charger internally.
- When creating it, evcc asks for a `BootNotification` via `TriggerMessage`.
  The Enovates firmware confirms with `Accepted`, but then sends only a **Heartbeat** and no BootNotification.
- evcc waits until it times out (`cannot create charger type 'ocpp': context canceled` /
  `missing charger instance`). The wallbox stays in "Pending by backend" = Out of Order.
  After 240 reconnect attempts (`ocpp.reconnect.maxretriesBeforeReboot`) it reboots.

Instead of OCPP, the wallbox is therefore controlled over **Modbus TCP**.

## Changes in detail

### 1. Root access (once, see guide section 1)
| File (eMMC, `mmcblk0p2`) | Change | Backup |
|---|---|---|
| `/etc/shadow` | root password empty | `/etc/shadow.orig` |
| `/etc/ssh/sshd_config` | `PermitRootLogin yes`, `PermitEmptyPasswords yes` | `/etc/ssh/sshd_config.orig` |

Recommendation: then set a password with `passwd`.

### 2. Modbus bridge (new)
| File | Purpose |
|---|---|
| `/opt/charger/modbus-bridge/modbus_bridge.py` | Bridge (Python 2.7, standard library only) |
| `/opt/charger/modbus-bridge/state.json` | last setpoint + enable (survives reboots) |
| `/etc/init.d/modbus-bridge` + `/etc/rc{2..5}.d/S19…`, `/etc/rc{0,1,6}.d/K80…` | Autostart before LCCL (`S20`) |
| `/var/log/modbus-bridge.log` | Log (new on every start, previous one as `.old`) |

The bridge:
- runs an HTTP server on `127.0.0.1:8081` that pretends to be a "Phoenix" grid meter (`/api/v1/information`,
  `/api/v1/measurements`, fields `i1-i3`, `u1-u3`, `p1-p3`),
- runs a Modbus TCP server on port 502 (only while LCCL is polling the Phoenix endpoint),
- reads status and measurements from LCCL: `/user/status` (without login) and `/admin/driver/status/state`,
  `/admin/driver/status/meter?type=…` (Basic auth with the admin login from `lccl.properties`).

**Control logic:** LCCL computes per phase `limit = limit.current × margin − grid current`
(default 40 A × 0.95 = 38 A). The bridge reports `grid current = 38 − setpoint`, so LCCL limits to exactly the
setpoint. Increases are ramped by LCCL (about 20 s from 0 to 16 A). LCCL sends a new limit at most every 5 s.

### 3. LCCL configuration (`/opt/charger/LCCL/etc/lccl.properties`)
| Key | Value | Purpose |
|---|---|---|
| `smart.external.limit.device` | `none` / `PhoenixRest` | **Modbus switch** (set in the web UI) |
| `smart.external.limit.PhoenixRest.rest.url.endpoint` | `http\://127.0.0.1\:8081/` | Address of the bridge |
| `com.enovates.phoenixrestExternalLimitDevice` | key from the factory presets | Enables LCCL's DPM function |
| `chargepoint.max.amp.per.phase.soft` (+ `.connector.1`) | 6-32 | **Max. charging current** (web UI, default 16) |

Unchanged but relevant: `smart.external.limit.fallback.limit.current=8` (current when the bridge fails),
`chargepoint.min.amp.per.phase=8` (minimum current), `chargepoint.max.amp.per.phase=32` (hardware limit).

Backup before installation: `/opt/charger/LCCL/etc/lccl.properties.pre-modbus`

### 4. Web UI (`ChargePointControllerLight-1.10.2.1.jar`)
In `com/enovates/lccl/admin/admin.html` (inside the jar) `installer/patch_webui.py` adds:
- the selections **"Modbus TCP (port 502)"** and **"Max. charging current per phase"** in the config block,
- loading of the current values (`configure/value?key=…`),
- saving via the existing endpoint `configure?key=…&value=…` and then the existing reboot function
  (the device switch only takes effect after an LCCL restart).

Backup: `/opt/charger/ChargePointControllerLight-1.10.2.1.jar.orig`

## Not changed
- Serial protocol / firmware of the charge controller (`eNo0027`, `/dev/ttyXRUSB2`)
- OCPP configuration (can stay or be switched off; with evcc via Modbus OCPP is not needed)
- Kernel, bootloader, partitions

## Known limitations
- **No 1p/3p switching:** the contactor of this model always switches all 3 phases together.
- **Minimum current 8 A** (LCCL); setpoints of 6-7 A lead to a charging pause.
- Units of current/voltage from `driver/status/meter` are not verified yet with mains voltage
  (current assumed in mA, voltage auto-detected). The bridge logs the raw values once with mains voltage.
- The LCCL web server sends a fixed `Last-Modified` header, so after updates reload the page with Ctrl+Shift+R.
