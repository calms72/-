"""Measurement-only worker wrapper with bounded WIRE1 and MAP1 audit files.

Loads one bounded plain-data profile from cwd/profile.tsv. Original SHADOW1
reply bytes and six NONE motor targets are returned unchanged. Predictions
exist only in the exclusive cwd/mapped_preview.tsv file; no device or motion
interface is added. Any audit failure exits before the corresponding reply.
"""

import math
import os
import sys

import readonly_worker
from recording_worker import WireRecorder
from index_preview_mapping import IndexPreviewMapping

PROFILE_PATH = 'profile.tsv'
PROFILE_MAX_BYTES = 256
MAP_PATH = 'mapped_preview.tsv'
MAP_MAX_BYTES = 1024 * 1024
MAP_MAX_RECORDS = 2000


def load_profile(path=PROFILE_PATH):
    """Read exact INDEX_PREVIEW_V1 + four numeric fields, never executable data."""
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0)
    flags |= getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_mode & 0o170000 != 0o100000:
            raise ValueError('Profile must be a regular data file')
        if metadata.st_size <= 0 or metadata.st_size > PROFILE_MAX_BYTES:
            raise ValueError('Profile length outside fixed bound')
        data = b''
        while len(data) <= PROFILE_MAX_BYTES:
            chunk = os.read(descriptor, PROFILE_MAX_BYTES + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > PROFILE_MAX_BYTES:
            raise ValueError('Profile exceeds fixed bound')
    finally:
        os.close(descriptor)
    if not data.endswith(b'\n') or data.count(b'\n') != 1:
        raise ValueError('Profile must be exactly one LF-terminated line')
    fields = data[:-1].split(b'\t')
    if len(fields) != 5 or fields[0] != b'INDEX_PREVIEW_V1':
        raise ValueError('Profile header or exact field count invalid')
    values = []
    for text in fields[1:]:
        if not text or len(text) > 48 or any(value not in b'+-0123456789.eE' for value in text):
            raise ValueError('Profile fields must be bounded plain numbers')
        value = float(text.decode('ascii'))
        if not math.isfinite(value):
            raise ValueError('Profile values must be finite')
        values.append(value)
    return IndexPreviewMapping(*values)


def _prediction_from_reply(mapping, response, epoch, seq, received):
    if not isinstance(response, bytes) or not response.endswith(b'\n') or response.count(b'\n') != 1:
        raise ValueError('Unexpected calculation reply framing')
    fields = response[:-1].split(b'\t')
    reference = [str(value).encode('ascii') for value in (epoch, seq, received)]
    if len(fields) < 4 or fields[1:4] != reference:
        raise ValueError('Calculation reply reference mismatch')
    if fields[0] == b'REJECT1':
        if len(fields) != 5 or fields[4] != b'FRAME_INVALID':
            raise ValueError('Unexpected source rejection')
        result = mapping.predict(None, valid=False)
        return False, b'NONE', result, 'source_rejected'
    if len(fields) != 8 or fields[0] != b'SHADOW1' or fields[4] != b'1':
        raise ValueError('Unexpected calculation reply')
    mask = readonly_worker.number(fields[5])
    if mask > 31 or fields[7] != b'NONE,NONE,NONE,NONE,NONE,NONE':
        raise ValueError('Calculation reply contains unexpected qualification or target')
    values = fields[6].split(b',')
    if len(values) != 6:
        raise ValueError('Calculation reply must contain six measurements')
    raw_text = values[1]
    raw = None if raw_text == b'NONE' else float(raw_text.decode('ascii'))
    valid = bool(mask & 2)
    if valid and raw is None:
        raise ValueError('Valid index cannot be NONE')
    result = mapping.predict(raw, valid=valid)
    return bool(result['data_valid']), raw_text, result, result['reason']


class MapRecorder:
    def __init__(self):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0)
        self.fd = os.open(MAP_PATH, flags, 0o600)
        self.bytes_written = 0
        self.records_written = 0

    def append(self, epoch, seq, received, valid, raw_text, result, reason):
        if (result.get('preview_only') is not True or result.get('motion_authorized') is not False or
                result.get('motor_node') != 3):
            raise ValueError('Mapping lost preview-only invariant')
        positions = result.get('positions')
        expected = ('thumb_curl', 'index_curl', 'middle_curl', 'ring_curl',
                    'little_curl', 'thumb_spread_diagnostic')
        prediction = result.get('predicted_position')
        if (not isinstance(positions, dict) or len(positions) != 6 or
                any(name not in positions for name in expected) or
                positions['index_curl'] != prediction or
                any(positions[name] is not None for name in expected if name != 'index_curl')):
            raise ValueError('Mapping unexpectedly targets another channel')
        if prediction is not None and not math.isfinite(prediction):
            raise ValueError('Nonfinite prediction cannot be recorded')
        if not valid and prediction is not None:
            raise ValueError('Invalid measurement cannot have prediction')
        if not isinstance(reason, str) or not reason or len(reason) > 96 or any(
                ord(value) < 33 or ord(value) > 126 for value in reason):
            raise ValueError('Invalid mapping reason')
        prediction_text = 'NONE' if prediction is None else format(prediction, '.17g')
        in_range = bool(valid and result['within_visual_preview_window'] and result['within_motor_preview_window'])
        line = ('MAP1\t%d\t%d\t%d\t%d\t%s\t%s\t%d\t%s\n' %
                (epoch, seq, received, int(valid), raw_text.decode('ascii'), prediction_text,
                 int(in_range), reason)).encode('ascii')
        if self.records_written >= MAP_MAX_RECORDS or len(line) > MAP_MAX_BYTES - self.bytes_written:
            raise OSError('Mapped preview log limit reached')
        offset = 0
        while offset < len(line):
            count = os.write(self.fd, line[offset:])
            if count <= 0:
                raise OSError('Mapped preview log write failed')
            offset += count
            self.bytes_written += count
        self.records_written += 1

    def close(self):
        if self.fd is not None:
            descriptor = self.fd
            self.fd = None
            os.close(descriptor)


def main():
    wire, mapped = None, None
    original_calculate = readonly_worker.calculate
    try:
        mapping = load_profile()
        wire = WireRecorder()
        mapped = MapRecorder()

        def mapped_calculate(epoch, seq, received, payload):
            wire.append(epoch, seq, received, payload)
            response = original_calculate(epoch, seq, received, payload)
            valid, raw_text, result, reason = _prediction_from_reply(mapping, response, epoch, seq, received)
            mapped.append(epoch, seq, received, valid, raw_text, result, reason)
            return response

        readonly_worker.calculate = mapped_calculate
        return readonly_worker.main()
    finally:
        readonly_worker.calculate = original_calculate
        try:
            if mapped is not None:
                mapped.close()
        finally:
            if wire is not None:
                wire.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OverflowError, OSError):
        sys.exit(3)
