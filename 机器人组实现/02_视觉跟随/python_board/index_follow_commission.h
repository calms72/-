#ifndef INDEX_FOLLOW_COMMISSION_H
#define INDEX_FOLLOW_COMMISSION_H
#include <stdbool.h>
#include <stdint.h>
/* Pure bounded commission decision machine, no I/O or normal ARM authority. */
typedef enum { IFC_STANDBY, IFC_PREPARE, IFC_RUN, IFC_STOP_DRAIN,
    IFC_HOLD_ACK, IFC_HOLD_VERIFY, IFC_DONE, IFC_POWER_REQUIRED } ifc_state;
typedef enum { IFC_NO_COMMAND, IFC_MOVE, IFC_HOLD } ifc_command;
#define IFC_HOLD_TARGET_TOLERANCE 8u
#define IFC_TRACKING_ERROR_MAX 16u
#define IFC_STABLE_SPAN_MAX 2u
#define IFC_STABLE_MIN_SAMPLES 5u
#define IFC_STABLE_MIN_NS UINT64_C(500000000)
typedef struct {
    uint64_t now_ns, feedback_ns, ack_serial, ack_ns;
    bool stop_requested, can_trustworthy, feedback_valid;
    bool protocol_failed, exposure_persisted;
    uint16_t position;
    int velocity;
} ifc_input;
typedef struct { ifc_command command; uint16_t position; ifc_state state; } ifc_output;
typedef struct {
    ifc_state state;
    bool started, pending, hold_attempted, stop_feedback_verified;
    uint64_t start_ns, prepare_until, run_until, last_now, last_move_ns;
    uint64_t stop_until, pending_since, pending_serial, attempts, moves, holds;
    uint64_t hold_ack_ns, stable_since, stable_last_ns;
    unsigned stable_count;
    uint16_t reference, pending_target, hold_target, stable_position, stable_min, stable_max;
    ifc_command pending_kind;
    const char *reason;
    const char *stop_reason; /* first stop/failure cause survives hold verification */
} ifc_machine;
void ifc_init(ifc_machine *machine);
/* Persistence and true current P0/zero-velocity preflight are caller facts.
 * A successful start is single-use, including when subsequently cancelled. */
bool ifc_start(ifc_machine *machine, uint64_t now_ns, bool preflight_verified,
               bool attempt_persisted, bool exposure_persisted, uint16_t actual_position);
ifc_output ifc_step(ifc_machine *machine, const ifc_input *input);
/* Caller repeats STOP/time/feedback checks immediately before this commit,
 * then performs at most one write. Failed/short/uncertain write is terminal.
 * This consumes the attempt before syscall, never after it. */
bool ifc_begin_attempt(ifc_machine *machine, const ifc_input *input,
                       ifc_command command, uint16_t position);
#endif
