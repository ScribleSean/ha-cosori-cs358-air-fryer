# Bundled pyvesync

Source: https://github.com/ScribleSean/pyvesync

Commit: `8060c5b139fb40f436c2d5023777abf982d8d9da`

Regenerate from the repository root: `python scripts/vendor_pyvesync.py 8060c5b139fb40f436c2d5023777abf982d8d9da`

MIT license: [LICENSE](LICENSE). Absolute imports are rewritten to `custom_components.cosori_fryer._vendor.pyvesync` to isolate this copy from Home Assistant's core VeSync integration.
HA 2026.10.4 package_constraints.txt pins aiohttp==3.14.5 and orjson==3.11.9, and requires mashumaro>=3.17.0. All three are in HA's runtime dependency closure (mashumaro through aiohasupervisor and webrtc-models). Verified by tests/test_vendor.py.
