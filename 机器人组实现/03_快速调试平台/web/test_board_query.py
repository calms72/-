"""Offline shell contracts; board-side sh -n and read-only probes follow deploy.

These tests never invoke a shell, CAN tool, board connection or motor action.
"""
from pathlib import Path
import re

import pytest


SOURCE = Path(__file__).with_name('board-query.sh').read_text(encoding='utf-8')
DISPATCH = SOURCE.split('case "$mode" in\n', 1)[1]
PREAMBLE = SOURCE.split('case "$mode" in\n', 1)[0]


def branch(mode):
    # Top-level branch headings are unindented; nested cases use indentation.
    return re.search(r'(?ms)^' + mode + r'\)\n(.*?)(?=^[a-z*]+\))', DISPATCH + '\nend)')[1]


def test_arguments_are_validated_before_any_dispatch_or_directory_use():
    assert '[ "$#" -ge 2 ] && [ "$#" -le 3 ] || exit 2' in PREAMBLE
    assert '[ "${#id}" -eq 32 ] || exit 2' in PREAMBLE
    assert 'case "$id" in *[!0-9a-f]*) exit 2 ;; esac' in PREAMBLE
    assert 'case "$pid" in \'\'|*[!0-9]*) exit 2 ;; esac' in PREAMBLE
    assert PREAMBLE.index('case "$id"') < PREAMBLE.index('d=/tmp/handui-$id')
    assert '[ ! -L "$d" ] || exit 2' in PREAMBLE


@pytest.mark.parametrize('mode', ['state', 'hold'])
def test_read_queries_have_no_motion_or_process_mutation(mode):
    body = branch(mode)
    assert not re.search(r'\b(cansend|canplayer|candump|nohup|touch|mkdir|rm|mv|exec|eval)\b', body)
    assert not re.search(r'(?<![0-9])>(?!&)', body)  # Only stderr redirection 2> is allowed.
    assert 'stop-request' not in body
    assert 'handover-request' not in body
    assert not re.search(r'kill\s+(?!-0\b)', body)


def test_only_noninteractive_start_branch_launches_and_reports_pid():
    body = branch('start')
    assert 'mkdir -p "$d"' in body
    assert 'nohup sh /tmp/handui-motion.sh "$d" "$d.plan" > "$d/console.log" 2>&1 < /dev/null &' in body
    assert 'printf \'\\n__STARTED__%s\\n\' "$!"' in body
    assert SOURCE.count('nohup ') == 1
    assert 'cansend' not in SOURCE and 'canplayer' not in SOURCE


def test_state_prefers_terminal_exit_and_requires_matching_live_owner():
    body = branch('state')
    assert 'hand_status=missing; hand_progress=0' in body
    assert body.index('[ -f "$d/exit-code" ]') < body.index('is_current "$pid"')
    assert 'for p in /proc/[0-9]*/cmdline' in body
    assert 'if is_current "$p"; then hand_status=running; break; fi' in body
    assert 'printf \'\\n__STATE__%s:%s\\n\' "$hand_status" "$hand_progress"' in body
    assert '*"/tmp/handui-motion.sh $d "*|*"/tmp/handui-motion.py $d "*' in PREAMBLE


def test_hold_has_all_framing_markers_and_checked_pid_liveness():
    body = branch('hold')
    for marker in ('__HOLD_BEGIN__', '__HOLD_END__', '__BOARD_NOW__', '__HOLD_ALIVE__'):
        assert marker in body
    assert 'cat "$d/holding.status" 2>/dev/null || true' in body
    assert 'date +%s' in body
    assert 'if is_current "$p" && kill -0 "$p" 2>/dev/null; then' in body


def test_short_calls_leave_room_for_serial_completion_marker():
    task_id = 'a' * 32
    for mode, suffix in [('start', ''), ('state', ' 12345'), ('state', ' 0'), ('hold', '')]:
        command = 'sh /tmp/handui-query.sh ' + mode + ' ' + task_id + suffix
        assert len(command.encode('ascii')) + 71 < 160
