#include "index_follow_bus_schedule.h"
#include <string.h>

static bool fail(ibs_schedule *s, ibs_reason reason)
{
    if (s) { s->failed = true; s->reason = reason; }
    return false;
}
static bool advance(ibs_schedule *s, uint64_t now)
{
    if (!s || !s->initialized || s->failed) return false;
    if (now < s->last_now_ns) return fail(s, IBS_CLOCK);
    s->last_now_ns = now;
    return true;
}
bool ibs_init(ibs_schedule *s, uint64_t now)
{
    if (!s) return false;
    memset(s, 0, sizeof *s);
    s->initialized = true;
    s->last_now_ns = s->next_a0_due_ns = now;
    s->reason = IBS_OK;
    return true;
}
ibs_action ibs_plan(ibs_schedule *s, uint64_t now, bool wants_aa)
{
    if (!advance(s, now)) return IBS_BLOCKED;
    if (s->aa_pending) { s->reason = IBS_AA_IN_FLIGHT; return IBS_WAIT; }
    if (s->a0_pending_mask) { s->reason = IBS_A0_IN_FLIGHT; return IBS_WAIT; }
    if (now >= s->next_a0_due_ns) {
        if (s->have_aa_ack && now - s->last_aa_ack_ns < IBS_QUIET_NS) {
            s->reason = IBS_ACK_QUIET; return IBS_WAIT;
        }
        s->reason = IBS_A0_PRIORITY;
        return IBS_A0_ROUND;
    }
    if (!wants_aa) { s->reason = IBS_OK; return IBS_WAIT; }
    if (!s->have_round || s->a0_complete_mask != IBS_ALL_NODES || !s->have_reply) {
        s->reason = IBS_NO_COMPLETE_ROUND; return IBS_WAIT;
    }
    if (now - s->last_reply_ns < IBS_QUIET_NS) {
        s->reason = IBS_REPLY_QUIET; return IBS_WAIT;
    }
    s->reason = IBS_OK;
    return IBS_AA_SLOT;
}
bool ibs_begin_a0(ibs_schedule *s, uint64_t now)
{
    if (ibs_plan(s, now, false) != IBS_A0_ROUND) return false;
    if (s->round_id == UINT64_MAX || s->a0_rounds == UINT64_MAX ||
        now > UINT64_MAX - IBS_A0_PERIOD_NS) return fail(s, IBS_OVERFLOW);
    ++s->round_id;
    ++s->a0_rounds;
    s->have_round = true;
    s->a0_pending_mask = IBS_ALL_NODES;
    s->a0_complete_mask = 0;
    s->next_a0_due_ns = now + IBS_A0_PERIOD_NS;
    s->reason = IBS_OK;
    return true;
}
bool ibs_note_reply(ibs_schedule *s, uint64_t now)
{
    if (!advance(s, now)) return false;
    s->last_reply_ns = now;
    s->have_reply = true;
    return true;
}
bool ibs_a0_complete(ibs_schedule *s, uint64_t round, unsigned node, uint64_t now)
{
    if (!advance(s, now)) return false;
    if (!s->have_round || round != s->round_id || s->aa_pending) return fail(s, IBS_BAD_ROUND);
    if (!node || node > 6u) return fail(s, IBS_BAD_NODE);
    uint8_t bit = (uint8_t)(1u << (node - 1u));
    if (!(s->a0_pending_mask & bit)) return fail(s, IBS_DUPLICATE);
    s->a0_pending_mask &= (uint8_t)~bit;
    s->a0_complete_mask |= bit;
    return ibs_note_reply(s, now);
}
bool ibs_begin_aa(ibs_schedule *s, uint64_t now)
{
    if (ibs_plan(s, now, true) != IBS_AA_SLOT) return false;
    if (s->aa_attempts == UINT64_MAX) return fail(s, IBS_OVERFLOW);
    ++s->aa_attempts;
    s->aa_pending = true;
    s->reason = IBS_OK;
    return true;
}
bool ibs_aa_complete(ibs_schedule *s, uint64_t now)
{
    if (!advance(s, now)) return false;
    if (!s->aa_pending || s->a0_pending_mask) return fail(s, IBS_UNEXPECTED_ACK);
    s->aa_pending = false;
    s->have_aa_ack = true;
    s->last_aa_ack_ns = now;
    s->reason = IBS_OK;
    return ibs_note_reply(s, now);
}
const char *ibs_reason_name(ibs_reason reason)
{
    switch (reason) {
    case IBS_OK: return "ok";
    case IBS_CLOCK: return "clock_reversed";
    case IBS_A0_IN_FLIGHT: return "a0_round_in_flight";
    case IBS_AA_IN_FLIGHT: return "AA_in_flight";
    case IBS_REPLY_QUIET: return "reply_quiet_interval";
    case IBS_ACK_QUIET: return "AA_ack_quiet_interval";
    case IBS_A0_PRIORITY: return "a0_due_has_priority";
    case IBS_NO_COMPLETE_ROUND: return "no_complete_six_node_round";
    case IBS_BAD_ROUND: return "wrong_or_overlapping_round";
    case IBS_BAD_NODE: return "node_outside_1_to_6";
    case IBS_DUPLICATE: return "duplicate_completion";
    case IBS_UNEXPECTED_ACK: return "unexpected_AA_completion";
    case IBS_OVERFLOW: return "counter_or_deadline_overflow";
    }
    return "unknown";
}
