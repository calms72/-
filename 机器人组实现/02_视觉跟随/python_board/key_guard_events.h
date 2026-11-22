#ifndef KEY_GUARD_EVENTS_H
#define KEY_GUARD_EVENTS_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Pure event classification, no device I/O and no physical-stop semantics. */
#define KG_EV_SYN UINT16_C(0)
#define KG_EV_KEY UINT16_C(1)
#define KG_SYN_DROPPED UINT16_C(3)
#define KG_STOP_CODE UINT16_C(259)
typedef enum { KG_IGNORE, KG_STOP_EVENT, KG_EVENT_LOSS, KG_BAD_EVENT } kg_event_result;
kg_event_result kg_classify(uint16_t type, uint16_t code, int32_t value);
bool kg_batch_valid(size_t bytes, size_t event_size);
#endif
