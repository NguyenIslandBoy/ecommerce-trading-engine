"""Detector registry.

Each detector takes a Context and returns Signals. It never decides whether it
should ultimately fire -- classification, FDR control and suppression happen
afterwards in engine.run, so every detector is judged on the same terms.
"""

from __future__ import annotations

from collections.abc import Callable

REGISTRY: dict[str, Callable] = {}


def register(name: str):
    def wrap(fn):
        REGISTRY[name] = fn
        fn.detector_name = name
        return fn
    return wrap


from engine.detectors import acquisition, lifecycle, product, quality  # noqa: E402,F401

__all__ = ["REGISTRY", "register"]
