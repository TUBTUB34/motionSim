"""Run a command on a separate, invisible Windows desktop.

UI tests and the EXE smoke test open real windows and take keyboard focus. On their
own desktop they cannot cover the screen or steal input from the person at the PC:

    py scripts\\run_hidden.py .venv-build\\Scripts\\python -m unittest discover -s tests
    py scripts\\run_hidden.py build_windows.bat

Output goes to this console as usual; the exit code is the command's.
On Linux use a virtual display instead: xvfb-run -a <command>.
"""
import ctypes
from ctypes import wintypes
import subprocess
import sys

DESKTOP = 'URMotionStudioTests'


class STARTUPINFO(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR), ('lpDesktop', wintypes.LPWSTR),
                ('lpTitle', wintypes.LPWSTR), ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
                ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD), ('dwXCountChars', wintypes.DWORD),
                ('dwYCountChars', wintypes.DWORD), ('dwFillAttribute', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD), ('lpReserved2', ctypes.c_void_p),
                ('hStdInput', wintypes.HANDLE), ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE),
                ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]


def run(command):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    desktop = user32.CreateDesktopW(DESKTOP, None, None, 0, 0x10000000, None)  # GENERIC_ALL
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        startup = STARTUPINFO(cb=ctypes.sizeof(STARTUPINFO), lpDesktop=DESKTOP, dwFlags=0x100,  # USESTDHANDLES
                              hStdInput=kernel32.GetStdHandle(-10), hStdOutput=kernel32.GetStdHandle(-11),
                              hStdError=kernel32.GetStdHandle(-12))
        process = PROCESS_INFORMATION()
        line = ctypes.create_unicode_buffer('cmd.exe /c ' + subprocess.list2cmdline(command))
        if not kernel32.CreateProcessW(None, line, None, None, True, 0, None, None,
                                       ctypes.byref(startup), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.WaitForSingleObject(process.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
        kernel32.CloseHandle(process.hThread)
        kernel32.CloseHandle(process.hProcess)
        return code.value
    finally:
        user32.CloseDesktop(desktop)


if __name__ == '__main__':
    if sys.platform != 'win32' or len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(run(sys.argv[1:]))
