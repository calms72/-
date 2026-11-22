"""Offline IPC/profile/filter-sidecar tests; synthetic poses have no motor meaning."""
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import readonly_worker
import filtered_preview_worker as worker
from index_preview_mapping import IndexPreviewMapping
from index_preview_filter import IndexPreviewFilter

PROFILE = b'INDEX_PREVIEW_V1\t0.003\t0.53\t0\t128\n'
FILTER_PROFILE = b'FILTER_PREVIEW_V1\t3\t0.03\t3\t0.02\t150000000\t150000000\n'


def frame(bend=False):
    points = [[float(i), 0., 0.] for i in range(21)]
    if bend:
        for i, point in zip(range(5, 9), ((5, 0), (6, 0), (6, 1), (5, 1))):
            points[i][:2] = point
    payload = (',' + ','.join(format(v, '.2f') for point in points for v in point) + ',').encode()
    return b'\r' + ('%03d' % (len(payload) - 1)).encode() + payload + b'\n'


def request(seq, rx, payload=None):
    payload = frame() if payload is None else payload
    return ('SHADOW1\t101\t%d\t%d\t%s\n' % (seq, rx, payload.hex())).encode()


def profiles(path, filter_data=FILTER_PROFILE):
    (path / 'profile.tsv').write_bytes(PROFILE)
    (path / 'filter_profile.tsv').write_bytes(filter_data)


def run(path, data, now=None, kind='filtered', injection=''):
    module = {'filtered': 'filtered_preview_worker', 'mapped': 'mapped_preview_worker', 'original': 'readonly_worker'}[kind]
    code = 'import sys;sys.path.insert(0,%r);import %s as w;sys.argv=[%r];' % (str(ROOT), module, module + '.py')
    if now is not None:
        code += 'clock=iter(%r);w.time.monotonic_ns=lambda:next(clock);' % list(now)
    code += injection + '\ntry: sys.exit(w.main())\nexcept (ValueError,OverflowError,OSError): sys.exit(3)'
    return subprocess.run([sys.executable, '-B', '-c', code], input=data, cwd=path,
                          capture_output=True, timeout=5)


def filter_rows(path):
    rows = []
    for line in (path / 'filtered_preview.tsv').read_text(encoding='ascii').splitlines():
        fields = line.split('\t')
        assert fields[0] == 'FILTER1' and len(fields) == len(worker.FILTER_FIELDS) + 1
        rows.append(dict(zip(worker.FILTER_FIELDS, fields[1:])))
    return rows


@contextlib.contextmanager
def cwd(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class FilteredWorkerTests(unittest.TestCase):
    def test_original_ipc_raw_wire_and_raw_map_unchanged(self):
        times = [1_000_000_000 + i * 33_000_000 for i in range(4)]
        data = b''.join(request(i + 1, rx) for i, rx in enumerate(times))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            mapped_path, filtered_path = base / 'mapped', base / 'filtered'
            mapped_path.mkdir(); filtered_path.mkdir()
            profiles(mapped_path); profiles(filtered_path)
            original = run(base, data, kind='original')
            raw_mapped = run(mapped_path, data, kind='mapped')
            filtered = run(filtered_path, data, now=[rx + 10_000_000 for rx in times])
            self.assertEqual((filtered.returncode, filtered.stdout, filtered.stderr),
                             (original.returncode, original.stdout, original.stderr))
            self.assertEqual(filtered.stdout, raw_mapped.stdout)
            for name in ('source_capture.tsv', 'mapped_preview.tsv'):
                self.assertEqual((mapped_path / name).read_bytes(), (filtered_path / name).read_bytes())
            rows = filter_rows(filtered_path)
            self.assertEqual([row['filter_valid'] for row in rows], ['0', '0', '1', '1'])
            self.assertTrue(all(row['motion_authorized'] == '0' for row in rows))
            for row, source, map_line, reply in zip(rows,
                    (filtered_path / 'source_capture.tsv').read_text().splitlines(),
                    (filtered_path / 'mapped_preview.tsv').read_text().splitlines(),
                    filtered.stdout.decode().splitlines()[1:]):
                reference = [row['epoch'], row['seq'], row['rxns']]
                self.assertEqual(reference, source.split('\t')[1:4])
                self.assertEqual(reference, map_line.split('\t')[1:4])
                self.assertEqual(reference, reply.split('\t')[1:4])
                self.assertEqual(reply.split('\t')[-1], 'NONE,NONE,NONE,NONE,NONE,NONE')

    def test_jump_rejection_clears_prediction_and_requires_three_new_accepts(self):
        times = [1_000_000_000 + i * 33_000_000 for i in range(7)]
        data = b''.join(request(i + 1, rx, frame(bend=i == 3)) for i, rx in enumerate(times))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); profiles(path)
            result = run(path, data, now=[rx + 10_000_000 for rx in times])
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = filter_rows(path)
            self.assertEqual(rows[3]['reason'], 'jump_rejected')
            self.assertEqual(rows[3]['last_accepted_ns'], str(times[2]))
            self.assertEqual([row['filter_valid'] for row in rows[3:]], ['0', '0', '0', '1'])
            for row in rows[3:6]:
                self.assertEqual((row['filtered'], row['prediction'], row['out_of_window']), ('NONE', 'NONE', 'NONE'))
            self.assertGreater(float((path / 'mapped_preview.tsv').read_text().splitlines()[3].split('\t')[6]), 128)

    def test_old_source_and_equal_rx_never_retain_old_prediction(self):
        for stale in (True, False):
            times = [1_000_000_000, 1_033_000_000, 1_066_000_000, 1_066_000_000]
            data = b''.join(request(i + 1, rx) for i, rx in enumerate(times))
            now = [rx + (150_000_000 if stale else 10_000_000) for rx in times]
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as directory:
                path = Path(directory); profiles(path)
                result = run(path, data, now=now)
                self.assertEqual(result.returncode, 0, result.stderr)
                rows = filter_rows(path)
                self.assertEqual(rows[-1]['reason'], 'source_time_not_increasing')
                self.assertEqual(rows[-1]['prediction'], 'NONE')
                if stale:
                    self.assertTrue(all(row['prediction'] == 'NONE' for row in rows))
                    self.assertEqual(rows[0]['reason'], 'source_stale')

    def test_source_reject_records_invalid_and_original_reject_bytes(self):
        bad = frame().replace(b'0.', b'X.', 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); profiles(path)
            result = run(path, request(1, 1000, bad), now=[1001])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'REJECT1\t101\t1\t1000\tFRAME_INVALID\n', result.stdout)
            row = filter_rows(path)[0]
            self.assertEqual((row['source_valid'], row['raw'], row['prediction']), ('0', 'NONE', 'NONE'))
            self.assertEqual(row['reason'], 'invalid_measurement')

    def test_profile_invalid_or_missing_fails_before_ready_and_logs(self):
        invalid = [b'', FILTER_PROFILE[:-1], b'x' * 257,
            FILTER_PROFILE + b'\n', FILTER_PROFILE.replace(b'FILTER_PREVIEW_V1', b'OLD'),
            FILTER_PROFILE.replace(b'\t3\t0.03', b'\t1\t0.03'),
            FILTER_PROFILE.replace(b'\t0.03\t', b'\t0\t'),
            FILTER_PROFILE.replace(b'\t0.03\t', b'\tnan\t'),
            FILTER_PROFILE.replace(b'\t0.02\t', b'\t-0.02\t'),
            FILTER_PROFILE.replace(b'\t150000000\n', b'\t0\n'),
            FILTER_PROFILE.replace(b'\t0.03\t', b'\t0;import os\t')]
        for data in invalid:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                path = Path(directory); profiles(path, data)
                result = run(path, request(1, 1000))
                self.assertEqual((result.returncode, result.stdout), (3, b''))
                self.assertFalse((path / 'source_capture.tsv').exists())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); (path / 'profile.tsv').write_bytes(PROFILE)
            self.assertEqual(run(path, request(1, 1000)).stdout, b'')

    def test_all_three_audit_files_exclusive_and_preserve_old_bytes(self):
        for name in ('source_capture.tsv', 'mapped_preview.tsv', 'filtered_preview.tsv'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory); profiles(path)
                target = path / name; target.write_bytes(b'OLD EVIDENCE\n')
                result = run(path, request(1, 1000))
                self.assertEqual((result.returncode, result.stdout), (3, b''))
                self.assertEqual(target.read_bytes(), b'OLD EVIDENCE\n')

    def test_filter_log_limit_fails_before_unlogged_ipc_reply(self):
        for injection in ('w.FILTER_MAX_RECORDS=1', 'w.FILTER_MAX_BYTES=1'):
            with self.subTest(injection=injection), tempfile.TemporaryDirectory() as directory:
                path = Path(directory); profiles(path)
                result = run(path, request(1, 1000) + request(2, 2000), now=[1001, 2001], injection=injection)
                self.assertEqual(result.returncode, 3)
                self.assertEqual(len(result.stdout.splitlines()), 2 if 'RECORDS' in injection else 1)

    def test_log_partial_zero_error_writes_and_no_unverified_other_target(self):
        mapping = IndexPreviewMapping(0, 1, 0, 128)
        f = IndexPreviewFilter(3, .03, 3, .02, 150_000_000, 150_000_000)
        quality = f.update(.4, 1000, now_ns=1001)
        prediction = mapping.predict(quality['filtered'], valid=quality['valid'])
        with tempfile.TemporaryDirectory() as directory, cwd(directory):
            logger = worker.FilterRecorder(); original = os.write
            try:
                with mock.patch.object(worker.os, 'write', side_effect=lambda fd, data: original(fd, data[:7])):
                    logger.append(1, 1, 1000, True, b'.4', quality, prediction, 1001)
                for effect in (lambda *_: 0, OSError('fixture failure')):
                    with mock.patch.object(worker.os, 'write', side_effect=effect), self.assertRaises(OSError):
                        logger.append(1, 2, 2000, True, b'.4', quality, prediction, 2001)
                prediction['positions']['middle_curl'] = 7
                with self.assertRaises(ValueError):
                    logger.append(1, 2, 2000, True, b'.4', quality, prediction, 2001)
                self.assertEqual(logger.records_written, 1)
            finally:
                logger.close()

    def test_main_failure_closes_all_files_and_restores_original_calculate(self):
        original = readonly_worker.calculate
        with tempfile.TemporaryDirectory() as directory, cwd(directory):
            path = Path(directory); profiles(path)
            with mock.patch.object(readonly_worker, 'main', side_effect=OSError('fixture main failure')):
                with self.assertRaises(OSError):
                    worker.main()
            self.assertIs(readonly_worker.calculate, original)
            for name in ('source_capture.tsv', 'mapped_preview.tsv', 'filtered_preview.tsv'):
                Path(name).unlink()

    def test_partial_initialization_failure_closes_wire_and_raw_map(self):
        with tempfile.TemporaryDirectory() as directory, cwd(directory):
            path = Path(directory); profiles(path)
            Path('filtered_preview.tsv').write_bytes(b'OLD\n')
            with self.assertRaises(OSError):
                worker.main()
            Path('source_capture.tsv').unlink()
            Path('mapped_preview.tsv').unlink()
            self.assertEqual(Path('filtered_preview.tsv').read_bytes(), b'OLD\n')


if __name__ == '__main__':
    unittest.main()
