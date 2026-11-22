/* Re-run the independent v1 cases using C's zero/default for the appended v2
 * policy field, then explicitly challenge preserve=true. No device I/O. */
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wmissing-field-initializers"
#define main v1_review_main
#include "native_index_follow_math_review.c"
#undef main
#pragma clang diagnostic pop

static int preserved_history_review(void)
{
    ifm_filter state = {0};
    ifm_filter_config c = {.median_window=3, .ema_tau_s=.03, .max_raw_rate=3,
        .absolute_tolerance=.02, .max_gap_ns=150u * MS, .max_age_ns=150u * MS,
        .preserve_smoothing_on_jump=true};
    ifm_filter_result r;
    CHECK(ifm_filter_init(&state, &c));
    r = ifm_filter_update(&state, true, .2, 0, 0);
    CHECK(!r.valid);
    r = ifm_filter_update(&state, true, .2, 33u * MS, 33u * MS);
    CHECK(!r.valid);
    r = ifm_filter_update(&state, true, .2, 66u * MS, 66u * MS);
    CHECK(r.valid && r.filtered_present && state.have_filtered && state.used == 3);
    r = ifm_filter_update(&state, true, .9, 99u * MS, 99u * MS);
    CHECK(!r.valid && !r.filtered_present && r.reason == IFM_JUMP_REJECTED);
    CHECK(r.event == IFM_EVENT_JUMP_PRESERVED_HISTORY && r.reset_count == 0);
    CHECK(r.accepted_count == 3 && r.jump_rejected_count == 1 && r.last_accepted_ns == 66u * MS);
    CHECK(state.have_filtered && state.used == 3 && state.jump_pending);
    r = ifm_filter_snapshot(&state, 100u * MS);
    CHECK(!r.valid && !r.filtered_present && r.accepted_age_ns == 34u * MS);
    r = ifm_filter_update(&state, true, .21, 132u * MS, 132u * MS);
    CHECK(r.valid && r.filtered_present && r.warmup_accepted_samples == 3);
    CHECK(r.accepted_count == 4 && r.reset_count == 0 && !state.jump_pending);
    CHECK(fabs(r.filtered - .2) < 1e-12);
    r = ifm_filter_update(&state, true, .9, 165u * MS, 165u * MS);
    CHECK(!r.valid && !r.filtered_present && r.last_accepted_ns == 132u * MS);
    r = ifm_filter_update(&state, true, .9, 198u * MS, 198u * MS);
    CHECK(!r.valid && !r.filtered_present && r.last_accepted_ns == 132u * MS);
    r = ifm_filter_update(&state, true, .9, 230u * MS, 230u * MS);
    CHECK(!r.valid && !r.filtered_present && r.last_accepted_ns == 132u * MS);
    r = ifm_filter_snapshot(&state, 282u * MS);
    CHECK(!r.valid && !r.filtered_present && !r.accepted_present && r.reason == IFM_ACCEPTED_EXPIRED);
    CHECK(!state.have_filtered && state.used == 0 && !state.jump_pending);
    r = ifm_filter_update(&state, true, .8, 300u * MS, 300u * MS);
    CHECK(!r.valid && r.warmup_accepted_samples == 1);
    r = ifm_filter_update(&state, true, .8, 333u * MS, 333u * MS);
    CHECK(!r.valid && r.warmup_accepted_samples == 2);
    r = ifm_filter_update(&state, true, .8, 366u * MS, 366u * MS);
    CHECK(r.valid && fabs(r.filtered - .8) < 1e-12);
    r = ifm_filter_update(&state, false, .8, 399u * MS, 399u * MS);
    CHECK(!r.valid && !r.accepted_present && !state.have_filtered && state.used == 0);
    r = ifm_filter_update(&state, true, .1, 430u * MS, 430u * MS);
    CHECK(!r.valid);
    r = ifm_filter_update(&state, true, .1, 463u * MS, 463u * MS);
    CHECK(!r.valid);
    r = ifm_filter_update(&state, true, .1, 496u * MS, 496u * MS);
    CHECK(r.valid);
    r = ifm_filter_update(&state, true, .9, 646u * MS, 646u * MS);
    CHECK(!r.valid && r.warmup_accepted_samples == 1 && r.event == IFM_EVENT_FRAME_GAP_RESET);
    r = ifm_filter_snapshot(&state, 645u * MS);
    CHECK(!r.valid && !r.accepted_present && !state.have_filtered && state.used == 0);
    CHECK(ifm_filter_init(&state, &c));
    (void)ifm_filter_update(&state, true, .4, 0, 0);
    (void)ifm_filter_update(&state, true, .4, 33u * MS, 33u * MS);
    r = ifm_filter_update(&state, true, .4, 66u * MS, 66u * MS);
    CHECK(r.valid);
    r = ifm_filter_update(&state, true, .4, 66u * MS, 67u * MS);
    CHECK(!r.valid && !state.have_filtered && !r.accepted_present);
    return 0;
}

int main(void)
{
    if (v1_review_main() || preserved_history_review()) return 1;
    (void)printf("PASS %u total independent v2/default-false+preserve-true assertions\n", checks);
    return 0;
}
