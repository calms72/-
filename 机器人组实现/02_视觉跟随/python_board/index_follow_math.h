#ifndef INDEX_FOLLOW_MATH_H
#define INDEX_FOLLOW_MATH_H

/* Pure arithmetic only: no I/O, ARM, fault clearing or qualification grants.
 * A valid result is a mathematical candidate, NEVER permission to transmit.
 * The caller must run control_gate/session/feedback/fault checks separately.
 */
#include <stdbool.h>
#include <stdint.h>

typedef enum {
    IFM_FILTERED, IFM_CONFIG_INVALID, IFM_NO_ACCEPTED_SAMPLE, IFM_WARMING_UP,
    IFM_JUMP_REJECTED, IFM_INVALID_MEASUREMENT, IFM_INVALID_RAW,
    IFM_RAW_OUTSIDE_FEATURE_DOMAIN, IFM_NOW_CLOCK_INVALID,
    IFM_SOURCE_CLOCK_INVALID, IFM_SOURCE_TIME_NOT_INCREASING,
    IFM_SOURCE_STALE, IFM_ACCEPTED_EXPIRED, IFM_MANUAL_RESET,
    IFM_REFERENCE_READY, IFM_REFERENCE_UNINITIALIZED,
    IFM_REFERENCE_INVALID_FEEDBACK, IFM_REFERENCE_STALE_FEEDBACK,
    IFM_REFERENCE_CLOCK_INVALID, IFM_REFERENCE_GAP,
    IFM_REFERENCE_INVALID_TARGET
} ifm_reason;

typedef enum {
    IFM_EVENT_NONE, IFM_EVENT_FRAME_GAP_RESET, IFM_EVENT_ACCEPTED_AGE_RESET,
    IFM_EVENT_JUMP_SMOOTHING_RESET, IFM_EVENT_JUMP_PRESERVED_HISTORY
} ifm_event;

typedef struct {
    unsigned median_window;              /* explicit 3 or 5 */
    double ema_tau_s, max_raw_rate, absolute_tolerance;
    uint64_t max_gap_ns, max_age_ns;
    /* Explicit v2 policy. Zero/default preserves the frozen v1 reset policy.
     * true keeps numeric history ONLY; rejected output/snapshot remain invalid
     * until a NEW acceptable sample. Accepted age/reference never refreshes.
     * Invalid data, clock, stale or gap still discard all history/warm up. */
    bool preserve_smoothing_on_jump;
} ifm_filter_config;

typedef struct {
    bool valid, raw_present, filtered_present, accepted_present;
    bool accepted_age_present, seen_present, gate_delta_present;
    ifm_reason reason;
    ifm_event event;
    double raw, filtered, gate_allowed_raw_delta;
    uint64_t last_accepted_ns, accepted_age_ns, last_seen_rx_ns;
    unsigned warmup_accepted_samples, warmup_required;
    uint64_t accepted_count, jump_rejected_count, bad_data_count;
    uint64_t clock_rejected_count, stale_count, reset_count;
} ifm_filter_result;

typedef struct {
    bool configured, have_seen, have_now, have_accepted, have_filtered, jump_pending;
    ifm_filter_config config;
    uint64_t last_seen_rx_ns, last_now_ns, last_accepted_ns, filtered_rx_ns;
    double last_raw, filtered, window[5];
    unsigned used;
    uint64_t accepted_count, jump_rejected_count, bad_data_count;
    uint64_t clock_rejected_count, stale_count, reset_count;
} ifm_filter;

bool ifm_filter_init(ifm_filter *state, const ifm_filter_config *config);
ifm_filter_result ifm_filter_reset(ifm_filter *state);
ifm_filter_result ifm_filter_update(ifm_filter *state, bool raw_valid,
    double raw, uint64_t rx_ns, uint64_t now_ns);
ifm_filter_result ifm_filter_snapshot(ifm_filter *state, uint64_t now_ns);
const char *ifm_reason_name(ifm_reason reason);
const char *ifm_event_name(ifm_event event);

typedef struct {
    double raw_straight, raw_gentle, preview_straight, preview_gentle;
    int32_t mechanical_min, mechanical_max; /* explicit candidate bounds */
} ifm_map_config;

typedef struct {
    bool valid, within_visual_window, within_preview_window;
    bool mechanical_clamped;
    double raw, fraction, predicted_unclamped, mechanical_position;
} ifm_map_result;

bool ifm_map_config_valid(const ifm_map_config *config);
ifm_map_result ifm_map(const ifm_map_config *config, bool raw_valid, double raw);

typedef struct {
    double rate_per_s;
    int32_t position_min, position_max;
    uint64_t max_gap_ns, feedback_max_age_ns;
} ifm_reference_config;

typedef struct {
    bool configured, initialized;
    ifm_reference_config config;
    int32_t position;
    int direction;
    uint64_t last_ns;
    double fraction;
} ifm_reference;

typedef struct {
    bool valid, initialized, moved, direction_budget_reset;
    ifm_reason reason;
    int32_t position, desired_integer;
    uint64_t elapsed_ns, allowed_whole, moved_whole;
    double fraction_before, fraction_after;
} ifm_reference_result;

/* Initialization REQUIRES a valid, fresh, finite INTEGER actual feedback P.
 * No zero fallback or truncation. reset withdraws the reference completely.
 * Stop/invalid/recovery callers must reset and supply fresh actual P again.
 */
ifm_reference_result ifm_reference_init(ifm_reference *state,
    const ifm_reference_config *config, bool feedback_valid,
    double actual_position, uint64_t feedback_rx_ns, uint64_t now_ns);
void ifm_reference_reset(ifm_reference *state);

/* allowance = floor(rate * ACTUAL elapsed seconds + fraction_before).
 * Thus a call can exceed floor(rate * this-call dt) by one integer unit;
 * the conserved fraction represents previously unspent sub-unit time.
 * Movement <= allowance; only the <1 fractional remainder survives a call.
 * Unused whole allowances are discarded, never banked for catch-up.
 * Idle/arrival and direction reversal discard the fractional budget too.
 * Repeated/reversed time, gap>=max_gap, or bad/outside target reset entirely.
 */
ifm_reference_result ifm_reference_step(ifm_reference *state,
    bool target_valid, double mechanical_target, uint64_t now_ns);

#endif
