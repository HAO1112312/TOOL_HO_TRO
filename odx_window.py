"""Local window sizing and preferences, independent of recording/settings sync."""
import ctypes
import json
import os
from pathlib import Path
import re
import tempfile
from tkinter import TclError


def _window_api(root):
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowRect.restype = wintypes.BOOL
    window = user.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
    return user, window


def get_work_area(root, bounds=None):
    """Use the saved/current monitor's usable area, excluding the taskbar."""
    if os.name == "nt" and getattr(root, "tk", None) is not None:
        try:
            from ctypes import wintypes
            user, window = _window_api(root)

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

            user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user.MonitorFromWindow.restype = wintypes.HANDLE
            user.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
            user.MonitorFromRect.restype = wintypes.HANDLE
            user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
            user.GetMonitorInfoW.restype = wintypes.BOOL
            if bounds is not None:
                width, height, x, y = bounds
                rect = wintypes.RECT(x, y, x + width, y + height)
                monitor = user.MonitorFromRect(ctypes.byref(rect), 2)
            else:
                monitor = user.MonitorFromWindow(window, 2)
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            if monitor and user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                area = info.rcWork
                if area.right > area.left and area.bottom > area.top:
                    return area.left, area.top, area.right, area.bottom
        except (OSError, AttributeError, ValueError, TclError, ctypes.ArgumentError):
            pass
    x, y = root.winfo_vrootx(), root.winfo_vrooty()
    return x, y, x + root.winfo_vrootwidth(), y + root.winfo_vrootheight()


def _frame_padding(root):
    if os.name == "nt" and getattr(root, "tk", None) is not None:
        try:
            from ctypes import wintypes
            user, window = _window_api(root)
            user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user.GetWindowLongW.restype = wintypes.LONG
            user.GetDpiForWindow.argtypes = [wintypes.HWND]
            user.GetDpiForWindow.restype = wintypes.UINT
            user.AdjustWindowRectExForDpi.argtypes = [ctypes.POINTER(wintypes.RECT),
                                                    wintypes.DWORD, wintypes.BOOL,
                                                    wintypes.DWORD, wintypes.UINT]
            user.AdjustWindowRectExForDpi.restype = wintypes.BOOL
            rect = wintypes.RECT(0, 0, 100, 100)
            if user.AdjustWindowRectExForDpi(
                    ctypes.byref(rect), user.GetWindowLongW(window, -16), False,
                    user.GetWindowLongW(window, -20), user.GetDpiForWindow(window) or 96):
                return max(20, rect.right - rect.left - 100 + 4), max(48, rect.bottom - rect.top - 100 + 8)
        except (OSError, AttributeError, ValueError, TclError, ctypes.ArgumentError):
            pass
    return 20, 48


def fit_bounds(bounds, work_area, padding=(20, 48)):
    """Clamp client bounds and reserve space for the Windows title/frame."""
    left, top, right, bottom = work_area
    border_width, border_height = padding
    available_width, available_height = max(1, right - left - border_width), max(1, bottom - top - border_height)
    width, height, x, y = bounds
    width = min(available_width, max(min(820, available_width), width))
    height = min(available_height, max(min(620, available_height), height))
    x = min(max(left, x), right - width - border_width)
    y = min(max(top, y), bottom - height - border_height)
    return width, height, x, y


def _read_bounds(root):
    match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", root.geometry())
    if not match:
        return None
    bounds = tuple(int(value) for value in match.groups())
    if os.name == "nt" and getattr(root, "tk", None) is not None:
        try:
            from ctypes import wintypes
            user, window = _window_api(root)
            rect = wintypes.RECT()
            if user.GetWindowRect(window, ctypes.byref(rect)):
                return bounds[0], bounds[1], rect.left, rect.top
        except (OSError, AttributeError, ValueError, TclError, ctypes.ArgumentError):
            pass
    return bounds


def _place_bounds(root, bounds):
    width, height, x, y = bounds
    if os.name == "nt" and getattr(root, "tk", None) is not None:
        try:
            from ctypes import wintypes
            # Native placement supports monitors left/above the primary screen;
            # negative Tk geometry offsets instead refer to the opposite edge.
            root.geometry(f"{width}x{height}")
            root.update_idletasks()
            user, window = _window_api(root)
            user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user.SetWindowPos.restype = wintypes.BOOL
            if user.SetWindowPos(window, None, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010):
                return
        except (OSError, AttributeError, ValueError, TclError, ctypes.ArgumentError):
            pass
    root.geometry(f"{width}x{height}{x:+d}{y:+d}")


class WindowPreferences:
    """Restore a fitting window and save only this machine's UI preferences."""

    def __init__(self, root, path):
        self.root, self.path = root, Path(path)
        self.last_error = None
        self._normal = None
        self._maximized = False
        self._after_id = self._binding = None
        self._closed = False

    def restore(self):
        if self._binding:
            return self._normal
        self.root.update_idletasks()
        saved = None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8-sig"))
            normal = value.get("normal") if isinstance(value, dict) else None
            if (isinstance(normal, dict) and all(type(normal.get(key)) is int for key in ("width", "height", "x", "y"))
                    and 0 < normal["width"] <= 100000 and 0 < normal["height"] <= 100000
                    and abs(normal["x"]) <= 100000 and abs(normal["y"]) <= 100000):
                saved = tuple(normal[key] for key in ("width", "height", "x", "y"))
                self._maximized = value.get("maximized") is True
        except (OSError, ValueError, TypeError):
            pass
        work = get_work_area(self.root, saved)
        if saved is None:
            width = max(1040, self.root.winfo_reqwidth())
            height = max(840, self.root.winfo_reqheight())
            saved = width, height, work[0] + max(0, (work[2] - work[0] - width) // 2), work[1] + 16
        padding = _frame_padding(self.root)
        self._normal = fit_bounds(saved, work, padding)
        self.root.minsize(min(820, max(1, work[2] - work[0] - padding[0])),
                          min(620, max(1, work[3] - work[1] - padding[1])))
        _place_bounds(self.root, self._normal)
        # Never map a withdrawn test/loading window just to restore zoomed state.
        if self._maximized and self.root.state() != "withdrawn":
            self.root.state("zoomed")
        self._binding = self.root.bind("<Configure>", self._on_configure, add="+")
        return self._normal

    def _capture(self):
        state = self.root.state()
        if state == "zoomed":
            self._maximized = True
        elif state == "normal":
            bounds = _read_bounds(self.root)
            if bounds is not None and bounds[0] > 1 and bounds[1] > 1:
                self._normal = bounds
                self._maximized = False

    def _on_configure(self, event):
        if self._closed or event.widget is not self.root:
            return
        self._capture()
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
        self._after_id = self.root.after(400, self._save)

    def _save(self):
        self._after_id = None
        if self._normal is None:
            return False
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".odx-window-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                value = dict(version=1, normal=dict(zip(("width", "height", "x", "y"), self._normal)),
                             maximized=self._maximized)
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.last_error = None
            return True
        except OSError as exc:
            self.last_error = str(exc)
            return False
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        self._capture()
        self._save()
        if self._binding:
            self.root.unbind("<Configure>", self._binding)
            self._binding = None
