# Enovates wallbox → Modbus TCP (for evcc & co.)

Retrofits an **Enovates wallbox** (tested: model *BUSLITE*, firmware LCCL 1.10.2.1, controller board
SECO uQ7-MX6 / i.MX6 Solo) with a **Modbus TCP interface**, so it can be controlled by
[evcc](https://evcc.io) or other energy managers (PV surplus charging, load management).

The original Enovates software and its **web UI keep running**. In the web UI there is a new switch
**"Modbus TCP"** (on/off) and a selection for the **max. charging current (6–32 A)**.

> ⚠️ **Disclaimer:** Unofficial project, not affiliated with Enovates or SECO. Changes to a charging station
> that runs on mains voltage are at your own risk. Warranty and certifications may be void.
> Electrical work only by qualified persons.

## How it works

```
evcc ──Modbus TCP:502──▶ modbus_bridge.py ──"Phoenix" HTTP──▶ Enovates software (LCCL) ──▶ charge controller
                         (on the wallbox)     fake grid meter     dynamic load mgmt.
```

The Enovates software has a built-in **dynamic load management** ("External Limit", device `PhoenixRest`)
that regularly asks an external grid meter over HTTP for the current load and limits the charging current
accordingly. The bridge **emulates this meter**: if evcc requests *X* amps, the bridge reports a grid load
that leaves exactly *X* amps available. Locking, transactions, error handling and the web UI stay with
Enovates; the serial protocol to the charge controller is not touched.

Fail-safe: if the bridge crashes, LCCL falls back to the configured fallback current
(`smart.external.limit.fallback.limit.current`, default 8 A).

## Features

| | |
|---|---|
| Set charging current | 6–32 A via Modbus (pause with 0 A or enable=0) |
| Vehicle status | A/B/C/F directly from the charge controller (mode 3: A1, B1, C2 …) |
| Measurements | power, currents & voltages L1–L3, meter reading (Wh), session energy |
| Web UI | switch "Modbus TCP" on/off, max. current 6–32 A |
| Autostart | init script, starts before the Enovates software |
| Not possible | 1p/3p switching (the contactor always switches all 3 phases together) |

## Quick start

1. **Get root access** to the wallbox (once, via SD card with a SECO image) →
   [docs/ANLEITUNG.md](docs/ANLEITUNG.md#1-root-zugang-einrichten)
2. **Install:**
   ```sh
   tar czf - bridge installer | ssh -o PubkeyAuthentication=no root@WALLBOX-IP \
       'mkdir -p /tmp/ewm && tar xzf - -C /tmp/ewm && sh /tmp/ewm/installer/install.sh && reboot'
   ```
3. **Enable:** `http://WALLBOX-IP:12800/admin/admin.html` → reload with **Ctrl+Shift+R** →
   "Modbus TCP" = *On* → Save → confirm the restart.
4. **evcc:** insert [evcc/evcc-charger.yaml](evcc/evcc-charger.yaml) as a "user-defined charger"
   (replace `WALLBOX-IP`), loadpoint minCurrent **8 A**.

Full guide: **[docs/ANLEITUNG.md](docs/ANLEITUNG.md)** · All changes to the wallbox:
**[docs/AENDERUNGEN.md](docs/AENDERUNGEN.md)** · Technical background: [docs/PROTOCOL.md](docs/PROTOCOL.md)

## Modbus register map

Holding registers (FC3, identical via FC4), write with FC6/FC16, unit ID arbitrary, port 502.

| Addr | R/W | Meaning | Unit |
|---|---|---|---|
| 0 | R | vehicle status 1=A, 2=B, 3=C (charging), 4=F (error), 0=unknown | |
| 1 | R | charging enabled (enable=1 and setpoint ≥ 6 A) | 0/1 |
| 2 | R | charging power | W |
| 3–4 | R | energy of the current session (uint32, hi word first) | Wh |
| 5 | R | limit reported by LCCL | 0.1 A |
| 6 | R | bridge active (PhoenixRest mode on) | 0/1 |
| 7 | R | raw LCCL connector status (diagnostics) | code |
| 8 | R | vehicle plugged in | 0/1 |
| 9 | R | vehicle charging | 0/1 |
| **10** | **R/W** | **max. charging current** (0 or 6–32) | A |
| **11** | **R/W** | **enable** (1 = charge, 0 = pause) | 0/1 |
| 12 | R | maximum current of the wallbox (setting in the web UI) | A |
| 13–15 | R | current L1–L3 | 0.01 A |
| 16–18 | R | voltage L1–L3 | 0.1 V |
| 19–20 | R | meter reading, total energy (uint32, hi word first) | Wh |
| 21 | R | mode-3 state as ASCII (e.g. `0x4332` = "C2") | |

Test tool: `python3 tools/mbtest.py WALLBOX-IP read 0 22` · `python3 tools/mbtest.py WALLBOX-IP write 10 16`

## Project structure

```
bridge/      modbus_bridge.py (Python 2.7, runs on the wallbox), init script
installer/   install.sh, uninstall.sh, patch_webui.py (patches the web UI on the wallbox itself)
evcc/        evcc-charger.yaml
tools/       mbtest.py (Modbus test client, Python 3)
docs/        ANLEITUNG.md, AENDERUNGEN.md, PROTOCOL.md
image/       SD card image (not in the repo, only link + checksum)
```

No Enovates files and no license keys are included in this repository. The installer works with
the files present on your own wallbox.

## Status

- ✅ Tested without a vehicle: setting the current (0/10/16 A), enable, web UI switch, autostart, reboot
- ⏳ Open: test with a vehicle and 3-phase mains voltage (status mapping, units of current/voltage)

## License

[MIT](LICENSE) - applies to the code in this repository. Enovates and SECO software is not part of the project.
