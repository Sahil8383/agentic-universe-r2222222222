"""HTTP client for the simulator.

Two kinds of failure, handled differently:
- the network or the server broke (timeout, connection reset, 5xx): retry with backoff, but only when retrying
  is safe (see `idempotent`);
- the simulator said no (4xx): raise `SimError` with the status and the error code, so callers can branch on
  `409 stale_base_version`, `410 approval_lapsed`, `402 out_of_credits` and so on.
"""
from __future__ import annotations

import time

import httpx


class SimError(Exception):
    def __init__(self, status: int, code: str, detail=None):
        super().__init__(f"{status} {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


class SimClient:
    def __init__(self, base_url: str, timeout: float = 300.0, retries: int = 8):
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self.retries = retries
        self.calls = 0  # requests made, for the metrics table

    def _request(self, method: str, path: str, idempotent: bool, **kw) -> httpx.Response:
        delay = 0.5
        for attempt in range(self.retries + 1):
            try:
                self.calls += 1
                r = self.http.request(method, path, **kw)
            except httpx.ConnectError:
                pass  # request never reached the server: always safe to retry
            except httpx.TransportError:
                if not idempotent:
                    raise  # it may have been applied; the caller must reconcile instead of blindly retrying
            else:
                if r.status_code < 500:
                    return r
                if not idempotent:
                    raise SimError(r.status_code, "server_error", r.text[:200])
            if attempt == self.retries:
                break
            time.sleep(delay)
            delay = min(delay * 2, 10.0)
        raise SimError(503, "unreachable", f"{method} {path} failed after {self.retries} retries")

    def _json(self, r: httpx.Response):
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {"error": "http_error", "detail": r.text[:200]}
            code = body.get("error") or "http_error"
            raise SimError(r.status_code, code, body.get("detail"))
        return r.json()

    # ---- reads: always safe to retry
    def get(self, path: str, **params):
        return self._json(self._request("GET", path, True, params=params or None))

    def iter_chunk_lines(self, n: int):
        """Yields the NDJSON lines of one chunk. Streams, so a 250k-event chunk is never held as one string."""
        delay = 0.5
        for attempt in range(self.retries + 1):
            try:
                with self.http.stream("GET", f"/events/chunks/{n}") as r:
                    if r.status_code >= 500:
                        raise httpx.TransportError(f"chunk {n}: {r.status_code}")
                    if r.status_code >= 400:
                        r.read()
                        self._json(r)
                    lines = list(r.iter_lines())  # complete download first: a half-read chunk is never used
                return lines
            except httpx.TransportError:
                if attempt == self.retries:
                    raise SimError(503, "unreachable", f"chunk {n}")
                time.sleep(delay)
                delay = min(delay * 2, 10.0)

    # ---- writes: each states whether a retry is safe
    def post(self, path: str, body: dict, idempotent: bool):
        return self._json(self._request("POST", path, idempotent, json=body))

    def put(self, path: str, body: dict, idempotent: bool):
        return self._json(self._request("PUT", path, idempotent, json=body))

    def advance(self, minutes: int, from_minute: int) -> dict:
        """POST /sim/advance is not idempotent: a retry after a lost response would skip time. So on a
        transport error we ask the simulator where it is instead of resending."""
        try:
            return self.post("/sim/advance", {"minutes": minutes}, idempotent=False)
        except (httpx.TransportError, SimError) as e:
            if isinstance(e, SimError) and e.status < 500:
                raise
            st = self.get("/sim/state")
            if st["sim_minute"] > from_minute:
                return {"sim_minute": st["sim_minute"], "ts": st["ts"], "finished": st["finished"],
                        "new_chunks": None}  # None = "unknown": the ingestor lists chunks itself anyway
            return self.post("/sim/advance", {"minutes": minutes}, idempotent=False)
