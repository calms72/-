#ifndef INDEX_FOLLOW_BUS_SCHEDULE_H
#define INDEX_FOLLOW_BUS_SCHEDULE_H
#include <stdbool.h>
#include <stdint.h>

/* Pure arbitration only: no I/O, retries, targets, safety qualification or
 * freshness extension. The actual-position/vision/STOP gates still apply. */
#define IBS_A0_PERIOD_NS UINT64_C(100000000)
#define IBS_QUIET_NS UINT64_C(5000000)
#define IBS_ALL_NODES UINT8_C(63)
typedef enum { IBS_WAIT, IBS_A0_ROUND, IBS_AA_SLOT, IBS_BLOCKED } ibs_action;
typedef enum { IBS_OK, IBS_CLOCK, IBS_A0_IN_FLIGHT, IBS_AA_IN_FLIGHT,
    IBS_REPLY_QUIET, IBS_ACK_QUIET, IBS_A0_PRIORITY, IBS_NO_COMPLETE_ROUND,
    IBS_BAD_ROUND, IBS_BAD_NODE, IBS_DUPLICATE, IBS_UNEXPECTED_ACK,
    IBS_OVERFLOW } ibs_reason;
typedef struct {
    bool initialized, failed, have_round, have_reply, have_aa_ack, aa_pending;
    uint8_t a0_pending_mask, a0_complete_mask;
    uint64_t last_now_ns, next_a0_due_ns, last_reply_ns, last_aa_ack_ns;
    uint64_t round_id, a0_rounds, aa_attempts;
    ibs_reason reason;
} ibs_schedule;

bool ibs_init(ibs_schedule *schedule, uint64_t now_ns);
/* Same-time A0 due always wins. Calls may advance the monotonic observation,
 * but WAIT/plan never consumes a round/AA attempt or shifts a deadline. */
ibs_action ibs_plan(ibs_schedule *schedule, uint64_t now_ns, bool wants_aa);
bool ibs_begin_a0(ibs_schedule *schedule, uint64_t now_ns);
/* Call once only after BOTH existing A0 receive paths match that round/node. */
bool ibs_a0_complete(ibs_schedule *schedule, uint64_t round_id,
                     unsigned node_id, uint64_t now_ns);
/* Every valid remote reply, from either receive path, restarts the AA quiet
 * interval. This must be called before evaluating/committing an AA slot. */
bool ibs_note_reply(ibs_schedule *schedule, uint64_t now_ns);
bool ibs_begin_aa(ibs_schedule *schedule, uint64_t now_ns);
/* Only after BOTH existing AA receive paths associate the one outstanding AA. */
bool ibs_aa_complete(ibs_schedule *schedule, uint64_t now_ns);
const char *ibs_reason_name(ibs_reason reason);
#endif
