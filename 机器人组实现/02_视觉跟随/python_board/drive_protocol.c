#include "drive_protocol.h"

#include <string.h>

static bool clear_and_check_node(uint64_t node_id, drive_protocol_frame *output)
{
    if (!output) return false;
    memset(output, 0, sizeof *output);
    return node_id >= UINT64_C(1) && node_id <= UINT64_C(6);
}

/* Called only after the public entry point has checked the full-width value. */
static void put_u16_le(uint8_t *destination, uint64_t value)
{
    destination[0] = (uint8_t)(value & UINT64_C(0xff));
    destination[1] = (uint8_t)((value >> 8U) & UINT64_C(0xff));
}

bool drive_protocol_encode_a0(uint64_t node_id, drive_protocol_frame *output)
{
    if (!clear_and_check_node(node_id, output)) return false;
    output->id = (uint32_t)node_id;
    output->dlc = 1U;
    output->data[0] = 0xa0U;
    return true;
}

bool drive_protocol_encode_a1(uint64_t node_id, uint64_t position_raw,
                              uint64_t speed_raw, drive_protocol_frame *output)
{
    if (!clear_and_check_node(node_id, output)) return false;
    if (position_raw > UINT64_C(4095) || speed_raw < UINT64_C(1) ||
        speed_raw > UINT64_C(65535)) return false;
    output->id = (uint32_t)node_id;
    output->dlc = 5U;
    output->data[0] = 0xa1U;
    put_u16_le(&output->data[1], position_raw);
    put_u16_le(&output->data[3], speed_raw);
    return true;
}

bool drive_protocol_encode_ba(uint64_t node_id, uint64_t mode,
                              drive_protocol_frame *output)
{
    if (!clear_and_check_node(node_id, output)) return false;
    if (mode != DRIVE_PROTOCOL_BA_MODE_NO_RESISTANCE &&
        mode != DRIVE_PROTOCOL_BA_MODE_WITH_RESISTANCE) return false;
    output->id = (uint32_t)node_id;
    output->dlc = 2U;
    output->data[0] = 0xbaU;
    output->data[1] = (uint8_t)mode;
    return true;
}

bool drive_protocol_encode_aa(uint64_t node_id, uint64_t position_raw,
                              uint64_t speed_raw, uint64_t max_current_raw,
                              drive_protocol_frame *output)
{
    if (!clear_and_check_node(node_id, output)) return false;
    if (position_raw > UINT64_C(4095) || speed_raw < UINT64_C(1) ||
        speed_raw > UINT64_C(65535) || max_current_raw < UINT64_C(1) ||
        max_current_raw > UINT64_C(32767)) return false;
    output->id = (uint32_t)node_id;
    output->dlc = 7U;
    output->data[0] = 0xaaU;
    put_u16_le(&output->data[1], position_raw);
    put_u16_le(&output->data[3], speed_raw);
    put_u16_le(&output->data[5], max_current_raw);
    return true;
}
