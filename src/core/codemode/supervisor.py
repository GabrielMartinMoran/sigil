"""Trusted Linux helper: own the delegated payload cgroup until host acknowledgement.

Invoked as an isolated Python script by the sandbox runner, never by user code.
"""

from __future__ import annotations

import json
import os
import select
import signal
import socket
import sys
import time
from pathlib import Path


def wait_empty(fd: int) -> None:
    watcher = select.poll()
    watcher.register(fd, select.POLLPRI | select.POLLERR)
    deadline = time.monotonic() + 5
    while True:
        state = dict(line.split() for line in os.pread(fd, 4096, 0).decode('ascii').splitlines())
        if state.get('populated') == '0':
            return
        if state.get('populated') != '1':
            raise RuntimeError('Invalid payload cgroup state')
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not watcher.poll(max(1, int(remaining * 1000))):
            raise RuntimeError('Payload cgroup did not drain')


def main() -> int:
    control_fd, broker_fd = map(int, sys.argv[1:3])
    command = sys.argv[3:]
    control = socket.socket(fileno=control_fd)
    control.settimeout(10)
    membership = Path('/proc/self/cgroup').read_text().strip()
    if not membership.startswith('0::/'):
        raise RuntimeError('Unified cgroup v2 required')
    root = Path('/sys/fs/cgroup') / membership[4:]
    memory = int((root / 'memory.max').read_text())
    tasks = int((root / 'pids.max').read_text())
    quota, period = (root / 'cpu.max').read_text().split()
    if memory > 536870912 or tasks > 32 or quota == 'max' or int(quota) > int(period):
        raise RuntimeError('Aggregate resource limits not enforced')
    payload = root / 'payload'
    payload.mkdir()
    pid: int | None = None
    events: int | None = None
    kill_fd: int | None = None
    pid_fd: int | None = None
    try:
        events = os.open(payload / 'cgroup.events', os.O_RDONLY | os.O_CLOEXEC)
        kill_fd = os.open(payload / 'cgroup.kill', os.O_WRONLY | os.O_CLOEXEC)
        pid = os.fork()
        if pid == 0:
            try:
                control.close()
                os.close(events)
                os.close(kill_fd)
                os.kill(os.getpid(), signal.SIGSTOP)
                os.execv(command[0], command)
            finally:
                os._exit(125)
        _, status = os.waitpid(pid, os.WUNTRACED)
        if not os.WIFSTOPPED(status):
            raise RuntimeError('Payload failed to stop before placement')
        (payload / 'cgroup.procs').write_text(str(pid))
        expected = f'{membership[3:]}/payload'
        if Path(f'/proc/{pid}/cgroup').read_text().strip() != f'0::{expected}':
            raise RuntimeError('Cannot verify payload placement')
        if 'populated 1' not in os.pread(events, 4096, 0).decode('ascii'):
            raise RuntimeError('Payload not accounted for')
        pid_fd = os.pidfd_open(pid)
        if broker_fd >= 0:
            os.close(broker_fd)
        # The host owns these pipes; the supervisor must not keep them open.
        for fd in (0, 1, 2):
            os.close(fd)
        control.sendall(json.dumps({'ready': expected, 'pid': os.getpid()}).encode() + b'\n')
        # Do not execute the payload until the host pins and validates its events fd.
        if control.recv(1) != b'G':
            raise RuntimeError('Host disconnected before payload start')
        os.kill(pid, signal.SIGCONT)
        watcher = select.poll()
        watcher.register(control, select.POLLIN | select.POLLHUP | select.POLLERR)
        watcher.register(pid_fd, select.POLLIN)
        while True:
            notifications = watcher.poll(130000)
            if not notifications:
                raise RuntimeError('Supervisor lifetime exceeded')
            if any(fd == control.fileno() for fd, _ in notifications):
                # Stop request or host death: kill the entire payload subtree.
                control.recv(1)
                os.write(kill_fd, b'1')
                break
            if any(fd == pid_fd for fd, _ in notifications):
                break
        os.write(kill_fd, b'1')
        wait_empty(events)
        _, status = os.waitpid(pid, 0)
        pid = None
        control.sendall(json.dumps({'done': os.waitstatus_to_exitcode(status)}).encode() + b'\n')
        while True:
            acknowledgement = control.recv(1)
            if acknowledgement == b'A':
                break
            if acknowledgement != b'K':
                raise RuntimeError('Host disconnected before cleanup acknowledgement')
        return 0
    finally:
        # A setup failure can leave the stopped child outside the payload group.
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # Always kill the payload, including descendants in other sessions.
        if kill_fd is not None:
            os.write(kill_fd, b'1')
        if events is not None:
            wait_empty(events)
        if pid is not None:
            os.waitpid(pid, 0)
        if pid_fd is not None:
            os.close(pid_fd)
        if kill_fd is not None:
            os.close(kill_fd)
        if events is not None:
            os.close(events)
        payload.rmdir()
        control.close()


if __name__ == '__main__':
    sys.exit(main())
