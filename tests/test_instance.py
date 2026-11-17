import ctypes
import os
import unittest
from unittest.mock import Mock, patch
import uuid

from odx_instance import SingleInstance


class InstanceTests(unittest.TestCase):
    def test_existing_gui_lock_is_not_adopted(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 456
        with patch.object(ctypes, "get_last_error", return_value=183):
            guard = SingleInstance(kernel=kernel)
            self.assertFalse(guard.acquire())
        self.assertIsNone(guard.handle)
        kernel.CloseHandle.assert_called_once_with(456)

    def test_success_acquires_once_and_release_is_idempotent(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 123
        with patch.object(ctypes, "get_last_error", return_value=0):
            guard = SingleInstance(kernel=kernel)
            self.assertTrue(guard.acquire())
            self.assertTrue(guard.acquire())
        kernel.CreateMutexW.assert_called_once()
        guard.release()
        guard.release()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_os_failure_is_explicit(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 0
        with patch.object(ctypes, "get_last_error", return_value=5), self.assertRaises(OSError):
            SingleInstance(kernel=kernel).acquire()

    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_native_lock_blocks_second_and_allows_after_release(self):
        name = "Local\\OdxAutoPilotTest-" + uuid.uuid4().hex
        first, second = SingleInstance(name), SingleInstance(name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
        finally:
            first.release()
            second.release()


if __name__ == "__main__":
    unittest.main()
