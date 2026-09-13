"""
workshop — Helio's full-screen making surface.

The Forge planet is the bench: what is on it, its source, the rack of past
builds. The workshop is the room. It goes full screen, and everything Helio
makes lands on it as a slab you can drag, resize, push into a corner and come
back to — so a build can be running in one place while you work in another and
ask Helio about a third.
"""
from .slab import Slab
from .canvas import WorkshopCanvas, get_canvas, canvas_is_open

__all__ = ["Slab", "WorkshopCanvas", "get_canvas", "canvas_is_open"]
