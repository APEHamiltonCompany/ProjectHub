from __future__ import annotations

import sys
import threading
import time

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

GW_OWNER = 4
SW_RESTORE = 9
SW_SHOW = 5
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE_NOMOVE_SHOW = 0x0001 | 0x0002 | 0x0040
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def top_windows() -> dict[int, int]:
    found: dict[int, int] = {}
    if sys.platform != "win32":
        return found

    def collect(hwnd, _):
        if (
            user32.IsWindowVisible(hwnd)
            and not user32.GetWindow(hwnd, GW_OWNER)
            and user32.GetWindowTextLengthW(hwnd) > 0
        ):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found[int(hwnd)] = int(pid.value)
        return True

    user32.EnumWindows(WNDENUMPROC(collect), 0)
    return found


def process_tree(root_pid: int) -> set[int]:
    pids = {int(root_pid)}
    if sys.platform != "win32" or not root_pid:
        return pids
    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return pids
    parents: list[tuple[int, int]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    grew = True
    while grew:
        grew = False
        for pid, ppid in parents:
            if ppid in pids and pid not in pids:
                pids.add(pid)
                grew = True
    return pids


def force_foreground(hwnd: int) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
    try:
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE_NOMOVE_SHOW)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE_NOMOVE_SHOW)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def find_launched_window(before: dict[int, int], pid: int | None, timeout: float = 15.0) -> int | None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        now = top_windows()
        new = [h for h in now if h not in before]
        if pid:
            tree = process_tree(pid)
            own = [h for h in now if now[h] in tree]
            if own:
                return own[0]
        if new and (not pid or time.monotonic() - start > 5):
            return new[0]
        time.sleep(0.25)
    return None


def raise_after_launch(before: dict[int, int], pid: int | None = None, timeout: float = 15.0) -> threading.Thread:
    def run():
        try:
            hwnd = find_launched_window(before, pid, timeout)
            if not hwnd:
                return
            force_foreground(hwnd)
            for _ in range(4):
                time.sleep(0.5)
                latest = find_launched_window(before, pid, 1.0) or hwnd
                if latest != hwnd or int(user32.GetForegroundWindow() or 0) != int(hwnd):
                    hwnd = latest
                    force_foreground(hwnd)
        except Exception:
            pass

    thread = threading.Thread(target=run, name="launch-focus", daemon=True)
    thread.start()
    return thread
