"""Bounded, hardware-free parser for the currently observed visual wire format.

API:
    parser = VisualFrameParser(frame_timeout_ns=100_000_000)
    frames = parser.feed(captured_bytes, local_monotonic_now_ns)
    expired = parser.expire(local_monotonic_now_ns)

Each published plain dict has values (a new tuple of 63 finite floats),
received_ns (time the final byte was submitted), started_ns (CR arrival time),
declared_length and wire_length. These are local receive times, not camera
timestamps. Input chunks may split/combine frames arbitrarily. now_ns must be a
nonnegative, nondecreasing integer from one monotonic clock.

Observed wire: CR, three ASCII decimal digits N, N+1 payload bytes, LF;
payload = leading comma + 63 signed decimal numbers + trailing comma. Numbers
require integer digits and exactly two fractional digits; an optional +/- is
accepted, although the current captures contain only optional minus signs.
No whitespace, exponent, empty token, NaN or infinity is accepted.

The default 100 ms candidate deadline is a configurable diagnostic setting,
not a calibrated loss-of-hand/control safety timeout. Call expire even during
silence. Deadlines are absolute from CR and never extended by later bytes.
No hand identity, coordinate scale, geometry validity, motion target or hardware
control is inferred. No hardware I/O occurs. The only import is math.
"""

import math


FIELD_COUNT = 63
MAX_DECLARED_LENGTH = 999
# Smallest token is '0.00': 63 * 4 characters + 64 commas = 316 payload bytes.
MIN_DECLARED_LENGTH = 315
MAX_TOKEN_BYTES = 32


class VisualFrameParser:
    def __init__(self, frame_timeout_ns=100_000_000,
                 max_declared_length=MAX_DECLARED_LENGTH,
                 max_token_bytes=MAX_TOKEN_BYTES):
        if type(frame_timeout_ns) is not int or frame_timeout_ns <= 0:
            raise ValueError("frame_timeout_ns must be a positive integer")
        if (type(max_declared_length) is not int or
                not MIN_DECLARED_LENGTH <= max_declared_length <= MAX_DECLARED_LENGTH):
            raise ValueError("max_declared_length must be between 315 and 999")
        if type(max_token_bytes) is not int or not 4 <= max_token_bytes <= MAX_TOKEN_BYTES:
            raise ValueError("max_token_bytes must be between 4 and 32")
        self.frame_timeout_ns = frame_timeout_ns
        self.max_declared_length = max_declared_length
        self.max_token_bytes = max_token_bytes
        self._capacity = max_declared_length + 6
        self._buf = bytearray()
        self._expected = None
        self._started_ns = None
        self._last_now_ns = None
        self._counts = dict((name, 0) for name in (
            "input_bytes", "frames", "garbage_bytes", "discarded_bytes",
            "bad_header", "bad_length", "bad_trailer", "early_lf",
            "bad_delimiters", "bad_fields", "bad_number", "token_too_long",
            "timeouts", "resyncs", "buffer_overflows", "manual_resets",
            "clock_errors", "buffer_peak_bytes"))

    @property
    def counters(self):
        """Return a copy, so callers cannot mutate parser bookkeeping."""
        return dict(self._counts)

    @property
    def buffered_bytes(self):
        return len(self._buf)

    @property
    def buffer_capacity(self):
        return self._capacity

    def _check_time(self, now_ns):
        if (type(now_ns) is not int or now_ns < 0 or
                (self._last_now_ns is not None and now_ns < self._last_now_ns)):
            self._counts["clock_errors"] += 1
            raise ValueError("now_ns must be a nondecreasing nonnegative integer")
        self._last_now_ns = now_ns

    def _clear(self, discarded):
        if discarded:
            self._counts["discarded_bytes"] += len(self._buf)
        self._buf.clear()
        self._expected = None
        self._started_ns = None

    def _expire_checked(self, now_ns):
        if self._buf and now_ns - self._started_ns >= self.frame_timeout_ns:
            self._counts["timeouts"] += 1
            self._clear(True)
            return True
        return False

    def expire(self, now_ns):
        """Discard a timed-out partial candidate; return True only if discarded."""
        self._check_time(now_ns)
        return self._expire_checked(now_ns)

    def discard_pending(self):
        """Explicitly discard partial input on a caller-managed stream boundary.

        Published frames are not retained by the parser. This does not reset its
        monotonic clock contract or counters, and performs no hardware action.
        """
        self._counts["manual_resets"] += 1
        self._clear(True)

    def _append(self, value):
        # Check before appending: the candidate buffer never exceeds capacity.
        if len(self._buf) >= self._capacity:
            self._counts["buffer_overflows"] += 1
            self._counts["discarded_bytes"] += 1
            self._clear(True)
            return False
        self._buf.append(value)
        self._counts["buffer_peak_bytes"] = max(
            self._counts["buffer_peak_bytes"], len(self._buf))
        return True

    def _number(self, token):
        size = len(token)
        position = 0
        if size and token[0] in (43, 45):
            position = 1
        first_digit = position
        while position < size and 48 <= token[position] <= 57:
            position += 1
        if position == first_digit or position + 3 != size or token[position] != 46:
            return None
        if not (48 <= token[position + 1] <= 57 and 48 <= token[position + 2] <= 57):
            return None
        try:
            value = float(bytes(token))
        except (ValueError, OverflowError):
            return None
        return value if math.isfinite(value) else None

    def _payload(self):
        # LF has already been checked. No payload copy or unbounded split list.
        if self._buf[4] != 44 or self._buf[-2] != 44:
            self._counts["bad_delimiters"] += 1
            return None
        values = []
        token = bytearray()
        for position in range(5, len(self._buf) - 1):
            value = self._buf[position]
            if value == 44:
                if len(values) >= FIELD_COUNT:
                    self._counts["bad_fields"] += 1
                    return None
                if not token:
                    self._counts["bad_number"] += 1
                    return None
                number = self._number(token)
                if number is None:
                    self._counts["bad_number"] += 1
                    return None
                values.append(number)
                token.clear()
            else:
                if len(token) >= self.max_token_bytes:
                    self._counts["token_too_long"] += 1
                    return None
                token.append(value)
        if token or len(values) != FIELD_COUNT:
            self._counts["bad_fields"] += 1
            return None
        return tuple(values)

    def feed(self, chunk, now_ns):
        """Return only complete, fully validated frame dicts from these bytes.

        Memory retained between calls is bounded by buffer_capacity (<=1005).
        The returned list scales with complete frames in the supplied chunk;
        callers should use bounded read chunks and discard superseded targets.
        A CR always abandons a partial candidate, because CR is illegal inside
        this ASCII payload. Damaged frames cannot pollute a later publication.
        """
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes or bytearray")
        self._check_time(now_ns)
        self._expire_checked(now_ns)
        published = []
        for value in chunk:
            self._counts["input_bytes"] += 1
            if value == 13:
                if self._buf:
                    self._counts["resyncs"] += 1
                    self._clear(True)
                self._started_ns = now_ns
                self._append(value)
                continue
            if not self._buf:
                self._counts["garbage_bytes"] += 1
                self._counts["discarded_bytes"] += 1
                continue
            if not self._append(value):
                continue
            size = len(self._buf)
            if size <= 4:
                if not 48 <= value <= 57:
                    self._counts["bad_header"] += 1
                    self._clear(True)
                elif size == 4:
                    declared = ((self._buf[1] - 48) * 100 +
                                (self._buf[2] - 48) * 10 + self._buf[3] - 48)
                    if not MIN_DECLARED_LENGTH <= declared <= self.max_declared_length:
                        self._counts["bad_length"] += 1
                        self._clear(True)
                    else:
                        self._expected = declared + 6
                continue
            if value == 10 and size != self._expected:
                self._counts["early_lf"] += 1
                self._clear(True)
                continue
            if size == self._expected:
                if value != 10:
                    self._counts["bad_trailer"] += 1
                    self._clear(True)
                    continue
                values = self._payload()
                if values is not None:
                    published.append({"values": values, "received_ns": now_ns,
                                      "started_ns": self._started_ns,
                                      "declared_length": self._expected - 6,
                                      "wire_length": self._expected})
                    self._counts["frames"] += 1
                    self._clear(False)
                else:
                    self._clear(True)
        return published
