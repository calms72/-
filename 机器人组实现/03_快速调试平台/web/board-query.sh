#!/bin/sh
# Short serial entry point. Only "start" launches the explicitly requested job.
set -eu
[ "$#" -ge 2 ] && [ "$#" -le 3 ] || exit 2
mode=$1; id=$2; pid=${3:-0}
[ "${#id}" -eq 32 ] || exit 2
case "$id" in *[!0-9a-f]*) exit 2 ;; esac
case "$pid" in ''|*[!0-9]*) exit 2 ;; esac
d=/tmp/handui-$id
[ ! -L "$d" ] || exit 2

is_current() {
    # Read only argv, never infer ownership from a PID's existence alone.
    a=$(tr '\000' ' ' 2>/dev/null < "/proc/$1/cmdline") || return 1
    case "$a" in
        *"/tmp/handui-motion.sh $d "*|*"/tmp/handui-motion.py $d "*) return 0 ;;
        *) return 1 ;;
    esac
}

case "$mode" in
start)
    mkdir -p "$d"
    nohup sh /tmp/handui-motion.sh "$d" "$d.plan" > "$d/console.log" 2>&1 < /dev/null &
    printf '\n__STARTED__%s\n' "$!"
    ;;
state)
    hand_status=missing; hand_progress=0
    if [ -f "$d/exit-code" ]; then
        hand_status=$(cat "$d/exit-code")
    elif [ "$pid" != 0 ]; then
        if is_current "$pid"; then hand_status=running; fi
    else
        for p in /proc/[0-9]*/cmdline; do
            p=${p%/cmdline}; p=${p#/proc/}
            if is_current "$p"; then hand_status=running; break; fi
        done
    fi
    if [ -f "$d/progress" ]; then hand_progress=$(cat "$d/progress"); fi
    printf '\n__STATE__%s:%s\n' "$hand_status" "$hand_progress"
    ;;
hold)
    printf '\n__HOLD_BEGIN__\n'
    cat "$d/holding.status" 2>/dev/null || true
    printf '\n__HOLD_END__\n__BOARD_NOW__'
    date +%s
    p=$(sed -n 's/^pid=//p' "$d/holding.status" 2>/dev/null || true)
    case "$p" in ''|*[!0-9]*) ;; *)
        if is_current "$p" && kill -0 "$p" 2>/dev/null; then
            printf '__HOLD_ALIVE__\n'
        fi
    ;; esac
    ;;
*) exit 2 ;;
esac
