#include "key_guard_events.h"

kg_event_result kg_classify(uint16_t type, uint16_t code, int32_t value)
{
    if (type > 31u) return KG_BAD_EVENT;
    if (type == KG_EV_SYN) {
        if (code == KG_SYN_DROPPED) return KG_EVENT_LOSS;
        return value == 0 ? KG_IGNORE : KG_BAD_EVENT;
    }
    if (type != KG_EV_KEY) return KG_IGNORE;
    if (code > 767u || value < 0 || value > 2) return KG_BAD_EVENT;
    /* Active polarity is deliberately irrelevant: release, press and repeat
     * all withdraw this read-only session. Static startup levels do not arm. */
    return code == KG_STOP_CODE ? KG_STOP_EVENT : KG_IGNORE;
}

bool kg_batch_valid(size_t bytes, size_t event_size)
{
    return event_size > 0 && event_size <= SIZE_MAX / 16u && bytes > 0 &&
        bytes <= event_size * 16u && bytes % event_size == 0;
}
