#include "../index_follow_commission.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

/* Independent TEST_ONLY state-machine counterexamples. No CAN/device access.
 * Synthetic ACK/feedback facts below are not hardware qualification. */
static unsigned checks;
#define MS UINT64_C(1000000)
#define CHECK(test) do { ++checks; if (!(test)) { \
    (void)fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #test); return 1; \
} } while (0)

static ifc_input fresh(uint64_t now, uint16_t position)
{
    ifc_input in = {0};
    in.now_ns = in.feedback_ns = now;
    in.can_trustworthy = in.feedback_valid = in.exposure_persisted = true;
    in.position = position;
    return in;
}

static int start_guards(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    ifc_init(&m);
    CHECK(m.state == IFC_STANDBY && !m.started);
    CHECK(!ifc_start(&m, 0, false, true, true, 0));
    CHECK(!ifc_start(&m, 0, true, false, true, 0));
    CHECK(!ifc_start(&m, 0, true, true, false, 0));
    CHECK(!ifc_start(&m, 0, true, true, true, 1));
    CHECK(!ifc_start(&m, UINT64_MAX, true, true, true, 0));
    CHECK(m.state == IFC_STANDBY && !m.started && !m.attempts);
    CHECK(ifc_start(&m, 0, true, true, true, 0));
    CHECK(!ifc_start(&m, 1, true, true, true, 0));
    in = fresh(5000u * MS, 0); in.stop_requested = true;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_DONE && !m.attempts);
    CHECK(!m.stop_feedback_verified);
    CHECK(!ifc_start(&m, 6000u * MS, true, true, true, 0));
    ifc_init(&m); in = fresh(0, 0); in.stop_requested = true;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_DONE);
    CHECK(!ifc_start(&m, 1, true, true, true, 0));
    return 0;
}

static int stop_at_commit_and_pending_drain(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    ifc_init(&m); CHECK(ifc_start(&m, 0, true, true, true, 0));
    in = fresh(5125u * MS, 0);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_MOVE && out.position == 1 && !m.attempts);
    in.stop_requested = true;
    CHECK(!ifc_begin_attempt(&m, &in, out.command, out.position));
    CHECK(!m.attempts && m.state == IFC_DONE);
    ifc_init(&m); CHECK(ifc_start(&m, 0, true, true, true, 0));
    in = fresh(5125u * MS, 0);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_MOVE && out.position == 1);
    CHECK(!ifc_begin_attempt(&m, &in, IFC_MOVE, 2));
    CHECK(!m.attempts && m.reference == 0);
    CHECK(ifc_begin_attempt(&m, &in, IFC_MOVE, 1));
    CHECK(m.pending && m.attempts == 1 && m.moves == 1 && m.reference == 1);
    CHECK(!ifc_begin_attempt(&m, &in, IFC_MOVE, 1));
    CHECK(m.attempts == 1);
    in = fresh(5150u * MS, 1); in.stop_requested = true;
    out = ifc_step(&m, &in);
    CHECK(m.state == IFC_STOP_DRAIN && m.pending && out.command == IFC_NO_COMMAND);
    CHECK(!ifc_begin_attempt(&m, &in, IFC_HOLD, 1));
    CHECK(!m.holds);
    in = fresh(5160u * MS, 1); in.stop_requested = true;
    in.ack_serial = 1; in.ack_ns = in.now_ns;
    out = ifc_step(&m, &in);
    CHECK(!m.pending && m.state == IFC_STOP_DRAIN);
    CHECK(out.command == IFC_HOLD && out.position == 1);
    CHECK(ifc_begin_attempt(&m, &in, IFC_HOLD, 1));
    CHECK(m.pending && m.hold_attempted && m.holds == 1 && m.attempts == 2 && m.state == IFC_HOLD_ACK);
    CHECK(!ifc_begin_attempt(&m, &in, IFC_HOLD, 1));
    CHECK(m.holds == 1);
    return 0;
}

static int hold_requires_new_stable_feedback(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    unsigned i;
    ifc_init(&m); CHECK(ifc_start(&m, 0, true, true, true, 0));
    in = fresh(5125u * MS, 0);
    CHECK(ifc_begin_attempt(&m, &in, IFC_MOVE, 1));
    in = fresh(5135u * MS, 1); in.stop_requested = true;
    in.ack_serial = 1; in.ack_ns = in.now_ns;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_HOLD && out.position == 1);
    CHECK(ifc_begin_attempt(&m, &in, IFC_HOLD, 1));
    in = fresh(5145u * MS, 1); in.ack_serial = 2; in.ack_ns = in.now_ns;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_HOLD_VERIFY);
    CHECK(!m.stop_feedback_verified && m.stable_count == 0);
    /* Repeated feedback from the ACK instant is not post-ACK stability. */
    in.now_ns = 5155u * MS;
    out = ifc_step(&m, &in);
    CHECK(!m.stop_feedback_verified && m.stable_count == 0);
    for (i = 0; i < 5; ++i) {
        in = fresh((5160u + (uint64_t)i * 100u) * MS, 1);
        out = ifc_step(&m, &in);
        CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_HOLD_VERIFY && !m.stop_feedback_verified);
    }
    CHECK(m.stable_count == 5 && m.stable_last_ns - m.stable_since == 400u * MS);
    /* A duplicate sample cannot make the fifth sample span 500ms. */
    in.now_ns += 20u * MS;
    out = ifc_step(&m, &in);
    CHECK(m.stable_count == 5 && !m.stop_feedback_verified && out.command == IFC_NO_COMMAND);
    in = fresh(5660u * MS, 1);
    out = ifc_step(&m, &in);
    CHECK(m.state == IFC_DONE && m.stop_feedback_verified && out.command == IFC_NO_COMMAND);
    CHECK(m.holds == 1 && m.moves == 1 && m.attempts == 2);
    CHECK(strstr(m.reason, "mechanical_confirmation_required") != NULL);
    in = fresh(6000u * MS, 1);
    CHECK(ifc_step(&m, &in).command == IFC_NO_COMMAND);
    CHECK(!ifc_start(&m, 6000u * MS, true, true, true, 0));
    return 0;
}

static int bad_ack_or_bus_never_adds_hold(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    unsigned scenario;
    for (scenario = 0; scenario < 6; ++scenario) {
        ifc_init(&m); CHECK(ifc_start(&m, 0, true, true, true, 0));
        in = fresh(5125u * MS, 0);
        CHECK(ifc_begin_attempt(&m, &in, IFC_MOVE, 1));
        in = fresh(5130u * MS, 1); in.stop_requested = true;
        if (scenario == 0) in.can_trustworthy = false;
        if (scenario == 1) in.protocol_failed = true;
        if (scenario == 2) in.exposure_persisted = false;
        if (scenario >= 3) {
            in = fresh(5325u * MS, 1); in.stop_requested = true;
            in.ack_serial = scenario == 3 ? 99 : 1;
            in.ack_ns = scenario == 5 ? 5326u * MS : scenario == 4 ? 5124u * MS : in.now_ns;
        }
        out = ifc_step(&m, &in);
        CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_POWER_REQUIRED);
        CHECK(m.holds == 0 && m.attempts == 1 && !m.stop_feedback_verified);
        in = fresh(5400u * MS, 1); in.ack_serial = 1; in.ack_ns = in.now_ns;
        out = ifc_step(&m, &in);
        CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_POWER_REQUIRED && m.holds == 0);
    }
    return 0;
}

static int no_catchup_and_bounded_endpoint(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    unsigned i;
    ifc_init(&m); CHECK(ifc_start(&m, 0, true, true, true, 0));
    in = fresh(8000u * MS, 0);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_MOVE && out.position == 1);
    CHECK(ifc_begin_attempt(&m, &in, IFC_MOVE, 1));
    in = fresh(8001u * MS, 1); in.ack_serial = 1; in.ack_ns = in.now_ns;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_NO_COMMAND);
    in = fresh(8124u * MS, 1);
    CHECK(ifc_step(&m, &in).command == IFC_NO_COMMAND);
    in = fresh(8125u * MS, 1);
    CHECK(ifc_step(&m, &in).position == 2);
    ifc_init(&m); CHECK(ifc_start(&m, 0, true, true, true, 0));
    for (i = 1; i <= 128; ++i) {
        in = fresh((5000u + (uint64_t)i * 125u) * MS, (uint16_t)(i - 1u));
        out = ifc_step(&m, &in);
        CHECK(out.command == IFC_MOVE && out.position == i && out.position <= 128u);
        CHECK(ifc_begin_attempt(&m, &in, IFC_MOVE, (uint16_t)i));
        in = fresh(in.now_ns + MS, (uint16_t)i);
        in.ack_serial = m.pending_serial; in.ack_ns = in.now_ns;
        out = ifc_step(&m, &in);
        CHECK(out.command != IFC_MOVE);
    }
    CHECK(m.moves == 128 && m.attempts == 128 && m.state == IFC_STOP_DRAIN);
    CHECK(out.command == IFC_HOLD && out.position == 128);
    return 0;
}

int main(void)
{
    if (start_guards() || stop_at_commit_and_pending_drain() ||
        hold_requires_new_stable_feedback() || bad_ack_or_bus_never_adds_hold() ||
        no_catchup_and_bounded_endpoint()) return 1;
    (void)printf("PASS %u independent TEST_ONLY commission assertions; no devices or motion qualification\n", checks);
    return 0;
}
