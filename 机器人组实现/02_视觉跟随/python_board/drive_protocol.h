#ifndef DRIVE_PROTOCOL_H
#define DRIVE_PROTOCOL_H

#include <stdbool.h>
#include <stdint.h>

/* PURE OFFLINE ENCODER. No CAN/device I/O, send function, clock, permission,
 * calibration, control gate or proof of actual stopping. A successful return
 * means only protocol-format validity. The output is NOT a native CAN struct.
 * All callers must check the return value; an all-zero rejected output must
 * NEVER be submitted as a frame (ID 0 is a protocol broadcast address).
 */
typedef struct {
    uint32_t id;
    uint8_t dlc;
    uint8_t data[8];
} drive_protocol_frame;

/* These mirror only the original BA labels, not verified braking behavior. */
#define DRIVE_PROTOCOL_BA_MODE_NO_RESISTANCE UINT64_C(0)
#define DRIVE_PROTOCOL_BA_MODE_WITH_RESISTANCE UINT64_C(1)

/* Nodes are restricted to 1..6. Non-NULL output is entirely zeroed on failure,
 * and every unused data byte is zero on success. NULL output returns false.
 * Parameters remain uint64_t until validated, so wide invalid values cannot
 * silently wrap into a valid node, u16 position/speed/current or u8 BA mode.
 */
bool drive_protocol_encode_a0(uint64_t node_id, drive_protocol_frame *output);

/* Original single-motor row 11: D11:E11 = position bytes 1..2, 0..4095;
 * F11:G11 = speed bytes 3..4, 0..65535; A5 specifies little endian.
 * This API REQUIRES explicit speed 1..65535, and offers no omitted/zero-speed
 * variant. Speed units in that row are 0.001 stroke/s, NOT a safety limit.
 */
bool drive_protocol_encode_a1(uint64_t node_id, uint64_t position_raw,
                              uint64_t speed_raw, drive_protocol_frame *output);

/* Original row 20: D20:E20 = position, F20:G20 = speed, H20:I20 =
 * process maximum current, all two-byte little endian. H20 describes signed
 * -32768..32767; the official SDK declares u16 but repeats that signed range.
 * This API uses the unambiguous positive-current subset 1..32767, rejecting
 * zero/omitted current and bit patterns that denote negative signed values.
 * Position 0..4095 and explicit speed 1..65535 follow the A1 format policy.
 * Every bound here is a protocol-format bound, NOT a calibrated mechanical,
 * speed, current, force or thermal safety limit. Success cannot authorize I/O.
 */
bool drive_protocol_encode_aa(uint64_t node_id, uint64_t position_raw,
                              uint64_t speed_raw, uint64_t max_current_raw,
                              drive_protocol_frame *output);

/* Explicit BA mode 0 or 1 only. This encodes bytes; it does not issue a stop,
 * choose a safe stop mode, verify support or observe an actual stop.
 */
bool drive_protocol_encode_ba(uint64_t node_id, uint64_t mode,
                              drive_protocol_frame *output);

#endif
