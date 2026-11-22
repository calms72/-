#ifndef CONTROL_GATE_H
#define CONTROL_GATE_H

#include <stdbool.h>
#include <stdint.h>

/* OFFLINE DECISION CORE ONLY. No I/O, clocks, CAN encoding or actual stop.
 * All *_ns are caller-supplied monotonic times. Trusted envelope/feedback and
 * health inputs must eventually come from C I/O, NEVER from Python replies.
 * Test fixture parameters are fictitious and are not mechanical calibration.
 */
#define CG_CHANNELS 6
#define CG_ALL_MASK UINT8_C(0x3f)

typedef enum { CG_STANDBY, CG_ARMED, CG_TRIPPED } cg_state;
typedef enum { CG_BLOCKED, CG_READY, CG_CANDIDATE, CG_STOP_REQUIRED } cg_decision;
typedef enum {
    CG_OK, CG_CONFIG, CG_CLOCK, CG_MANUAL_STOP, CG_HEALTH, CG_FEEDBACK,
    CG_NODE_FAULT, CG_NO_TARGET, CG_FEATURE, CG_REFERENCE, CG_STALE,
    CG_SEQUENCE, CG_RANGE, CG_CYCLE, CG_TRACKING, CG_COMPUTE, CG_SCOPE
} cg_reason;

typedef struct {
    uint64_t version;
    /* Nonempty subset of six channels; zero and bits outside CG_ALL_MASK fail.
     * The certifications below cover this entire selected set, its node
     * mapping, validation parameters and configuration version together.
     * A certification for a smaller set cannot qualify an expanded set. */
    uint8_t active_mask;
    bool mechanical_verified, vision_verified, feedback_verified;
    bool stop_strategy_verified, lower_stop_verified;
    /* Active IDs must be 1..254. Inactive IDs may be zero (undeclared); every
     * declared nonzero ID must be 1..254 and unique across the whole mapping.
     * Calibration/rate/tracking parameters are required only for active axes. */
    uint16_t node_id[CG_CHANNELS];
    double minimum[CG_CHANNELS], maximum[CG_CHANNELS];
    double max_speed[CG_CHANNELS], max_reference_error[CG_CHANNELS];
    uint64_t frame_max_age_ns, feedback_max_age_ns;
    uint64_t compute_max_age_ns, max_step_ns;
} cg_config;

typedef struct {
    bool valid;
    uint64_t epoch, received_ns;
    uint16_t node_id;
    uint8_t status;
    double position;
} cg_feedback;

typedef struct {
    bool present;
    uint64_t epoch, seq, config_version;
    bool feature_valid[CG_CHANNELS], position_present[CG_CHANNELS];
    /* Inactive axes MUST have both flags false and a finite zero storage value.
     * That zero is a canonical absent field, NEVER a physical position target.
     * A present feature or hidden/noncanonical value is a scope error even
     * when the overall target is absent. No missing active target is invented. */
    double position[CG_CHANNELS];
} cg_target;

typedef struct {
    uint64_t now_ns;
    bool manual_start, manual_stop;
    /* Explicit start EVENT authorized for this epoch, not a held button level. */
    uint64_t manual_start_epoch;
    /* Health and fault retention remain global and supplied by trusted C.
     * active_mask only limits candidate axes; it does not certify physical
     * disabling of inactive drives. Any known nonzero feedback status, even
     * inactive/stale/invalid, rejects the group. The caller must retain known
     * faults instead of replacing them with empty feedback or healthy=true. */
    bool python_healthy, can_healthy, single_control_source;
    bool compute_pending;
    uint64_t compute_started_ns;
    /* Trusted identity of the exact in-flight frame offered to Python.
     * rx_ns is original LOCAL receive-complete time, not a source timestamp. */
    uint64_t issued_epoch, issued_seq, issued_rx_ns;
    cg_feedback feedback[CG_CHANNELS];
    cg_target target;
} cg_input;

typedef struct {
    cg_state state;
    cg_decision decision;
    cg_reason reason;
    /* Only CG_CANDIDATE may carry references: exactly the configured active
     * set, as one complete group. All other outputs have mask=0/valid=false.
     * Inactive array zeros are absent storage, never sendable references. */
    uint8_t reference_mask;
    bool reference_valid;
    double reference[CG_CHANNELS];
    /* Always false: this library has no transmission capability. */
    bool tx_capable;
} cg_output;

typedef struct {
    /* Immutable snapshot copied at init. No reconfiguration through step or
     * recover. Changing selection/calibration requires separately qualified
     * version + new epoch after external stop confirmation, then a fresh init.
     * As for all public state in this C API, caller must not mutate internals. */
    cg_config config;
    cg_state state;
    cg_reason latched_reason;
    uint64_t epoch, last_now_ns, last_seq, last_rx_ns, reference_ns;
    double reference[CG_CHANNELS];
} cg_gate;

/* epoch must be nonzero and must not be reused across process restarts.
 * This pure core cannot generate/persist a boot epoch; caller owns that duty. */
void cg_init(cg_gate *gate, const cg_config *config, uint64_t epoch, uint64_t now_ns);
/* A new epoch must be STRICTLY greater; no wrap/reuse. stop_confirmed is a
 * trusted external fact covering the controlled system, never inferred from
 * STOP_REQUIRED, an inactive mask bit or a CAN write. Configuration/selection
 * are preserved; references are cleared and a new explicit start is required.
 * Recovery does not clear drive faults or establish a physical stop. */
bool cg_recover(cg_gate *gate, uint64_t new_epoch, uint64_t now_ns, bool stop_confirmed);
cg_output cg_step(cg_gate *gate, const cg_input *input);

#endif
