#include "../control_gate.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

/* TEST_ONLY fictitious coordinate units and times. Never mechanical parameters. */
static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)

static cg_config fixture_config(void)
{
    cg_config c = {0};
    unsigned i;
    c.version = 7;
    c.active_mask = CG_ALL_MASK;
    c.mechanical_verified = c.vision_verified = c.feedback_verified = true;
    c.stop_strategy_verified = c.lower_stop_verified = true;
    c.frame_max_age_ns = 1000;
    c.feedback_max_age_ns = 500;
    c.compute_max_age_ns = 200;
    c.max_step_ns = 300;
    for (i = 0; i < CG_CHANNELS; ++i) {
        c.node_id[i] = (uint16_t)(20 + i);
        c.minimum[i] = -10; c.maximum[i] = 10;
        c.max_speed[i] = 1e6; c.max_reference_error[i] = 2;
    }
    return c;
}

static cg_input fixture_input(uint64_t epoch, uint64_t seq, uint64_t now)
{
    cg_input in = {0};
    unsigned i;
    in.now_ns = now;
    in.manual_start_epoch = epoch;
    in.python_healthy = in.can_healthy = in.single_control_source = true;
    in.issued_epoch = epoch; in.issued_seq = seq; in.issued_rx_ns = now;
    in.target.present = true;
    in.target.epoch = epoch; in.target.seq = seq; in.target.config_version = 7;
    for (i = 0; i < CG_CHANNELS; ++i) {
        in.feedback[i].valid = true;
        in.feedback[i].epoch = epoch;
        in.feedback[i].node_id = (uint16_t)(20 + i);
        in.feedback[i].received_ns = now;
        in.feedback[i].position = 1;
        in.target.feature_valid[i] = in.target.position_present[i] = true;
        in.target.position[i] = 8;
    }
    return in;
}

static cg_output start(cg_gate *g, const cg_config *c)
{
    cg_input in = fixture_input(1, 1, 1000);
    cg_init(g, c, 1, 900);
    in.manual_start = true;
    return cg_step(g, &in);
}

/* Partial-axis fixtures remain invented. Zero inactive target storage means
 * absence, never a motor request to position zero or proof of physical disable. */
static cg_config subset_config(uint8_t mask)
{
    cg_config c = fixture_config();
    unsigned i;
    c.active_mask = mask;
    for (i = 0; i < CG_CHANNELS; ++i) if (!(mask & (UINT8_C(1) << i))) {
        c.node_id[i] = 0;
        c.minimum[i] = c.maximum[i] = 0;
        c.max_speed[i] = c.max_reference_error[i] = 0;
    }
    return c;
}

static cg_input subset_input(uint8_t mask, uint64_t epoch, uint64_t seq, uint64_t now)
{
    cg_input in = fixture_input(epoch, seq, now);
    unsigned i;
    for (i = 0; i < CG_CHANNELS; ++i) {
        if (mask & (UINT8_C(1) << i)) in.feedback[i].position = (double)(i + 1u);
        else {
            memset(&in.feedback[i], 0, sizeof in.feedback[i]);
            in.target.feature_valid[i] = in.target.position_present[i] = false;
            in.target.position[i] = 0;
        }
    }
    return in;
}

static cg_output subset_start(cg_gate *g, const cg_config *c)
{
    cg_input in = subset_input(c->active_mask, 1, 1, 1000);
    cg_init(g, c, 1, 900);
    in.manual_start = true;
    return cg_step(g, &in);
}

static int partial_axis_tests(void)
{
    cg_gate g;
    cg_config c;
    cg_input in;
    cg_output o;
    unsigned i, axis;
    const uint8_t invalid_masks[] = {0, 64, 128, 255};
    const uint8_t pair = UINT8_C(0x21); /* axes 0 and 5, deliberately nonadjacent */
    double saved[CG_CHANNELS];

    for (i = 0; i < sizeof invalid_masks / sizeof invalid_masks[0]; ++i) {
        c = fixture_config(); c.active_mask = invalid_masks[i];
        o = start(&g, &c);
        CHECK(o.decision == CG_BLOCKED && o.reason == CG_CONFIG);
        CHECK(o.reference_mask == 0 && !o.reference_valid && !o.tx_capable);
    }

    /* Exercise each selectable axis independently with no inactive feedback or
     * calibration. Observable first reference must equal THAT axis's actual
     * input (1..6), not the visual target 8 or a neighboring axis's feedback. */
    for (axis = 0; axis < CG_CHANNELS; ++axis) {
        uint8_t mask = (uint8_t)(UINT8_C(1) << axis);
        c = subset_config(mask);
        cg_init(&g, &c, 1, 900);
        in = subset_input(mask, 1, 1, 1000);
        o = cg_step(&g, &in);
        CHECK(o.decision == CG_READY && o.reference_mask == 0 && !o.reference_valid);
        in.manual_start = true; o = cg_step(&g, &in);
        CHECK(o.decision == CG_CANDIDATE && o.reference_mask == mask && o.reference_valid && !o.tx_capable);
        CHECK(o.reference[axis] == (double)(axis + 1u));
        for (i = 0; i < CG_CHANNELS; ++i) if (i != axis) CHECK(o.reference[i] == 0);
        in = subset_input(mask, 1, 2, 1100);
        in.target.position[axis] = -8;
        o = cg_step(&g, &in);
        CHECK(o.decision == CG_CANDIDATE && o.reference_mask == mask);
        CHECK(fabs(o.reference[axis] - ((double)(axis + 1u) - 0.1)) < 1e-12);
    }

    /* Inactive calibration is not silently required for a one-axis claim.
     * Declared node identities, however, cannot alias an active node. */
    c = subset_config(1); c.minimum[2] = NAN; c.max_speed[2] = -1;
    o = subset_start(&g, &c); CHECK(o.decision == CG_CANDIDATE && o.reference_mask == 1);
    c = subset_config(1); c.node_id[5] = c.node_id[0];
    CHECK(subset_start(&g, &c).reason == CG_CONFIG);
    c = subset_config(1); c.node_id[5] = 255;
    CHECK(subset_start(&g, &c).reason == CG_CONFIG);
    c = subset_config(1); c.node_id[0] = 0;
    CHECK(subset_start(&g, &c).reason == CG_CONFIG);

    /* No selected axis may borrow a global certification or a missing limit. */
    c = subset_config(pair); c.mechanical_verified = false;
    CHECK(subset_start(&g, &c).reason == CG_CONFIG);
    c = subset_config(pair); c.feedback_verified = false;
    CHECK(subset_start(&g, &c).reason == CG_CONFIG);
    c = subset_config(pair); c.max_speed[5] = 0;
    CHECK(subset_start(&g, &c).reason == CG_CONFIG);
    c = subset_config(pair); c.max_reference_error[5] = NAN;
    CHECK(subset_start(&g, &c).reason == CG_CONFIG);

    /* Failing the second selected axis cannot partially initialize or update
     * the first. Vary independently meaningful missing/invalid observations. */
    for (i = 0; i < 10u; ++i) {
        cg_reason expected = CG_FEEDBACK;
        c = subset_config(pair);
        o = subset_start(&g, &c); CHECK(o.reference_mask == pair);
        memcpy(saved, g.reference, sizeof saved);
        in = subset_input(pair, 1, 2, 1100);
        switch (i) {
        case 0: in.feedback[5].valid = false; break;
        case 1: in.feedback[5].epoch = 2; break;
        case 2: in.feedback[5].node_id = 99; break;
        case 3: in.feedback[5].received_ns = 600; break; /* age exactly limit */
        case 4: in.feedback[5].position = NAN; break;
        case 5: in.feedback[5].position = 11; break;
        case 6: in.target.feature_valid[5] = false; expected = CG_FEATURE; break;
        case 7: in.target.position_present[5] = false; expected = CG_FEATURE; break;
        case 8: in.target.position[5] = INFINITY; expected = CG_RANGE; break;
        default: in.feedback[5].position = -5; expected = CG_TRACKING; break;
        }
        o = cg_step(&g, &in);
        CHECK(o.decision == CG_STOP_REQUIRED && o.reason == expected);
        CHECK(o.reference_mask == 0 && !o.reference_valid && !o.tx_capable);
        CHECK(!memcmp(saved, g.reference, sizeof saved) && g.last_seq == 1 && g.reference_ns == 1000);
    }
    c = subset_config(pair); cg_init(&g, &c, 1, 900);
    in = subset_input(pair, 1, 1, 1000); in.manual_start = true;
    in.target.position_present[5] = false;
    o = cg_step(&g, &in);
    CHECK(o.decision == CG_BLOCKED && o.reference_mask == 0 && g.state == CG_STANDBY);
    CHECK(g.reference[0] == 0 && g.reference[5] == 0 && g.last_seq == 0);

    /* Unselected target content is rejected, not ignored, clamped or copied.
     * Even an overall absent target must not smuggle an inactive numeric value. */
    for (i = 0; i < 6u; ++i) {
        unsigned j;
        c = subset_config(1); (void)subset_start(&g, &c);
        in = subset_input(1, 1, 2, 1100);
        switch (i) {
        case 0: in.target.feature_valid[2] = true; break;
        case 1: in.target.position_present[2] = true; break;
        case 2: in.target.position[2] = -1; break;
        case 3: in.target.position[2] = NAN; break;
        case 4: in.target.position[2] = INFINITY; break;
        default: in.target.present = false; in.target.position[2] = 1; break;
        }
        o = cg_step(&g, &in);
        CHECK(o.decision == CG_STOP_REQUIRED && o.reason == CG_SCOPE);
        CHECK(o.reference_mask == 0 && !o.reference_valid);
        for (j = 0; j < CG_CHANNELS; ++j) CHECK(o.reference[j] == 0);
    }

    /* A mask is not a physical disable. Retained inactive node faults and
     * global CAN diagnostics still reject the selected group's candidate. */
    c = subset_config(1); (void)subset_start(&g, &c);
    in = subset_input(1, 1, 2, 1100); in.feedback[4].status = 7;
    o = cg_step(&g, &in);
    CHECK(o.decision == CG_STOP_REQUIRED && o.reason == CG_NODE_FAULT && o.reference_mask == 0);
    CHECK(cg_recover(&g, 2, 1200, true));
    in = subset_input(1, 2, 1, 1300); in.manual_start = true; in.feedback[4].status = 7;
    o = cg_step(&g, &in);
    CHECK(o.decision == CG_BLOCKED && o.reason == CG_NODE_FAULT && o.reference_mask == 0);
    (void)subset_start(&g, &c);
    in = subset_input(1, 1, 2, 1100); in.can_healthy = false;
    CHECK(cg_step(&g, &in).reason == CG_HEALTH);

    /* Altering the caller's original config cannot widen an initialized gate.
     * New certification is an external assertion, not something this pure
     * core can infer or manufacture by copying old boolean flags. */
    c = subset_config(1); cg_init(&g, &c, 1, 900);
    c = fixture_config(); c.active_mask = pair; c.version = 8;
    in = subset_input(1, 1, 1, 1000); in.manual_start = true;
    o = cg_step(&g, &in);
    CHECK(o.reference_mask == 1 && g.config.active_mask == 1 && g.config.version == 7);
    in = subset_input(pair, 1, 2, 1100); in.target.config_version = 8;
    o = cg_step(&g, &in);
    CHECK(o.reason == CG_SCOPE && o.reference_mask == 0 && g.config.active_mask == 1);

    /* Mixed-direction slew, all-selected commit, stop and recovery preserve
     * the exact noncontiguous set while discarding all old references. */
    c = subset_config(pair); o = subset_start(&g, &c);
    CHECK(o.reference[0] == 1 && o.reference[5] == 6 && o.reference_mask == pair);
    in = subset_input(pair, 1, 2, 1100); in.target.position[5] = -8;
    o = cg_step(&g, &in);
    CHECK(fabs(o.reference[0] - 1.1) < 1e-12 && fabs(o.reference[5] - 5.9) < 1e-12);
    memcpy(saved, g.reference, sizeof saved);
    in = subset_input(pair, 1, 3, 1200); in.target.position[5] = -8; in.feedback[5].position = -5;
    o = cg_step(&g, &in);
    CHECK(o.reason == CG_TRACKING && o.reference_mask == 0 && !memcmp(saved, g.reference, sizeof saved));
    CHECK(!cg_recover(&g, 1, 1300, true));
    CHECK(!cg_recover(&g, 2, 1300, false));
    CHECK(cg_recover(&g, 2, 1300, true) && g.config.active_mask == pair && g.config.version == 7);
    CHECK(g.last_seq == 0 && g.reference_ns == 0 && g.reference[0] == 0 && g.reference[5] == 0);
    in = subset_input(pair, 1, 3, 1400); in.manual_start = true;
    o = cg_step(&g, &in); CHECK(o.reason == CG_REFERENCE && o.reference_mask == 0);
    in = subset_input(pair, 2, 1, 1400); in.target.epoch = 1;
    CHECK(cg_step(&g, &in).reason == CG_REFERENCE);
    in = subset_input(pair, 2, 1, 1400); in.issued_epoch = 1;
    CHECK(cg_step(&g, &in).reason == CG_REFERENCE);
    in = subset_input(pair, 2, 1, 1400); o = cg_step(&g, &in);
    CHECK(o.decision == CG_READY && o.reference_mask == 0 && !o.reference_valid);
    in.manual_start = true; in.feedback[0].position = -2; in.feedback[5].position = 4;
    o = cg_step(&g, &in);
    CHECK(o.decision == CG_CANDIDATE && o.reference_mask == pair && o.reference[0] == -2 && o.reference[5] == 4);
    in = subset_input(pair, 2, 2, 1500); in.manual_start = in.manual_stop = true;
    o = cg_step(&g, &in);
    CHECK(o.reason == CG_MANUAL_STOP && o.reference_mask == 0 && !o.reference_valid && !o.tx_capable);
    in.manual_stop = false; o = cg_step(&g, &in);
    CHECK(o.decision == CG_STOP_REQUIRED && o.reference_mask == 0);

    c = subset_config(pair); (void)subset_start(&g, &c);
    in = subset_input(pair, 1, 2, 1100); in.target.present = false;
    o = cg_step(&g, &in);
    CHECK(o.reason == CG_NO_TARGET && o.reference_mask == 0 && !o.reference_valid);
    in = subset_input(pair, 1, 3, 2000); in.target.present = false;
    o = cg_step(&g, &in);
    CHECK(o.reason == CG_STALE && o.decision == CG_STOP_REQUIRED && o.reference_mask == 0);
    return 0;
}

int main(void)
{
    cg_gate g;
    cg_config c = fixture_config(), zero = {0};
    cg_input in;
    cg_output o;
    unsigned i;

    /* Safe defaults, explicit certifications and no implicit start. */
    o = start(&g, &zero); CHECK(o.decision == CG_BLOCKED && o.reason == CG_CONFIG);
    c.stop_strategy_verified = false; o = start(&g, &c); CHECK(o.reason == CG_CONFIG);
    c = fixture_config(); c.mechanical_verified = false; CHECK(start(&g, &c).reason == CG_CONFIG);
    c = fixture_config(); c.vision_verified = false; CHECK(start(&g, &c).reason == CG_CONFIG);
    c = fixture_config(); c.lower_stop_verified = false; CHECK(start(&g, &c).reason == CG_CONFIG);
    c = fixture_config(); c.node_id[5] = c.node_id[0]; CHECK(start(&g, &c).reason == CG_CONFIG);
    c = fixture_config();
    cg_init(&g, &c, 1, 900); in = fixture_input(1, 1, 1000);
    o = cg_step(&g, &in); CHECK(o.decision == CG_READY && g.state == CG_STANDBY);
    in.manual_start = true; o = cg_step(&g, &in);
    CHECK(o.decision == CG_CANDIDATE && !o.tx_capable && o.reference_valid);
    for (i = 0; i < CG_CHANNELS; ++i) CHECK(o.reference[i] == 1); /* not visual 8 */

    /* A large valid visual step is rate limited relative to actual initialization. */
    in = fixture_input(1, 2, 1100); o = cg_step(&g, &in);
    CHECK(o.decision == CG_CANDIDATE && fabs(o.reference[0] - 1.1) < 1e-12);
    in = fixture_input(1, 3, 1200); in.target.position[5] = 11;
    o = cg_step(&g, &in); CHECK(o.decision == CG_STOP_REQUIRED && o.reason == CG_RANGE);
    CHECK(fabs(g.reference[0] - 1.1) < 1e-12); /* no partial group update */
    in = fixture_input(1, 4, 1300); CHECK(cg_step(&g, &in).reason == CG_RANGE);
    CHECK(!cg_recover(&g, 1, 1400, true)); CHECK(!cg_recover(&g, 2, 1400, false));
    CHECK(cg_recover(&g, 2, 1400, true));
    in = fixture_input(1, 5, 1500); in.manual_start = true;
    CHECK(cg_step(&g, &in).reason == CG_REFERENCE); /* old start event rejected first */
    in = fixture_input(2, 1, 1500); o = cg_step(&g, &in); CHECK(o.decision == CG_READY);
    in.manual_start = true; in.manual_start_epoch = 1;
    CHECK(cg_step(&g, &in).reason == CG_REFERENCE);
    in.manual_start_epoch = 2;
    in.manual_start = true; CHECK(cg_step(&g, &in).decision == CG_CANDIDATE);

    /* Current side-swing invalid / motor None must prevent ALL six. */
    start(&g, &c); in = fixture_input(1, 2, 1100); in.target.feature_valid[5] = false;
    CHECK(cg_step(&g, &in).reason == CG_FEATURE && g.state == CG_TRIPPED);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.target.position_present[3] = false;
    CHECK(cg_step(&g, &in).reason == CG_FEATURE);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.target.position[1] = NAN;
    CHECK(cg_step(&g, &in).reason == CG_RANGE);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.target.position[1] = INFINITY;
    CHECK(cg_step(&g, &in).reason == CG_RANGE);

    /* Echoed target identity cannot replace trusted issued frame metadata. */
    start(&g, &c); in = fixture_input(1, 1, 1100); CHECK(cg_step(&g, &in).reason == CG_SEQUENCE);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.target.seq = 3;
    CHECK(cg_step(&g, &in).reason == CG_SEQUENCE);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.target.epoch = 2;
    CHECK(cg_step(&g, &in).reason == CG_REFERENCE);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.target.config_version = 8;
    CHECK(cg_step(&g, &in).reason == CG_REFERENCE);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.issued_rx_ns = 999;
    CHECK(cg_step(&g, &in).reason == CG_CLOCK); /* fresh, but local time regressed */
    start(&g, &c); in = fixture_input(1, 2, 1100); in.issued_rx_ns = 1000;
    CHECK(cg_step(&g, &in).decision == CG_CANDIDATE); /* same read batch allowed */
    start(&g, &c); in = fixture_input(1, 2, 1100); in.issued_rx_ns = 100;
    CHECK(cg_step(&g, &in).reason == CG_STALE); /* age exactly T */
    cg_init(&g, &c, 1, 900); in = fixture_input(1, 1, 1100); in.issued_rx_ns = 101;
    in.manual_start = true;
    CHECK(cg_step(&g, &in).decision == CG_CANDIDATE);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.issued_rx_ns = 1101;
    CHECK(cg_step(&g, &in).reason == CG_STALE);

    /* One missing/faulty/stale actual node invalidates whole candidate group. */
    for (i = 0; i < CG_CHANNELS; ++i) {
        start(&g, &c); in = fixture_input(1, 2, 1100); in.feedback[i].status = 255;
        CHECK(cg_step(&g, &in).reason == CG_NODE_FAULT);
        start(&g, &c); in = fixture_input(1, 2, 1100); in.feedback[i].received_ns = 600;
        CHECK(cg_step(&g, &in).reason == CG_FEEDBACK);
        start(&g, &c); in = fixture_input(1, 2, 1100); in.feedback[i].valid = false;
        CHECK(cg_step(&g, &in).reason == CG_FEEDBACK);
    }
    start(&g, &c); in = fixture_input(1, 2, 1100); in.feedback[0].node_id = 99;
    CHECK(cg_step(&g, &in).reason == CG_FEEDBACK);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.feedback[0].position = NAN;
    CHECK(cg_step(&g, &in).reason == CG_FEEDBACK);

    /* Independent fault inputs and deadlines; heartbeat cannot freshen old input. */
    start(&g, &c); in = fixture_input(1, 2, 1100); in.python_healthy = false;
    CHECK(cg_step(&g, &in).reason == CG_HEALTH);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.can_healthy = false;
    CHECK(cg_step(&g, &in).reason == CG_HEALTH);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.single_control_source = false;
    CHECK(cg_step(&g, &in).reason == CG_HEALTH);
    start(&g, &c); in = fixture_input(1, 2, 1200); in.compute_pending = true; in.compute_started_ns = 1000;
    CHECK(cg_step(&g, &in).reason == CG_COMPUTE);
    start(&g, &c); in = fixture_input(1, 2, 2000); in.target.present = false;
    CHECK(cg_step(&g, &in).reason == CG_STALE);
    start(&g, &c); in = fixture_input(1, 2, 999); CHECK(cg_step(&g, &in).reason == CG_CLOCK);
    start(&g, &c); in = fixture_input(1, 2, 1000); CHECK(cg_step(&g, &in).reason == CG_CYCLE);
    start(&g, &c); in = fixture_input(1, 2, 1301); CHECK(cg_step(&g, &in).reason == CG_CYCLE);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.feedback[0].position = -5;
    CHECK(cg_step(&g, &in).reason == CG_TRACKING);
    start(&g, &c); in = fixture_input(1, 2, 1100); in.manual_stop = in.manual_start = true;
    o = cg_step(&g, &in); CHECK(o.decision == CG_STOP_REQUIRED && o.reason == CG_MANUAL_STOP);
    CHECK(!o.reference_valid && !o.tx_capable);
    CHECK(!cg_recover(&g, 2, 1000, true)); /* clock rollback */
    cg_init(&g, &c, UINT64_MAX, 1000); CHECK(!cg_recover(&g, 0, 1100, true));
    printf("PASS original %u checks with explicit all-axis mask 63.\n", checks);
    if (partial_axis_tests()) return 1;
    printf("PASS %u total checks; TEST_ONLY parameters; no hardware I/O or TX; STOP_REQUIRED is not actual stop.\n", checks);
    return 0;
}
