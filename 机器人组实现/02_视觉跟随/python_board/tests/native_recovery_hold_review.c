/* Independent TEST_ONLY recovery counterexamples: no device/proof I/O.
 * Caller booleans and ACKs below are synthetic, never onsite qualification. */
#include "../recovery_hold_fsm.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define MS UINT64_C(1000000)
static unsigned checks;
#define CHECK(t) do { ++checks; if (!(t)) { \
    (void)fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #t); return 1; \
} } while (0)

static rhp_input fresh(uint64_t now, uint16_t position)
{
    rhp_input in = {0};
    in.now_ns = in.feedback_query_ns = in.feedback_rx_ns = now;
    in.actual_position = position;
    in.all_six_fresh_healthy = in.can_trustworthy = in.ledger_verified = true;
    in.attempt_durable = in.slot_ready = true;
    return in;
}
static int prepared(rhp_machine *m)
{
    rhp_init(m);
    CHECK(rhp_start(m, 0));
    CHECK(rhp_prepared(m, MS, true));
    CHECK(m->state == RHP_WAIT_SLOT && m->attempt_durable && !m->attempts);
    return 0;
}
static int verify(rhp_machine *m, uint16_t target)
{
    rhp_input in;
    rhp_output out;
    if (prepared(m)) return 1;
    in = fresh(10u * MS, target);
    CHECK(rhp_commit(m, &in, target));
    CHECK(m->state == RHP_WAIT_ACK && m->attempts == 1 && m->committed);
    in = fresh(20u * MS, target); in.ack_dual_matched = true; in.ack_ns = in.now_ns;
    out = rhp_step(m, &in);
    CHECK(out.command == RHP_NO_COMMAND && m->state == RHP_VERIFY);
    CHECK(!m->feedback_hold_verified && m->stable_count == 0);
    return 0;
}

static int manual_preflight_and_terminal(void)
{
    rhp_machine m;
    rhp_input in = fresh(0, 0);
    rhp_output out;
    rhp_init(&m); out = rhp_step(&m, &in);
    CHECK(out.command == RHP_NO_COMMAND && m.state == RHP_STANDBY);
    CHECK(!rhp_prepared(&m, 0, true) && m.state == RHP_STANDBY);
    in.stop_requested = true; out = rhp_step(&m, &in);
    CHECK(m.state == RHP_FAILED && !m.attempts && out.command == RHP_NO_COMMAND);
    CHECK(!rhp_start(&m, 1));
    rhp_init(&m); CHECK(rhp_start(&m, 0));
    CHECK(!rhp_start(&m, 0) && m.state == RHP_PREFLIGHT);
    CHECK(!rhp_prepared(&m, MS, false));
    CHECK(m.state == RHP_FAILED && !m.attempts && !m.attempt_durable);
    rhp_init(&m); CHECK(rhp_start(&m, 0));
    CHECK(!rhp_prepared(&m, RHP_WHOLE_NS, true) && m.state == RHP_FAILED);
    rhp_init(&m); CHECK(!rhp_start(&m, UINT64_MAX - RHP_WHOLE_NS + 1u));
    CHECK(m.state == RHP_FAILED && !m.started);
    rhp_init(&m); CHECK(rhp_start(&m, 100));
    CHECK(!rhp_prepared(&m, 99, true) && m.state == RHP_FAILED);
    CHECK(!strcmp(m.reason, "clock_reversed_no_retry"));
    if (prepared(&m)) return 1;
    in = fresh(2u * MS, 0); in.stop_requested = true;
    out = rhp_step(&m, &in);
    CHECK(m.state == RHP_FAILED && !m.attempts && m.attempt_durable);
    CHECK(out.command == RHP_NO_COMMAND);
    CHECK(!rhp_start(&m, 3u * MS) && !rhp_prepared(&m, 3u * MS, true));
    return 0;
}

static int wait_and_final_candidate(void)
{
    rhp_machine m;
    rhp_input in;
    rhp_output out;
    unsigned i;
    if (prepared(&m)) return 1;
    for (i = 0; i < 100; ++i) {
        in = fresh((2u + (uint64_t)i) * MS, 32); in.slot_ready = false;
        out = rhp_step(&m, &in);
        CHECK(out.command == RHP_NO_COMMAND && m.state == RHP_WAIT_SLOT);
        CHECK(!m.committed && !m.attempts && m.attempt_durable);
    }
    in = fresh(102u * MS, 32); out = rhp_step(&m, &in);
    CHECK(out.command == RHP_HOLD_CURRENT_ONCE && out.position == 32 && !m.attempts);
    in = fresh(103u * MS, 33);
    CHECK(!rhp_commit(&m, &in, 32));
    CHECK(m.state == RHP_FAILED && !m.attempts && m.attempt_durable);
    CHECK(!rhp_commit(&m, &in, 33));
    if (prepared(&m)) return 1;
    in = fresh(2u * MS, 0); CHECK(rhp_commit(&m, &in, 0));
    CHECK(m.attempts == 1 && m.committed);
    rhp_fail(&m, "TEST_ONLY_stop_after_commit_before_syscall");
    CHECK(m.state == RHP_FAILED && m.attempts == 1 && m.attempt_durable);
    CHECK(!rhp_commit(&m, &in, 0));
    CHECK(!strcmp(m.reason, "TEST_ONLY_stop_after_commit_before_syscall"));
    if (prepared(&m)) return 1;
    in = fresh(RHP_WHOLE_NS, 0); out = rhp_step(&m, &in);
    CHECK(m.state == RHP_FAILED && !m.attempts && out.command == RHP_NO_COMMAND);
    return 0;
}

static int freshness_and_trust_boundaries(void)
{
    rhp_machine m;
    rhp_input in;
    rhp_output out;
    unsigned kind;
    if (prepared(&m)) return 1;
    in = fresh(200u * MS, 160); in.feedback_query_ns = in.feedback_rx_ns = 50u * MS + 1u;
    out = rhp_step(&m, &in);
    CHECK(out.command == RHP_HOLD_CURRENT_ONCE && out.position == 160);
    CHECK(rhp_commit(&m, &in, 160) && m.attempts == 1);
    for (kind = 0; kind < 12; ++kind) {
        if (prepared(&m)) return 1;
        in = fresh(200u * MS, 32);
        switch (kind) {
        case 0: in.feedback_query_ns = in.feedback_rx_ns = 50u * MS; break;
        case 1: in.feedback_rx_ns = in.now_ns + 1u; break;
        case 2: in.feedback_query_ns = in.now_ns + 1u; break;
        case 3: in.actual_position = 161; break;
        case 4: in.velocity = 1; break;
        case 5: in.velocity = -1; break;
        case 6: in.status = 1; break;
        case 7: in.all_six_fresh_healthy = false; break;
        case 8: in.can_trustworthy = false; break;
        case 9: in.ledger_verified = false; break;
        case 10: in.attempt_durable = false; break;
        default: in.stop_requested = true; break;
        }
        out = rhp_step(&m, &in);
        CHECK(m.state == RHP_FAILED && out.command == RHP_NO_COMMAND && !m.attempts);
        CHECK(!rhp_commit(&m, &in, 32));
        CHECK(m.state == RHP_FAILED && m.attempt_durable);
    }
    return 0;
}

static int ack_boundary_and_stop_after_send(void)
{
    rhp_machine m;
    rhp_input in;
    rhp_output out;
    unsigned boundary, i;
    for (boundary = 0; boundary < 2; ++boundary) {
        if (prepared(&m)) return 1;
        in = fresh(10u * MS, 32); CHECK(rhp_commit(&m, &in, 32));
        in = fresh(210u * MS - (boundary ? 0u : 1u), 32);
        in.ack_dual_matched = true; in.ack_ns = in.now_ns;
        out = rhp_step(&m, &in);
        CHECK(out.command == RHP_NO_COMMAND && m.attempts == 1);
        CHECK(m.state == (boundary ? RHP_FAILED : RHP_VERIFY));
        CHECK(!m.feedback_hold_verified);
    }
    if (prepared(&m)) return 1;
    in = fresh(10u * MS, 0); CHECK(rhp_commit(&m, &in, 0));
    for (i = 0; i < 100; ++i) {
        in = fresh((11u + (uint64_t)i) * MS, 0); in.stop_requested = true;
        out = rhp_step(&m, &in);
        CHECK(out.command == RHP_NO_COMMAND && m.state == RHP_WAIT_ACK && m.attempts == 1);
    }
    in = fresh(111u * MS, 0); in.stop_requested = true;
    in.ack_dual_matched = true; in.ack_ns = in.now_ns;
    out = rhp_step(&m, &in);
    CHECK(m.state == RHP_VERIFY && m.stop_seen && m.attempts == 1);
    CHECK(out.command == RHP_NO_COMMAND);
    in = fresh(m.verify_until_ns, 0); out = rhp_step(&m, &in);
    CHECK(m.state == RHP_FAILED && !m.feedback_hold_verified && m.attempts == 1);
    return 0;
}

static int post_ack_query_unique_time_tolerance_and_span(void)
{
    rhp_machine m;
    rhp_input in;
    rhp_output out;
    unsigned i, kind;
    if (verify(&m, 32)) return 1;
    in = fresh(25u * MS - 1u, 32); out = rhp_step(&m, &in);
    CHECK(!m.stable_count && !m.feedback_hold_verified && out.command == RHP_NO_COMMAND);
    in = fresh(25u * MS, 32); out = rhp_step(&m, &in);
    CHECK(m.stable_count == 1 && !m.feedback_hold_verified);
    for (i = 0; i < 10; ++i) {
        in = fresh((26u + (uint64_t)i) * MS, 32); in.feedback_query_ns = 25u * MS;
        out = rhp_step(&m, &in);
        CHECK(m.stable_count == 1 && !m.feedback_hold_verified);
    }
    for (i = 1; i <= 3; ++i) {
        in = fresh((25u + (uint64_t)i * 100u) * MS, 32); out = rhp_step(&m, &in);
        CHECK(m.stable_count == i + 1u && m.state == RHP_VERIFY);
    }
    in = fresh(525u * MS - 1u, 32); out = rhp_step(&m, &in);
    CHECK(m.stable_count == 5 && m.state == RHP_VERIFY && !m.feedback_hold_verified);
    in = fresh(525u * MS, 32); out = rhp_step(&m, &in);
    CHECK(m.state == RHP_DONE && m.feedback_hold_verified && m.attempts == 1);
    CHECK(out.command == RHP_NO_COMMAND);
    for (kind = 0; kind < 4; ++kind) {
        if (verify(&m, 32)) return 1;
        for (i = 0; i < 5; ++i) {
            uint16_t pos = kind == 0 ? 40 : kind == 1 ? 41 :
                (uint16_t)(31u + (i % 2u) * (kind == 2 ? 2u : 3u));
            in = fresh((25u + (uint64_t)i * 125u) * MS, pos);
            out = rhp_step(&m, &in);
            CHECK(out.command == RHP_NO_COMMAND && m.attempts == 1);
        }
        CHECK(m.feedback_hold_verified == (kind == 0 || kind == 2));
        CHECK(m.state == ((kind == 0 || kind == 2) ? RHP_DONE : RHP_VERIFY));
    }
    return 0;
}

int main(void)
{
    if (manual_preflight_and_terminal() || wait_and_final_candidate() ||
        freshness_and_trust_boundaries() || ack_boundary_and_stop_after_send() ||
        post_ack_query_unique_time_tolerance_and_span()) return 1;
    (void)printf("independent recovery hold review: %u assertions PASS; TEST_ONLY no hardware or physical-stop proof\n", checks);
    return 0;
}
