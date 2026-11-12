"""Measure logical sizes, Windows allocated file storage and net workspace growth."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[2]


def measure(root=ROOT):
    root = Path(root).resolve()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True) if os.name == 'nt' else None
    if kernel:
        class StandardInfo(ctypes.Structure):
            _fields_ = [('allocation', ctypes.c_longlong), ('end', ctypes.c_longlong), ('links', wintypes.DWORD),
                        ('delete_pending', ctypes.c_ubyte), ('directory', ctypes.c_ubyte)]
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    groups = {}; skipped = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink() and not getattr((Path(directory) / name).stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400]
        for name in names:
            path = Path(directory) / name
            if path.is_symlink(): skipped += 1; continue
            try:
                size = path.stat().st_size
                relative = path.relative_to(root)
                key = relative.parts[0] if len(relative.parts) > 1 else '(root files)'
                group = groups.setdefault(key, {'files': 0, 'logical_bytes': 0, 'allocated_bytes': 0, 'allocation_unavailable': 0})
                group['files'] += 1; group['logical_bytes'] += size
                if kernel:
                    handle = kernel.CreateFileW(str(path), 0, 7, None, 3, 0x80, None)
                    if handle in (None, ctypes.c_void_p(-1).value):
                        group['allocation_unavailable'] += 1
                    else:
                        try:
                            info = StandardInfo()
                            if kernel.GetFileInformationByHandleEx(handle, 1, ctypes.byref(info), ctypes.sizeof(info)):
                                group['allocated_bytes'] += info.allocation
                            else:
                                group['allocation_unavailable'] += 1
                        finally:
                            kernel.CloseHandle(handle)
                else:
                    blocks = getattr(path.stat(), 'st_blocks', None)
                    if blocks is None: group['allocation_unavailable'] += 1
                    else: group['allocated_bytes'] += blocks * 512
            except OSError:
                skipped += 1
    inventory = root / 'work/inventory/files.json'
    initial = json.loads(inventory.read_text('utf-8')) if inventory.exists() else []
    initial_applications = sum(item['bytes'] for item in initial if item['owner'] in ('home', 'french'))
    initial_plan = (root / 'Bot_Control_Center_Final_Codex_Plan.md').stat().st_size if (root / 'Bot_Control_Center_Final_Codex_Plan.md').exists() else 0
    current = sum(group['logical_bytes'] for group in groups.values())
    report = {'schema': 1, 'created': time.time(), 'status': 'Passed', 'root': str(root), 'groups': groups,
              'initial_application_and_plan_logical_bytes': initial_applications + initial_plan,
              'current_root_logical_bytes': current, 'net_workspace_logical_growth_bytes': current - initial_applications - initial_plan,
              'current_allocated_file_bytes': sum(group['allocated_bytes'] for group in groups.values()),
              'allocation_unavailable_files': sum(group['allocation_unavailable'] for group in groups.values()),
              'links_or_concurrent_missing_files_skipped': skipped, 'volume_free_bytes': shutil.disk_usage(root).free,
              'measurement_scope': 'Workspace root including new environments, backups, logs and work; initial external AppData is outside this root',
              'allocation_scope': 'GetFileInformationByHandleEx FileStandardInfo.AllocationSize; filesystem metadata is not attributed per file and hard-linked streams may be counted per pathname',
              'active_writer_caveat': 'Point-in-time traversal; unrelated/concurrent writers can change sizes and volume free space.'}
    output = root / 'docs/cleanup-measurements.json'
    output.write_text(json.dumps(report, indent=2, sort_keys=True), 'utf-8')
    return {key: report[key] for key in ('status', 'current_root_logical_bytes', 'current_allocated_file_bytes', 'net_workspace_logical_growth_bytes', 'volume_free_bytes')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--root', type=Path, default=ROOT); args = parser.parse_args()
    print(json.dumps(measure(args.root)))
