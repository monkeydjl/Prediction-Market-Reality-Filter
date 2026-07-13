"""Kernel adapters — bridges between sport-specific services and Kernel Protocols.

This package is the ONE place where kernel code touches sport-specific services
(e.g. ``world_cup_*``). The core kernel modules (``app.kernel.*``) remain clean
of any sport-specific imports; only this ``adapters`` sub-package depends on them.
"""
