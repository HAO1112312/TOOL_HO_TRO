"""Prevent competing GUI instances from owning the same global Odx hotkeys."""
import ctypes
import os


class SingleInstance:
    def __init__(self, name=r"Local\OdxAutoPilotStudio", kernel=None):
        self.name = name
        self.handle = None
        self.kernel = kernel

    def acquire(self):
        if self.handle is not None:
            return True
        if os.name != "nt" and self.kernel is None:
            raise RuntimeError("ODX Auto-Pilot chỉ hỗ trợ Windows.")
        from ctypes import wintypes
        kernel = self.kernel or ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = kernel.CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise OSError(error, "Không tạo được khóa phiên Auto-Pilot.")
        if error == 183:  # ERROR_ALREADY_EXISTS; another GUI holds the named object.
            kernel.CloseHandle(handle)
            return False
        self.kernel, self.handle = kernel, handle
        return True

    def release(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            self.kernel.CloseHandle(handle)

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("ODX Auto-Pilot đang chạy; hãy dùng cửa sổ đã mở.")
        return self

    def __exit__(self, *_):
        self.release()
