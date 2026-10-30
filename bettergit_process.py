"""Bounded POSIX subprocess probes, including streaming output limits."""
import os
import selectors
import signal
import subprocess
import tempfile
import time

OUTPUT_LIMIT = 4 * 1024 * 1024


def capture(command, cwd=None, timeout=3, env=None, limit=OUTPUT_LIMIT, input_data=None):
    """Enforce time and per-stream byte limits while the child is running.

    No stdout/stderr spool files: a verbose driver cannot exhaust disk before
    the final output-size check. Exit 124 = timeout, 125 = output limit.
    """
    if timeout <= 0:
        return 124, '', 'time budget exhausted'
    if limit < 0:
        raise ValueError('output limit must be nonnegative')
    child_env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    for key in tuple(child_env):
        if key.startswith('GIT_TRACE') or key == 'GIT_CURL_VERBOSE':
            child_env.pop(key)
    if env:
        child_env.update(env)
    with tempfile.TemporaryFile() as source:
        if input_data is not None:
            source.write(input_data.encode() if isinstance(input_data, str) else input_data)
            source.seek(0)
        deadline = time.monotonic() + timeout
        p = subprocess.Popen(command, cwd=cwd, env=child_env,
                             stdin=source if input_data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        output = [bytearray(), bytearray()]
        rc = None
        def stop():
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(p.stdout, selectors.EVENT_READ, 0)
                selector.register(p.stderr, selectors.EVENT_READ, 1)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        rc = 124
                        break
                    if p.poll() is not None:
                        stop()  # clean helpers inheriting pipes after parent exit
                    for key, _ in selector.select(min(.05, remaining)):
                        data = os.read(key.fileobj.fileno(), 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        buffer = output[key.data]
                        space = limit - len(buffer)
                        buffer.extend(data[:space])
                        if len(data) > space:
                            rc = 125
                            break
                    if rc is not None:
                        break
                if rc is None:
                    try:
                        rc = p.wait(timeout=max(.001, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        rc = 124
        finally:
            stop()
            p.wait()
            p.stdout.close()
            p.stderr.close()
        return rc, os.fsdecode(bytes(output[0])), os.fsdecode(bytes(output[1]))
