/* Tracking-v3 independent review. Reuses old API and predeclared tolerance
 * tests; old-goal fixture now stays within tracking16 until its final pending
 * target. All ACK/feedback inputs are TEST_ONLY, not hardware evidence. */
#define main v1_commission_review_main
#include "native_index_follow_commission_review.c"
#undef main

static int enter_hold_verify(ifc_machine *m, uint16_t hold_target)
{
    ifc_input in;
    ifc_output out;
    ifc_init(m); CHECK(ifc_start(m, 0, true, true, true, 0));
    in = fresh(5125u * MS, 0);
    CHECK(ifc_begin_attempt(m, &in, IFC_MOVE, 1));
    in = fresh(5135u * MS, hold_target); in.stop_requested = true;
    in.ack_serial = 1; in.ack_ns = in.now_ns;
    out = ifc_step(m, &in);
    CHECK(out.command == IFC_HOLD && out.position == hold_target);
    CHECK(ifc_begin_attempt(m, &in, IFC_HOLD, hold_target));
    in = fresh(5145u * MS, hold_target); in.ack_serial = 2; in.ack_ns = in.now_ns;
    out = ifc_step(m, &in);
    CHECK(out.command == IFC_NO_COMMAND && m->state == IFC_HOLD_VERIFY && !m->stop_feedback_verified);
    return 0;
}

static int target_error_boundary(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    unsigned error, i;
    CHECK(IFC_HOLD_TARGET_TOLERANCE == 8u && IFC_STABLE_SPAN_MAX == 2u);
    CHECK(IFC_STABLE_MIN_SAMPLES == 5u && IFC_STABLE_MIN_NS == 500u * MS);
    for (error = 8; error <= 9; ++error) {
        if (enter_hold_verify(&m, 32)) return 1;
        for (i = 0; i < 6; ++i) {
            in = fresh((5200u + (uint64_t)i * 100u) * MS, (uint16_t)(32u + error));
            out = ifc_step(&m, &in);
            CHECK(out.command == IFC_NO_COMMAND);
            if (i < 5 || error == 9) CHECK(!m.stop_feedback_verified);
        }
        if (error == 8) CHECK(m.state == IFC_DONE && m.stop_feedback_verified);
        else {
            CHECK(m.state == IFC_HOLD_VERIFY && !m.stop_feedback_verified);
            in = fresh(m.stop_until, 41);
            out = ifc_step(&m, &in);
            CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_POWER_REQUIRED && !m.stop_feedback_verified);
        }
        CHECK(m.holds == 1 && m.attempts == 2);
    }
    return 0;
}

static int cluster_span_boundary(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    unsigned span, i;
    for (span = 2; span <= 3; ++span) {
        if (enter_hold_verify(&m, 32)) return 1;
        for (i = 0; i < 6; ++i) {
            uint16_t position = (uint16_t)(31u + (i % 2u ? span : 0u));
            in = fresh((5200u + (uint64_t)i * 100u) * MS, position);
            out = ifc_step(&m, &in);
            CHECK(out.command == IFC_NO_COMMAND);
        }
        CHECK(m.stop_feedback_verified == (span == 2));
        CHECK(m.state == (span == 2 ? IFC_DONE : IFC_HOLD_VERIFY));
        CHECK(m.holds == 1);
    }
    return 0;
}

static int time_and_query_boundary(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    const uint64_t times[] = {5200u * MS, 5300u * MS, 5400u * MS,
                             5500u * MS, 5700u * MS - 1u};
    unsigned i;
    if (enter_hold_verify(&m, 32)) return 1;
    /* Input receive/processing is later, but query reference <=ACK cannot count. */
    in = fresh(5200u * MS, 32); in.feedback_ns = m.hold_ack_ns;
    out = ifc_step(&m, &in);
    CHECK(m.stable_count == 0 && !m.stop_feedback_verified && out.command == IFC_NO_COMMAND);
    for (i = 0; i < sizeof times / sizeof times[0]; ++i) {
        in = fresh(times[i], 32);
        out = ifc_step(&m, &in);
        CHECK(!m.stop_feedback_verified && out.command == IFC_NO_COMMAND);
    }
    CHECK(m.stable_count == 5 && m.stable_last_ns - m.stable_since == 500u * MS - 1u);
    in = fresh(5700u * MS, 32);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_DONE && m.stop_feedback_verified);
    return 0;
}

static int old_far_goal_is_not_stopped_near_hold(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    unsigned i;
    ifc_init(&m); CHECK(ifc_start(&m, 0, true, true, true, 0));
    for (i = 1; i <= 128; ++i) {
        in = fresh((5000u + (uint64_t)i * 125u) * MS, i > 112 ? 112 : (uint16_t)(i - 1u));
        CHECK(ifc_begin_attempt(&m, &in, IFC_MOVE, (uint16_t)i));
        if (i < 128) {
            in = fresh(in.now_ns + MS, i > 112 ? 112 : (uint16_t)i);
            in.ack_serial = m.pending_serial; in.ack_ns = in.now_ns;
            (void)ifc_step(&m, &in);
        }
    }
    in = fresh(21010u * MS, 112); in.stop_requested = true;
    out = ifc_step(&m, &in);
    CHECK(m.state == IFC_STOP_DRAIN && m.pending && out.command == IFC_NO_COMMAND);
    in = fresh(21020u * MS, 112); in.stop_requested = true;
    in.ack_serial = 128; in.ack_ns = in.now_ns;
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_HOLD && out.position == 112);
    CHECK(ifc_begin_attempt(&m, &in, IFC_HOLD, 112));
    in = fresh(21030u * MS, 112); in.ack_serial = 129; in.ack_ns = in.now_ns;
    (void)ifc_step(&m, &in);
    CHECK(m.state == IFC_HOLD_VERIFY && !m.stop_feedback_verified);
    /* Stable old goal128 is NOT proof the requested hold112 stopped the drive. */
    for (i = 0; i < 6; ++i) {
        in = fresh((21100u + (uint64_t)i * 100u) * MS, 128);
        out = ifc_step(&m, &in);
        CHECK(!m.stop_feedback_verified && out.command == IFC_NO_COMMAND);
    }
    in = fresh(m.stop_until, 128);
    out = ifc_step(&m, &in);
    CHECK(m.state == IFC_POWER_REQUIRED && !m.stop_feedback_verified && out.command == IFC_NO_COMMAND);
    CHECK(m.holds == 1 && m.attempts == 129);
    return 0;
}

int prior_v2_compatible_review(void)
{
    if (v1_commission_review_main() || target_error_boundary() || cluster_span_boundary() ||
        time_and_query_boundary() || old_far_goal_is_not_stopped_near_hold()) return 1;
    (void)printf("PASS %u total independent new-tolerance commission assertions; no physical Stop qualification\n", checks);
    return 0;
}

/* ACKs acknowledge only protocol receipt: actual P deliberately stays zero. */
static int acknowledged_but_motionless_until_sixteen(ifc_machine *m)
{
    ifc_input in;
    ifc_output out;
    unsigned i;
    ifc_init(m); CHECK(ifc_start(m, 0, true, true, true, 0));
    for (i = 1; i <= 16; ++i) {
        in = fresh((5000u + (uint64_t)i * 125u) * MS, 0);
        out = ifc_step(m, &in);
        CHECK(out.command == IFC_MOVE && out.position == i);
        CHECK(ifc_begin_attempt(m, &in, IFC_MOVE, (uint16_t)i));
        CHECK(m->moves == i && m->attempts == i);
        in = fresh(in.now_ns + MS, 0);
        in.ack_serial = m->pending_serial; in.ack_ns = in.now_ns;
        out = ifc_step(m, &in);
        CHECK(out.command == IFC_NO_COMMAND && !m->pending && m->state == IFC_RUN);
    }
    CHECK(m->reference == 16 && m->moves == 16 && m->holds == 0);
    return 0;
}

static int tracking_sixteen_and_seventeen(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    CHECK(IFC_TRACKING_ERROR_MAX == 16u);
    /* next17 - actual1 ==16 is still an allowed, consumed MOVE. */
    if (acknowledged_but_motionless_until_sixteen(&m)) return 1;
    in = fresh(7125u * MS, 1);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_MOVE && out.position == 17);
    CHECK(ifc_begin_attempt(&m, &in, IFC_MOVE, 17));
    CHECK(m.moves == 17 && m.attempts == 17 && m.pending && !m.hold_attempted);
    /* next17 - actual0 ==17 must NOT be accepted, even after 16 good ACKs. */
    if (acknowledged_but_motionless_until_sixteen(&m)) return 1;
    in = fresh(7125u * MS, 0);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_HOLD && out.position == 0 && m.state == IFC_STOP_DRAIN);
    CHECK(m.moves == 16 && m.attempts == 16 && !m.pending);
    CHECK(m.stop_reason && strstr(m.stop_reason, "tracking_error_exceeded_16") != NULL);
    CHECK(!ifc_begin_attempt(&m, &in, IFC_MOVE, 17));
    CHECK(m.moves == 16 && m.attempts == 16);
    CHECK(ifc_begin_attempt(&m, &in, IFC_HOLD, 0));
    CHECK(m.holds == 1 && m.attempts == 17 && m.moves == 16);
    CHECK(!ifc_begin_attempt(&m, &in, IFC_HOLD, 0));
    /* Successful feedback confirmation of hold0 must preserve trial failure. */
    in = fresh(7135u * MS, 0); in.ack_serial = m.pending_serial; in.ack_ns = in.now_ns;
    (void)ifc_step(&m, &in);
    CHECK(m.state == IFC_HOLD_VERIFY && !m.stop_feedback_verified);
    for (unsigned i = 0; i < 6; ++i) {
        in = fresh((7150u + (uint64_t)i * 100u) * MS, 0);
        out = ifc_step(&m, &in);
        CHECK(out.command == IFC_NO_COMMAND);
    }
    CHECK(m.state == IFC_DONE && m.stop_feedback_verified && m.moves == 16 && m.holds == 1);
    CHECK(strstr(m.stop_reason, "tracking_error_exceeded_16") != NULL);
    CHECK(!ifc_start(&m, 8000u * MS, true, true, true, 0));
    return 0;
}

static int candidate_commit_feedback_changes(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    /* A candidate seen with actual1 is not authority after actual changes. */
    if (acknowledged_but_motionless_until_sixteen(&m)) return 1;
    in = fresh(7125u * MS, 1);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_MOVE && out.position == 17);
    in.position = 0;
    CHECK(!ifc_begin_attempt(&m, &in, IFC_MOVE, 17));
    CHECK(m.state == IFC_STOP_DRAIN && m.moves == 16 && m.attempts == 16 && !m.pending);
    CHECK(strstr(m.stop_reason, "tracking_error_exceeded_16") != NULL);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_HOLD && out.position == 0);
    /* Absolute difference also protects an actual position ahead of target. */
    if (acknowledged_but_motionless_until_sixteen(&m)) return 1;
    in = fresh(7125u * MS, 1);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_MOVE);
    in.position = 34;
    CHECK(!ifc_begin_attempt(&m, &in, IFC_MOVE, 17));
    CHECK(m.state == IFC_STOP_DRAIN && m.moves == 16 && m.attempts == 16);
    out = ifc_step(&m, &in);
    CHECK(out.command == IFC_HOLD && out.position == 34);
    CHECK(ifc_begin_attempt(&m, &in, IFC_HOLD, 34));
    CHECK(m.holds == 1);
    return 0;
}

static int untrustworthy_change_never_adds_hold(void)
{
    ifc_machine m;
    ifc_input in;
    ifc_output out;
    for (unsigned scenario = 0; scenario < 5; ++scenario) {
        if (acknowledged_but_motionless_until_sixteen(&m)) return 1;
        in = fresh(7125u * MS, 1);
        out = ifc_step(&m, &in);
        CHECK(out.command == IFC_MOVE && out.position == 17);
        if (scenario == 0) in.position = 161;
        if (scenario == 1) in.can_trustworthy = false;
        if (scenario == 2) in.protocol_failed = true;
        if (scenario == 3) in.feedback_valid = false;
        if (scenario == 4) in.feedback_ns = in.now_ns - 150u * MS;
        CHECK(!ifc_begin_attempt(&m, &in, IFC_MOVE, 17));
        CHECK(m.state == IFC_POWER_REQUIRED && m.moves == 16 && m.attempts == 16 && m.holds == 0);
        in = fresh(7130u * MS, 0);
        out = ifc_step(&m, &in);
        CHECK(out.command == IFC_NO_COMMAND && m.state == IFC_POWER_REQUIRED && !m.stop_feedback_verified);
        CHECK(!ifc_begin_attempt(&m, &in, IFC_HOLD, 0));
        CHECK(m.holds == 0);
    }
    return 0;
}

int main(void)
{
    if (prior_v2_compatible_review() || tracking_sixteen_and_seventeen() ||
        candidate_commit_feedback_changes() || untrustworthy_change_never_adds_hold()) return 1;
    (void)printf("PASS %u total independent tracking-v3 TEST_ONLY assertions; protocol ACK is not movement/Stop qualification\n", checks);
    return 0;
}
