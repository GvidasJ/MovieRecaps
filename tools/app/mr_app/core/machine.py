"""Which computer this is -- the PC or the laptop (night 11) -- and where each one saves the finished projects.

Both computers report the same host name ("Gvidas"), so the name alone cannot tell them apart. In this order:

1. Settings' "machine" when it is not "auto" (you said which one it is);
2. the host-name table in Settings (pc_names / laptop_names) when this computer's name is in exactly one list;
3. what the hardware says, asked from Windows itself through ctypes in microseconds (WMI through PowerShell takes
   seconds): the firmware's power profile (ACPI's Preferred_PM_Profile -- what Win32_ComputerSystem.PCSystemType
   reports: 2 = mobile, 8 = tablet), a lid, a system battery (GetSystemPowerStatus: BatteryFlag 128 = no system
   battery, 255 = unknown). A battery Windows calls short-term, or one on a computer whose firmware says "desktop",
   is a UPS, not a laptop's;
4. else the PC.

The hardware is asked once per process. The PC is 192.168.101.11 and shares D:\\Shared as "Shared", so the PC saves
the projects to D:\\Shared\\Projects and the laptop to \\\\192.168.101.11\\Shared\\Projects."""
from __future__ import annotations

import getpass
import os
import socket
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PC = "pc"
LAPTOP = "laptop"
KINDS = (PC, LAPTOP)

PC_ADDRESS = "192.168.101.11"          # the PC on the home network
SHARE = "Shared"                       # the PC's D:\Shared, shared under this name
PC_ROOT = Path(r"D:\Shared\Projects")
LAPTOP_ROOT = Path(rf"\\{PC_ADDRESS}\{SHARE}\Projects")

MOBILE_PROFILES = {2, 8}               # ACPI Preferred_PM_Profile: mobile, tablet
DESKTOP_PROFILES = {1, 3, 4, 5, 6, 7}  # desktop, workstation, servers, appliance PC


@dataclass(frozen=True)
class Hardware:
    """What Windows says about this computer (None: it could not be asked)."""
    battery: bool | None = None        # a system battery (GetSystemPowerStatus)
    short_term: bool | None = None     # Windows calls the batteries short-term: a UPS (GetPwrCapabilities)
    lid: bool | None = None            # a lid (GetPwrCapabilities)
    profile: int | None = None         # the firmware's power profile (ACPI FADT Preferred_PM_Profile)


# ---------------------------------------------------------------------------------------------------------------------
# asking Windows (ctypes, microseconds)
# ---------------------------------------------------------------------------------------------------------------------

def _battery() -> bool | None:
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
    st = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
        return None
    if st.BatteryFlag == 255:                                  # unknown
        return None
    return not (st.BatteryFlag & 128)                          # 128: no system battery


def _capabilities() -> tuple[bool | None, bool | None]:
    """(a lid, the batteries are short-term = a UPS) from GetPwrCapabilities."""
    import ctypes
    from ctypes import wintypes

    class BATTERY_REPORTING_SCALE(ctypes.Structure):
        _fields_ = [("Granularity", wintypes.DWORD), ("Capacity", wintypes.DWORD)]

    class SYSTEM_POWER_CAPABILITIES(ctypes.Structure):          # 76 bytes, winnt.h
        _fields_ = [(n, ctypes.c_ubyte) for n in (
            "PowerButtonPresent", "SleepButtonPresent", "LidPresent", "SystemS1", "SystemS2", "SystemS3",
            "SystemS4", "SystemS5", "HiberFilePresent", "FullWake", "VideoDimPresent", "ApmPresent", "UpsPresent",
            "ThermalControl", "ProcessorThrottle", "ProcessorMinThrottle", "ProcessorMaxThrottle", "FastSystemS4",
            "Hiberboot", "WakeAlarmPresent", "AoAc", "DiskSpinDown", "HiberFileType", "AoAcConnectivitySupported")
        ] + [("spare3", ctypes.c_ubyte * 6), ("SystemBatteriesPresent", ctypes.c_ubyte),
             ("BatteriesAreShortTerm", ctypes.c_ubyte), ("BatteryScale", BATTERY_REPORTING_SCALE * 3),
             ("AcOnLineWake", ctypes.c_int), ("SoftLidWake", ctypes.c_int), ("RtcWake", ctypes.c_int),
             ("MinDeviceWakeState", ctypes.c_int), ("DefaultLowLatencyWake", ctypes.c_int)]
    caps = SYSTEM_POWER_CAPABILITIES()
    if not ctypes.windll.powrprof.GetPwrCapabilities(ctypes.byref(caps)):
        return None, None
    return bool(caps.LidPresent), bool(caps.SystemBatteriesPresent and caps.BatteriesAreShortTerm)


def _profile() -> int | None:
    """The firmware's power profile: byte 45 of the ACPI FADT ('FACP'; GetSystemFirmwareTable wants its signature
    with the bytes reversed). 1 desktop, 2 mobile, 3 workstation ... 8 tablet; None when it cannot be read."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.GetSystemFirmwareTable.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    k32.GetSystemFirmwareTable.restype = wintypes.UINT
    provider, table = int.from_bytes(b"ACPI", "big"), int.from_bytes(b"FACP", "little")
    n = k32.GetSystemFirmwareTable(provider, table, None, 0)
    if not n:
        return None
    buf = ctypes.create_string_buffer(n)
    got = k32.GetSystemFirmwareTable(provider, table, buf, n)
    raw = buf.raw[:got]
    if got < 46 or raw[:4] != b"FACP":
        return None
    return int(raw[45])


def _probe() -> Hardware:
    """Ask Windows; anything that cannot be asked stays None (never raises)."""
    out: dict[str, Any] = {}
    for key, fn in (("battery", _battery), ("caps", _capabilities), ("profile", _profile)):
        try:
            out[key] = fn()
        except Exception:  # noqa: BLE001 - not Windows, an old Windows, a locked-down firmware table
            out[key] = None
    lid, short = out.get("caps") or (None, None)
    return Hardware(battery=out.get("battery"), short_term=short, lid=lid, profile=out.get("profile"))


@lru_cache(maxsize=1)
def hardware() -> Hardware:
    """What Windows says about this computer, asked once per process."""
    return _probe()


def host() -> str:
    """This computer's name (both of yours say "Gvidas")."""
    try:
        return socket.gethostname()
    except OSError:
        return os.environ.get("COMPUTERNAME", "") or "this computer"


@lru_cache(maxsize=1)
def machine_id() -> str:
    """An id of this Windows installation that the other computer cannot have (the registry's MachineGuid), so a
    lock tells the two computers apart even with one host name; the host name when it cannot be read."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
            if guid:
                return str(guid)
    except (ImportError, OSError):
        pass
    return f"host:{host()}"


def user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name in the environment
        return os.environ.get("USERNAME", "")


# ---------------------------------------------------------------------------------------------------------------------
# the PC or the laptop
# ---------------------------------------------------------------------------------------------------------------------

def _settings(settings: Any = None) -> Any:
    if settings is not None:
        return settings
    try:
        from . import settings as S
        return S.load()
    except Exception:  # noqa: BLE001 - no settings: auto
        return None


def _names(settings: Any, key: str) -> set[str]:
    return {str(n).strip().lower() for n in (getattr(settings, key, None) or []) if str(n).strip()}


def decide(settings: Any = None) -> tuple[str, str]:
    """(PC or LAPTOP, why in plain words) -- the module docstring's order."""
    s = _settings(settings)
    forced = str(getattr(s, "machine", "auto") or "auto").strip().lower()
    if forced in KINDS:
        return forced, "set in Settings"
    name = host()
    in_pc, in_laptop = name.lower() in _names(s, "pc_names"), name.lower() in _names(s, "laptop_names")
    if in_pc != in_laptop:
        return (PC, f"{name}, in Settings' PC names") if in_pc else (LAPTOP, f"{name}, in Settings' laptop names")
    hw = hardware()
    if hw.battery and not hw.short_term and hw.profile not in DESKTOP_PROFILES:
        return LAPTOP, f"{name}, battery"
    if hw.profile in MOBILE_PROFILES:
        return LAPTOP, f"{name}, mobile firmware"
    if hw.lid:
        return LAPTOP, f"{name}, lid"
    if hw.battery:
        return PC, f"{name}, desktop with a UPS battery"
    if hw.battery is None and hw.profile is None and hw.lid is None:
        return PC, f"{name}, the hardware could not be asked"
    return PC, f"{name}, no battery"


def which(settings: Any = None) -> str:
    """"pc" or "laptop" (``settings``: a settings.Settings; None reads this computer's settings)."""
    return decide(settings)[0]


def label(kind: str) -> str:
    """"PC" / "laptop" -- how the app names a computer."""
    return "PC" if kind == PC else "laptop" if kind == LAPTOP else str(kind or "computer")


def describe(settings: Any = None) -> str:
    """"PC (Gvidas, no battery)", "laptop (Gvidas, battery)", "PC (set in Settings)" -- for the Settings dialog."""
    kind, why = decide(settings)
    return f"{label(kind)} ({why})"


def default_save_root(kind: str) -> Path:
    """Where finished projects go by default: the PC's own D:\\Shared\\Projects, or the same folder over the network
    from the laptop."""
    return LAPTOP_ROOT if kind == LAPTOP else PC_ROOT


def identity(settings: Any = None) -> dict:
    """This computer for lock files and project.json: {"computer": "pc"|"laptop", "host", "id", "user"}."""
    return {"computer": which(settings), "host": host(), "id": machine_id(), "user": user()}
