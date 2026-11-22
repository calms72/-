#ifndef RECOVERY_HOLD_FSM_H
#define RECOVERY_HOLD_FSM_H
#include <stdbool.h>
#include <stdint.h>
/* Pure decision module. All preflight/ledger/transport facts are supplied by
 * the independently audited I/O layer; booleans are not proof by themselves.
 * There is no MOVE command and no configurable target/speed/node API. */
#define RHP_FRESH_NS UINT64_C(150000000)
#define RHP_ACK_NS UINT64_C(200000000)
#define RHP_QUIET_NS UINT64_C(5000000)
#define RHP_VERIFY_NS UINT64_C(2000000000)
#define RHP_WHOLE_NS UINT64_C(5000000000)
#define RHP_STABLE_NS UINT64_C(500000000)
typedef enum { RHP_STANDBY, RHP_PREFLIGHT, RHP_WAIT_SLOT,
    RHP_WAIT_ACK, RHP_VERIFY, RHP_DONE, RHP_FAILED } rhp_state;
typedef enum { RHP_NO_COMMAND, RHP_HOLD_CURRENT_ONCE } rhp_command;
typedef struct {
    uint64_t now_ns, feedback_query_ns, feedback_rx_ns, ack_ns;
    uint16_t actual_position;
    int velocity;
    unsigned status;
    bool all_six_fresh_healthy, can_trustworthy, ledger_verified;
    bool attempt_durable, slot_ready, stop_requested, ack_dual_matched;
} rhp_input;
typedef struct { rhp_command command; uint16_t position; rhp_state state; } rhp_output;
typedef struct {
    rhp_state state;
    bool started, attempt_durable, committed, stop_seen, feedback_hold_verified;
    uint64_t last_now_ns, started_ns, whole_until_ns, sent_ns, ack_ns, verify_until_ns;
    uint64_t stable_first_query_ns, stable_last_query_ns;
    unsigned stable_count, attempts;
    uint16_t hold_target, stable_min, stable_max;
    const char *reason;
} rhp_machine;
void rhp_init(rhp_machine *machine);
/* Manual start only; runtime must subsequently perform the real bounded
 * preflight and fsynced O_EXCL write before prepared(...,true). */
bool rhp_start(rhp_machine *machine, uint64_t now_ns);
bool rhp_prepared(rhp_machine *machine, uint64_t now_ns, bool durable_preflight);
rhp_output rhp_step(rhp_machine *machine, const rhp_input *input);
/* Recomputes the candidate using final actual P. Any stale/changed candidate
 * is terminal; no retry. Caller also commits the bus slot before the one
 * syscall. On pre-syscall cancellation it marks failure, retains the durable
 * marker, and MUST NOT return this FSM to WAIT_SLOT. */
bool rhp_commit(rhp_machine *machine, const rhp_input *input, uint16_t target);
void rhp_fail(rhp_machine *machine, const char *reason);
#endif
