"""Independent REACH1/WIRE1 audit wrapper; original IPC bytes unchanged.

No profile, mapping, prediction, device, target or control qualification.
Exclusive bounded files in a new cwd: source_capture.tsv and reach_preview.tsv.
REACH1 fields: marker/epoch/seq/rx/kind/raw-or-NONE/valid/prediction-NONE/JSON.
References are C-local receive references, never camera timestamps. Legacy R
validity is preserved in JSON and is not overridden by reach numeric validity.
The last file row is history, not a continuously fresh sample. A live display
requires an alive fault-free C runtime, fresh accepted source time, and the
LATEST REACH1 row's exact (epoch,seq,rx) match plus source_valid. A later REJECT
must suppress an older matching row. Original index.valid stays independent.
"""
import math
import os
import sys

import readonly_worker
from recording_worker import WireRecorder
from index_reach_xy import CHANNELS, KIND, measure_index_reach_xy

REACH_PATH = 'reach_preview.tsv'
REACH_MAX_BYTES = 4 * 1024 * 1024
REACH_MAX_RECORDS = 2000
REACH_MAX_LINE = 4096


def _quality_json(value, depth=0):
    """Bounded local encoder: PLC image has no json standard module.

    Inputs are static-key candidate diagnostics, never source text/code. The
    host tests independently parse every emitted object with strict json.
    """
    if depth > 6:
        raise ValueError('Quality nesting limit')
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('Nonfinite quality diagnostic')
        return format(value, '.17g')
    if type(value) is str:
        if len(value) > 128 or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise ValueError('Quality strings must be bounded printable ASCII')
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    if type(value) in (tuple, list):
        if len(value) > 64:
            raise ValueError('Quality array bound')
        return '[' + ','.join(_quality_json(item, depth+1) for item in value) + ']'
    if type(value) is dict:
        if len(value) > 64 or any(type(key) is not str for key in value):
            raise ValueError('Quality object bound or key type')
        return '{' + ','.join(_quality_json(key, depth+1)+':'+_quality_json(value[key], depth+1)
                              for key in sorted(value)) + '}'
    raise ValueError('Unsupported quality data type')


def measurement_for_reply(response, epoch, seq, received, payload):
    if not isinstance(response, bytes) or not response.endswith(b'\n') or response.count(b'\n') != 1:
        raise ValueError('Original reply framing changed')
    fields = response[:-1].split(b'\t')
    reference = [str(value).encode('ascii') for value in (epoch, seq, received)]
    if len(fields) < 4 or fields[1:4] != reference:
        raise ValueError('Original reply reference changed')
    if fields[0] == b'REJECT1':
        if len(fields) != 5 or fields[4] != b'FRAME_INVALID':
            raise ValueError('Unexpected original rejection')
        result = measure_index_reach_xy(None)
        result.update(reason='source_rejected', source_valid=False, legacy_index_valid=False,
                      legacy_index_raw=None, legacy_reply_kind='REJECT1')
        result['quality_flags'].append('source_rejected')
        return result
    if (len(fields) != 8 or fields[0] != b'SHADOW1' or fields[4] != b'1' or
            fields[7] != b'NONE,NONE,NONE,NONE,NONE,NONE'):
        raise ValueError('Original reply changed or contains targets')
    mask = readonly_worker.number(fields[5])
    values = fields[6].split(b',')
    if mask > 31 or len(values) != 6:
        raise ValueError('Unexpected original measurement scope')
    legacy_raw = None if values[1] == b'NONE' else float(values[1].decode('ascii'))
    if legacy_raw is not None and not math.isfinite(legacy_raw):
        raise ValueError('Nonfinite original measurement')
    if mask & 2 and legacy_raw is None:
        raise ValueError('Valid original index cannot be NONE')
    parser = readonly_worker.VisualFrameParser()
    frames = parser.feed(payload, received)
    if len(frames) != 1 or parser.buffered_bytes or frames[0]['wire_length'] != len(payload):
        raise ValueError('Original accepted source failed unchanged parser')
    result = measure_index_reach_xy(frames[0])
    result.update(source_valid=True, legacy_index_valid=bool(mask & 2),
                  legacy_index_raw=legacy_raw, legacy_reply_kind='SHADOW1')
    return result


class ReachRecorder:
    def __init__(self):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0)
        self.fd = os.open(REACH_PATH, flags, 0o600)
        self.bytes_written = self.records_written = 0

    def append(self, epoch, seq, received, result):
        if (result['kind'] != KIND or result['predicted_position'] is not None or result['motor'] is not None or
                result['motion_authorized'] is not False or result['normal_control_qualified'] is not False or
                set(result['predicted_positions']) != set(CHANNELS) or
                set(result['other_channels']) != set(CHANNELS) - {'index_curl'} or
                any(value is not None for value in result['predicted_positions'].values()) or
                any(value is not None for value in result['other_channels'].values())):
            raise ValueError('Reach measurement lost no-motion invariant')
        raw = result['raw']
        if (raw is not None and (not math.isfinite(raw) or raw < 0)) or (result['valid'] and raw is None):
            raise ValueError('Invalid independent reach numeric value')
        quality = dict(result)
        del quality['raw']
        del quality['valid']
        raw_text = 'NONE' if raw is None else format(raw, '.17g')
        encoded_quality = _quality_json(quality)
        line = ('REACH1\t%d\t%d\t%d\t%s\t%s\t%d\tNONE\t%s\n' %
                (epoch, seq, received, KIND, raw_text, int(result['valid']), encoded_quality)).encode('ascii')
        if (self.records_written >= REACH_MAX_RECORDS or len(line) > REACH_MAX_LINE or
                len(line) > REACH_MAX_BYTES - self.bytes_written):
            raise OSError('Reach audit log limit reached')
        offset = 0
        while offset < len(line):
            count = os.write(self.fd, line[offset:])
            if count <= 0:
                raise OSError('Reach audit write failed')
            offset += count
            self.bytes_written += count
        self.records_written += 1

    def close(self):
        if self.fd is not None:
            descriptor, self.fd = self.fd, None
            os.close(descriptor)


def main():
    wire, reach = None, None
    original_calculate = readonly_worker.calculate
    try:
        wire = WireRecorder()
        reach = ReachRecorder()

        def reach_calculate(epoch, seq, received, payload):
            wire.append(epoch, seq, received, payload)
            response = original_calculate(epoch, seq, received, payload)
            result = measurement_for_reply(response, epoch, seq, received, payload)
            reach.append(epoch, seq, received, result)
            return response

        readonly_worker.calculate = reach_calculate
        return readonly_worker.main()
    finally:
        readonly_worker.calculate = original_calculate
        try:
            if reach is not None:
                reach.close()
        finally:
            if wire is not None:
                wire.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OverflowError, OSError):
        sys.exit(3)
