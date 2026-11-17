"""Hook-priority regression tests; native input uses only private tagged F24."""
import ctypes
from ctypes import wintypes
import os
import threading
import time
import unittest
from unittest.mock import Mock

from odx_hotkeys import replace_keyboard_hook


def _process_integrity_level(pid):
    """Read a process token without changing focus, privileges or its state."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    security.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    security.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    security.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

    class MandatoryLabel(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

    process = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not process:
        return None
    token = wintypes.HANDLE()
    try:
        if not security.OpenProcessToken(process, 8, ctypes.byref(token)):  # TOKEN_QUERY
            return None
        needed = wintypes.DWORD()
        security.GetTokenInformation(token, 25, None, 0, ctypes.byref(needed))  # TokenIntegrityLevel
        if not needed.value:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 25, buffer, needed, ctypes.byref(needed)):
            return None
        label = ctypes.cast(buffer, ctypes.POINTER(MandatoryLabel)).contents
        count = security.GetSidSubAuthorityCount(label.sid).contents.value
        return security.GetSidSubAuthority(label.sid, count - 1).contents.value
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


class HookReplacementTests(unittest.TestCase):
    def test_new_hook_is_installed_before_old_hook_is_removed_with_full_width_handles(self):
        api = Mock()
        events = []
        callback = Mock()
        previous, replacement = 2 ** 40 + 1, 2 ** 40 + 2
        api.SetWindowsHookExW.side_effect = lambda *args: events.append(("install", args)) or replacement
        api.UnhookWindowsHookEx.side_effect = lambda handle: events.append(("remove", handle)) or True
        self.assertEqual(replace_keyboard_hook(api, callback, 2 ** 40, previous), replacement)
        self.assertEqual(events, [("install", (13, callback, 2 ** 40, 0)), ("remove", previous)])
        self.assertIs(api.SetWindowsHookExW.restype, wintypes.HANDLE)
        self.assertEqual(api.UnhookWindowsHookEx.argtypes, [wintypes.HANDLE])

    def test_failed_installation_leaves_old_hook_intact(self):
        api = Mock()
        api.SetWindowsHookExW.return_value = None
        self.assertIsNone(replace_keyboard_hook(api, Mock(), 10, 20))
        api.UnhookWindowsHookEx.assert_not_called()

    def test_failure_retiring_old_hook_rolls_back_only_replacement(self):
        api = Mock()
        api.SetWindowsHookExW.return_value = 30
        api.UnhookWindowsHookEx.side_effect = [False, True]
        self.assertIsNone(replace_keyboard_hook(api, Mock(), 10, 20))
        self.assertEqual([args.args[0] for args in api.UnhookWindowsHookEx.call_args_list], [20, 30])

    def test_rollback_failure_exposes_live_replacement_for_owner_cleanup(self):
        api = Mock()
        api.SetWindowsHookExW.return_value = 30
        api.UnhookWindowsHookEx.return_value = False
        with self.assertRaises(OSError) as raised:
            replace_keyboard_hook(api, Mock(), 10, 20)
        self.assertEqual(raised.exception.replacement_handle, 30)

    def test_initial_install_never_tries_to_remove_a_null_handle(self):
        api = Mock()
        api.SetWindowsHookExW.return_value = 30
        self.assertEqual(replace_keyboard_hook(api, Mock(), 10, None), 30)
        api.UnhookWindowsHookEx.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows low-level hooks")
class NativeHookPrecedenceTests(unittest.TestCase):
    def test_replacement_receives_private_f24_before_a_later_consuming_peer(self):
        # F24 is intentionally unrelated to capture controls. The unique tag
        # ensures both callbacks ignore every real user key and other injected
        # key. Every hook is removed before this test finishes, even on failure.
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        result_type = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(result_type, ctypes.c_int, wintypes.WPARAM,
                                          wintypes.LPARAM)

        class KeyEvent(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class HardwareInput(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                        ("wParamH", wintypes.WORD)]

        class InputData(ctypes.Union):
            _fields_ = [("ki", KeyboardInput), ("mi", MouseInput), ("hi", HardwareInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("data",)
            _fields_ = [("type", wintypes.DWORD), ("data", InputData)]

        user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE,
                                         wintypes.DWORD]
        user.SetWindowsHookExW.restype = wintypes.HANDLE
        user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM,
                                      wintypes.LPARAM]
        user.CallNextHookEx.restype = result_type
        user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user.UnhookWindowsHookEx.restype = wintypes.BOOL
        user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                     wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user.TranslateMessage.restype = wintypes.BOOL
        user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user.DispatchMessageW.restype = result_type
        user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        user.SendInput.restype = wintypes.UINT
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        foreground_pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(foreground_pid))
        own_integrity = _process_integrity_level(os.getpid())
        foreground_integrity = _process_integrity_level(foreground_pid.value)
        if (own_integrity is not None and foreground_integrity is not None
                and foreground_integrity > own_integrity):
            # SendInput is subject to UIPI; do not lower/elevate or move the
            # user's active elevated Tool/Odx just to satisfy an input fixture.
            self.skipTest("Native hook input test requires foreground at same/lower integrity; "
                          "leaving active Odx/Tool untouched")
        token = (os.getpid() << 16) | 0x5A24
        received = []
        failures = []
        ready, done = threading.Event(), threading.Event()

        def make_callback(name):
            def callback(code, message, pointer):
                try:
                    if code >= 0:
                        event = ctypes.cast(pointer, ctypes.POINTER(KeyEvent)).contents
                        if event.vkCode == 0x87 and event.dwExtraInfo == token:
                            received.append((name, message))
                            return 1
                except BaseException as exc:
                    failures.append(exc)
                return user.CallNextHookEx(None, code, message, pointer)
            return callback_type(callback)

        tool_callback = make_callback("tool")
        peer_callback = make_callback("peer")
        # Hook callbacks need a pumping owner thread. The main thread injects
        # tagged F24 while the hook owner performs both replacement and pumping.
        replace_requested, replacement_done = threading.Event(), threading.Event()

        def hook_worker():
            tool = peer = None
            try:
                module = kernel.GetModuleHandleW(None)
                tool = replace_keyboard_hook(user, tool_callback, module, None)
                peer = user.SetWindowsHookExW(13, peer_callback, module, 0)
                if not tool or not peer:
                    raise ctypes.WinError(ctypes.get_last_error())
                ready.set()
                message = wintypes.MSG()
                while not done.is_set():
                    if replace_requested.is_set() and not replacement_done.is_set():
                        replacement = replace_keyboard_hook(user, tool_callback, module, tool)
                        if not replacement:
                            raise RuntimeError("Could not reclaim keyboard-hook priority")
                        tool = replacement
                        replacement_done.set()
                    while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                        user.TranslateMessage(ctypes.byref(message))
                        user.DispatchMessageW(ctypes.byref(message))
                    done.wait(.002)
            except BaseException as exc:
                failures.append(exc)
                ready.set()
                replacement_done.set()
            finally:
                if peer:
                    user.UnhookWindowsHookEx(peer)
                if tool:
                    user.UnhookWindowsHookEx(tool)

        worker = threading.Thread(target=hook_worker, daemon=True)
        foreground = user.GetForegroundWindow()
        worker.start()

        def press_private_key():
            inputs = (Input * 2)()
            for index, flags in enumerate((0, 2)):
                inputs[index].type = 1
                inputs[index].ki = KeyboardInput(0x87, 0, flags, 0, token)
            self.assertEqual(user.SendInput(2, inputs, ctypes.sizeof(Input)), 2)

        def wait_for_events(count):
            deadline = time.monotonic() + 3
            while len(received) < count and time.monotonic() < deadline and not failures:
                time.sleep(.005)
            self.assertFalse(failures, repr(failures))
            self.assertGreaterEqual(len(received), count)

        try:
            self.assertTrue(ready.wait(3), "Hook worker did not start")
            self.assertFalse(failures, repr(failures))
            press_private_key()
            wait_for_events(2)
            self.assertEqual([name for name, _ in received], ["peer", "peer"])
            replace_requested.set()
            self.assertTrue(replacement_done.wait(3), "Hook replacement did not finish")
            self.assertFalse(failures, repr(failures))
            press_private_key()
            wait_for_events(4)
            self.assertEqual([name for name, _ in received], ["peer", "peer", "tool", "tool"])
            self.assertEqual(user.GetForegroundWindow(), foreground)
        finally:
            if worker.is_alive() and not failures:
                release = Input(type=1)
                release.ki = KeyboardInput(0x87, 0, 2, 0, token)
                user.SendInput(1, ctypes.byref(release), ctypes.sizeof(Input))
            done.set()
            worker.join(5)
        self.assertFalse(worker.is_alive(), "The isolated hook thread must finish")
        self.assertFalse(failures, repr(failures))


if __name__ == "__main__":
    unittest.main()
