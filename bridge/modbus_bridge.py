#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Enovates LCCL <-> Modbus TCP bridge (Python 2.7, standard library only).

How it works:
  * Emulates a "Phoenix" DPM meter (HTTP /api/v1/information + /api/v1/measurements).
    LCCL (smart.external.limit.device=PhoenixRest) computes per phase:
        limit = limit.current * margin - grid_current
    We report grid_current = limit.current * margin - setpoint, so LCCL
    limits the charging current to exactly `setpoint`.
  * Modbus TCP server (port 502): evcc/openWB/... sets the setpoint and the
    enable flag, and reads status and meter values.
  * Data sources (LCCL on 127.0.0.1:12800):
      - /user/status (no login): connector status, limits, transaction
      - /admin/driver/status/state and /admin/driver/status/meter (Basic auth with
        admin.web.secured.username/password from lccl.properties):
        mode-3 state of the charge controller (A1, B1, C2, ...) and meter values per phase
  * On/off: active only while LCCL is polling the Phoenix endpoint (i.e.
    smart.external.limit.device=PhoenixRest, switchable in the web UI).
    Otherwise the Modbus port is closed and LCCL runs as before.
    If the bridge crashes, LCCL uses smart.external.limit.fallback.limit.current.

Modbus register map (holding registers FC3, the same values via input registers FC4):
  addr  R/W  meaning
   0    R    vehicle status: 1=A (no vehicle) 2=B (connected) 3=C (charging) 4=F (error) 0=unknown
   1    R    charging enabled (1/0) = enable flag AND setpoint >= 6
   2    R    charging power [W] (uint16)
   3-4  R    energy of the current session [Wh] (uint32, hi word first)
   5    R    current limit actually requested by LCCL [0.1 A]
   6    R    bridge active (1 = PhoenixRest mode on)
   7    R    LCCL connector status as code (see STATUS_CODES), for diagnostics
   8    R    vehicle plugged in (1/0)  -> evcc status source "combined"
   9    R    vehicle charging (1/0)
  10    R/W  max. charging current [A] (0 or 6..32)
  11    R/W  enable (1 = charge, 0 = pause)
  12    R    hardware maximum current [A] (connector max from LCCL)
  13-15 R    current L1..L3 [0.01 A]
  16-18 R    voltage L1..L3 [0.1 V]
  19-20 R    meter reading, total energy [Wh] (uint32, hi word first)
  21    R    mode-3 state of the charge controller as ASCII: hi byte letter, lo byte digit ("C2" = 0x4332)
"""
import base64
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib
import urllib2
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from SocketServer import ThreadingMixIn, TCPServer, BaseRequestHandler

PROPS = "/opt/charger/LCCL/etc/lccl.properties"
STATE_FILE = "/opt/charger/modbus-bridge/state.json"
LCCL = "http://127.0.0.1:12800"
PHOENIX_PORT = 8081
MODBUS_PORT = 502
MIN_CURRENT = 6
MAX_CURRENT = 32
POLL_TIMEOUT = 20   # s without a Phoenix request from LCCL -> Modbus off
POLL_INTERVAL = 4   # s between LCCL queries

# Units of the raw values from /admin/driver/status/meter.
# Energy in Wh is verified (event log meterValue). Current: assumed mA, NOT verified yet
# (box has no mains voltage on the desk). Voltage is auto-detected (see volt_factor).
CURRENT_FACTOR = 0.001   # raw -> A
POWER_FACTOR = 1.0       # raw -> W
ENERGY_FACTOR = 1.0      # raw -> Wh

METER_TYPES = ["Current.Import.l1", "Current.Import.l2", "Current.Import.l3",
               "Voltage.l1", "Voltage.l2", "Voltage.l3",
               "Power.Active.Import", "Energy.Active.Import.Register"]

STATUS_CODES = {"ON": 1, "OFF": 2, "ERROR": 3, "FAULTED": 4, "UNAVAILABLE": 5,
                "CHARGING": 10, "SUSPENDED_EV": 11, "SUSPENDED_EVSE": 12,
                "A1": 20, "B1": 21, "B2": 22, "C1": 23, "C2": 24, "D1": 25, "D2": 26,
                "SCHEDULEDTOOFF": 30, "OCCUPIED": 31}


def log(msg):
    sys.stdout.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stdout.flush()


def read_props():
    props = {}
    try:
        for line in open(PROPS):
            line = line.strip()
            if not line or line[0] in "#!" or "=" not in line:
                continue
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip().replace("\\:", ":")
    except IOError as e:
        log("cannot read %s: %s" % (PROPS, e))
    return props


class State(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.setpoint = MAX_CURRENT  # A, written by Modbus
        self.enable = 1
        self.active = False          # PhoenixRest mode on? (= LCCL is polling us)
        self.last_poll = 0.0         # time of LCCL's last Phoenix request
        self.dpm_base = 38.0         # limit.current * margin
        self.hw_max = 16
        self.status = None           # last /user/status connector dict
        self.status_ok = False
        self.drv_state = ""          # "A1", "B2", "C2", ... from the charge controller
        self.meter = {}              # measurand -> raw value
        self.load()

    def load(self):
        try:
            d = json.load(open(STATE_FILE))
            self.setpoint = int(d.get("setpoint", self.setpoint))
            self.enable = int(d.get("enable", self.enable))
        except (IOError, ValueError):
            pass

    def save(self):
        try:
            tmp = STATE_FILE + ".tmp"
            json.dump({"setpoint": self.setpoint, "enable": self.enable}, open(tmp, "w"))
            os.rename(tmp, STATE_FILE)
        except (IOError, OSError) as e:
            log("cannot save state: %s" % e)

    def effective_current(self):
        """Current that LCCL should allow (0 = pause)."""
        if not self.enable or self.setpoint < MIN_CURRENT:
            return 0.0
        return float(min(self.setpoint, self.hw_max))


STATE = State()


# --------------------------------------------------------------- LCCL polling
class Lccl(object):
    def __init__(self):
        self.auth = None
        self.auth_warned = False

    def refresh_auth(self, props):
        pw = props.get("admin.web.secured.password")
        user = props.get("admin.web.secured.username", "admin")
        self.auth = "Basic " + base64.b64encode("%s:%s" % (user, pw)) if pw else None

    def get_json(self, path, admin=False):
        headers = {}
        if admin:
            if not self.auth:
                return None
            headers["Authorization"] = self.auth
        req = urllib2.Request(LCCL + path, headers=headers)
        try:
            return json.load(urllib2.urlopen(req, timeout=5))
        except urllib2.HTTPError as e:
            if e.code == 401 and not self.auth_warned:
                log("admin login rejected (401), check admin.web.secured.* in lccl.properties")
                self.auth_warned = True
            return None
        except Exception:
            return None


def parse_power_w(rate):
    # "Charging rate: 3.6kw 1 phase [16A pf ] "
    try:
        part = rate.split("Charging rate:")[1].strip().split("kw")[0]
        return int(round(float(part) * 1000))
    except (IndexError, ValueError):
        return 0


def volt_factor(raw):
    """Auto-detect the voltage unit from the magnitude (mains voltage 180-260 V)."""
    for f in (1.0, 0.1, 0.01, 0.001):
        if 150 <= raw * f <= 280:
            return f
    return None


def vehicle_status(drv, con):
    """1=A 2=B 3=C 4=F 0=unknown. Prefers the mode-3 state of the charge controller."""
    if drv:
        c = drv[0].upper()
        if c == "A":
            return 1
        if c == "B":
            return 2
        if c in ("C", "D"):
            # C2/D2 = vehicle charging with PWM on; C1/D1 = vehicle wants to but is not allowed (yet)
            return 3 if drv.endswith("2") else 2
        if c in ("E", "F"):
            return 4
    st = (con.get("status") or "").upper()
    if st in ("ERROR", "FAULTED") or con.get("errors"):
        return 4
    if con.get("transaction"):
        return 2
    if st in ("ON", "OFF", "SCHEDULEDTOOFF", "UNAVAILABLE"):
        return 1
    return 0


def poller():
    lccl = Lccl()
    last_seen = None
    logged_raw = False
    while True:
        props = read_props()
        lccl.refresh_auth(props)
        try:
            base = float(props.get("smart.external.limit.limit.current", "40")) * \
                float(props.get("smart.external.limit.limit.margin", "0.95"))
        except ValueError:
            base = 38.0
        # Active = LCCL is currently polling the Phoenix endpoint. That only happens
        # with smart.external.limit.device=PhoenixRest, regardless of which config
        # layer (root/user) the web UI wrote the switch to.
        with STATE.lock:
            active = (time.time() - STATE.last_poll) < POLL_TIMEOUT
        d = lccl.get_json("/user/status")
        con = (d.get("connectors") or [None])[0] if d else None
        drv = lccl.get_json("/admin/driver/status/state?connID=1", admin=True)
        drv = str(drv.get("value") or "") if drv else ""
        meter = {}
        for t in METER_TYPES:
            v = lccl.get_json("/admin/driver/status/meter?connID=1&type=" + urllib.quote(t), admin=True)
            if v and isinstance(v.get("value"), (int, long, float)):
                meter[t] = v["value"]
        with STATE.lock:
            if active != STATE.active:
                log("Modbus mode %s" % ("ON (PhoenixRest)" if active else "OFF"))
            STATE.active, STATE.dpm_base = active, base
            STATE.status, STATE.status_ok = con, con is not None
            STATE.drv_state, STATE.meter = drv, meter
            if con and con.get("max"):
                try:
                    STATE.hw_max = int(float(con["max"].get("current", STATE.hw_max)))
                except (TypeError, ValueError):
                    pass
        state_txt = "%s/%s" % (con.get("status") if con else "?", drv or "?")
        if state_txt != last_seen:
            log("status: LCCL=%s controller=%s" % tuple(state_txt.split("/")))
            last_seen = state_txt
        # log raw values once as soon as there is mains voltage, to verify units
        if not logged_raw and meter.get("Voltage.l1", 0) > 0:
            log("raw meter values (unit check): %s" % json.dumps(meter, sort_keys=True))
            logged_raw = True
        time.sleep(POLL_INTERVAL)


# ------------------------------------------------------- Phoenix emulation
def item(id_, value, unit):
    return {"href": "", "id": id_, "name": id_, "value": value, "unit": unit, "description": id_}


class PhoenixHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith("/api/v1/information"):
            body = {"context": "information", "timestamp": ts,
                    "items": [item("name", "LCCL-Modbus-Bridge", ""),
                              item("serial-number", "BRIDGE0001", "")]}
        elif path.endswith("/api/v1/measurements"):
            with STATE.lock:
                STATE.last_poll = time.time()
                grid = max(0.0, STATE.dpm_base - STATE.effective_current())
            items = []
            for n in (1, 2, 3):
                items += [item("i%d" % n, grid, "A"), item("u%d" % n, 230.0, "V"),
                          item("p%d" % n, grid * 230.0, "W")]
            body = {"context": "measurements", "timestamp": ts, "items": items}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # LCCL sometimes closes the connection early (broken pipe): not an error
        if not isinstance(sys.exc_info()[1], socket.error):
            HTTPServer.handle_error(self, request, client_address)


# ------------------------------------------------------------- Modbus TCP
def u16(v):
    return max(0, min(int(round(v)), 0xFFFF))


def registers():
    with STATE.lock:
        con = STATE.status or {}
        m = STATE.meter
        tx = con.get("transaction") or {}
        try:
            session_wh = int(round(float(tx.get("consumed") or 0) * 1000))  # kWh -> Wh (assumed)
        except (TypeError, ValueError):
            session_wh = 0
        lim = con.get("limit") or con.get("max") or {}
        try:
            lim_a = float(lim.get("current", 0))
        except (TypeError, ValueError):
            lim_a = 0.0
        power = m.get("Power.Active.Import", -1)
        power_w = power * POWER_FACTOR if power >= 0 else parse_power_w(con.get("chargingRate") or "")
        vf = volt_factor(m.get("Voltage.l1", 0)) or 1.0
        total_wh = int(max(0, m.get("Energy.Active.Import.Register", 0)) * ENERGY_FACTOR)
        status = vehicle_status(STATE.drv_state, con) if (STATE.status_ok or STATE.drv_state) else 0
        drv = (STATE.drv_state + "  ")[:2]

        regs = [0] * 24
        regs[0] = status
        regs[1] = 1 if STATE.effective_current() > 0 else 0
        regs[2] = u16(power_w)
        regs[3], regs[4] = (session_wh >> 16) & 0xFFFF, session_wh & 0xFFFF
        regs[5] = u16(lim_a * 10)
        regs[6] = 1 if STATE.active else 0
        regs[7] = STATUS_CODES.get((con.get("status") or "").upper(), 0)
        regs[8] = 1 if status in (2, 3) else 0   # plugged (for evcc "combined")
        regs[9] = 1 if status == 3 else 0        # charging
        regs[10] = STATE.setpoint
        regs[11] = STATE.enable
        regs[12] = STATE.hw_max
        for n in range(3):
            regs[13 + n] = u16(max(0, m.get("Current.Import.l%d" % (n + 1), 0)) * CURRENT_FACTOR * 100)
            regs[16 + n] = u16(max(0, m.get("Voltage.l%d" % (n + 1), 0)) * vf * 10)
        regs[19], regs[20] = (total_wh >> 16) & 0xFFFF, total_wh & 0xFFFF
        regs[21] = (ord(drv[0]) << 8) | ord(drv[1])
    return regs


def write_register(addr, value):
    """Returns None on success, otherwise a Modbus exception code."""
    with STATE.lock:
        if addr == 10:
            if value != 0 and not (MIN_CURRENT <= value <= MAX_CURRENT):
                return 3
            if value != STATE.setpoint:
                log("setpoint %d A -> %d A" % (STATE.setpoint, value))
            STATE.setpoint = value
        elif addr == 11:
            if value not in (0, 1):
                return 3
            if value != STATE.enable:
                log("enable %d -> %d" % (STATE.enable, value))
            STATE.enable = value
        else:
            return 2
        STATE.save()
    return None


class ModbusHandler(BaseRequestHandler):
    def recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.request.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def handle(self):
        self.request.settimeout(300)
        try:
            while self.server.serving:
                tid, pid, length, uid = struct.unpack(">HHHB", self.recv_exact(7))
                pdu = self.recv_exact(length - 1)
                resp = self.process(pdu)
                self.request.sendall(struct.pack(">HHHB", tid, pid, len(resp) + 1, uid) + resp)
        except (EOFError, socket.error, socket.timeout, struct.error):
            pass

    def process(self, pdu):
        fc = ord(pdu[0])

        def exc(code):
            return struct.pack(">BB", fc | 0x80, code)

        try:
            if fc in (3, 4):
                addr, count = struct.unpack(">HH", pdu[1:5])
                regs = registers()
                if count < 1 or count > 125 or addr + count > len(regs):
                    return exc(2)
                vals = regs[addr:addr + count]
                return struct.pack(">BB", fc, count * 2) + struct.pack(">%dH" % count, *vals)
            if fc == 6:
                addr, value = struct.unpack(">HH", pdu[1:5])
                err = write_register(addr, value)
                return exc(err) if err else pdu[:5]
            if fc == 16:
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                vals = struct.unpack(">%dH" % count, pdu[6:6 + nbytes])
                for i, v in enumerate(vals):
                    err = write_register(addr + i, v)
                    if err:
                        return exc(err)
                return struct.pack(">BHH", fc, addr, count)
            return exc(1)
        except struct.error:
            return exc(3)


class ModbusServer(ThreadingMixIn, TCPServer):
    daemon_threads = True
    allow_reuse_address = True
    serving = True


def modbus_supervisor():
    """Modbus port is open only while PhoenixRest mode is active."""
    server = None
    while True:
        with STATE.lock:
            active = STATE.active
        if active and server is None:
            try:
                server = ModbusServer(("0.0.0.0", MODBUS_PORT), ModbusHandler)
                t = threading.Thread(target=server.serve_forever)
                t.daemon = True
                t.start()
                log("Modbus TCP listening on port %d" % MODBUS_PORT)
            except socket.error as e:
                log("cannot open Modbus port: %s" % e)
                server = None
        elif not active and server is not None:
            server.serving = False
            server.shutdown()
            server.server_close()
            server = None
            log("Modbus TCP stopped")
        time.sleep(2)


def main():
    try:
        os.makedirs(os.path.dirname(STATE_FILE))
    except OSError:
        pass
    log("bridge start: setpoint=%d A enable=%d" % (STATE.setpoint, STATE.enable))
    for target in (poller, modbus_supervisor):
        t = threading.Thread(target=target)
        t.daemon = True
        t.start()
    ThreadedHTTPServer(("127.0.0.1", PHOENIX_PORT), PhoenixHandler).serve_forever()


if __name__ == "__main__":
    main()
