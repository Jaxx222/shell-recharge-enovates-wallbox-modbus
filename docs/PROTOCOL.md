# Enovates EVCC serial protocol (reverse engineered on 2026-11-19)

Link: LCCL (Java) <-> charge controller board `eNo0027-09.01-900001-000` via `/dev/ttyXRUSB2`
(Exar XR21V14xx USB-UART). Captured with `strace` on the running LCCL, idle state, no vehicle.

## Framing
    Request : req[<boardId>]:<seq>;<command>|<ck>;\r
    Response: resp[<boardId>]<seq>:OK;<data>|<ck>;
    Log line (unsolicited, every ~10 s): log[<boardId>]:<fields...>|<ck>;
- `<seq>` = 2-digit hex counter (00..ff)
- `<ck>` = **sum of the bytes after the first `;` up to `|`, mod 256**, written as 2 hex digits (verified)
- In between, the board also sends debug text without framing ("--> no valid ime/abb kwh data returned !",
  "mod frame received false"). Ignore it.

## Observed commands (idle)
| Command | Response | Meaning (assumed) |
|---|---|---|
| `at&ping` | `OK;` | keepalive |
| `at#temp` | `41.625` | board temperature °C |
| `at&cp?` | `> CP : +11.77 \| -0.24` | CP voltage +/- (12 V = no vehicle, state A) |
| `at+mb0203701E0010` | `0203 20 <32 bytes>` | **Modbus RTU passthrough**: slave 2, FC03, reg 0x701E, 16 regs (meter) |
| `at+mb0269` | `0269` | Modbus passthrough slave 2, FC 0x69 (?) |

Periodic log: `N000x;0;0;12;0;100.00;0,,,,,,,,0,...;;6,;S32A|11|7|11-->1176,24:0;m:0;p:1;;`
(N = state? 12 = CP voltage? S32A = max current? Still needs decoding.)

## Further commands (strings from ChargePointControllerLight-1.10.2.1.jar)
Current/charging: `at&pwm=<x>`, `at&pwm?`, `at&go=0/1`, `at&ready=`, `at&susp`, `at&chmp=<1|3>,<A>`, `at&phase=1/3`
Lock: `at&l?`, `at&l0`, `at&l1`, `at&ll0/1`
Status: `at&cp?`, `at&pp?`, `at&o?`, `at&i?`, `at&di?`, `at&do?`, `at&d?`, `ati`
Meter: `at+kwh`, `at+kwh2`, `at+i`, `at+u`, `at+p`, `at+f`, `at+mb...`
LED: `at&led=0..7`, `at&blink=`, `at&blinkColor=`, ...
Semantics of `pwm`/`go`/`chmp` are **untested**. Test only without a vehicle first.

## Alternative access without the serial port
LCCL admin (port 12800, login required): `/admin/driver/sendCommand?command=<at...>&connID=1`
and `/admin/driver/getLog?since=<seq>&connID=1` (from monitor.html).

## Planned bridge design (as of 2026-11-19, untested)
Goal: keep the Enovates software and web UI; switch Modbus TCP on/off in the web UI; while it is on, the Modbus master sets the current.

LCCL's built-in feature **"external limit" (DPM / dynamic load balancing)** is used, with device `PhoenixRest`:
- LCCL periodically polls `GET <endpoint>/api/v1/measurements` (grid currents L1-L3) and `/api/v1/information` (device/serial).
- Properties: `smart.external.limit.device=PhoenixRest`, `smart.external.limit.PhoenixRest.rest.url.endpoint=http://127.0.0.1:<port>/`,
  `...request.interval`, `...rest.timeout.*`, `smart.external.limit.limit.current` (main fuse), `...limit.margin` (0.95),
  `smart.external.limit.fallback.limit.current` (used when the device is unreachable, i.e. fail-safe).
- License check: "Invalid license for DPM". The key `com.enovates.phoenixrestExternalLimitDevice=...` comes from the presets in the jar.
- The bridge emulates the Phoenix device. Modbus register "target current X" → reported grid current = limit*margin - X (+ own usage?).
  The exact formula must be determined empirically.
- Switch: `smart.external.limit.device` = `PhoenixRest` (Modbus on) / `none` (Enovates normal).
  The web UI sets keys generically via `GET /admin/configure?key=..&value=..`. A checkbox in admin.html would be enough (jar patch).
- Presets (`setConfigPreset`) replace the whole config, so they are NOT suitable as a switch.

## Test result for PhoenixRest (2026-11-19, without vehicle)
- License key from the preset is **accepted** ("Allowed license for DPM ... licensee NewMotion").
- LCCL polls `/api/v1/information` + `/api/v1/measurements` every 2 s and sends a new limit at most every 5 s.
- Formula (per phase): **limit = limit.current × margin − grid current**, floored at 0
  (40 × 0.95 = 38: grid 0 → 38 A, grid 30 → 8 A, grid 36 → 0 A). Upward changes are ramped
  (L1 from 8 → 14.4 instead of 16; change.margin.up).
- Still open: is the connector's own charging current added back (real meter includes the charger)? Test under load.
- Config backup: `/opt/charger/LCCL/etc/lccl.properties.pre-modbus-20261119`
