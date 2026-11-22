#include "control_gate.h"
#include <math.h>
#include <string.h>

static bool active(const cg_config *c, unsigned i)
{
    return (c->active_mask & (UINT8_C(1) << i)) != 0;
}

static bool config_ok(const cg_config *c)
{
    unsigned i, j;
    if (!c->version || !c->active_mask || (c->active_mask & ~CG_ALL_MASK) ||
        !c->mechanical_verified || !c->vision_verified ||
        !c->feedback_verified || !c->stop_strategy_verified || !c->lower_stop_verified ||
        !c->frame_max_age_ns || !c->feedback_max_age_ns ||
        !c->compute_max_age_ns || !c->max_step_ns) return false;
    for (i = 0; i < CG_CHANNELS; ++i) {
        if (c->node_id[i] > 254 || (active(c, i) && !c->node_id[i])) return false;
        if (c->node_id[i])
            for (j = 0; j < i; ++j) if (c->node_id[i] == c->node_id[j]) return false;
        if (!active(c, i)) continue;
        if (!isfinite(c->minimum[i]) || !isfinite(c->maximum[i]) ||
            c->minimum[i] >= c->maximum[i] ||
            !isfinite(c->max_speed[i]) || c->max_speed[i] <= 0 ||
            !isfinite(c->max_reference_error[i]) || c->max_reference_error[i] <= 0)
            return false;
    }
    return true;
}

static bool fresh(uint64_t now, uint64_t then, uint64_t limit)
{
    return then <= now && now - then < limit;
}

static cg_output output(const cg_gate *g, cg_decision d, cg_reason r)
{
    cg_output o;
    memset(&o, 0, sizeof o);
    o.state = g->state;
    o.decision = d;
    o.reason = r;
    return o;
}

static cg_output reject(cg_gate *g, cg_reason r)
{
    if (g->state == CG_ARMED) {
        g->state = CG_TRIPPED;
        g->latched_reason = r;
    }
    return output(g, g->state == CG_TRIPPED ? CG_STOP_REQUIRED : CG_BLOCKED,
                  g->state == CG_TRIPPED ? g->latched_reason : r);
}

void cg_init(cg_gate *g, const cg_config *c, uint64_t epoch, uint64_t now)
{
    memset(g, 0, sizeof *g);
    if (c) g->config = *c;
    g->epoch = epoch;
    g->last_now_ns = now;
    g->state = CG_STANDBY;
}

bool cg_recover(cg_gate *g, uint64_t epoch, uint64_t now, bool stopped)
{
    cg_config c;
    if (!stopped || !epoch || epoch <= g->epoch || now < g->last_now_ns) return false;
    c = g->config;
    cg_init(g, &c, epoch, now);
    return true;
}

cg_output cg_step(cg_gate *g, const cg_input *in)
{
    const cg_config *c = &g->config;
    const cg_target *t = &in->target;
    cg_output o;
    double proposed[CG_CHANNELS] = {0};
    unsigned i;
    if (g->state == CG_TRIPPED) return output(g, CG_STOP_REQUIRED, g->latched_reason);
    if (in->now_ns < g->last_now_ns) return reject(g, CG_CLOCK);
    g->last_now_ns = in->now_ns;
    if (in->manual_stop) {
        /* Stopping also invalidates this session, even in standby. */
        g->state = CG_TRIPPED;
        g->latched_reason = CG_MANUAL_STOP;
        return output(g, CG_STOP_REQUIRED, CG_MANUAL_STOP);
    }
    if (!g->epoch || !config_ok(c)) return reject(g, CG_CONFIG);
    if (in->manual_start && in->manual_start_epoch != g->epoch)
        return reject(g, CG_REFERENCE);
    if (!in->python_healthy || !in->can_healthy || !in->single_control_source)
        return reject(g, CG_HEALTH);
    if (in->compute_pending &&
        !fresh(in->now_ns, in->compute_started_ns, c->compute_max_age_ns))
        return reject(g, CG_COMPUTE);
    /* Selection cannot hide a known drive fault. A missing inactive sample is
     * allowed, but clearing a retained fault is the trusted caller's duty. */
    for (i = 0; i < CG_CHANNELS; ++i)
        if (in->feedback[i].status) return reject(g, CG_NODE_FAULT);
    for (i = 0; i < CG_CHANNELS; ++i) {
        const cg_feedback *f = &in->feedback[i];
        if (!active(c, i)) continue;
        if (!f->valid || f->epoch != g->epoch || f->node_id != c->node_id[i] ||
            !fresh(in->now_ns, f->received_ns, c->feedback_max_age_ns) ||
            !isfinite(f->position) || f->position < c->minimum[i] ||
            f->position > c->maximum[i]) return reject(g, CG_FEEDBACK);
    }
    for (i = 0; i < CG_CHANNELS; ++i) {
        if (!active(c, i) && (t->feature_valid[i] || t->position_present[i] ||
            !isfinite(t->position[i]) || t->position[i] != 0)) return reject(g, CG_SCOPE);
    }
    if (!t->present) {
        if (g->state == CG_ARMED && !fresh(in->now_ns, g->last_rx_ns, c->frame_max_age_ns))
            return reject(g, CG_STALE);
        return output(g, CG_BLOCKED, CG_NO_TARGET);
    }
    if (t->epoch != g->epoch || in->issued_epoch != g->epoch ||
        t->config_version != c->version) return reject(g, CG_REFERENCE);
    if (!t->seq || t->seq != in->issued_seq || t->seq <= g->last_seq)
        return reject(g, CG_SEQUENCE);
    if (!fresh(in->now_ns, in->issued_rx_ns, c->frame_max_age_ns))
        return reject(g, CG_STALE);
    if (g->last_seq && in->issued_rx_ns < g->last_rx_ns) return reject(g, CG_CLOCK);
    for (i = 0; i < CG_CHANNELS; ++i) {
        if (!active(c, i)) continue;
        if (!t->feature_valid[i] || !t->position_present[i]) return reject(g, CG_FEATURE);
        if (!isfinite(t->position[i]) || t->position[i] < c->minimum[i] ||
            t->position[i] > c->maximum[i]) return reject(g, CG_RANGE);
    }
    if (g->state == CG_STANDBY && !in->manual_start) return output(g, CG_READY, CG_OK);
    if (g->state == CG_STANDBY) {
        /* Starting never jumps to a visual target. First reference is actual feedback. */
        for (i = 0; i < CG_CHANNELS; ++i)
            if (active(c, i)) proposed[i] = in->feedback[i].position;
    } else {
        uint64_t dt = in->now_ns - g->reference_ns;
        if (!dt || dt > c->max_step_ns) return reject(g, CG_CYCLE);
        for (i = 0; i < CG_CHANNELS; ++i) {
            if (!active(c, i)) continue;
            double step = c->max_speed[i] * ((double)dt / 1e9);
            double delta = t->position[i] - g->reference[i];
            if (!isfinite(step) || !isfinite(delta)) return reject(g, CG_RANGE);
            if (delta > step) delta = step;
            if (delta < -step) delta = -step;
            proposed[i] = g->reference[i] + delta;
            if (!isfinite(proposed[i]) || proposed[i] < c->minimum[i] ||
                proposed[i] > c->maximum[i]) return reject(g, CG_RANGE);
            if (fabs(proposed[i] - in->feedback[i].position) > c->max_reference_error[i])
                return reject(g, CG_TRACKING);
        }
    }
    /* No partial update if any selected channel failed. This is an internal
     * candidate commit, not a CAN transaction or proof of actual movement. */
    g->state = CG_ARMED;
    g->last_seq = t->seq;
    g->last_rx_ns = in->issued_rx_ns;
    g->reference_ns = in->now_ns;
    for (i = 0; i < CG_CHANNELS; ++i) g->reference[i] = proposed[i];
    o = output(g, CG_CANDIDATE, CG_OK);
    o.reference_mask = c->active_mask;
    o.reference_valid = true;
    for (i = 0; i < CG_CHANNELS; ++i) o.reference[i] = proposed[i];
    return o;
}
