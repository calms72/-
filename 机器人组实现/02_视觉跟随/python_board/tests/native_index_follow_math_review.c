#include "../index_follow_math.h"
#include <math.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

/* Independent arithmetic/quality counterexamples only. No devices or sends.
 * Position/time/rate numbers below are fictitious TEST_ONLY values. */
static unsigned checks;
#define CHECK(test) do { ++checks; if (!(test)) { \
    (void)fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #test); return 1; \
} } while (0)
#define MS UINT64_C(1000000)

static ifm_reference_config config(void)
{
    ifm_reference_config c = {10.0, 0, 128, 150u * MS, 500u * MS};
    return c;
}

static int initialization(void)
{
    ifm_reference state;
    ifm_reference_config c = config();
    ifm_reference_result r;
    unsigned i;
    const double bad[] = {NAN, INFINITY, -INFINITY, -1.0, 129.0,
                          64.5, 5.000000000001, 1e300};
    memset(&state, 0, sizeof state);
    r = ifm_reference_step(&state, true, 64.0, 100u * MS);
    CHECK(!r.valid && !r.initialized);
    r = ifm_reference_init(&state, &c, true, 64.0, 900u * MS, 1000u * MS);
    CHECK(r.valid && r.initialized && r.position == 64 && !r.moved);
    ifm_reference_reset(&state);
    CHECK(!state.initialized);
    r = ifm_reference_step(&state, true, 65.0, 1010u * MS);
    CHECK(!r.valid && !r.initialized);
    for (i = 0; i < sizeof bad / sizeof bad[0]; ++i) {
        r = ifm_reference_init(&state, &c, true, bad[i], 0, 0);
        CHECK(!r.valid && !r.initialized && !state.initialized);
    }
    r = ifm_reference_init(&state, &c, false, 0.0, 0, 0);
    CHECK(!r.valid && !r.initialized);
    r = ifm_reference_init(&state, &c, true, 0.0, 1, 0);
    CHECK(!r.valid && !r.initialized);
    r = ifm_reference_init(&state, &c, true, 64.0, 0, 500u * MS);
    CHECK(!r.valid && !r.initialized);
    r = ifm_reference_init(&state, &c, true, 128.0, 1, 499u * MS);
    CHECK(r.valid && r.position == 128);
    c.rate_per_s = 0;
    r = ifm_reference_init(&state, &c, true, 64.0, 0, 0);
    CHECK(!r.valid && !r.initialized);
    c = config(); c.position_max = -1;
    r = ifm_reference_init(&state, &c, true, 0.0, 0, 0);
    CHECK(!r.valid && !r.initialized);
    return 0;
}

static int fraction_budget(void)
{
    ifm_reference state = {0};
    ifm_reference_config c = config();
    ifm_reference_result r;
    unsigned i;
    r = ifm_reference_init(&state, &c, true, 10.0, 0, 0);
    CHECK(r.valid);
    for (i = 1; i <= 3; ++i) {
        r = ifm_reference_step(&state, true, 128.0, (uint64_t)i * 25u * MS);
        CHECK(r.valid && r.position == 10 && !r.moved);
        CHECK(fabs(r.fraction_after - .25 * (double)i) < 1e-12);
    }
    r = ifm_reference_step(&state, true, 128.0, 100u * MS);
    CHECK(r.valid && r.position == 11 && r.moved_whole == 1 && r.allowed_whole == 1);
    CHECK(r.fraction_after >= 0 && r.fraction_after < 1);
    r = ifm_reference_step(&state, true, 11.0, 125u * MS);
    CHECK(r.valid && r.position == 11 && r.fraction_after == 0);
    r = ifm_reference_step(&state, true, 128.0, 150u * MS);
    CHECK(r.valid && r.position == 11 && fabs(r.fraction_after - .25) < 1e-12);
    /* Exactly the maximum gap invalidates instead of making a catch-up step. */
    r = ifm_reference_step(&state, true, 128.0, 300u * MS);
    CHECK(!r.valid && !r.initialized && !state.initialized);
    r = ifm_reference_step(&state, true, 128.0, 301u * MS);
    CHECK(!r.valid && !r.initialized);
    return 0;
}

static int idle_and_direction(void)
{
    ifm_reference state = {0};
    ifm_reference_config c = config();
    ifm_reference_result r;
    unsigned i;
    r = ifm_reference_init(&state, &c, true, 64.0, 0, 0);
    CHECK(r.valid);
    for (i = 1; i <= 100; ++i) {
        r = ifm_reference_step(&state, true, 64.0, (uint64_t)i * 100u * MS);
        CHECK(r.valid && r.position == 64 && r.fraction_after == 0);
    }
    r = ifm_reference_step(&state, true, 128.0, 10005u * MS);
    CHECK(r.valid && r.position == 64 && !r.moved && r.allowed_whole == 0);
    CHECK(fabs(r.fraction_after - .05) < 1e-12);
    r = ifm_reference_init(&state, &c, true, 64.0, 0, 0);
    CHECK(r.valid);
    r = ifm_reference_step(&state, true, 128.0, 25u * MS);
    CHECK(r.valid && fabs(r.fraction_after - .25) < 1e-12);
    r = ifm_reference_step(&state, true, 128.0, 50u * MS);
    CHECK(r.valid && fabs(r.fraction_after - .5) < 1e-12);
    r = ifm_reference_step(&state, true, 0.0, 75u * MS);
    CHECK(r.valid && r.direction_budget_reset && r.position == 64);
    CHECK(fabs(r.fraction_after - .25) < 1e-12);
    r = ifm_reference_step(&state, true, 0.0, 100u * MS);
    CHECK(r.valid && r.position == 64);
    r = ifm_reference_step(&state, true, 0.0, 125u * MS);
    CHECK(r.valid && r.position == 64);
    r = ifm_reference_step(&state, true, 0.0, 150u * MS);
    CHECK(r.valid && r.position == 63 && r.moved_whole == 1);
    /* A large allowance mostly unused by a nearby target is not banked. */
    c.rate_per_s = 1000;
    r = ifm_reference_init(&state, &c, true, 10.0, 0, 0);
    CHECK(r.valid);
    r = ifm_reference_step(&state, true, 11.0, 100u * MS);
    CHECK(r.valid && r.position == 11 && r.moved_whole == 1);
    r = ifm_reference_step(&state, true, 128.0, 101u * MS);
    CHECK(r.valid && r.position == 12 && r.moved_whole == 1);
    return 0;
}

static int invalidation(void)
{
    ifm_reference state = {0};
    ifm_reference_config c = config();
    ifm_reference_result r;
    const double bad[] = {NAN, INFINITY, -1.0, 129.0};
    unsigned i;
    for (i = 0; i < sizeof bad / sizeof bad[0]; ++i) {
        r = ifm_reference_init(&state, &c, true, 64.0, 0, 0);
        CHECK(r.valid);
        r = ifm_reference_step(&state, true, bad[i], 10u * MS);
        CHECK(!r.valid && !r.initialized && !state.initialized);
    }
    r = ifm_reference_init(&state, &c, true, 64.0, 0, 0);
    CHECK(r.valid);
    r = ifm_reference_step(&state, false, 64.0, 10u * MS);
    CHECK(!r.valid && !r.initialized);
    r = ifm_reference_init(&state, &c, true, 64.0, 10u * MS, 10u * MS);
    CHECK(r.valid);
    r = ifm_reference_step(&state, true, 65.0, 10u * MS);
    CHECK(!r.valid && !r.initialized);
    r = ifm_reference_init(&state, &c, true, 64.0, 10u * MS, 10u * MS);
    CHECK(r.valid);
    r = ifm_reference_step(&state, true, 65.0, 9u * MS);
    CHECK(!r.valid && !r.initialized);
    return 0;
}

static int map_review(void)
{
    ifm_map_config c = {.raw_straight=.2, .raw_gentle=.6,
        .preview_straight=20, .preview_gentle=100, .mechanical_min=0, .mechanical_max=128};
    ifm_map_result r;
    CHECK(ifm_map_config_valid(&c));
    r = ifm_map(&c, true, .8);
    CHECK(r.valid && !r.within_visual_window && !r.within_preview_window);
    CHECK(fabs(r.predicted_unclamped - 140) < 1e-10);
    CHECK(r.mechanical_clamped && r.mechanical_position == 128);
    r = ifm_map(&c, true, 0);
    CHECK(r.valid && r.predicted_unclamped < 0 && r.mechanical_position == 0 && r.mechanical_clamped);
    r = ifm_map(&c, true, .4);
    CHECK(r.valid && r.within_visual_window && r.within_preview_window && !r.mechanical_clamped);
    CHECK(fabs(r.mechanical_position - 60) < 1e-10);
    r = ifm_map(&c, false, .4);
    CHECK(!r.valid);
    r = ifm_map(&c, true, NAN);
    CHECK(!r.valid);
    c.raw_gentle = c.raw_straight;
    CHECK(!ifm_map_config_valid(&c));
    c.raw_gentle = .6; c.mechanical_max = -1;
    CHECK(!ifm_map_config_valid(&c));
    return 0;
}

static int filter_review(void)
{
    ifm_filter state = {0};
    ifm_filter_config c = {3, .03, 3, .02, 150u * MS, 150u * MS};
    ifm_filter_result r;
    CHECK(ifm_filter_init(&state, &c));
    r = ifm_filter_update(&state, true, .2, 0, 0);
    CHECK(!r.valid && !r.filtered_present);
    r = ifm_filter_update(&state, true, .2, 33u * MS, 33u * MS);
    CHECK(!r.valid && !r.filtered_present);
    r = ifm_filter_update(&state, true, .2, 66u * MS, 66u * MS);
    CHECK(r.valid && r.filtered_present && fabs(r.filtered - .2) < 1e-12);
    r = ifm_filter_update(&state, true, .9, 99u * MS, 99u * MS);
    CHECK(!r.valid && !r.filtered_present && r.reason == IFM_JUMP_REJECTED);
    CHECK(r.accepted_present && r.last_accepted_ns == 66u * MS);
    r = ifm_filter_snapshot(&state, 100u * MS);
    CHECK(!r.valid && !r.filtered_present && r.accepted_age_ns == 34u * MS);
    r = ifm_filter_snapshot(&state, 216u * MS);
    CHECK(!r.valid && !r.filtered_present && !r.accepted_present && r.reason == IFM_ACCEPTED_EXPIRED);
    r = ifm_filter_update(&state, true, .8, 230u * MS, 231u * MS);
    CHECK(!r.valid && !r.filtered_present && r.warmup_accepted_samples == 1);
    r = ifm_filter_update(&state, true, .8, 260u * MS, 261u * MS);
    CHECK(!r.valid && !r.filtered_present);
    r = ifm_filter_update(&state, true, .8, 290u * MS, 291u * MS);
    CHECK(r.valid && r.filtered_present && fabs(r.filtered - .8) < 1e-12);
    r = ifm_filter_update(&state, true, .8, 290u * MS, 292u * MS);
    CHECK(!r.valid && !r.filtered_present && !r.accepted_present && r.reason == IFM_SOURCE_TIME_NOT_INCREASING);
    r = ifm_filter_update(&state, true, .8, 300u * MS, 450u * MS);
    CHECK(!r.valid && !r.filtered_present && !r.accepted_present && r.reason == IFM_SOURCE_STALE);
    return 0;
}

int main(void)
{
    if (initialization() || fraction_budget() || idle_and_direction() ||
        invalidation() || map_review() || filter_review()) return 1;
    (void)printf("PASS %u independent TEST_ONLY assertions; no I/O or motion qualification\n", checks);
    return 0;
}
