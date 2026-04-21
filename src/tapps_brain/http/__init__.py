"""tapps-brain HTTP adapter sub-package (TAP-604).

Split from the original monolithic ``tapps_brain.http_adapter`` module:

* :mod:`tapps_brain.http.settings`         - ``_Settings``, ``get_settings``
* :mod:`tapps_brain.http.probe_cache`      - ``_probe_db``, pool stats
* :mod:`tapps_brain.http.metrics_collector` - Prometheus text rendering
* :mod:`tapps_brain.http.profile_resolver` - singleton ``ProfileResolver``
* :mod:`tapps_brain.http.auth`             - bearer-token auth dependencies
* :mod:`tapps_brain.http.middleware`       - ASGI middleware classes

The public surface of ``tapps_brain.http_adapter`` is **unchanged**:
all names are re-exported from that module for backward compatibility.
"""
