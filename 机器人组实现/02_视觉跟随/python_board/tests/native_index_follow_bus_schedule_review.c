/* Independent TEST_ONLY arbitration/FSM counterexamples. No device access,
 * no CAN, no runtime entry point; synthetic ACKs are not hardware evidence. */
#include "../index_follow_bus_schedule.h"
#include "../index_follow_commission.h"
#include <stdio.h>
#include <string.h>

#define MS UINT64_C(1000000)
static unsigned checks;
static unsigned permutations;
#define CHECK(t) do { ++checks; if (!(t)) { \
    (void)fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #t); return 1; \
} } while (0)

static int finish_round(ibs_schedule *s, uint64_t now)
{
    unsigned node;
    for (node = 1; node <= 6; ++node)
        CHECK(ibs_a0_complete(s, s->round_id, node, now));
    CHECK(s->a0_pending_mask == 0 && s->a0_complete_mask == IBS_ALL_NODES);
    return 0;
}

static int boundaries_and_priority(void)
{
    ibs_schedule s;
    uint64_t due;
    unsigned i;
    CHECK(IBS_A0_PERIOD_NS == 100u * MS && IBS_QUIET_NS == 5u * MS);
    CHECK(!ibs_init(NULL, 0));
    memset(&s, 0, sizeof s);
    CHECK(ibs_plan(&s, 0, true) == IBS_BLOCKED);
    CHECK(ibs_init(&s, 0));
    CHECK(ibs_plan(&s, 0, true) == IBS_A0_ROUND);
    CHECK(!ibs_begin_aa(&s, 0) && !s.aa_attempts);
    CHECK(ibs_begin_a0(&s, 0));
    due = s.next_a0_due_ns;
    CHECK(s.round_id == 1 && s.a0_rounds == 1 && due == 100u * MS);
    CHECK(!ibs_begin_aa(&s, 0) && !s.aa_attempts);
    CHECK(!ibs_begin_a0(&s, 0) && s.a0_rounds == 1);
    for (i = 1; i <= 5; ++i) {
        CHECK(ibs_a0_complete(&s, 1, i, MS));
        CHECK(ibs_plan(&s, MS, true) == IBS_WAIT);
        CHECK(!s.aa_attempts && s.next_a0_due_ns == due);
    }
    CHECK(s.a0_pending_mask == 32u && s.a0_complete_mask == 31u);
    CHECK(ibs_plan(&s, 80u * MS, true) == IBS_WAIT);
    CHECK(!ibs_begin_aa(&s, 80u * MS) && !s.aa_attempts);
    CHECK(ibs_a0_complete(&s, 1, 6, 80u * MS));
    CHECK(ibs_plan(&s, 85u * MS - 1u, true) == IBS_WAIT);
    CHECK(s.reason == IBS_REPLY_QUIET && !s.aa_attempts);
    CHECK(!ibs_begin_aa(&s, 85u * MS - 1u));
    CHECK(ibs_plan(&s, 85u * MS, true) == IBS_AA_SLOT);
    CHECK(ibs_plan(&s, 85u * MS, false) == IBS_WAIT);
    CHECK(ibs_note_reply(&s, 86u * MS));
    CHECK(ibs_plan(&s, 91u * MS - 1u, true) == IBS_WAIT);
    CHECK(ibs_plan(&s, 91u * MS, true) == IBS_AA_SLOT);
    CHECK(ibs_plan(&s, 100u * MS, true) == IBS_A0_ROUND);
    CHECK(s.reason == IBS_A0_PRIORITY && !s.aa_attempts && s.next_a0_due_ns == due);
    CHECK(!ibs_begin_aa(&s, 100u * MS));
    CHECK(ibs_begin_a0(&s, 150u * MS));
    CHECK(s.next_a0_due_ns == 250u * MS && s.a0_rounds == 2);
    if (finish_round(&s, 151u * MS)) return 1;
    CHECK(ibs_plan(&s, 156u * MS, true) == IBS_AA_SLOT);
    CHECK(!s.aa_attempts); /* A proposal is never an attempt. */
    return 0;
}

static int aa_exclusion_and_ack_quiet(void)
{
    ibs_schedule s;
    CHECK(ibs_init(&s, 0) && ibs_begin_a0(&s, 0));
    if (finish_round(&s, MS)) return 1;
    CHECK(ibs_begin_aa(&s, 95u * MS));
    CHECK(s.aa_pending && s.aa_attempts == 1);
    CHECK(ibs_plan(&s, 100u * MS, true) == IBS_WAIT);
    CHECK(s.reason == IBS_AA_IN_FLIGHT);
    CHECK(!ibs_begin_a0(&s, 100u * MS));
    CHECK(!ibs_begin_aa(&s, 100u * MS));
    CHECK(s.aa_attempts == 1 && s.a0_rounds == 1);
    CHECK(ibs_note_reply(&s, 101u * MS)); /* First matcher alone is not ACK. */
    CHECK(ibs_plan(&s, 106u * MS, true) == IBS_WAIT && s.aa_pending);
    CHECK(ibs_aa_complete(&s, 107u * MS));
    CHECK(!s.aa_pending && s.have_aa_ack);
    CHECK(ibs_plan(&s, 112u * MS - 1u, false) == IBS_WAIT);
    CHECK(s.reason == IBS_ACK_QUIET);
    CHECK(!ibs_begin_a0(&s, 112u * MS - 1u));
    CHECK(ibs_plan(&s, 112u * MS, true) == IBS_A0_ROUND);
    CHECK(ibs_begin_a0(&s, 112u * MS));
    CHECK(s.a0_rounds == 2 && s.aa_attempts == 1);
    if (finish_round(&s, 113u * MS)) return 1;
    CHECK(ibs_plan(&s, 118u * MS, true) == IBS_AA_SLOT);
    return 0;
}

static int all_reply_orders(unsigned depth, unsigned used, unsigned order[6])
{
    unsigned node, i;
    if (depth < 6) {
        for (node = 1; node <= 6; ++node) {
            unsigned bit = 1u << (node - 1u);
            if (!(used & bit)) {
                order[depth] = node;
                if (all_reply_orders(depth + 1u, used | bit, order)) return 1;
            }
        }
        return 0;
    }
    {
        ibs_schedule s;
        uint64_t now = MS;
        CHECK(ibs_init(&s, 0) && ibs_begin_a0(&s, 0));
        for (i = 0; i < 6; ++i) {
            ++now;
            CHECK(ibs_a0_complete(&s, 1, order[i], now));
            CHECK(ibs_plan(&s, now, true) == IBS_WAIT);
            if (i < 5) CHECK(s.a0_pending_mask != 0 && !s.aa_attempts);
        }
        CHECK(!s.a0_pending_mask && s.a0_complete_mask == IBS_ALL_NODES);
        CHECK(ibs_plan(&s, now + 5u * MS - 1u, true) == IBS_WAIT);
        CHECK(ibs_begin_aa(&s, now + 5u * MS));
        CHECK(s.aa_attempts == 1 && s.aa_pending);
        ++permutations;
    }
    return 0;
}

static int rejected_input_latches(void)
{
    ibs_schedule base, s;
    unsigned node;
    CHECK(ibs_init(&base, 20u * MS) && ibs_begin_a0(&base, 20u * MS));
    s = base;
    CHECK(!ibs_a0_complete(&s, 0, 1, 21u * MS));
    CHECK(s.failed && s.reason == IBS_BAD_ROUND);
    CHECK(ibs_plan(&s, 200u * MS, true) == IBS_BLOCKED);
    CHECK(!ibs_begin_a0(&s, 200u * MS) && !s.aa_attempts);
    for (node = 0; node <= 7; node += 7) {
        s = base;
        CHECK(!ibs_a0_complete(&s, 1, node, 21u * MS));
        CHECK(s.failed && s.reason == IBS_BAD_NODE);
    }
    s = base;
    CHECK(ibs_a0_complete(&s, 1, 3, 21u * MS));
    CHECK(!ibs_a0_complete(&s, 1, 3, 21u * MS));
    CHECK(s.failed && s.reason == IBS_DUPLICATE);
    s = base;
    CHECK(!ibs_aa_complete(&s, 21u * MS));
    CHECK(s.failed && s.reason == IBS_UNEXPECTED_ACK);
    s = base;
    CHECK(!ibs_note_reply(&s, 20u * MS - 1u));
    CHECK(s.failed && s.reason == IBS_CLOCK);
    s = base;
    CHECK(ibs_plan(&s, 20u * MS - 1u, true) == IBS_BLOCKED);
    CHECK(s.failed && s.reason == IBS_CLOCK);
    s = base;
    CHECK(!ibs_begin_a0(&s, 20u * MS - 1u) && s.failed);
    s = base;
    CHECK(!ibs_begin_aa(&s, 20u * MS - 1u) && s.failed);
    s = base;
    CHECK(!ibs_a0_complete(&s, 1, 1, 20u * MS - 1u) && s.failed);
    s = base;
    CHECK(!ibs_aa_complete(&s, 20u * MS - 1u) && s.failed);
    CHECK(ibs_init(&s, UINT64_MAX));
    CHECK(!ibs_begin_a0(&s, UINT64_MAX));
    CHECK(s.failed && s.reason == IBS_OVERFLOW && !s.a0_rounds);
    CHECK(ibs_init(&s, 0)); s.round_id = UINT64_MAX;
    CHECK(!ibs_begin_a0(&s, 0) && s.failed && s.reason == IBS_OVERFLOW);
    CHECK(ibs_init(&s, 0)); s.a0_rounds = UINT64_MAX;
    CHECK(!ibs_begin_a0(&s, 0) && s.failed && s.reason == IBS_OVERFLOW);
    CHECK(ibs_init(&s, 0) && ibs_begin_a0(&s, 0));
    if (finish_round(&s, MS)) return 1;
    s.aa_attempts = UINT64_MAX;
    CHECK(!ibs_begin_aa(&s, 6u * MS));
    CHECK(s.failed && s.reason == IBS_OVERFLOW && !s.aa_pending);
    CHECK(!strcmp(ibs_reason_name(IBS_CLOCK), "clock_reversed"));
    CHECK(!strcmp(ibs_reason_name((ibs_reason)999), "unknown"));
    return 0;
}

static ifc_input fresh(uint64_t now, uint16_t position)
{
    ifc_input in = {0};
    in.now_ns = in.feedback_ns = now;
    in.can_trustworthy = in.feedback_valid = in.exposure_persisted = true;
    in.position = position;
    return in;
}

static int fsm_defer_stop_hold_once(void)
{
    ibs_schedule s;
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    unsigned i;
    ifc_init(&m);
    CHECK(ifc_start(&m, 0, true, true, true, 0));
    CHECK(ibs_init(&s, 5125u * MS));
    in = fresh(5125u * MS, 0); out = ifc_step(&m, &in);
    CHECK(out.command == IFC_MOVE && out.position == 1);
    CHECK(ibs_plan(&s, in.now_ns, true) == IBS_A0_ROUND);
    CHECK(!m.attempts && !s.aa_attempts); /* Caller must defer commit. */
    CHECK(ibs_begin_a0(&s, in.now_ns));
    if (finish_round(&s, 5130u * MS)) return 1;
    CHECK(ibs_plan(&s, 5135u * MS - 1u, true) == IBS_WAIT);
    CHECK(!m.attempts && !m.reference && !s.aa_attempts);
    in = fresh(5135u * MS, 0); out = ifc_step(&m, &in);
    CHECK(ibs_plan(&s, in.now_ns, true) == IBS_AA_SLOT);
    CHECK(ifc_begin_attempt(&m, &in, out.command, out.position));
    CHECK(ibs_begin_aa(&s, in.now_ns));
    in = fresh(5136u * MS, 1); in.stop_requested = true;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_NO_COMMAND && m.pending && !m.holds);
    CHECK(ibs_plan(&s, in.now_ns, true) == IBS_WAIT && s.aa_pending);
    CHECK(ibs_aa_complete(&s, 5140u * MS));
    in = fresh(5140u * MS, 1); in.stop_requested = true;
    in.ack_serial = 1; in.ack_ns = in.now_ns;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_HOLD && !m.pending);
    for (i = 0; i < 5; ++i) {
        in = fresh((5140u + (uint64_t)i) * MS, 1); in.stop_requested = true;
        out = ifc_step(&m, &in);
        CHECK(out.command == IFC_HOLD);
        CHECK(ibs_plan(&s, in.now_ns, true) == IBS_WAIT);
        CHECK(!m.holds && !m.pending && m.attempts == 1 && s.aa_attempts == 1);
    }
    in = fresh(5145u * MS, 1); in.stop_requested = true;
    out = ifc_step(&m, &in);
    CHECK(ibs_plan(&s, in.now_ns, true) == IBS_AA_SLOT);
    CHECK(ifc_begin_attempt(&m, &in, out.command, out.position));
    CHECK(ibs_begin_aa(&s, in.now_ns));
    CHECK(m.holds == 1 && s.aa_attempts == 2 && m.attempts == 2);
    CHECK(!ifc_begin_attempt(&m, &in, IFC_HOLD, 1));
    CHECK(!ibs_begin_aa(&s, in.now_ns));
    CHECK(m.holds == 1 && s.aa_attempts == 2);
    CHECK(ibs_aa_complete(&s, 5146u * MS));
    in = fresh(5146u * MS, 1); in.ack_serial = 2; in.ack_ns = in.now_ns;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_HOLD_VERIFY);
    for (i = 0; i < 6; ++i) {
        uint64_t query = (5225u + (uint64_t)i * 100u) * MS;
        CHECK(ibs_begin_a0(&s, query));
        if (finish_round(&s, query + 5u * MS)) return 1;
        in = fresh(query + 5u * MS, 1); in.feedback_ns = query;
        out = ifc_step(&m, &in);
        CHECK(out.command == IFC_NO_COMMAND && m.holds == 1 && s.aa_attempts == 2);
    }
    CHECK(m.state == IFC_DONE && m.stop_feedback_verified);
    CHECK(!strcmp(m.stop_reason, "software_stop_requested"));
    return 0;
}

static int defer_cannot_extend_feedback_freshness(void)
{
    ibs_schedule s;
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    ifc_init(&m); CHECK(ifc_start(&m, 0, true, true, true, 0));
    CHECK(ibs_init(&s, 5100u * MS) && ibs_begin_a0(&s, 5100u * MS));
    if (finish_round(&s, 5100u * MS)) return 1;
    in = fresh(5125u * MS, 0); in.feedback_ns = 5100u * MS;
    out = ifc_step(&m, &in);
    CHECK(ifc_begin_attempt(&m, &in, out.command, out.position));
    CHECK(ibs_begin_aa(&s, in.now_ns));
    CHECK(ibs_aa_complete(&s, 5246u * MS));
    in = fresh(5246u * MS, 0); in.feedback_ns = 5100u * MS;
    in.stop_requested = true; in.ack_serial = 1; in.ack_ns = in.now_ns;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_HOLD && !m.holds);
    CHECK(ibs_plan(&s, in.now_ns, true) == IBS_WAIT && s.reason == IBS_ACK_QUIET);
    in = fresh(5250u * MS, 0); in.feedback_ns = 5100u * MS;
    in.stop_requested = true;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_POWER_REQUIRED);
    CHECK(!m.holds && m.attempts == 1 && s.aa_attempts == 1);
    CHECK(ibs_plan(&s, in.now_ns, true) == IBS_WAIT);
    CHECK(ibs_plan(&s, 5251u * MS, true) == IBS_A0_ROUND);
    CHECK(m.state == IFC_POWER_REQUIRED && !m.holds); /* No late rescue. */
    return 0;
}

int main(void)
{
    unsigned order[6] = {0};
    if (boundaries_and_priority() || aa_exclusion_and_ack_quiet() ||
        all_reply_orders(0, 0, order) || rejected_input_latches() ||
        fsm_defer_stop_hold_once() || defer_cannot_extend_feedback_freshness()) return 1;
    CHECK(permutations == 720);
    (void)printf("independent bus schedule review: %u assertions, %u reply permutations PASS; TEST_ONLY no hardware\n", checks, permutations);
    return 0;
}
