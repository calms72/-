#include "index_follow_commission.h"
#include <string.h>
#define MS UINT64_C(1000000)
#define PREPARE_NS (5000u * MS)
#define WHOLE_NS (25000u * MS)
#define STEP_NS (125u * MS)
#define ACK_NS (200u * MS)
#define STOP_NS (2000u * MS)
#define FRESH_NS (150u * MS)

void ifc_init(ifc_machine *m)
{
    memset(m, 0, sizeof *m);
    m->state = IFC_STANDBY;
    m->reason = "standby_no_motion";
    m->stop_reason = "none";
}
bool ifc_start(ifc_machine *m, uint64_t now, bool verified, bool attempted,
               bool exposed, uint16_t actual)
{
    if (m->state != IFC_STANDBY || m->started || !verified || !attempted || !exposed ||
        actual != 0 || now > UINT64_MAX - WHOLE_NS) return false;
    m->started = true;
    m->state = IFC_PREPARE;
    m->start_ns = m->last_now = now;
    m->prepare_until = now + PREPARE_NS;
    m->run_until = now + WHOLE_NS;
    m->reference = actual;
    m->last_move_ns = m->prepare_until;
    m->reason = "commission_prepare_five_seconds";
    return true;
}
static bool fresh_position(const ifc_input *in)
{
    return in->feedback_valid && in->feedback_ns <= in->now_ns &&
        in->now_ns - in->feedback_ns < FRESH_NS && in->position <= 160u;
}
static bool tracking_ok(uint16_t target, uint16_t actual)
{
    unsigned difference = target > actual ? (unsigned)target - actual : (unsigned)actual - target;
    return difference <= IFC_TRACKING_ERROR_MAX;
}
static void fail(ifc_machine *m, const char *reason)
{
    m->state = m->attempts ? IFC_POWER_REQUIRED : IFC_DONE;
    m->reason = reason;
    if (!m->stop_reason || !strcmp(m->stop_reason, "none")) m->stop_reason = reason;
    m->stop_feedback_verified = false;
}
static ifc_output output(const ifc_machine *m, ifc_command command, uint16_t target)
{
    ifc_output out = {command, target, m->state};
    return out;
}
static void request_stop(ifc_machine *m, uint64_t now, const char *reason)
{
    if (m->state == IFC_STOP_DRAIN || m->state == IFC_HOLD_ACK || m->state == IFC_HOLD_VERIFY) return;
    m->reason = reason;
    if (!m->stop_reason || !strcmp(m->stop_reason, "none")) m->stop_reason = reason;
    if (!m->attempts) { m->state = IFC_DONE; return; }
    if (now > UINT64_MAX - STOP_NS) { fail(m, "stop_clock_overflow_power_required"); return; }
    m->state = IFC_STOP_DRAIN;
    m->stop_until = now + STOP_NS;
    m->reference = 0; /* absent storage after stop; NEVER a return-to-zero target */
}
ifc_output ifc_step(ifc_machine *m, const ifc_input *in)
{
    if (!m || !in) return (ifc_output){IFC_NO_COMMAND, 0, IFC_POWER_REQUIRED};
    if (m->state == IFC_DONE || m->state == IFC_POWER_REQUIRED) return output(m, IFC_NO_COMMAND, 0);
    if (in->now_ns < m->last_now) { fail(m, "clock_reversed_power_required"); return output(m, IFC_NO_COMMAND, 0); }
    m->last_now = in->now_ns;
    if (m->state == IFC_STANDBY) {
        if (in->stop_requested) { m->state = IFC_DONE; m->reason = "standby_stopped_no_AA"; }
        return output(m, IFC_NO_COMMAND, 0);
    }
    if (!in->can_trustworthy || in->protocol_failed || !in->exposure_persisted) {
        fail(m, "CAN_or_write_or_ack_uncertain_power_required"); return output(m, IFC_NO_COMMAND, 0);
    }
    if (in->feedback_valid && in->position > 160u) {
        fail(m, "actual_position_outside_guard_power_required"); return output(m, IFC_NO_COMMAND, 0);
    }
    if (m->pending) {
        if (in->ack_serial == m->pending_serial && in->ack_ns >= m->pending_since &&
            in->ack_ns <= in->now_ns && in->ack_ns - m->pending_since < ACK_NS) {
            m->pending = false;
            if (m->pending_kind == IFC_HOLD) {
                m->hold_ack_ns = in->ack_ns;
                m->state = IFC_HOLD_VERIFY;
                m->reason = "hold_ACK_not_physical_stop";
            }
        } else if (in->now_ns - m->pending_since >= ACK_NS) {
            fail(m, "AA_ack_timeout_power_required"); return output(m, IFC_NO_COMMAND, 0);
        }
    }
    if (m->state == IFC_PREPARE || m->state == IFC_RUN) {
        if (in->stop_requested || in->now_ns >= m->run_until || !fresh_position(in))
            request_stop(m, in->now_ns, in->stop_requested ? "software_stop_requested" : "commission_deadline_or_feedback_guard");
    }
    if (m->state == IFC_PREPARE) {
        if (in->position != 0 || in->velocity != 0) request_stop(m, in->now_ns, "prepare_actual_position_changed");
        else if (in->now_ns >= m->prepare_until) { m->state = IFC_RUN; m->reason = "commission_running_bounded"; }
    }
    if (m->state == IFC_RUN) {
        if (m->reference >= 128u) request_stop(m, in->now_ns, "commission_reference_endpoint_stop");
        else if (!m->pending && in->now_ns - m->last_move_ns >= STEP_NS) {
            uint16_t next = (uint16_t)(m->reference + 1u);
            if (!tracking_ok(next, in->position))
                request_stop(m, in->now_ns, "commission_tracking_error_exceeded_16");
            else return output(m, IFC_MOVE, next);
        }
    }
    if (m->state == IFC_STOP_DRAIN) {
        if (in->now_ns >= m->stop_until) { fail(m, "stop_drain_timeout_power_required"); }
        else if (!m->pending && !fresh_position(in)) { fail(m, "no_trustworthy_stop_position_power_required"); }
        else if (!m->pending && fresh_position(in) && !m->hold_attempted)
            return output(m, IFC_HOLD, in->position);
    }
    if (m->state == IFC_HOLD_ACK && in->now_ns >= m->stop_until)
        fail(m, "hold_ack_deadline_power_required");
    if (m->state == IFC_HOLD_VERIFY) {
        if (in->now_ns >= m->stop_until || !fresh_position(in)) { fail(m, "hold_verification_timeout_or_feedback_power_required"); }
        else if (in->feedback_ns > m->hold_ack_ns && in->feedback_ns > m->stable_last_ns) {
            unsigned difference = in->position > m->hold_target ? in->position - m->hold_target : m->hold_target - in->position;
            if (in->velocity || difference > IFC_HOLD_TARGET_TOLERANCE) { m->stable_count = 0; }
            else {
                uint16_t lower = in->position < m->stable_min ? in->position : m->stable_min;
                uint16_t upper = in->position > m->stable_max ? in->position : m->stable_max;
                if (!m->stable_count || (unsigned)(upper - lower) > IFC_STABLE_SPAN_MAX) {
                    m->stable_count = 1;
                    m->stable_since = in->feedback_ns;
                    m->stable_position = in->position;
                    m->stable_min = m->stable_max = in->position;
                } else {
                    ++m->stable_count;
                    m->stable_min = lower;
                    m->stable_max = upper;
                }
                if (m->stable_count >= IFC_STABLE_MIN_SAMPLES && in->feedback_ns - m->stable_since >= IFC_STABLE_MIN_NS) {
                    m->stop_feedback_verified = true;
                    m->state = IFC_DONE;
                    m->reason = "hold_ACK_and_A0_stability_observed_mechanical_confirmation_required";
                }
            }
            m->stable_last_ns = in->feedback_ns;
        }
    }
    return output(m, IFC_NO_COMMAND, 0);
}
bool ifc_begin_attempt(ifc_machine *m, const ifc_input *in, ifc_command command, uint16_t target)
{
    ifc_output candidate = ifc_step(m, in);
    if (m->pending || command == IFC_NO_COMMAND || candidate.command != command ||
        candidate.position != target || !in->can_trustworthy || !in->exposure_persisted || !fresh_position(in)) return false;
    if (command == IFC_MOVE) {
        if (in->stop_requested || m->state != IFC_RUN || target > 128u || target != m->reference + 1u) return false;
        /* Explicit final guard; ifc_step above also recomputed the candidate
         * using this newest actual position, so old proposals cannot bypass. */
        if (!tracking_ok(target, in->position)) {
            request_stop(m, in->now_ns, "commission_tracking_error_exceeded_16");
            return false;
        }
        m->reference = target;
        m->last_move_ns = in->now_ns;
        ++m->moves;
    } else {
        if (m->state != IFC_STOP_DRAIN || m->hold_attempted || target > 160u || target != in->position) return false;
        m->hold_attempted = true;
        m->hold_target = target;
        m->state = IFC_HOLD_ACK;
        ++m->holds;
    }
    ++m->attempts;
    m->pending = true;
    m->pending_since = in->now_ns;
    m->pending_serial = m->attempts;
    m->pending_target = target;
    m->pending_kind = command;
    return true;
}
