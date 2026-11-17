"""Replace an owned low-level keyboard hook without an unprotected gap.

Windows calls the newest low-level hook first. Odx installs its own consuming
hook during startup, so Auto-Pilot must install its replacement after Odx loads.
Call this helper on the thread that owns the keyboard message loop, and keep the
callback alive until every associated hook is removed.
"""
import ctypes
from ctypes import wintypes


WH_KEYBOARD_LL = 13


def replace_keyboard_hook(api, callback, module, old_handle):
    """Return the replacement handle, or None while retaining the old hook.

    Install first so a successful replacement has no interval without a hook.
    If retiring the old hook fails, remove the new hook and report failure; the
    caller must keep its existing handle/callback and mark protection unready.
    """
    api.SetWindowsHookExW.argtypes = [ctypes.c_int, type(callback), wintypes.HINSTANCE,
                                    wintypes.DWORD]
    api.SetWindowsHookExW.restype = wintypes.HANDLE
    api.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
    api.UnhookWindowsHookEx.restype = wintypes.BOOL
    replacement = api.SetWindowsHookExW(WH_KEYBOARD_LL, callback, module, 0)
    if not replacement:
        return None
    if old_handle and not api.UnhookWindowsHookEx(old_handle):
        if not api.UnhookWindowsHookEx(replacement):
            # Keep the still-live handle available to the owner for cleanup.
            error = OSError("Could not roll back a keyboard-hook replacement")
            error.replacement_handle = replacement
            raise error
        return None
    return replacement
