# Bundled pyvesync

Source: https://github.com/ScribleSean/pyvesync

Commit: `ab273812e816019c554d290e2e2d853d1f7e5ce6`

Regenerate from the repository root: `python scripts/vendor_pyvesync.py ab273812e816019c554d290e2e2d853d1f7e5ce6`

MIT license: [LICENSE](LICENSE). Absolute imports are rewritten to `custom_components.cosori_fryer._vendor.pyvesync` to isolate this copy from Home Assistant's core VeSync integration.
HA 2026.10.4 package_constraints.txt pins aiohttp==3.14.5 and orjson==3.11.9, and requires mashumaro>=3.17.0. All three are in HA's runtime dependency closure (mashumaro through aiohasupervisor and webrtc-models). Verified by tests/test_vendor.py.
