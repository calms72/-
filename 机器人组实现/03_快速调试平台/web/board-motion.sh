#!/bin/sh
# Descriptor 9 retains exclusive ownership until Python and its CAN tools exit.
set -eu
run=${1:?}; plan=${2:?}
case "$run" in /tmp/handui-*) ;; *) echo 'Invalid run directory' >&2; exit 1 ;; esac
suffix=${run#/tmp/handui-}
[ "${#suffix}" -eq 32 ] || exit 1
case "$suffix" in *[!0-9a-f]*) exit 1 ;; esac
mkdir -p "$run"
startup_failure() {
    result=$?
    trap - EXIT HUP INT TERM
    if [ ! -f "$run/exit-code" ]; then
        : > "$run/final.log"
        : > "$run/can.log"
        {
            printf 'status=error\npositions=?,?,?,?,?,?\ntarget=?,?,?,?,?,?\n'
            printf 'message=Holder failed before starting CAN\nupdated_at=%s\npid=%s\n' "$(date +%s)" "$$"
        } > "$run/holding.status.new"
        mv "$run/holding.status.new" "$run/holding.status"
        printf '1\n' > "$run/exit-code"
    fi
    exit "$result"
}
trap startup_failure EXIT
trap 'exit 130' HUP INT TERM
exec 9>/tmp/handui-motion.lock
attempt=0
while ! flock -n 9; do
    old=$(cat /tmp/handui-motion-owner 2>/dev/null || true)
    case "$old" in /tmp/handui-*)
        old_suffix=${old#/tmp/handui-}
        if [ "${#old_suffix}" -eq 32 ]; then
            case "$old_suffix" in *[!0-9a-f]*) ;; *)
                if [ ! -L "$old" ] && [ -d "$old" ]; then
                    : > "$old/handover-request"
                fi
            ;; esac
        fi
    ;; esac
    attempt=$((attempt+1))
    if [ "$attempt" -ge 60 ]; then
        echo 'Previous hand controller did not release its lock within 3 seconds' >&2
        : > "$run/final.log"
        : > "$run/can.log"
        printf '1\n' > "$run/exit-code"
        exit 1
    fi
    sleep 0.05
done
export HANDUI_LOCKED=1
python3 /tmp/handui-motion.py --check
exec python3 /tmp/handui-motion.py "$run" "$plan"
