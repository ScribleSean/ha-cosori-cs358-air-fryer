# Bundled pyvesync

Source: https://github.com/ScribleSean/pyvesync

Commit: `efaa5d2469d7a4c590914a08b7e5b4f63a97ddcc`

Regenerate from the repository root: `python scripts/vendor_pyvesync.py efaa5d2469d7a4c590914a08b7e5b4f63a97ddcc`

MIT license: [LICENSE](LICENSE). Absolute imports are rewritten to `custom_components.cosori_fryer._vendor.pyvesync` to isolate this copy from Home Assistant's core VeSync integration.
HA 2026.10.4 package_constraints.txt pins aiohttp==3.14.5 and orjson==3.11.9, and requires mashumaro>=3.17.0. All three are in HA's runtime dependency closure (mashumaro through aiohasupervisor and webrtc-models). Verified by tests/test_vendor.py.
