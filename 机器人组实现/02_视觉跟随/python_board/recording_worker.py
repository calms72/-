"""Bounded source recording wrapper for the unchanged read-only worker.

Only requests actually reaching calculate are recorded. WIRE1 references use
C-local epoch/sequence/receive-complete time, never camera image timestamps.
The exclusive cwd/source_capture.tsv file is never resumed or overwritten.
Capture failures terminate this worker; the existing C runtime then latches.
No device, motion, configuration or recovery interface is added.
"""
import os
import sys
import readonly_worker

CAPTURE_PATH = 'source_capture.tsv'
MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 2000


class WireRecorder:
    def __init__(self):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0)
        self.fd = os.open(CAPTURE_PATH, flags, 0o600)
        self.bytes_written = 0
        self.records_written = 0

    def append(self, epoch, seq, received, payload):
        line = ('WIRE1\t%d\t%d\t%d\t%s\n' %
                (epoch, seq, received, payload.hex())).encode('ascii')
        if (self.records_written >= MAX_RECORDS or
                len(line) > MAX_BYTES - self.bytes_written):
            raise OSError('Source capture limit reached')
        offset = 0
        while offset < len(line):
            count = os.write(self.fd, line[offset:])
            if count <= 0:
                raise OSError('Source capture write failed')
            offset += count
            self.bytes_written += count
        self.records_written += 1

    def close(self):
        if self.fd is not None:
            descriptor = self.fd
            self.fd = None
            os.close(descriptor)


def main():
    recorder = None
    original_calculate = readonly_worker.calculate
    try:
        recorder = WireRecorder()

        def recorded_calculate(epoch, seq, received, payload):
            recorder.append(epoch, seq, received, payload)
            return original_calculate(epoch, seq, received, payload)

        readonly_worker.calculate = recorded_calculate
        return readonly_worker.main()
    finally:
        readonly_worker.calculate = original_calculate
        if recorder is not None:
            recorder.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OverflowError, OSError):
        sys.exit(3)
