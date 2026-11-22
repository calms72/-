#include "../control_gate.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

/* Independent pure-core scope regression. TEST_ONLY fictional coordinates,
 * IDs, flags and clocks. No devices, real clock, CAN, arming UI or calibration. */
static unsigned checks;
#define CHECK(expr) do { ++checks; if (!(expr)) { fprintf(stderr, "FAIL scope-review line %d: %s\n", __LINE__, #expr); return 1; } } while (0)

static bool selected(uint8_t mask, unsigned i)
{
    return (mask & (UINT8_C(1) << i)) != 0;
}

static cg_config fiction(uint8_t mask)
{
    cg_config c = {0};
    unsigned i;
    c.version = 7;
    c.active_mask = mask;
    c.mechanical_verified = c.vision_verified = c.feedback_verified = true;
    c.stop_strategy_verified = c.lower_stop_verified = true;
    c.frame_max_age_ns = 1000;
    c.feedback_max_age_ns = 500;
    c.compute_max_age_ns = 200;
    c.max_step_ns = 300;
    for (i = 0; i < CG_CHANNELS; ++i) if (selected(mask, i)) {
        c.node_id[i] = (uint16_t)(20u + i);
        c.minimum[i] = -10; c.maximum[i] = 10;
        c.max_speed[i] = 1e6; c.max_reference_error[i] = 2;
    }
    return c;
}

static cg_input input_for(const cg_config *c, uint64_t epoch, uint64_t seq, uint64_t now)
{
    cg_input in = {0};
    unsigned i;
    in.now_ns = now;
    in.manual_start_epoch = epoch;
    in.python_healthy = in.can_healthy = in.single_control_source = true;
    in.issued_epoch = epoch; in.issued_seq = seq; in.issued_rx_ns = now;
    in.target.present = true;
    in.target.epoch = epoch; in.target.seq = seq; in.target.config_version = c->version;
    for (i = 0; i < CG_CHANNELS; ++i) if (selected(c->active_mask, i)) {
        in.feedback[i].valid = true;
        in.feedback[i].epoch = epoch; in.feedback[i].received_ns = now;
        in.feedback[i].node_id = c->node_id[i]; in.feedback[i].position = 1;
        in.target.feature_valid[i] = in.target.position_present[i] = true;
        in.target.position[i] = 8;
    }
    return in;
}

static bool absent_output(cg_output o)
{
    unsigned i;
    if (o.reference_mask || o.reference_valid || o.tx_capable) return false;
    for (i = 0; i < CG_CHANNELS; ++i) if (o.reference[i] != 0) return false;
    return true;
}

static cg_output begin(cg_gate *g, const cg_config *c)
{
    cg_input in = input_for(c, 1, 1, 1000);
    cg_init(g, c, 1, 900);
    in.manual_start = true;
    return cg_step(g, &in);
}

int main(void)
{
    cg_gate g;
    cg_config c;
    cg_input in;
    cg_output o;
    unsigned i, variant;

    /* Selecting one axis does not supply any of the missing qualifications. */
    c = fiction(1); c.mechanical_verified = false;
    o = begin(&g, &c); CHECK(o.reason == CG_CONFIG && absent_output(o));
    c = fiction(1); c.vision_verified = false;
    o = begin(&g, &c); CHECK(o.reason == CG_CONFIG && absent_output(o));
    c = fiction(1); c.lower_stop_verified = false;
    o = begin(&g, &c); CHECK(o.reason == CG_CONFIG && absent_output(o));
    c = fiction(UINT8_C(32));
    cg_init(&g, &c, 1, 900); in = input_for(&c, 1, 1, 1000);
    in.manual_start = true; in.target.feature_valid[5] = false;
    o = cg_step(&g, &in); CHECK(o.reason == CG_FEATURE && absent_output(o));

    /* Config is copied; source mutation cannot expand an existing gate. */
    c = fiction(1); cg_init(&g, &c, 1, 900);
    c = fiction(CG_ALL_MASK);
    in = input_for(&g.config, 1, 1, 1000); in.manual_start = true;
    o = cg_step(&g, &in);
    CHECK(g.config.active_mask == 1 && o.reference_mask == 1);
    CHECK(o.decision == CG_CANDIDATE && o.reference_valid && !o.tx_capable);
    CHECK(o.reference[0] == 1);
    for (i = 1; i < CG_CHANNELS; ++i) CHECK(o.reference[i] == 0);

    /* Every inactive residual is rejected even when target.present is false. */
    for (variant = 0; variant < 5u; ++variant) {
        c = fiction(1); cg_init(&g, &c, 1, 900);
        in = input_for(&c, 1, 1, 1000); in.target.present = false;
        if (variant == 0) in.target.feature_valid[5] = true;
        if (variant == 1) in.target.position_present[5] = true;
        if (variant == 2) in.target.position[5] = NAN;
        if (variant == 3) in.target.position[5] = INFINITY;
        if (variant == 4) in.target.position[5] = -0.001;
        o = cg_step(&g, &in); CHECK(o.reason == CG_SCOPE && absent_output(o));
    }

    /* A retained inactive fault cannot be erased by valid=false, old time or
     * an undeclared/zero node slot; global can_healthy still applies as well. */
    for (i = 1; i < CG_CHANNELS; ++i) {
        c = fiction(1); CHECK(begin(&g, &c).decision == CG_CANDIDATE);
        in = input_for(&c, 1, 2, 1100);
        in.feedback[i].valid = false; in.feedback[i].epoch = 0;
        in.feedback[i].received_ns = 0; in.feedback[i].node_id = 0;
        in.feedback[i].position = NAN; in.feedback[i].status = 1;
        o = cg_step(&g, &in);
        CHECK(o.reason == CG_NODE_FAULT && o.decision == CG_STOP_REQUIRED && absent_output(o));
    }
    c = fiction(1); CHECK(begin(&g, &c).decision == CG_CANDIDATE);
    in = input_for(&c, 1, 2, 1100); in.can_healthy = false;
    o = cg_step(&g, &in); CHECK(o.reason == CG_HEALTH && absent_output(o));

    /* Scope failure is atomic and terminal; cleaned input cannot auto-resume. */
    c = fiction(1); CHECK(begin(&g, &c).decision == CG_CANDIDATE);
    in = input_for(&c, 1, 2, 1100); in.target.position[3] = 0.1;
    o = cg_step(&g, &in);
    CHECK(o.reason == CG_SCOPE && g.state == CG_TRIPPED && absent_output(o));
    CHECK(g.reference[0] == 1 && g.last_seq == 1);
    in = input_for(&c, 1, 3, 1200);
    o = cg_step(&g, &in); CHECK(o.reason == CG_SCOPE && absent_output(o));

    /* Recovery preserves scope, clears references and rejects the old start. */
    CHECK(!cg_recover(&g, 2, 1200, false));
    CHECK(cg_recover(&g, 2, 1200, true));
    CHECK(g.config.active_mask == 1 && g.state == CG_STANDBY && g.last_seq == 0);
    for (i = 0; i < CG_CHANNELS; ++i) CHECK(g.reference[i] == 0);
    in = input_for(&g.config, 2, 1, 1300);
    o = cg_step(&g, &in); CHECK(o.decision == CG_READY && absent_output(o));
    in.manual_start = true; in.manual_start_epoch = 1;
    o = cg_step(&g, &in); CHECK(o.reason == CG_REFERENCE && absent_output(o));
    in.manual_start_epoch = 2; in.feedback[0].position = 3;
    o = cg_step(&g, &in);
    CHECK(o.decision == CG_CANDIDATE && o.reference_mask == 1 && o.reference[0] == 3);
    for (i = 1; i < CG_CHANNELS; ++i) CHECK(o.reference[i] == 0);

    printf("PASS %u independent scope checks; TEST_ONLY; no hardware I/O or TX.\n", checks);
    return 0;
}
