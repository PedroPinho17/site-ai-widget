"""A tiny fake PostgreSQL server for hostile-server tests (loopback only, no Docker).

It speaks just enough of the wire protocol for psycopg: refuse SSL/GSS, accept the
login, then behave badly. Two modes:

- "silent": read queries and never answer.
- "endless_rows": answer BEGIN, then answer the schema query with rows forever.
"""
from __future__ import annotations

import socket
import threading

SSL_REQUEST_CODE = 80877103
GSS_REQUEST_CODE = 80877104
CANCEL_REQUEST_CODE = 80877102

TEXT_OID, BOOL_OID, INT4_OID = 25, 16, 23
SCHEMA_COLUMNS = (
    ("nspname", TEXT_OID),
    ("relname", TEXT_OID),
    ("relkind", TEXT_OID),
    ("attname", TEXT_OID),
    ("format_type", TEXT_OID),
    ("nullable", BOOL_OID),
    ("attnum", INT4_OID),
)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def _msg(tag: bytes, body: bytes) -> bytes:
    return tag + (4 + len(body)).to_bytes(4, "big") + body


def _login(sock: socket.socket) -> bool:
    """Handle SSL/GSS refusal and the startup packet. False for a cancel request."""
    while True:
        header = _recv_exact(sock, 8)
        length = int.from_bytes(header[:4], "big")
        code = int.from_bytes(header[4:8], "big")
        if length > 8:
            _recv_exact(sock, length - 8)
        if code in (SSL_REQUEST_CODE, GSS_REQUEST_CODE):
            sock.sendall(b"N")
            continue
        if code == CANCEL_REQUEST_CODE:
            return False
        break
    sock.sendall(
        _msg(b"R", (0).to_bytes(4, "big"))  # AuthenticationOk
        + _msg(b"S", b"server_version\x0017.0\x00")
        + _msg(b"S", b"client_encoding\x00UTF8\x00")
        + _msg(b"S", b"standard_conforming_strings\x00on\x00")
        + _msg(b"K", (1).to_bytes(4, "big") + (1).to_bytes(4, "big"))
        + _msg(b"Z", b"I")
    )
    return True


def _read_messages(sock: socket.socket):
    """Yield (tag, body) frontend messages until the client goes away."""
    while True:
        tag = sock.recv(1)
        if not tag:
            return
        length = int.from_bytes(_recv_exact(sock, 4), "big")
        yield tag, _recv_exact(sock, length - 4) if length > 4 else b""


def _row_description() -> bytes:
    body = len(SCHEMA_COLUMNS).to_bytes(2, "big")
    for name, oid in SCHEMA_COLUMNS:
        body += name.encode() + b"\x00"
        body += (0).to_bytes(4, "big") + (0).to_bytes(2, "big")  # table oid, attnum
        body += oid.to_bytes(4, "big", signed=True)
        body += (-1).to_bytes(2, "big", signed=True) + (-1).to_bytes(4, "big", signed=True)
        body += (0).to_bytes(2, "big")  # text format
    return _msg(b"T", body)


def _data_row(i: int) -> bytes:
    values = [b"public", b"t", b"r", f"c{i}".encode(), b"text", b"t", b"1"]
    body = len(values).to_bytes(2, "big")
    for value in values:
        body += len(value).to_bytes(4, "big") + value
    return _msg(b"D", body)


def _endless_rows(sock: socket.socket) -> None:
    sock.sendall(_row_description())
    i = 0
    while True:
        batch = b"".join(_data_row(i + k) for k in range(200))
        i += 200
        sock.sendall(batch)  # raises once the client closes the socket


def _serve_one(sock: socket.socket, mode: str, stats: dict) -> None:
    sock.settimeout(30)
    if not _login(sock):
        stats["cancel_requests"] += 1
        return
    stats["logins"] += 1
    batch: list[tuple[bytes, bytes]] = []
    for tag, body in _read_messages(sock):
        if mode == "silent":
            continue  # the attack: never answer
        if tag == b"Q":  # simple query (e.g. BEGIN)
            sock.sendall(_msg(b"C", b"BEGIN\x00") + _msg(b"Z", b"T"))
            continue
        batch.append((tag, body))
        if tag != b"S":  # wait for Sync: the end of one extended-protocol query
            continue
        sql = b"".join(b for t, b in batch if t == b"P")
        tags = [t for t, _ in batch]
        batch = []
        reply = b""
        if b"P" in tags:
            reply += _msg(b"1", b"")  # ParseComplete
        if b"B" in tags:
            reply += _msg(b"2", b"")  # BindComplete
        if b"pg_attribute" in sql:
            sock.sendall(reply)
            _endless_rows(sock)
            return
        if b"D" in tags:
            reply += _msg(b"n", b"")  # NoData
        sock.sendall(reply + _msg(b"C", b"BEGIN\x00") + _msg(b"Z", b"T"))


class FakePostgres:
    """Listen on 127.0.0.1:<random port>; each connection is served in its own thread."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.stats = {"logins": 0, "cancel_requests": 0}
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self.port = self._listener.getsockname()[1]
        self._threads: list[threading.Thread] = []
        self._acceptor = threading.Thread(target=self._accept_loop, daemon=True)
        self._acceptor.start()

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return  # listener closed
            thread = threading.Thread(target=self._handle, args=(conn,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            with conn:
                _serve_one(conn, self.mode, self.stats)
        except (OSError, ConnectionError):
            pass

    def close(self) -> None:
        self._listener.close()
        self._acceptor.join(timeout=5)
        for thread in self._threads:
            thread.join(timeout=5)
