"""Read Windows fullscreen/presentation state without inspecting window contents.

Win32 references: GetForegroundWindow, GetWindowRect, GetMonitorInfoW,
and SHQueryUserNotificationState on learn.microsoft.com.
"""
import ctypes
from ctypes import wintypes
import os


def covers_monitor(window, monitor, tolerance=2):
    return (window.left<=monitor.left+tolerance and window.top<=monitor.top+tolerance and
            window.right>=monitor.right-tolerance and window.bottom>=monitor.bottom-tolerance)


class WindowsFocus:
    class MonitorInfo(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('monitor',wintypes.RECT),('work',wintypes.RECT),('flags',wintypes.DWORD)]

    def __init__(self):
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.shell=ctypes.WinDLL('shell32',use_last_error=True)
        self.user.GetForegroundWindow.restype=wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        self.user.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        self.user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
        self.user.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD]
        self.user.MonitorFromWindow.restype=wintypes.HANDLE
        self.user.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(self.MonitorInfo)]
        self.shell.SHQueryUserNotificationState.argtypes=[ctypes.POINTER(ctypes.c_int)]
        self.shell.SHQueryUserNotificationState.restype=ctypes.c_long

    def blocked(self):
        state=ctypes.c_int()
        # Busy, locked, Direct3D fullscreen, presentation, and Windows quiet time.
        if self.shell.SHQueryUserNotificationState(ctypes.byref(state))==0 and state.value!=5:
            return True
        hwnd=self.user.GetForegroundWindow()
        if not hwnd:return False
        pid=wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value==os.getpid():return False
        name=ctypes.create_unicode_buffer(128)
        self.user.GetClassNameW(hwnd,name,len(name))
        if name.value in {'Progman','WorkerW','Shell_TrayWnd'}:return False
        rect=wintypes.RECT()
        monitor=self.MonitorInfo();monitor.size=ctypes.sizeof(monitor)
        handle=self.user.MonitorFromWindow(hwnd,2)
        if not self.user.GetWindowRect(hwnd,ctypes.byref(rect)) or not self.user.GetMonitorInfoW(handle,ctypes.byref(monitor)):
            return False
        return covers_monitor(rect,monitor.monitor)


_reader=None


def fullscreen_active():
    global _reader
    if os.name!='nt':return False
    try:
        if _reader is None:_reader=WindowsFocus()
        return _reader.blocked()
    except (OSError,AttributeError):
        return False
