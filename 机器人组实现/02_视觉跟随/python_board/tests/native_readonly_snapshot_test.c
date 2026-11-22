#include "../readonly_snapshot.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static unsigned checks, failures;
static void check(bool condition, const char *name)
{
    ++checks;
    if (!condition) { ++failures; (void)fprintf(stderr, "FAIL %s\n", name); }
}

static void contains(const char *line, const char *part, const char *name)
{
    check(strstr(line, part) != NULL, name);
}

static void emit(rp_input *input, char *line, size_t capacity)
{
    size_t size = rp_format(input, line, capacity);
    check(size > 0 && size == strlen(line), "complete bounded line");
    check(size < 2048u, "runtime buffer capacity");
    contains(line, "\"motion_tx\":0", "never motion output");
    contains(line, "\"predicted_position\":null", "no guessed mapping");
    contains(line, "\"motor_node\":3", "independently observed index binding");
    contains(line, "\"side_swing_valid\":false", "unverified side remains invalid");
    (void)fputs(line, stdout);
}

int main(void)
{
    rp_input input = {0};
    char line[2048], small[16];
    unsigned i;
    input.now_ns = UINT64_C(2000000000);
    emit(&input, line, sizeof line);
    contains(line, "\"index\":{\"raw\":null,\"valid\":false,\"age_ns\":null,\"reason\":\"no_frame\"", "no frame is null");
    contains(line, "\"node\":3,\"valid\":false,\"position\":null,\"velocity\":null,\"status\":null,\"age_ns\":null", "never invent initial feedback");

    input.live = true;
    input.accepted_seq = 1;
    input.accepted_rx_ns = input.now_ns - 1;
    input.measurement_mask = input.raw_present = 2;
    input.index_raw = 0.25;
    for (i = 0; i < 6u; ++i) {
        input.nodes[i].count = 1;
        input.nodes[i].received_ns = input.now_ns - 1;
        input.nodes[i].position = (uint16_t)(i + 10u);
        input.nodes[i].velocity = -(int)i;
    }
    emit(&input, line, sizeof line);
    contains(line, "\"index\":{\"raw\":0.25,\"valid\":true,\"age_ns\":1", "fresh index raw");
    contains(line, "\"node\":3,\"valid\":true,\"position\":12,\"velocity\":-2,\"status\":0,\"age_ns\":1", "fresh index feedback");

    input.accepted_rx_ns = input.now_ns - RP_VISION_FRESH_NS + 1u;
    emit(&input, line, sizeof line);
    contains(line, "\"raw\":0.25,\"valid\":true,\"age_ns\":149999999", "vision before boundary");
    input.accepted_rx_ns = input.now_ns - RP_VISION_FRESH_NS;
    emit(&input, line, sizeof line);
    contains(line, "\"raw\":null,\"valid\":false,\"age_ns\":150000000,\"reason\":\"vision_stale\"", "vision at boundary hidden");

    input.accepted_rx_ns = input.now_ns;
    input.measurement_mask = 0;
    emit(&input, line, sizeof line);
    contains(line, "\"raw\":null,\"valid\":false,\"age_ns\":0,\"reason\":\"measurement_invalid\"", "invalid latest hides old raw");
    input.measurement_mask = 2;
    input.raw_present = 0;
    emit(&input, line, sizeof line);
    contains(line, "\"raw\":null,\"valid\":false", "missing raw invalid despite mask");
    input.raw_present = 2;
    input.index_raw = NAN;
    emit(&input, line, sizeof line);
    contains(line, "\"raw\":null,\"valid\":false", "nan never JSON");
    input.index_raw = INFINITY;
    emit(&input, line, sizeof line);
    contains(line, "\"raw\":null,\"valid\":false", "infinity never JSON");
    input.index_raw = 1.01;
    emit(&input, line, sizeof line);
    contains(line, "\"raw\":null,\"valid\":false", "out of geometric range invalid");
    input.index_raw = 0.25;

    input.nodes[2].received_ns = input.now_ns - RP_FEEDBACK_FRESH_NS + 1u;
    emit(&input, line, sizeof line);
    contains(line, "\"node\":3,\"valid\":true,\"position\":12,\"velocity\":-2,\"status\":0,\"age_ns\":499999999", "feedback before boundary");
    input.nodes[2].received_ns = input.now_ns - RP_FEEDBACK_FRESH_NS;
    emit(&input, line, sizeof line);
    contains(line, "\"node\":3,\"valid\":false,\"position\":null,\"velocity\":null,\"status\":null,\"age_ns\":500000000,\"reason\":\"feedback_stale\"", "stale feedback hidden");

    input.nodes[2].received_ns = input.now_ns;
    input.nodes[2].status = 4;
    emit(&input, line, sizeof line);
    contains(line, "\"node\":3,\"valid\":false,\"position\":null,\"velocity\":null,\"status\":4,\"age_ns\":0,\"reason\":\"motor_status_nonzero\"", "fault status preserved fresh");
    input.faults = 64;
    emit(&input, line, sizeof line);
    contains(line, "\"raw\":null,\"valid\":false,\"age_ns\":0,\"reason\":\"runtime_fault\"", "fault hides even fresh vision");
    contains(line, "\"node\":1,\"valid\":false,\"position\":null,\"velocity\":null", "fault invalidates current feedback");

    input.faults = 0;
    input.accepted_rx_ns = input.now_ns + 1;
    input.nodes[2].received_ns = input.now_ns + 1;
    emit(&input, line, sizeof line);
    contains(line, "\"raw\":null,\"valid\":false,\"age_ns\":null,\"reason\":\"clock_reversed\"", "future vision timestamp rejected");
    contains(line, "\"node\":3,\"valid\":false,\"position\":null,\"velocity\":null,\"status\":null,\"age_ns\":null,\"reason\":\"clock_reversed\"", "future feedback timestamp rejected");
    input.now_ns = UINT64_MAX;
    input.accepted_rx_ns = 1;
    emit(&input, line, sizeof line);
    contains(line, "\"age_ns\":18446744073709551614", "large monotonic age survives");

    check(rp_format(NULL, line, sizeof line) == 0, "null input rejected");
    check(rp_format(&input, NULL, sizeof line) == 0, "null buffer rejected");
    check(rp_format(&input, line, 0) == 0, "empty capacity rejected");
    memset(small, 'x', sizeof small);
    check(rp_format(&input, small, sizeof small) == 0 && small[0] == 0, "truncation not publishable");
    (void)printf("PASS %u checks; %u failures; fixture-only no-I/O snapshot test\n", checks, failures);
    return failures ? 1 : 0;
}
