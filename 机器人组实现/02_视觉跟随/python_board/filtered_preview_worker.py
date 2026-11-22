"""Read-only IPC wrapper: original WIRE/MAP plus a quality-filter sidecar.

Original calculate/SHADOW1 bytes and six NONE targets remain unchanged. Loads
bounded plain profiles and exclusively creates bounded local audit files; no
device or motor API exists. FILTER1 rows describe source-processing instants,
not a live permit during later silence. Consumers must gate historical rows by
the C runtime's current valid exact reference and the accepted-age deadline.
"""

import math
import os
import sys
import time

import readonly_worker
import mapped_preview_worker as mapped
from recording_worker import WireRecorder
from index_preview_filter import IndexPreviewFilter

FILTER_PROFILE_PATH = 'filter_profile.tsv'
FILTER_PROFILE_MAX_BYTES = 256
FILTER_PATH = 'filtered_preview.tsv'
FILTER_MAX_BYTES = 1024 * 1024
FILTER_MAX_RECORDS = 2000

FILTER_FIELDS = ('epoch', 'seq', 'rxns', 'source_valid', 'raw', 'filter_valid',
                 'filtered', 'prediction', 'out_of_window', 'reason', 'state_event',
                 'last_accepted_ns', 'now_ns', 'accepted_age_ns',
                 'warmup_accepted_samples', 'warmup_required', 'accepted_count',
                 'jump_rejected_count', 'bad_data_count', 'clock_rejected_count',
                 'stale_count', 'reset_count', 'prediction_reason',
                 'gate_allowed_raw_delta', 'motion_authorized')


def load_filter_profile(path=FILTER_PROFILE_PATH):
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0)
    flags |= getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (info.st_mode & 0o170000) != 0o100000 or not 0 < info.st_size <= FILTER_PROFILE_MAX_BYTES:
            raise ValueError('Filter profile must be a bounded regular data file')
        data = b''
        while len(data) <= FILTER_PROFILE_MAX_BYTES:
            chunk = os.read(descriptor, FILTER_PROFILE_MAX_BYTES + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > FILTER_PROFILE_MAX_BYTES:
            raise ValueError('Filter profile exceeds fixed bound')
    finally:
        os.close(descriptor)
    if not data.endswith(b'\n') or data.count(b'\n') != 1:
        raise ValueError('Filter profile must be one LF-terminated line')
    fields = data[:-1].split(b'\t')
    if len(fields) != 7 or fields[0] != b'FILTER_PREVIEW_V1' or fields[1] not in (b'3', b'5'):
        raise ValueError('Invalid filter profile header/field count/median width')
    values = []
    for text in fields[2:5]:
        if not text or len(text) > 48 or any(value not in b'+-0123456789.eE' for value in text):
            raise ValueError('Filter profile requires bounded plain numeric fields')
        number = float(text.decode('ascii'))
        if not math.isfinite(number):
            raise ValueError('Filter profile values must be finite')
        values.append(number)
    return IndexPreviewFilter(median_window=int(fields[1]), ema_tau_s=values[0],
        max_raw_rate=values[1], absolute_tolerance=values[2],
        max_gap_ns=readonly_worker.number(fields[5], True),
        max_age_ns=readonly_worker.number(fields[6], True))


def _text(value):
    if value is None:
        return 'NONE'
    if type(value) is int:
        return str(value)
    if type(value) is float and math.isfinite(value):
        return format(value, '.17g')
    if isinstance(value, str) and 0 < len(value) <= 96 and all(33 <= ord(char) <= 126 for char in value):
        return value
    raise ValueError('Unsupported filter audit field')


class FilterRecorder:
    def __init__(self):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0)
        self.fd = os.open(FILTER_PATH, flags, 0o600)
        self.bytes_written = self.records_written = 0

    def append(self, epoch, seq, received, source_valid, raw_text, quality, prediction, now):
        if quality.get('feature_quality_only') is not True or quality.get('motion_authorized') is not False:
            raise ValueError('Quality filter lost no-motion invariant')
        good = quality.get('valid') is True
        filtered = quality.get('filtered')
        if good != (filtered is not None) or good != bool(prediction.get('data_valid')):
            raise ValueError('Invalid quality result cannot supply a prediction')
        if (prediction.get('preview_only') is not True or prediction.get('motion_authorized') is not False or
                prediction.get('motor_node') != 3):
            raise ValueError('Filtered mapping lost preview-only invariant')
        positions = prediction.get('positions')
        names = ('thumb_curl', 'index_curl', 'middle_curl', 'ring_curl',
                 'little_curl', 'thumb_spread_diagnostic')
        if (not isinstance(positions, dict) or len(positions) != 6 or
                any(name not in positions for name in names) or
                positions.get('index_curl') != prediction.get('predicted_position') or
                any(positions.get(name) is not None for name in (
                    'thumb_curl', 'middle_curl', 'ring_curl', 'little_curl', 'thumb_spread_diagnostic'))):
            raise ValueError('Filtered mapping contains unexpected other-channel target')
        if not good and prediction.get('predicted_position') is not None:
            raise ValueError('Invalid filter cannot retain old prediction')
        values = (epoch, seq, received, int(source_valid), raw_text.decode('ascii'), int(good),
                  filtered, prediction.get('predicted_position'),
                  int(prediction['out_of_window']) if good else None,
                  quality['reason'], quality['state_event'], quality['last_accepted_ns'],
                  now, quality['accepted_age_ns'], quality['warmup_accepted_samples'],
                  quality['warmup_required'], quality['accepted_count'],
                  quality['jump_rejected_count'], quality['bad_data_count'],
                  quality['clock_rejected_count'], quality['stale_count'],
                  quality['reset_count'], prediction['reason'],
                  quality['gate_allowed_raw_delta'], 0)
        line = ('FILTER1\t' + '\t'.join(_text(value) for value in values) + '\n').encode('ascii')
        if self.records_written >= FILTER_MAX_RECORDS or len(line) > FILTER_MAX_BYTES - self.bytes_written:
            raise OSError('Filtered preview audit limit reached')
        offset = 0
        while offset < len(line):
            count = os.write(self.fd, line[offset:])
            if count <= 0:
                raise OSError('Filtered preview audit write failed')
            offset += count
            self.bytes_written += count
        self.records_written += 1

    def close(self):
        if self.fd is not None:
            descriptor = self.fd
            self.fd = None
            os.close(descriptor)


def main():
    wire, raw_map, filtered_log = None, None, None
    original_calculate = readonly_worker.calculate
    try:
        mapping = mapped.load_profile()
        filter_ = load_filter_profile()
        wire = WireRecorder()
        raw_map = mapped.MapRecorder()
        filtered_log = FilterRecorder()

        def filtered_calculate(epoch, seq, received, payload):
            wire.append(epoch, seq, received, payload)
            response = original_calculate(epoch, seq, received, payload)
            valid, raw_text, raw_prediction, source_reason = mapped._prediction_from_reply(
                mapping, response, epoch, seq, received)
            raw_map.append(epoch, seq, received, valid, raw_text, raw_prediction, source_reason)
            raw = None if raw_text == b'NONE' else float(raw_text.decode('ascii'))
            now = time.monotonic_ns()
            quality = filter_.update(raw, received, valid=valid, now_ns=now)
            filtered_prediction = mapping.predict(quality['filtered'], valid=quality['valid'])
            filtered_log.append(epoch, seq, received, valid, raw_text, quality, filtered_prediction, now)
            return response

        readonly_worker.calculate = filtered_calculate
        return readonly_worker.main()
    finally:
        readonly_worker.calculate = original_calculate
        try:
            if filtered_log is not None:
                filtered_log.close()
        finally:
            try:
                if raw_map is not None:
                    raw_map.close()
            finally:
                if wire is not None:
                    wire.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OverflowError, OSError):
        sys.exit(3)
