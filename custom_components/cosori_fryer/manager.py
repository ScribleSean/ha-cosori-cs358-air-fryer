"""VeSync session policy for Home Assistant."""

from custom_components.cosori_fryer._vendor.pyvesync import VeSync


class FryerManager(VeSync):
    """Leave expired sessions to the config entry reauthentication flow."""

    async def _reauthenticate(self) -> bool:
        return False
