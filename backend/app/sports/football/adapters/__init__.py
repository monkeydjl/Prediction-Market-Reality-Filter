"""Football adapters — bridges between sport-specific services and Kernel Protocols.

This package is the ONE place where football-specific code touches existing
``world_cup_*`` services. The core kernel modules (``app.kernel.*``) remain
clean of any sport-specific imports; only this ``adapters`` sub-package
depends on them.
"""
