"""Paths as shown to the player, without their Windows user name.

The home folder (C:\\Users\\<name>) is shown as %USERPROFILE%, so a screenshot or a pasted log in a bug report
doesn't give away the user name, which is often a real name (Armchair, 2026-11-08). Files are still opened by
their real path; this is only for display.
"""

import os
from pathlib import Path

HOME_PLACEHOLDER = "%USERPROFILE%"


def shown(path, home=None):
    """The path as text, with the home folder replaced by %USERPROFILE%. Other paths are unchanged."""
    text = str(path)
    home = str(home or Path.home())
    # Windows paths aren't case sensitive, and the home folder must end at a separator, not mid-name.
    if os.path.normcase(text).startswith(os.path.normcase(home)) and text[len(home):len(home) + 1] in ("", "\\", "/"):
        return HOME_PLACEHOLDER + text[len(home):]
    return text


# FOLDERID_Documents: Windows' own record of the Documents folder, which OneDrive may have moved.
_DOCUMENTS_ID = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"


def documents_folder():
    """The real Documents folder, asked from Windows (a local call), so it's right when OneDrive has moved it.
    Falls back to <home>\\Documents if Windows doesn't answer."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD), ("data3", wintypes.WORD),
                        ("data4", ctypes.c_ubyte * 8)]

        guid = GUID()
        ctypes.oledll.ole32.CLSIDFromString(_DOCUMENTS_ID, ctypes.byref(guid))
        found = ctypes.c_wchar_p()
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(found))
        try:
            if found.value:
                return Path(found.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(found)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


def assist_folder():
    """Documents\\ASSIST: survey files, map images and recordings."""
    return documents_folder() / "ASSIST"


def recordings_folder():
    """Documents\\ASSIST\\recordings: the player's Status.json recordings (Milestone 9; was the project's recordings\\)."""
    return assist_folder() / "recordings"
