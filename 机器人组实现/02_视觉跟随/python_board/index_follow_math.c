#include "index_follow_math.h"
#include <math.h>
#include <stddef.h>
#include <string.h>

static void clear_smoothing(ifm_filter *s)
{
    s->used = 0; s->have_filtered = false; s->jump_pending = false;
    s->filtered = 0; s->filtered_rx_ns = 0;
    memset(s->window, 0, sizeof s->window);
}

static void clear_history(ifm_filter *s)
{
    clear_smoothing(s); s->have_accepted = false; s->last_raw = 0; s->last_accepted_ns = 0;
}

static void discard(ifm_filter *s) { clear_history(s); ++s->reset_count; }

static ifm_filter_result fr(const ifm_filter *s, bool valid, ifm_reason reason,
    ifm_event event, bool has_raw, double raw, bool has_gate, double gate)
{
    ifm_filter_result r = {0};
    r.valid = valid; r.reason = reason; r.event = event;
    if (!s) return r;
    r.raw_present = has_raw; r.raw = raw;
    r.filtered_present = valid && s->have_filtered;
    if (r.filtered_present) r.filtered = s->filtered;
    r.accepted_present = s->have_accepted; r.last_accepted_ns = s->last_accepted_ns;
    r.accepted_age_present = s->have_now && s->have_accepted && s->last_now_ns >= s->last_accepted_ns;
    if (r.accepted_age_present) r.accepted_age_ns = s->last_now_ns - s->last_accepted_ns;
    r.seen_present = s->have_seen; r.last_seen_rx_ns = s->last_seen_rx_ns;
    r.gate_delta_present = has_gate; r.gate_allowed_raw_delta = gate;
    r.warmup_accepted_samples = s->used; r.warmup_required = s->config.median_window;
    r.accepted_count = s->accepted_count; r.jump_rejected_count = s->jump_rejected_count;
    r.bad_data_count = s->bad_data_count; r.clock_rejected_count = s->clock_rejected_count;
    r.stale_count = s->stale_count; r.reset_count = s->reset_count;
    return r;
}

bool ifm_filter_init(ifm_filter *s, const ifm_filter_config *c)
{
    if (!s) return false;
    memset(s, 0, sizeof *s);
    if (!c || (c->median_window != 3u && c->median_window != 5u) ||
        !isfinite(c->ema_tau_s) || c->ema_tau_s <= 0 ||
        !isfinite(c->max_raw_rate) || c->max_raw_rate <= 0 ||
        !isfinite(c->absolute_tolerance) || c->absolute_tolerance < 0 ||
        !c->max_gap_ns || !c->max_age_ns ||
        !isfinite(c->absolute_tolerance + c->max_raw_rate * ((double)c->max_age_ns / 1e9))) return false;
    s->config = *c; s->configured = true;
    return true;
}

ifm_filter_result ifm_filter_reset(ifm_filter *s)
{
    if (!s || !s->configured) return fr(s, false, IFM_CONFIG_INVALID, IFM_EVENT_NONE, false, 0, false, 0);
    discard(s); s->have_seen = s->have_now = false;
    s->last_seen_rx_ns = s->last_now_ns = 0;
    return fr(s, false, IFM_MANUAL_RESET, IFM_EVENT_NONE, false, 0, false, 0);
}

static bool observe_now(ifm_filter *s, uint64_t now)
{
    if (s->have_now && now < s->last_now_ns) {
        ++s->clock_rejected_count; discard(s); return false;
    }
    s->have_now = true; s->last_now_ns = now; return true;
}

ifm_filter_result ifm_filter_snapshot(ifm_filter *s, uint64_t now)
{
    if (!s || !s->configured) return fr(s, false, IFM_CONFIG_INVALID, IFM_EVENT_NONE, false, 0, false, 0);
    if (!observe_now(s, now)) return fr(s, false, IFM_NOW_CLOCK_INVALID, IFM_EVENT_NONE, false, 0, false, 0);
    if (!s->have_accepted) return fr(s, false, IFM_NO_ACCEPTED_SAMPLE, IFM_EVENT_NONE, false, 0, false, 0);
    if (now - s->last_accepted_ns >= s->config.max_age_ns) {
        ++s->stale_count; discard(s);
        return fr(s, false, IFM_ACCEPTED_EXPIRED, IFM_EVENT_NONE, false, 0, false, 0);
    }
    return fr(s, s->have_filtered && !s->jump_pending,
              s->jump_pending ? IFM_JUMP_REJECTED : s->have_filtered ? IFM_FILTERED : IFM_WARMING_UP,
              IFM_EVENT_NONE, true, s->last_raw, false, 0);
}

ifm_filter_result ifm_filter_update(ifm_filter *s, bool raw_valid, double raw,
    uint64_t rx, uint64_t now)
{
    bool previous_seen, has_gate = false;
    uint64_t previous_rx;
    ifm_event event = IFM_EVENT_NONE;
    double gate = 0, sorted[5], median;
    unsigned i, j;
    if (!s || !s->configured) return fr(s, false, IFM_CONFIG_INVALID, event, false, 0, false, 0);
    if (!observe_now(s, now)) return fr(s, false, IFM_NOW_CLOCK_INVALID, event, false, 0, false, 0);
    if (rx > now) {
        ++s->clock_rejected_count; discard(s);
        return fr(s, false, IFM_SOURCE_CLOCK_INVALID, event, false, 0, false, 0);
    }
    if (s->have_seen && rx <= s->last_seen_rx_ns) {
        ++s->clock_rejected_count; discard(s);
        return fr(s, false, IFM_SOURCE_TIME_NOT_INCREASING, event, false, 0, false, 0);
    }
    previous_seen = s->have_seen; previous_rx = s->last_seen_rx_ns;
    s->have_seen = true; s->last_seen_rx_ns = rx;
    if (now - rx >= s->config.max_age_ns) {
        ++s->stale_count; discard(s);
        return fr(s, false, IFM_SOURCE_STALE, event, false, 0, false, 0);
    }
    if (previous_seen && rx - previous_rx >= s->config.max_gap_ns) {
        discard(s); event = IFM_EVENT_FRAME_GAP_RESET;
    } else if (s->have_accepted && now - s->last_accepted_ns >= s->config.max_age_ns) {
        ++s->stale_count; discard(s); event = IFM_EVENT_ACCEPTED_AGE_RESET;
    }
    if (!raw_valid) {
        ++s->bad_data_count; discard(s);
        return fr(s, false, IFM_INVALID_MEASUREMENT, event, false, 0, false, 0);
    }
    if (!isfinite(raw)) {
        ++s->bad_data_count; discard(s);
        return fr(s, false, IFM_INVALID_RAW, event, false, 0, false, 0);
    }
    if (raw < 0 || raw > 1) {
        ++s->bad_data_count; discard(s);
        return fr(s, false, IFM_RAW_OUTSIDE_FEATURE_DOMAIN, event, true, raw, false, 0);
    }
    if (s->have_accepted) {
        gate = s->config.absolute_tolerance + s->config.max_raw_rate * ((double)(rx - s->last_accepted_ns) / 1e9);
        has_gate = true;
        if (fabs(raw - s->last_raw) > gate) {
            ++s->jump_rejected_count;
            if (s->config.preserve_smoothing_on_jump) {
                s->jump_pending = true;
                return fr(s, false, IFM_JUMP_REJECTED, IFM_EVENT_JUMP_PRESERVED_HISTORY, true, raw, true, gate);
            }
            clear_smoothing(s); ++s->reset_count;
            return fr(s, false, IFM_JUMP_REJECTED, IFM_EVENT_JUMP_SMOOTHING_RESET, true, raw, true, gate);
        }
    }
    s->jump_pending = false;
    s->have_accepted = true; s->last_raw = raw; s->last_accepted_ns = rx; ++s->accepted_count;
    if (s->used == s->config.median_window) {
        for (i = 1; i < s->used; ++i) s->window[i - 1u] = s->window[i];
        --s->used;
    }
    s->window[s->used++] = raw;
    if (s->used < s->config.median_window)
        return fr(s, false, IFM_WARMING_UP, event, true, raw, has_gate, gate);
    memcpy(sorted, s->window, s->used * sizeof sorted[0]);
    for (i = 1; i < s->used; ++i) {
        double key = sorted[i]; j = i;
        while (j && sorted[j - 1u] > key) { sorted[j] = sorted[j - 1u]; --j; }
        sorted[j] = key;
    }
    median = sorted[s->used / 2u];
    if (!s->have_filtered) s->filtered = median;
    else {
        double alpha = -expm1(-(double)(rx - s->filtered_rx_ns) / (s->config.ema_tau_s * 1e9));
        s->filtered += alpha * (median - s->filtered);
    }
    s->have_filtered = true; s->filtered_rx_ns = rx;
    return fr(s, true, IFM_FILTERED, event, true, raw, has_gate, gate);
}

const char *ifm_reason_name(ifm_reason reason)
{
    static const char *const names[] = {
        "filtered", "config_invalid", "no_accepted_sample", "warming_up", "jump_rejected",
        "invalid_measurement", "invalid_raw", "raw_outside_feature_domain", "now_clock_invalid",
        "source_clock_invalid", "source_time_not_increasing", "source_stale", "accepted_expired",
        "manual_reset", "reference_ready", "reference_uninitialized", "reference_invalid_feedback",
        "reference_stale_feedback", "reference_clock_invalid", "reference_gap", "reference_invalid_target"
    };
    return (unsigned)reason < sizeof names / sizeof names[0] ? names[reason] : "unknown";
}

const char *ifm_event_name(ifm_event event)
{
    switch (event) {
    case IFM_EVENT_NONE: return "NONE";
    case IFM_EVENT_FRAME_GAP_RESET: return "frame_gap_reset";
    case IFM_EVENT_ACCEPTED_AGE_RESET: return "accepted_age_reset";
    case IFM_EVENT_JUMP_SMOOTHING_RESET: return "jump_smoothing_reset";
    case IFM_EVENT_JUMP_PRESERVED_HISTORY: return "jump_preserved_history";
    default: return "unknown";
    }
}

bool ifm_map_config_valid(const ifm_map_config *c)
{
    double raw_span, motor_span;
    if (!c || !isfinite(c->raw_straight) || !isfinite(c->raw_gentle) ||
        !isfinite(c->preview_straight) || !isfinite(c->preview_gentle) ||
        c->mechanical_min < 0 || c->mechanical_min >= c->mechanical_max) return false;
    raw_span = c->raw_gentle - c->raw_straight;
    motor_span = c->preview_gentle - c->preview_straight;
    return isfinite(raw_span) && raw_span > 0 && isfinite(motor_span) && motor_span > 0 && isfinite(motor_span / raw_span);
}

ifm_map_result ifm_map(const ifm_map_config *c, bool raw_valid, double raw)
{
    ifm_map_result r = {0};
    if (!ifm_map_config_valid(c) || !raw_valid || !isfinite(raw)) return r;
    r.raw = raw; r.fraction = (raw - c->raw_straight) / (c->raw_gentle - c->raw_straight);
    r.predicted_unclamped = c->preview_straight + r.fraction * (c->preview_gentle - c->preview_straight);
    if (!isfinite(r.fraction) || !isfinite(r.predicted_unclamped)) return (ifm_map_result){0};
    r.valid = true;
    r.within_visual_window = c->raw_straight <= raw && raw <= c->raw_gentle;
    r.within_preview_window = c->preview_straight <= r.predicted_unclamped && r.predicted_unclamped <= c->preview_gentle;
    r.mechanical_position = fmin((double)c->mechanical_max, fmax((double)c->mechanical_min, r.predicted_unclamped));
    r.mechanical_clamped = r.mechanical_position != r.predicted_unclamped;
    return r;
}

void ifm_reference_reset(ifm_reference *s)
{
    if (!s) return;
    s->initialized = false; s->position = 0; s->direction = 0; s->last_ns = 0; s->fraction = 0;
}

static ifm_reference_result rr(const ifm_reference *s, bool valid, ifm_reason reason)
{
    ifm_reference_result r = {0};
    r.valid = valid; r.reason = reason;
    if (s) { r.initialized = s->initialized; if (s->initialized) r.position = s->position; }
    return r;
}

ifm_reference_result ifm_reference_init(ifm_reference *s, const ifm_reference_config *c,
    bool feedback_valid, double actual, uint64_t feedback_rx, uint64_t now)
{
    if (!s) return rr(NULL, false, IFM_CONFIG_INVALID);
    memset(s, 0, sizeof *s);
    if (!c || !isfinite(c->rate_per_s) || c->rate_per_s <= 0 ||
        c->position_min < 0 || c->position_min >= c->position_max || !c->max_gap_ns ||
        !c->feedback_max_age_ns || !isfinite(c->rate_per_s * ((double)c->max_gap_ns / 1e9)) ||
        c->rate_per_s * ((double)c->max_gap_ns / 1e9) >= 4294967294.0)
        return rr(s, false, IFM_CONFIG_INVALID);
    s->config = *c; s->configured = true;
    if (!feedback_valid || !isfinite(actual) || floor(actual) != actual ||
        actual < c->position_min || actual > c->position_max)
        return rr(s, false, IFM_REFERENCE_INVALID_FEEDBACK);
    if (feedback_rx > now || now - feedback_rx >= c->feedback_max_age_ns)
        return rr(s, false, IFM_REFERENCE_STALE_FEEDBACK);
    s->initialized = true; s->position = (int32_t)actual; s->last_ns = now;
    return rr(s, true, IFM_REFERENCE_READY);
}

ifm_reference_result ifm_reference_step(ifm_reference *s, bool target_valid,
    double target, uint64_t now)
{
    ifm_reference_result r;
    uint64_t elapsed, allowance, distance, movement;
    double budget, remainder;
    int32_t desired;
    int direction;
    bool reversed;
    if (!s || !s->configured) return rr(s, false, IFM_CONFIG_INVALID);
    if (!s->initialized) return rr(s, false, IFM_REFERENCE_UNINITIALIZED);
    if (now <= s->last_ns) {
        ifm_reference_reset(s); return rr(s, false, IFM_REFERENCE_CLOCK_INVALID);
    }
    elapsed = now - s->last_ns;
    if (elapsed >= s->config.max_gap_ns) {
        ifm_reference_reset(s); return rr(s, false, IFM_REFERENCE_GAP);
    }
    if (!target_valid || !isfinite(target) || target < s->config.position_min || target > s->config.position_max) {
        ifm_reference_reset(s); return rr(s, false, IFM_REFERENCE_INVALID_TARGET);
    }
    desired = (int32_t)floor(target + .5);
    direction = desired > s->position ? 1 : desired < s->position ? -1 : 0;
    reversed = direction && s->direction && direction != s->direction;
    if (reversed || !direction) s->fraction = 0;
    budget = s->config.rate_per_s * ((double)elapsed / 1e9) + s->fraction;
    allowance = (uint64_t)floor(budget);
    remainder = budget - (double)allowance;
    distance = desired >= s->position ? (uint64_t)((int64_t)desired - s->position) : (uint64_t)((int64_t)s->position - desired);
    movement = distance < allowance ? distance : allowance;
    r = rr(s, true, IFM_REFERENCE_READY);
    r.elapsed_ns = elapsed; r.allowed_whole = allowance; r.moved_whole = movement;
    r.fraction_before = s->fraction; r.direction_budget_reset = reversed;
    r.desired_integer = desired; r.moved = movement != 0;
    if (direction > 0) s->position = (int32_t)((int64_t)s->position + (int64_t)movement);
    else if (direction < 0) s->position = (int32_t)((int64_t)s->position - (int64_t)movement);
    s->fraction = movement == distance ? 0 : remainder;
    s->direction = movement == distance ? 0 : direction;
    s->last_ns = now;
    r.position = s->position; r.fraction_after = s->fraction;
    return r;
}
