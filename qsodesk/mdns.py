"""mDNS for QSODesk, so the Pi answers to a friendly name on the local
network - http://qsodesk.local - with nothing extra to install.

A very small multicast-DNS responder: it listens on 224.0.0.251:5353 and
answers "what is the address of <name>.local?" with this machine's address.
It sits happily alongside Avahi (Raspberry Pi OS) and Bonjour (macOS), which
keep answering for the machine's own hostname.
"""
import re
import socket
import struct
import subprocess
import threading
import time

GROUP, PORT = "224.0.0.251", 5353


def clean_name(text):
    """A safe single label: letters, digits and hyphens."""
    name = re.sub(r"[^a-z0-9-]", "", (text or "").lower().replace(" ", "-")).strip("-")[:40]
    return name or "qsodesk"


def encode_name(name):
    out = b""
    for label in name.strip(".").split("."):
        raw = label.encode("ascii", "ignore")[:63]
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def read_name(data, pos):
    """Read a DNS name (following compression pointers). Returns (name, next)."""
    labels, jumped, end, hops = [], False, pos, 0
    while pos < len(data):
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:                      # pointer to an earlier name
            if pos + 1 >= len(data) or hops > 10:
                break
            if not jumped:
                end = pos + 2
            pos = ((n & 0x3F) << 8) | data[pos + 1]
            jumped, hops = True, hops + 1
            continue
        labels.append(data[pos + 1:pos + 1 + n].decode("ascii", "replace"))
        pos += 1 + n
    return ".".join(labels).lower(), (end if jumped else pos)


def questions(data):
    """The (name, type) pairs asked in a DNS query packet; [] for anything else."""
    if len(data) < 12:
        return []
    flags, count = struct.unpack("!HH", data[2:6])
    if flags & 0x8000:                            # a response, not a question
        return []
    out, pos = [], 12
    for _ in range(min(count, 20)):
        name, pos = read_name(data, pos)
        if pos + 4 > len(data):
            break
        qtype = struct.unpack("!H", data[pos:pos + 2])[0]
        out.append((name, qtype))
        pos += 4
    return out


def answer(name, ip, query_id=0, ttl=120):
    """A response packet saying <name> is at <ip>."""
    return (struct.pack("!HHHHHH", query_id, 0x8400, 0, 1, 0, 0) + encode_name(name) +
            struct.pack("!HHIH", 1, 0x8001, ttl, 4) + socket.inet_aton(ip))


def local_ips():
    ips = set()
    try:                                          # address used to reach the local network
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((GROUP, PORT))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:                                          # every address, on Linux
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=2).stdout
        ips.update(x for x in out.split() if re.match(r"^\d+\.\d+\.\d+\.\d+$", x))
    except (OSError, subprocess.SubprocessError):
        pass
    return [ip for ip in ips if not ip.startswith("127.") and ip != "0.0.0.0"]


def ip_towards(addr):
    """Our own address on the network that `addr` is on."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((addr, 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


class MDNS(threading.Thread):
    def __init__(self, get_name):
        super().__init__(daemon=True)
        self.get_name = get_name                  # returns the label, e.g. "qsodesk"
        self.ok = False
        self.error = ""
        self.joined = set()
        self.sock = None

    def fqdn(self):
        return clean_name(self.get_name()) + ".local"

    def _open(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(("", PORT))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.settimeout(5)
        return s

    def _join(self):
        """Join the mDNS group on every network we are on (and on new ones later)."""
        for ip in local_ips():
            if ip in self.joined:
                continue
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                     socket.inet_aton(GROUP) + socket.inet_aton(ip))
                self.joined.add(ip)
                self._send(answer(self.fqdn(), ip), ip)          # announce ourselves
            except OSError:
                pass

    def _send(self, packet, from_ip, to=(GROUP, PORT)):
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(from_ip))
            self.sock.sendto(packet, to)
        except OSError:
            pass

    def run(self):
        try:
            self.sock = self._open()
            self.ok = True
        except OSError as exc:
            self.error = "mDNS could not start: %s" % exc
            return
        last_join = 0
        while True:
            if time.time() - last_join > 30:      # also picks up Wi-Fi coming up later
                last_join = time.time()
                self._join()
            try:
                data, (addr, port) = self.sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(1)
                continue
            me = self.fqdn()
            for name, qtype in questions(data):
                if name == me and qtype in (1, 255):
                    ip = ip_towards(addr)
                    if not ip or ip.startswith("127."):
                        continue
                    self._send(answer(me, ip), ip)
                    if port != PORT:              # a plain DNS client asking directly
                        qid = struct.unpack("!H", data[:2])[0]
                        self._send(answer(me, ip, qid), ip, (addr, port))
                    break
