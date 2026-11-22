#include "readonly_snapshot.h"
#include <inttypes.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>

typedef struct {
    char *buffer;
    size_t capacity, used;
    bool ok;
} writer;

static void append(writer *out, const char *format, ...)
{
    int size;
    va_list arguments;
    if (!out->ok) return;
    va_start(arguments, format);
    size = vsnprintf(out->buffer + out->used, out->capacity - out->used,
                     format, arguments);
    va_end(arguments);
    if (size < 0 || (size_t)size >= out->capacity - out->used) out->ok = false;
    else out->used += (size_t)size;
}

static void age_json(char *buffer, size_t capacity, bool exists,
                     uint64_t now, uint64_t received)
{
    if (exists && now >= received)
        (void)snprintf(buffer, capacity, "%" PRIu64, now - received);
    else (void)snprintf(buffer, capacity, "null");
}

size_t rp_format(const rp_input *input, char *buffer, size_t capacity)
{
    writer out = {buffer, capacity, 0, true};
    char raw[40], age[32];
    const char *reason;
    bool exists, fresh, valid;
    unsigned i;
    if (!input || !buffer || !capacity) return 0;
    exists = input->accepted_seq != 0;
    fresh = exists && input->now_ns >= input->accepted_rx_ns &&
        input->now_ns - input->accepted_rx_ns < RP_VISION_FRESH_NS;
    valid = !input->faults && fresh && (input->measurement_mask & 2u) &&
        (input->raw_present & 2u) && isfinite(input->index_raw) &&
        input->index_raw >= 0.0 && input->index_raw <= 1.0;
    reason = input->faults ? "runtime_fault" : !exists ? "no_frame" :
        input->now_ns < input->accepted_rx_ns ? "clock_reversed" :
        !fresh ? "vision_stale" : !valid ? "measurement_invalid" :
        "numeric_geometry_only_not_calibrated";
    if (valid) (void)snprintf(raw, sizeof raw, "%.9g", input->index_raw);
    else (void)snprintf(raw, sizeof raw, "null");
    age_json(age, sizeof age, exists, input->now_ns, input->accepted_rx_ns);
    append(&out,
        "PREVIEW {\"schema\":\"readonly_preview_v1\",\"mode\":\"%s\","
        "\"monotonic_ns\":%" PRIu64 ",\"epoch\":%" PRIu64
        ",\"accepted_seq\":%" PRIu64 ",\"accepted_rx_ns\":%" PRIu64 ",\"fault_flags\":%u,"
        "\"index\":{\"raw\":%s,\"valid\":%s,\"age_ns\":%s,\"reason\":\"%s\","
        "\"calibrated\":false,\"motor_node\":3,\"predicted_position\":null,"
        "\"prediction_reason\":\"visual_endpoints_and_local_mechanical_range_unverified\"},"
        "\"feedback\":[",
        input->live ? "live" : "replay", input->now_ns, input->epoch,
        input->accepted_seq, input->accepted_rx_ns, input->faults,
        raw, valid ? "true" : "false", age, reason);
    for (i = 0; i < 6u; ++i) {
        const rp_node *node = &input->nodes[i];
        char position[16], velocity[16], status[8];
        bool received = node->count != 0;
        bool node_fresh = received && input->now_ns >= node->received_ns &&
            input->now_ns - node->received_ns < RP_FEEDBACK_FRESH_NS;
        bool node_valid = node_fresh && !node->status && !input->faults;
        const char *node_reason = input->faults ? "runtime_fault" :
            !received ? "no_feedback" : input->now_ns < node->received_ns ?
            "clock_reversed" : !node_fresh ? "feedback_stale" :
            node->status ? "motor_status_nonzero" : "fresh";
        if (node_valid) {
            (void)snprintf(position, sizeof position, "%u", (unsigned)node->position);
            (void)snprintf(velocity, sizeof velocity, "%d", node->velocity);
        } else {
            (void)snprintf(position, sizeof position, "null");
            (void)snprintf(velocity, sizeof velocity, "null");
        }
        /* Status can explain a fresh fault, but stale status is never current. */
        if (node_fresh) (void)snprintf(status, sizeof status, "%u", (unsigned)node->status);
        else (void)snprintf(status, sizeof status, "null");
        age_json(age, sizeof age, received, input->now_ns, node->received_ns);
        append(&out, "%s{\"node\":%u,\"valid\":%s,\"position\":%s,"
            "\"velocity\":%s,\"status\":%s,\"age_ns\":%s,\"reason\":\"%s\",\"count\":%" PRIu64 "}",
            i ? "," : "", i + 1u, node_valid ? "true" : "false", position,
            velocity, status, age, node_reason, node->count);
    }
    append(&out, "],\"side_swing_valid\":false,\"gate\":\"BLOCKED\",\"motion_tx\":0}\n");
    if (!out.ok) { buffer[0] = 0; return 0; }
    return out.used;
}
