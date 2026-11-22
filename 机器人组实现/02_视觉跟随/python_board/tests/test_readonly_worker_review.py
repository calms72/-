"""Independent synthetic IPC review; no devices, captures, clocks or calibration.

These tests establish only worker behavior. They do not establish the C
runtime's scheduling, fault latch, CAN behavior, output backpressure or cleanup.
"""
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKER = ROOT / "readonly_worker.py"
U64_MAX = (1 << 64) - 1
NO_TARGETS = b"NONE,NONE,NONE,NONE,NONE,NONE"


def frame(tokens=None):
    if tokens is None:
        tokens = [b"0.00"] * 63
    payload = b"," + b",".join(tokens) + b","
    declared = len(payload) - 1
    if declared > 999:
        raise ValueError("Synthetic source frame exceeds wire format")
    return b"\r" + ("%03d" % declared).encode("ascii") + payload + b"\n"


ZERO_FRAME = frame()


def request(seq=1, received=0, epoch=1, payload=ZERO_FRAME):
    return ("SHADOW1\t%d\t%d\t%d\t" % (epoch, seq, received)).encode("ascii") + payload.hex().encode("ascii") + b"\n"


def run_worker(data, mode="normal"):
    return subprocess.run(
        [sys.executable, "-B", str(WORKER), "--test-case", mode],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
    )


class IndependentWorkerReview(unittest.TestCase):
    def assert_no_targets(self, row):
        fields = row.split(b"\t")
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[0], b"SHADOW1")
        self.assertEqual(fields[4], b"1")
        self.assertEqual(int(fields[5]) & 32, 0)
        self.assertEqual(fields[7], NO_TARGETS)
        return fields

    def test_normal_no_hand_remains_persistent(self):
        result = run_worker(request() + request(seq=2, received=1))
        self.assertEqual(result.returncode, 0)
        rows = result.stdout.splitlines()
        self.assertEqual(rows[0], b"READY")
        self.assertEqual(len(rows), 3)
        for row in rows[1:]:
            fields = self.assert_no_targets(row)
            self.assertEqual(fields[5], b"0")
            self.assertEqual(fields[6], NO_TARGETS)

    def test_invalid_source_is_rejected_and_next_frame_recovers(self):
        invalid = [
            ZERO_FRAME[:-1],
            b"x" + ZERO_FRAME,
            ZERO_FRAME + b"x",
            ZERO_FRAME + ZERO_FRAME,
            frame([b"0.00"] * 62),
            frame([b"0.00"] * 64),
            frame([b"NaN"] + [b"0.00"] * 62),
            frame([b"1" * 30 + b".00"] + [b"0.00"] * 62),
            ZERO_FRAME.replace(b"315", b"314", 1),
        ]
        for source in invalid:
            with self.subTest(source_length=len(source)):
                result = run_worker(request(payload=source) + request(seq=2))
                self.assertEqual(result.returncode, 0)
                rows = result.stdout.splitlines()
                self.assertEqual(rows[1], b"REJECT1\t1\t1\t0\tFRAME_INVALID")
                self.assert_no_targets(rows[2])
                self.assertEqual(len(rows), 3)

    def test_wire_and_token_maximum_are_bounded_but_accepted(self):
        # Payload=1000, declared=999, wire=1005; all tokens at most 32 bytes.
        tokens = [b"1" * 29 + b".00"] * 24
        tokens += [b"1" * 13 + b".00"]
        tokens += [b"0.00"] * 38
        maximum = frame(tokens)
        self.assertEqual(len(maximum), 1005)
        result = run_worker(request(payload=maximum))
        self.assertEqual(result.returncode, 0)
        self.assert_no_targets(result.stdout.splitlines()[1])

    def test_real_worker_legitimately_emits_scientific_notation(self):
        # Pure synthetic numeric geometry, with five near-straight chains.
        # This does not assert anatomical identity or mechanical calibration.
        values = [0.0, -1.0, 0.0]
        for unused in range(5):
            for x, y in [(0.0, 0.0), (1.0, 0.0), (2.0, 0.01), (3.0, 0.01)]:
                values.extend([x, y, 0.0])
        payload = frame([("%.2f" % value).encode("ascii") for value in values])
        result = run_worker(request(payload=payload))
        self.assertEqual(result.returncode, 0)
        fields = self.assert_no_targets(result.stdout.splitlines()[1])
        self.assertEqual(fields[5], b"31")
        self.assertIn(b"e-", fields[6])

    def test_bad_ipc_never_produces_measurement(self):
        valid = request()
        invalid = [
            b"\n",
            valid.replace(b"SHADOW1", b"SHADOW2", 1),
            valid.replace(b"\t1\t1\t0\t", b"\t0\t1\t0\t", 1),
            valid.replace(b"\t1\t1\t0\t", b"\t1\t0\t0\t", 1),
            valid.replace(b"\t1\t1\t0\t", b"\t1\t1\t-1\t", 1),
            valid.replace(b"\t1\t1\t0\t", b"\t1\t1\t+1\t", 1),
            valid.replace(b"\t1\t1\t0\t", b"\t1\t1\t18446744073709551616\t", 1),
            valid.replace(b"\t1\t1\t0\t", b"\t1\t1\t000000000000000000000\t", 1),
            b"SHADOW1\t1\t1\t0\t\n",
            b"SHADOW1\t1\t1\t0\t0\n",
            b"SHADOW1\t1\t1\t0\t00\textra\n",
            b"SHADOW1\t1\t1\t0\t0g\n",
            valid[:-1] + b"\x00\n",
            b"x" * 2199 + b"\n",
            b"x" * 2200,
        ]
        for data in invalid:
            with self.subTest(length=len(data), prefix=data[:40]):
                result = run_worker(data)
                self.assertEqual(result.returncode, 3)
                self.assertEqual(result.stdout, b"READY\n")

    def test_u64_boundary_echoes_exactly(self):
        result = run_worker(request(seq=U64_MAX, received=U64_MAX, epoch=U64_MAX))
        self.assertEqual(result.returncode, 0)
        fields = self.assert_no_targets(result.stdout.splitlines()[1])
        self.assertEqual(fields[1:4], [str(U64_MAX).encode("ascii")] * 3)

    def test_fault_after_success_is_terminal(self):
        for second in [request(), request(seq=2, received=0, epoch=2)]:
            with self.subTest(second=second[:40]):
                result = run_worker(request() + second + request(seq=3, received=2))
                self.assertEqual(result.returncode, 3)
                self.assertEqual(len(result.stdout.splitlines()), 2)
                self.assert_no_targets(result.stdout.splitlines()[1])

    def test_partial_eof_is_failure_and_clean_eof_is_normal(self):
        partial = run_worker(request()[:-1])
        self.assertEqual(partial.returncode, 3)
        self.assertEqual(partial.stdout, b"READY\n")
        clean = run_worker(b"")
        self.assertEqual(clean.returncode, 0)
        self.assertEqual(clean.stdout, b"READY\n")

    def test_timeout_injection_reaches_ready_before_delayed_reply(self):
        child = subprocess.Popen(
            [sys.executable, "-B", str(WORKER), "--test-case", "timeout"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                child.communicate(request(), timeout=1)
        finally:
            child.kill()
            stdout, _ = child.communicate(timeout=2)
        self.assertEqual(stdout, b"READY\n")


if __name__ == "__main__":
    unittest.main()
