#!/usr/bin/env python3
"""Minimal Modbus TCP test client for the bridge.

  mbtest.py <host> read [addr] [count]
  mbtest.py <host> write <addr> <value>
"""
import socket
import struct
import sys


def call(host, pdu, uid=1):
    s = socket.create_connection((host, 502), timeout=5)
    s.sendall(struct.pack(">HHHB", 1, 0, len(pdu) + 1, uid) + pdu)
    hdr = s.recv(7)
    length = struct.unpack(">HHHB", hdr)[2]
    resp = b""
    while len(resp) < length - 1:
        resp += s.recv(length - 1 - len(resp))
    s.close()
    if resp[0] & 0x80:
        raise SystemExit("Modbus exception %d" % resp[1])
    return resp


def main():
    host, cmd = sys.argv[1], sys.argv[2]
    if cmd == "read":
        addr = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        count = int(sys.argv[4]) if len(sys.argv) > 4 else 13
        r = call(host, struct.pack(">BHH", 3, addr, count))
        vals = struct.unpack(">%dH" % count, r[2:2 + 2 * count])
        for i, v in enumerate(vals):
            print("%3d: %d" % (addr + i, v))
    elif cmd == "write":
        r = call(host, struct.pack(">BHH", 6, int(sys.argv[3]), int(sys.argv[4])))
        print("ok", r.hex())


if __name__ == "__main__":
    main()
