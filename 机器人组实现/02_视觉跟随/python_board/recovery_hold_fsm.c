#include "recovery_hold_fsm.h"
#include <string.h>

void rhp_init(rhp_machine *m)
{
    memset(m, 0, sizeof *m);
    m->state = RHP_STANDBY;
    m->reason = "standby_old_exposure_not_cleared";
}
void rhp_fail(rhp_machine *m, const char *reason)
{
    if (!m || m->state == RHP_FAILED) return;
    m->state = RHP_FAILED;
    m->reason = reason;
    m->feedback_hold_verified = false;
}
static bool advance(rhp_machine *m, uint64_t now)
{
    if (!m || now < m->last_now_ns) { rhp_fail(m, "clock_reversed_no_retry"); return false; }
    m->last_now_ns = now;
    return m->state != RHP_DONE && m->state != RHP_FAILED;
}
bool rhp_start(rhp_machine *m, uint64_t now)
{
    if (!m || m->state != RHP_STANDBY || m->started || !advance(m, now)) return false;
    if (now > UINT64_MAX - RHP_WHOLE_NS) { rhp_fail(m, "start_clock_overflow"); return false; }
    m->started = true;
    m->started_ns = now;
    m->whole_until_ns = now + RHP_WHOLE_NS;
    m->state = RHP_PREFLIGHT;
    m->reason = "manual_start_preflight_no_AA";
    return true;
}
bool rhp_prepared(rhp_machine *m, uint64_t now, bool durable)
{
    if (!m || m->state != RHP_PREFLIGHT || !advance(m, now)) return false;
    if (!durable || now >= m->whole_until_ns) {
        rhp_fail(m, "durable_preflight_failed_or_expired_no_retry"); return false;
    }
    m->attempt_durable = true;
    m->state = RHP_WAIT_SLOT;
    m->reason = "attempt_durable_waiting_for_one_hold_slot";
    return true;
}
static bool fresh(const rhp_input *in)
{
    return in->all_six_fresh_healthy && in->feedback_query_ns <= in->feedback_rx_ns &&
        in->feedback_rx_ns <= in->now_ns && in->now_ns - in->feedback_query_ns < RHP_FRESH_NS &&
        in->actual_position <= 160u && in->velocity == 0 && in->status == 0;
}
static rhp_output output(const rhp_machine *m, rhp_command cmd, uint16_t p)
{
    return (rhp_output){cmd, p, m->state};
}
rhp_output rhp_step(rhp_machine *m, const rhp_input *in)
{
    if (!m || !in) return (rhp_output){RHP_NO_COMMAND, 0, RHP_FAILED};
    if (m->state == RHP_DONE || m->state == RHP_FAILED) return output(m, RHP_NO_COMMAND, 0);
    if (!advance(m, in->now_ns)) return output(m, RHP_NO_COMMAND, 0);
    if (in->stop_requested) m->stop_seen = true;
    if (m->state == RHP_STANDBY) {
        if (m->stop_seen) rhp_fail(m, "standby_stop_no_AA_old_exposure_retained");
        return output(m, RHP_NO_COMMAND, 0);
    }
    if (m->state == RHP_PREFLIGHT) {
        if (m->stop_seen || in->now_ns >= m->whole_until_ns)
            rhp_fail(m, "preflight_cancelled_or_expired_no_AA");
        return output(m, RHP_NO_COMMAND, 0);
    }
    if (!in->can_trustworthy || !in->ledger_verified || !in->attempt_durable || !fresh(in)) {
        rhp_fail(m, "transport_ledger_attempt_or_feedback_untrusted_no_retry");
        return output(m, RHP_NO_COMMAND, 0);
    }
    if (in->now_ns >= m->whole_until_ns) {
        rhp_fail(m, "recovery_whole_deadline_no_retry"); return output(m, RHP_NO_COMMAND, 0);
    }
    if (m->state == RHP_WAIT_SLOT) {
        if (m->stop_seen) rhp_fail(m, "cancelled_before_hold_no_retry_marker_retained");
        else if (in->slot_ready) return output(m, RHP_HOLD_CURRENT_ONCE, in->actual_position);
    } else if (m->state == RHP_WAIT_ACK) {
        if (in->ack_dual_matched && in->ack_ns >= m->sent_ns && in->ack_ns <= in->now_ns &&
            in->ack_ns - m->sent_ns < RHP_ACK_NS) {
            m->ack_ns = in->ack_ns;
            if (in->ack_ns > UINT64_MAX - RHP_VERIFY_NS) rhp_fail(m, "verify_clock_overflow");
            else {
                m->verify_until_ns = in->ack_ns + RHP_VERIFY_NS;
                m->state = RHP_VERIFY;
                m->reason = "hold_ACK_only_not_physical_stop";
            }
        } else if (in->now_ns - m->sent_ns >= RHP_ACK_NS)
            rhp_fail(m, "hold_ACK_timeout_no_retry");
    }
    if (m->state == RHP_VERIFY) {
        if (in->now_ns >= m->verify_until_ns) rhp_fail(m, "hold_A0_verification_deadline_no_retry");
        else if (in->feedback_query_ns >= m->ack_ns + RHP_QUIET_NS &&
                 in->feedback_query_ns > m->stable_last_query_ns) {
            unsigned difference = in->actual_position > m->hold_target ?
                (unsigned)in->actual_position - m->hold_target : (unsigned)m->hold_target - in->actual_position;
            if (difference > 8u) m->stable_count = 0;
            else {
                uint16_t lo = in->actual_position < m->stable_min ? in->actual_position : m->stable_min;
                uint16_t hi = in->actual_position > m->stable_max ? in->actual_position : m->stable_max;
                if (!m->stable_count || (unsigned)(hi - lo) > 2u) {
                    m->stable_count = 1;
                    m->stable_first_query_ns = in->feedback_query_ns;
                    m->stable_min = m->stable_max = in->actual_position;
                } else {
                    ++m->stable_count;
                    m->stable_min = lo; m->stable_max = hi;
                }
                if (m->stable_count >= 5u && in->feedback_query_ns - m->stable_first_query_ns >= RHP_STABLE_NS) {
                    m->feedback_hold_verified = true;
                    m->state = RHP_DONE;
                    m->reason = "static_hold_feedback_verified_manual_mechanical_confirmation_required";
                }
            }
            m->stable_last_query_ns = in->feedback_query_ns;
        }
    }
    return output(m, RHP_NO_COMMAND, 0);
}
bool rhp_commit(rhp_machine *m, const rhp_input *in, uint16_t target)
{
    if (!m || !in) return false;
    rhp_output candidate = rhp_step(m, in);
    if (m->state != RHP_WAIT_SLOT || m->committed || m->attempts ||
        candidate.command != RHP_HOLD_CURRENT_ONCE || candidate.position != target) {
        rhp_fail(m, "final_hold_candidate_changed_or_cancelled_no_retry"); return false;
    }
    m->committed = true;
    m->attempts = 1;
    m->hold_target = target;
    m->sent_ns = in->now_ns;
    m->state = RHP_WAIT_ACK;
    m->reason = "one_hold_committed_no_retry";
    return true;
}
