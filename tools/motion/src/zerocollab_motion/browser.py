"""Private, fresh-profile rendering browsers; no app or room dependency."""
import asyncio
import os
import shutil
import subprocess
from pathlib import Path
WINDOWS_CREATE_SUSPENDED = 4
WINDOWS_JOB_LIMIT_PRIORITY_CLASS = 0x20
WINDOWS_JOB_LIMIT_KILL_ON_CLOSE = 0x2000

class WindowsBrowserProcess:
    """A suspended launch assigned to a kill-on-close job before it can create children."""
    def __init__(self, args, *, priority_class=0, env=None, cwd=None):
        if priority_class not in (0, subprocess.BELOW_NORMAL_PRIORITY_CLASS):
            raise ValueError('Windows worker priority must be default or below normal')
        import ctypes
        from ctypes import wintypes as w
        import _winapi
        self.api = _winapi
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', w.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active_processes', w.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.ResumeThread.argtypes = [w.HANDLE]
        self.kernel.ResumeThread.restype = w.DWORD
        self.job = self.kernel.CreateJobObjectW(None, None)
        self.handle = None
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.basic.flags = WINDOWS_JOB_LIMIT_KILL_ON_CLOSE
        if priority_class:
            limits.basic.flags |= WINDOWS_JOB_LIMIT_PRIORITY_CLASS
            limits.basic.priority = priority_class
        thread = None
        try:
            if not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle, thread, self.pid, _ = _winapi.CreateProcess(
                None, subprocess.list2cmdline(args), None, None, False,
                subprocess.CREATE_NO_WINDOW | WINDOWS_CREATE_SUSPENDED | priority_class,
                env, cwd, subprocess.STARTUPINFO())
            if not self.kernel.AssignProcessToJobObject(self.job, self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            if self.handle:
                _winapi.TerminateProcess(self.handle, 1)
            self.close_handles()
            raise
        finally:
            if thread:
                _winapi.CloseHandle(thread)

    @property
    def returncode(self):
        code = self.api.GetExitCodeProcess(self.handle)
        return None if code == 259 else code  # STILL_ACTIVE

    def terminate(self):
        self.api.TerminateProcess(self.handle, 1)

    kill = terminate

    async def wait(self):
        await asyncio.to_thread(self.api.WaitForSingleObject, self.handle, 0xFFFFFFFF)
        return self.returncode

    def close_handles(self):
        if self.job:
            self.kernel.CloseHandle(self.job)  # Also terminates all surviving children.
            self.job = None
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

def executable():
    configured = os.environ.get('ZEROCOLLAB_MOTION_BROWSER')
    if configured:
        if not Path(configured).is_file():
            raise ValueError('The configured motion browser does not exist')
        return configured
    candidates = [shutil.which(name) for name in ('msedge', 'google-chrome', 'chromium', 'chromium-browser')]
    if os.name == 'nt':
        candidates += [str(Path(root) / suffix) for root in
                       (os.environ.get('PROGRAMFILES(X86)', ''), os.environ.get('PROGRAMFILES', ''))
                       for suffix in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe')]
    elif os.uname().sysname == 'Darwin':
        candidates += ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                       '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    return next((p for p in candidates if p and Path(p).is_file()), None)
