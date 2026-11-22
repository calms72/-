#ifndef READONLY_SNAPSHOT_H
#define READONLY_SNAPSHOT_H

/* Pure formatting/freshness logic. This module has no device or send API. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define RP_VISION_FRESH_NS UINT64_C(150000000)
#define RP_FEEDBACK_FRESH_NS UINT64_C(500000000)

typedef struct {
    uint64_t count, received_ns;
    uint16_t position;
    int velocity;
    uint8_t status;
} rp_node;

typedef struct {
    bool live;
    unsigned faults, measurement_mask, raw_present;
    uint64_t now_ns, epoch, accepted_seq, accepted_rx_ns;
    double index_raw;
    rp_node nodes[6];
} rp_input;

/* Writes one PREVIEW-prefixed JSON line. 0 means invalid arguments/capacity;
 * no partial line should be published when the return value is zero. */
size_t rp_format(const rp_input *input, char *buffer, size_t capacity);

#endif
