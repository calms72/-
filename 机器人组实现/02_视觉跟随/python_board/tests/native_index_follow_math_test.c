#include "../index_follow_math.h"
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MS UINT64_C(1000000)
static unsigned checks, failures;
static void check(bool ok, const char *name)
{
    ++checks;
    if (!ok) { ++failures; (void)fprintf(stderr, "FAIL %s\n", name); }
}

static ifm_filter_config fc(unsigned width)
{
    ifm_filter_config c = {width, .03, 3, .02, 150u * MS, 150u * MS, false}; return c;
}

static int trace(unsigned width, bool preserve)
{
    ifm_filter s;
    ifm_filter_config c = fc(width);
    char line[256];
    c.preserve_smoothing_on_jump = preserve;
    if (!ifm_filter_init(&s, &c)) return 2;
    while (fgets(line, sizeof line, stdin)) {
        ifm_filter_result r;
        unsigned valid;
        double raw;
        uint64_t rx, now;
        if (line[0] == 'R') r = ifm_filter_reset(&s);
        else if (sscanf(line, "S\t%" SCNu64, &now) == 1) r = ifm_filter_snapshot(&s, now);
        else if (sscanf(line, "U\t%u\t%lf\t%" SCNu64 "\t%" SCNu64, &valid, &raw, &rx, &now) == 4)
            r = ifm_filter_update(&s, valid != 0u, raw, rx, now);
        else return 3;
        (void)printf("TRACE\t%s\t%s\t%u\t%u\t%.17g\t%u\t%.17g\t%u\t%" PRIu64
            "\t%u\t%" PRIu64 "\t%u\t%" PRIu64 "\t%u\t%.17g\t%u\t%u"
            "\t%" PRIu64 "\t%" PRIu64 "\t%" PRIu64 "\t%" PRIu64 "\t%" PRIu64 "\t%" PRIu64 "\n",
            ifm_reason_name(r.reason), ifm_event_name(r.event), (unsigned)r.valid,
            (unsigned)r.raw_present, r.raw, (unsigned)r.filtered_present, r.filtered,
            (unsigned)r.accepted_present, r.last_accepted_ns, (unsigned)r.accepted_age_present,
            r.accepted_age_ns, (unsigned)r.seen_present, r.last_seen_rx_ns,
            (unsigned)r.gate_delta_present, r.gate_allowed_raw_delta,
            r.warmup_accepted_samples, r.warmup_required, r.accepted_count,
            r.jump_rejected_count, r.bad_data_count, r.clock_rejected_count,
            r.stale_count, r.reset_count);
    }
    return ferror(stdin) ? 4 : 0;
}

int main(int argc, char **argv)
{
    ifm_filter f;
    ifm_filter_config filter_config = fc(3);
    ifm_map_config map = {.00284073624, .53041739067, 0, 128, 0, 160};
    ifm_reference_config config = {8, 0, 160, 150u * MS, 500u * MS};
    ifm_filter_result q;
    ifm_map_result m;
    ifm_reference ref;
    ifm_reference_result r;
    unsigned i;
    uint64_t now;
    if ((argc == 3 || argc == 4) && !strcmp(argv[1], "--trace")) {
        bool preserve = argc == 4 && !strcmp(argv[3], "1");
        if (argc == 4 && strcmp(argv[3], "0") && strcmp(argv[3], "1")) return 2;
        if (!strcmp(argv[2], "3")) return trace(3, preserve);
        if (!strcmp(argv[2], "5")) return trace(5, preserve);
        return 2;
    }
    if (argc != 1) return 2;
    check(!ifm_filter_init(NULL, &filter_config), "null filter");
    check(!ifm_filter_init(&f, NULL), "no default filter parameters");
    filter_config.median_window = 1;
    check(!ifm_filter_init(&f, &filter_config), "window explicit three/five");
    filter_config = fc(3);
    check(ifm_filter_init(&f, &filter_config), "explicit filter init");
    for (i = 0; i < 3u; ++i) {
        q = ifm_filter_update(&f, true, .4, i * 33u * MS, i * 33u * MS);
        check(q.valid == (i == 2u), "full filter warmup");
    }
    q = ifm_filter_update(&f, true, .9, 99u * MS, 99u * MS);
    check(!q.valid && q.reason == IFM_JUMP_REJECTED && q.last_accepted_ns == 66u * MS, "jump cannot renew accepted reference");
    check(q.warmup_accepted_samples == 0 && !q.filtered_present, "jump clears smoothing");
    q = ifm_filter_snapshot(&f, 215u * MS);
    check(!q.valid && q.accepted_age_ns == 149u * MS, "rejection keeps expiring anchor only");
    q = ifm_filter_snapshot(&f, 216u * MS);
    check(q.reason == IFM_ACCEPTED_EXPIRED && !q.accepted_present, "silence expires anchor");
    q = ifm_filter_update(&f, true, .8, 250u * MS, 250u * MS);
    check(!q.valid && q.warmup_accepted_samples == 1u, "reentry not automatically valid");
    q = ifm_filter_update(&f, true, NAN, 283u * MS, 283u * MS);
    check(!q.valid && q.reason == IFM_INVALID_RAW && !q.accepted_present, "nan withdraws all history");
    q = ifm_filter_update(&f, true, .4, 283u * MS, 300u * MS);
    check(q.reason == IFM_SOURCE_TIME_NOT_INCREASING, "duplicate source rejected");
    filter_config.preserve_smoothing_on_jump = true;
    check(ifm_filter_init(&f, &filter_config), "v2 preserve policy explicit");
    for (i = 0; i < 3u; ++i) q = ifm_filter_update(&f, true, .4, i * 33u * MS, i * 33u * MS);
    q = ifm_filter_update(&f, true, .9, 99u * MS, 99u * MS);
    check(!q.valid && !q.filtered_present && q.last_accepted_ns == 66u * MS && q.reset_count == 0, "preserve reject remains absent and does not renew anchor");
    q = ifm_filter_snapshot(&f, 100u * MS);
    check(!q.valid && !q.filtered_present && q.reason == IFM_JUMP_REJECTED, "snapshot cannot resurrect preserved history");
    q = ifm_filter_update(&f, true, .41, 132u * MS, 132u * MS);
    check(q.valid && q.last_accepted_ns == 132u * MS && q.warmup_accepted_samples == 3u, "new acceptable sample can continue preserved history");
    q = ifm_filter_update(&f, true, .9, 165u * MS, 165u * MS);
    check(!q.valid, "preserve subsequent jump absent");
    q = ifm_filter_snapshot(&f, 282u * MS);
    check(!q.valid && q.reason == IFM_ACCEPTED_EXPIRED && !q.accepted_present, "preserved history still expires at exact deadline");
    q = ifm_filter_update(&f, true, .9, 320u * MS, 320u * MS);
    check(!q.valid && q.warmup_accepted_samples == 1u, "stale recovery still requires warmup");
    check(ifm_map_config_valid(&map), "explicit map config");
    m = ifm_map(&map, true, .53041739067);
    check(m.valid && m.predicted_unclamped == 128 && !m.mechanical_clamped, "exact map endpoint");
    m = ifm_map(&map, true, .9);
    check(m.valid && m.predicted_unclamped > 200 && m.mechanical_clamped && m.mechanical_position == 160, "unclamped extrapolation plus mechanical clamp separate");
    m = ifm_map(&map, true, 0);
    check(m.valid && m.predicted_unclamped < 0 && m.mechanical_position == 0 && m.mechanical_clamped, "lower extrapolation retained");
    check(!ifm_map(&map, false, .5).valid && !ifm_map(&map, true, NAN).valid, "invalid feature never map candidate");
    map.raw_gentle = map.raw_straight;
    check(!ifm_map_config_valid(&map), "duplicate map endpoints rejected");
    check(!ifm_reference_init(&ref, &config, false, 75, 0, 0).valid, "actual feedback required");
    check(!ifm_reference_init(&ref, &config, true, 75.5, 0, 0).valid, "actual feedback never truncated");
    check(!ifm_reference_init(&ref, &config, true, 75, 0, 500u * MS).valid, "actual feedback exact age boundary stale");
    check(!ifm_reference_init(&ref, &config, true, 161, 0, 0).valid, "actual outside candidate bounds rejected");
    r = ifm_reference_init(&ref, &config, true, 75, 0, 0);
    check(r.valid && r.position == 75, "reference starts at actual P not zero");
    for (i = 1; i <= 4u; ++i) {
        r = ifm_reference_step(&ref, true, 128, i * 33u * MS);
        check(r.valid && r.moved_whole <= r.allowed_whole, "integer allowance bound");
        check(r.position == (i < 4u ? 75 : 76), "fractional elapsed budget avoids zero-step deadlock");
        check(r.fraction_after >= 0 && r.fraction_after < 1, "only subunit fraction survives");
    }
    r = ifm_reference_init(&ref, &config, true, 75, 0, 0);
    for (i = 1; i <= 100u; ++i) {
        r = ifm_reference_step(&ref, true, 75, i * 100u * MS);
        check(r.valid && !r.moved && r.fraction_after == 0, "idle banks neither whole nor fraction");
    }
    r = ifm_reference_step(&ref, true, 128, 10033u * MS);
    check(r.valid && r.position == 75 && r.allowed_whole == 0, "no idle catchup");
    (void)ifm_reference_init(&ref, &config, true, 75, 0, 0);
    r = ifm_reference_step(&ref, true, 128, 100u * MS);
    check(!r.moved && r.fraction_after > .79, "forward subunit budget");
    r = ifm_reference_step(&ref, true, 0, 125u * MS);
    check(r.valid && !r.moved && r.direction_budget_reset && r.fraction_before == 0, "reverse cannot spend old directional fraction");
    (void)ifm_reference_init(&ref, &config, true, 75, 0, 0);
    check(ifm_reference_step(&ref, true, 128, 150u * MS).reason == IFM_REFERENCE_GAP, "gap clears reference at boundary");
    check(!ifm_reference_step(&ref, true, 128, 151u * MS).initialized, "no automatic old target recovery");
    (void)ifm_reference_init(&ref, &config, true, 75, 100u * MS, 100u * MS);
    check(ifm_reference_step(&ref, true, 128, 100u * MS).reason == IFM_REFERENCE_CLOCK_INVALID, "repeated control time withdraws reference");
    (void)ifm_reference_init(&ref, &config, true, 75, 0, 0);
    check(ifm_reference_step(&ref, true, NAN, MS).reason == IFM_REFERENCE_INVALID_TARGET, "nan target withdraws reference");
    (void)ifm_reference_init(&ref, &config, true, 75, 0, 0);
    now = 0;
    for (i = 0; i < 500u; ++i) {
        static const uint64_t steps[] = {19u * MS, 47u * MS, 33u * MS, 21u * MS};
        now += steps[i % 4u];
        r = ifm_reference_step(&ref, true, 128, now);
        check(r.valid && r.position >= 75 && r.position <= 128 && r.moved_whole <= r.allowed_whole, "irregular-rate reference stays bounded");
        check(r.position - 75 <= (int32_t)floor(8 * (double)now / 1e9 + 1e-9), "total reference cannot spend future time");
    }
    ifm_reference_reset(&ref);
    check(!ifm_reference_step(&ref, true, 128, now + MS).valid, "reset requires fresh actual feedback");
    (void)printf("PASS %u checks; %u failures; pure mathematical candidates only\n", checks, failures);
    return failures ? 1 : 0;
}
