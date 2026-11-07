"""Outcome-first controller: a deterministic reconciliation loop that drives the simulator over HTTP.

It never imports `sim`, never reads `runs/` and never parses `scenarios/`. Everything it knows comes from the
HTTP API and its own SQLite store.
"""
