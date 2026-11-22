#include "../key_guard_events.h"
#include <limits.h>
#include <stdio.h>
static unsigned checks, failures;
static void check(bool ok, const char *name)
{
    ++checks;
    if (!ok) { ++failures; (void)fprintf(stderr, "FAIL %s\n", name); }
}
int main(void)
{
    int32_t value;
    for (value = 0; value <= 2; ++value) {
        check(kg_classify(1, 259, value) == KG_STOP_EVENT, "all STOP polarities/repeat stop");
        check(kg_classify(1, 258, value) == KG_IGNORE, "RUN never starts");
        check(kg_classify(1, 260, value) == KG_IGNORE, "RESET never recovers");
    }
    check(kg_classify(1, 259, -1) == KG_BAD_EVENT, "negative STOP invalid");
    check(kg_classify(1, 259, 3) == KG_BAD_EVENT, "unknown STOP value invalid");
    check(kg_classify(1, 259, INT32_MAX) == KG_BAD_EVENT, "large key value invalid");
    check(kg_classify(1, UINT16_MAX, 1) == KG_BAD_EVENT, "invalid key code");
    check(kg_classify(0, 3, 0) == KG_EVENT_LOSS, "SYN_DROPPED locks session");
    check(kg_classify(0, 3, INT32_MIN) == KG_EVENT_LOSS, "SYN_DROPPED never ignored");
    check(kg_classify(0, 0, 0) == KG_IGNORE, "SYN_REPORT normal");
    check(kg_classify(0, 0, 1) == KG_BAD_EVENT, "bad sync value");
    check(kg_classify(4, 4, 259) == KG_IGNORE, "MSC is not STOP by value");
    check(kg_classify(UINT16_MAX, 259, 1) == KG_BAD_EVENT, "unknown type invalid");
    check(kg_classify(0, 259, 0) == KG_IGNORE, "code alone cannot stop");
    for (size_t count = 1; count <= 16u; ++count)
        check(kg_batch_valid(count * 24u, 24u), "complete input_event batch");
    check(!kg_batch_valid(0, 24), "EOF not a valid event batch");
    check(!kg_batch_valid(23, 24), "partial event rejected");
    check(!kg_batch_valid(25, 24), "trailing event byte rejected");
    check(!kg_batch_valid(408, 24), "oversized batch rejected");
    check(!kg_batch_valid(1, 0), "zero event size rejected");
    check(!kg_batch_valid(SIZE_MAX, SIZE_MAX), "size multiplication cannot overflow");
    (void)printf("PASS %u checks; %u failures; no-I/O key event classifier\n", checks, failures);
    return failures ? 1 : 0;
}
