/* Host/offline only. No device access; diagnostics go only to stdout. */
#include "../drive_protocol.h"

#include <stdio.h>
#include <string.h>

static unsigned int checks;
static unsigned int failures;

static void check(bool passed, const char *description)
{
    ++checks;
    if (!passed) {
        ++failures;
        printf("FAIL %s\n", description);
    }
}

static bool zeroed(const drive_protocol_frame *frame)
{
    const unsigned char zeros[sizeof *frame] = {0};
    return memcmp(frame, zeros, sizeof *frame) == 0;
}

static void poison(drive_protocol_frame *frame)
{
    memset(frame, 0xa5, sizeof *frame);
}

static void golden(const drive_protocol_frame *frame, uint32_t id, uint8_t dlc,
                   const uint8_t expected[8], const char *description)
{
    check(frame->id == id && frame->dlc == dlc &&
          memcmp(frame->data, expected, sizeof frame->data) == 0, description);
}

/* Literal vectors include the newly captured hardware request. These fixtures
 * exercise only byte generation; no successful hardware run is reproduced. */
static void aa_tests(void)
{
    drive_protocol_frame frame;
    const uint8_t minimum[8] = {0xaaU, 0U, 0U, 1U, 0U, 1U, 0U, 0U};
    const uint8_t maximum[8] = {0xaaU, 0xffU, 0x0fU, 0xffU, 0xffU, 0xffU, 0x7fU, 0U};
    const uint8_t captured[8] = {0xaaU, 0x80U, 0U, 0xe8U, 0x09U, 0x64U, 0x03U, 0U};
    const uint8_t captured_return[8] = {0xaaU, 0U, 0U, 0xe8U, 0x09U, 0x64U, 0x03U, 0U};
    const uint8_t mixed[8] = {0xaaU, 0x23U, 1U, 0x67U, 0x45U, 0x45U, 0x23U, 0U};
    const uint8_t carry[8] = {0xaaU, 0U, 1U, 0U, 1U, 0U, 1U, 0U};
    const uint8_t a0[8] = {0xa0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
    const uint8_t a1[8] = {0xa1U, 0x23U, 1U, 0x67U, 0x45U, 0U, 0U, 0U};
    const uint8_t ba[8] = {0xbaU, 1U, 0U, 0U, 0U, 0U, 0U, 0U};

    for (uint64_t node = UINT64_C(1); node <= UINT64_C(6); ++node) {
        poison(&frame);
        check(drive_protocol_encode_aa(node, UINT64_C(0), UINT64_C(1), UINT64_C(1), &frame),
              "AA accepts each fixed node with explicit minimum speed/current");
        golden(&frame, (uint32_t)node, 7U, minimum, "AA minimum seven-byte payload and zero unused byte");
    }
    check(drive_protocol_encode_aa(UINT64_C(6), UINT64_C(4095), UINT64_C(65535), UINT64_C(32767), &frame),
          "AA accepts protocol endpoints without declaring them safe");
    golden(&frame, 6U, 7U, maximum, "AA signed-positive current maximum is 7FFF little endian");
    check(drive_protocol_encode_aa(UINT64_C(3), UINT64_C(128), UINT64_C(2536), UINT64_C(868), &frame),
          "AA accepts exact captured hardware request arguments");
    golden(&frame, 3U, 7U, captured, "AA matches captured AA8000E8096403, not rounded parameter guesses");
    check(drive_protocol_encode_aa(UINT64_C(3), UINT64_C(0), UINT64_C(2536), UINT64_C(868), &frame),
          "AA accepts captured return request without performing a return");
    golden(&frame, 3U, 7U, captured_return, "AA captured return vector");
    check(drive_protocol_encode_aa(UINT64_C(2), UINT64_C(291), UINT64_C(17767), UINT64_C(9029), &frame),
          "AA accepts distinct mixed-byte fields");
    golden(&frame, 2U, 7U, mixed, "AA position/speed/current stay in independent little-endian fields");
    check(drive_protocol_encode_aa(UINT64_C(5), UINT64_C(256), UINT64_C(256), UINT64_C(256), &frame),
          "AA accepts carry into each field's high byte");
    golden(&frame, 5U, 7U, carry, "AA all three fields cross byte boundary");

    const uint64_t invalid_nodes[] = {
        UINT64_C(0), UINT64_C(7), UINT64_C(254), UINT64_C(255), UINT64_C(256),
        UINT64_C(65537), UINT64_C(4294967297), UINT64_MAX
    };
    for (size_t i = 0U; i < sizeof invalid_nodes / sizeof invalid_nodes[0]; ++i) {
        poison(&frame);
        check(!drive_protocol_encode_aa(invalid_nodes[i], UINT64_C(128), UINT64_C(2536), UINT64_C(868), &frame),
              "AA rejects broadcast/out-of-scope/wide node before narrowing");
        check(zeroed(&frame), "AA node failure clears whole poisoned output");
    }
    const uint64_t invalid_positions[] = {
        UINT64_C(4096), UINT64_C(65535), UINT64_C(65536), UINT64_C(4294967424), UINT64_MAX
    };
    for (size_t i = 0U; i < sizeof invalid_positions / sizeof invalid_positions[0]; ++i) {
        check(drive_protocol_encode_aa(UINT64_C(3), UINT64_C(128), UINT64_C(2536), UINT64_C(868), &frame),
              "Prime captured AA before rejecting position");
        check(!drive_protocol_encode_aa(UINT64_C(3), invalid_positions[i], UINT64_C(2536), UINT64_C(868), &frame),
              "AA rejects position overflow including wide value truncating to captured position");
        check(zeroed(&frame), "AA invalid position cannot retain the previous captured request");
    }
    const uint64_t invalid_speeds[] = {
        UINT64_C(0), UINT64_C(65536), UINT64_C(65537), UINT64_C(4294969832), UINT64_MAX
    };
    for (size_t i = 0U; i < sizeof invalid_speeds / sizeof invalid_speeds[0]; ++i) {
        check(drive_protocol_encode_aa(UINT64_C(3), UINT64_C(128), UINT64_C(2536), UINT64_C(868), &frame),
              "Prime captured AA before rejecting speed");
        check(!drive_protocol_encode_aa(UINT64_C(3), UINT64_C(128), invalid_speeds[i], UINT64_C(868), &frame),
              "AA rejects omitted/zero/overflow/wide speed before narrowing");
        check(zeroed(&frame), "AA invalid speed cannot retain a previous valid request");
    }
    const uint64_t invalid_currents[] = {
        UINT64_C(0), UINT64_C(32768), UINT64_C(65535), UINT64_C(65536), UINT64_C(65537),
        UINT64_C(4294967297), UINT64_C(4294968164), UINT64_MAX
    };
    for (size_t i = 0U; i < sizeof invalid_currents / sizeof invalid_currents[0]; ++i) {
        check(drive_protocol_encode_aa(UINT64_C(3), UINT64_C(128), UINT64_C(2536), UINT64_C(868), &frame),
              "Prime captured AA before rejecting current");
        check(!drive_protocol_encode_aa(UINT64_C(3), UINT64_C(128), UINT64_C(2536), invalid_currents[i], &frame),
              "AA rejects zero/signed-negative-bit-pattern/overflow/wide current");
        check(zeroed(&frame), "AA invalid current cannot retain a previous valid request");
    }

    check(drive_protocol_encode_aa(UINT64_C(6), UINT64_C(4095), UINT64_C(65535), UINT64_C(32767), &frame),
          "Prime maximum AA before A0 reuse");
    check(drive_protocol_encode_a0(UINT64_C(1), &frame), "A0 can reuse a previous seven-byte AA output");
    golden(&frame, 1U, 1U, a0, "A0 clears all old AA current bytes");
    check(drive_protocol_encode_aa(UINT64_C(6), UINT64_C(4095), UINT64_C(65535), UINT64_C(32767), &frame),
          "Prime maximum AA before A1 reuse");
    check(drive_protocol_encode_a1(UINT64_C(1), UINT64_C(291), UINT64_C(17767), &frame),
          "A1 can reuse a previous seven-byte AA output");
    golden(&frame, 1U, 5U, a1, "A1 clears old AA current bytes");
    check(drive_protocol_encode_aa(UINT64_C(6), UINT64_C(4095), UINT64_C(65535), UINT64_C(32767), &frame),
          "Prime maximum AA before BA reuse");
    check(drive_protocol_encode_ba(UINT64_C(1), UINT64_C(1), &frame), "BA can reuse a previous seven-byte AA output");
    golden(&frame, 1U, 2U, ba, "BA clears old AA position/speed/current bytes");
    check(drive_protocol_encode_aa(UINT64_C(6), UINT64_C(4095), UINT64_C(65535), UINT64_C(32767), &frame),
          "Prime maximum AA before minimum AA reuse");
    check(drive_protocol_encode_aa(UINT64_C(1), UINT64_C(0), UINT64_C(1), UINT64_C(1), &frame),
          "Minimum AA can reuse a maximum AA output");
    golden(&frame, 1U, 7U, minimum, "AA minimum clears all previous high bytes and unused tail");
    check(!drive_protocol_encode_aa(UINT64_C(1), UINT64_C(0), UINT64_C(1), UINT64_C(1), NULL),
          "AA NULL output fails safely");
}

int main(void)
{
    drive_protocol_frame frame;
    const uint8_t a0[8] = {0xa0U, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
    const uint8_t a1_minimum[8] = {0xa1U, 0U, 0U, 1U, 0U, 0U, 0U, 0U};
    const uint8_t a1_maximum[8] = {0xa1U, 0xffU, 0x0fU, 0xffU, 0xffU, 0U, 0U, 0U};
    const uint8_t a1_endian[8] = {0xa1U, 0x23U, 1U, 0x67U, 0x45U, 0U, 0U, 0U};
    const uint8_t a1_carry[8] = {0xa1U, 0U, 1U, 0U, 1U, 0U, 0U, 0U};
    const uint8_t ba_zero[8] = {0xbaU, 0U, 0U, 0U, 0U, 0U, 0U, 0U};
    const uint8_t ba_one[8] = {0xbaU, 1U, 0U, 0U, 0U, 0U, 0U, 0U};

    for (uint64_t node = UINT64_C(1); node <= UINT64_C(6); ++node) {
        poison(&frame);
        check(drive_protocol_encode_a0(node, &frame), "A0 accepts each fixed node");
        golden(&frame, (uint32_t)node, 1U, a0, "A0 has only opcode and zero unused bytes");
        poison(&frame);
        check(drive_protocol_encode_a1(node, UINT64_C(0), UINT64_C(1), &frame),
              "A1 accepts each fixed node at minimum domain values");
        golden(&frame, (uint32_t)node, 5U, a1_minimum, "A1 minimum golden bytes");
        poison(&frame);
        check(drive_protocol_encode_ba(node, DRIVE_PROTOCOL_BA_MODE_NO_RESISTANCE, &frame),
              "BA accepts explicit mode zero for each fixed node");
        golden(&frame, (uint32_t)node, 2U, ba_zero, "BA zero golden bytes and zero tail");
        poison(&frame);
        check(drive_protocol_encode_ba(node, DRIVE_PROTOCOL_BA_MODE_WITH_RESISTANCE, &frame),
              "BA accepts explicit mode one for each fixed node");
        golden(&frame, (uint32_t)node, 2U, ba_one, "BA one golden bytes and zero tail");
    }

    poison(&frame);
    check(drive_protocol_encode_a1(UINT64_C(6), UINT64_C(4095), UINT64_C(65535), &frame),
          "A1 protocol endpoints accepted without confusing them with safe limits");
    golden(&frame, 6U, 5U, a1_maximum, "A1 endpoint golden bytes");
    check(drive_protocol_encode_a1(UINT64_C(3), UINT64_C(291), UINT64_C(17767), &frame),
          "A1 mixed-byte vector accepted");
    golden(&frame, 3U, 5U, a1_endian, "A1 independently specified little-endian vector");
    check(drive_protocol_encode_a1(UINT64_C(2), UINT64_C(256), UINT64_C(256), &frame),
          "A1 byte-carry vector accepted");
    golden(&frame, 2U, 5U, a1_carry, "A1 position and speed cross byte boundary");

    const uint64_t invalid_nodes[] = {
        UINT64_C(0), UINT64_C(7), UINT64_C(254), UINT64_C(255), UINT64_C(256),
        UINT64_C(65537), UINT64_C(4294967297), UINT64_MAX
    };
    for (size_t i = 0U; i < sizeof invalid_nodes / sizeof invalid_nodes[0]; ++i) {
        poison(&frame);
        check(!drive_protocol_encode_a0(invalid_nodes[i], &frame), "A0 rejects invalid/wide node");
        check(zeroed(&frame), "A0 rejection clears whole output including padding");
        poison(&frame);
        check(!drive_protocol_encode_a1(invalid_nodes[i], UINT64_C(100), UINT64_C(10), &frame),
              "A1 rejects invalid/wide node before narrowing");
        check(zeroed(&frame), "A1 node rejection clears whole output");
        poison(&frame);
        check(!drive_protocol_encode_ba(invalid_nodes[i], UINT64_C(1), &frame),
              "BA rejects invalid/wide node before narrowing");
        check(zeroed(&frame), "BA node rejection clears whole output");
    }

    const uint64_t invalid_positions[] = {
        UINT64_C(4096), UINT64_C(65535), UINT64_C(65536),
        UINT64_C(4294967396), UINT64_MAX
    };
    for (size_t i = 0U; i < sizeof invalid_positions / sizeof invalid_positions[0]; ++i) {
        check(drive_protocol_encode_a1(UINT64_C(1), UINT64_C(291), UINT64_C(17767), &frame),
              "Prime valid A1 before rejecting a position");
        check(!drive_protocol_encode_a1(UINT64_C(1), invalid_positions[i], UINT64_C(10), &frame),
              "A1 rejects protocol overflow and wide values that would truncate valid");
        check(zeroed(&frame), "Invalid position cannot leave previous valid frame");
    }

    const uint64_t invalid_speeds[] = {
        UINT64_C(0), UINT64_C(65536), UINT64_C(65537),
        UINT64_C(4294967297), UINT64_MAX
    };
    for (size_t i = 0U; i < sizeof invalid_speeds / sizeof invalid_speeds[0]; ++i) {
        check(drive_protocol_encode_a1(UINT64_C(1), UINT64_C(291), UINT64_C(17767), &frame),
              "Prime valid A1 before rejecting a speed");
        check(!drive_protocol_encode_a1(UINT64_C(1), UINT64_C(100), invalid_speeds[i], &frame),
              "A1 rejects zero/overflow/wide speed instead of omitting or truncating");
        check(zeroed(&frame), "Invalid speed cannot leave previous valid frame");
    }

    const uint64_t invalid_modes[] = {
        UINT64_C(2), UINT64_C(255), UINT64_C(256), UINT64_C(257),
        UINT64_C(65536), UINT64_C(4294967297), UINT64_MAX
    };
    for (size_t i = 0U; i < sizeof invalid_modes / sizeof invalid_modes[0]; ++i) {
        check(drive_protocol_encode_ba(UINT64_C(1), UINT64_C(1), &frame),
              "Prime valid BA before rejecting a mode");
        check(!drive_protocol_encode_ba(UINT64_C(1), invalid_modes[i], &frame),
              "BA rejects invalid/wide mode instead of wrapping to zero/one");
        check(zeroed(&frame), "Invalid BA mode cannot leave previous valid frame");
    }

    /* Reusing the same output across formats must not leak old payload bytes. */
    check(drive_protocol_encode_a1(UINT64_C(6), UINT64_C(4095), UINT64_C(65535), &frame),
          "Prime maximum A1 before A0 reuse");
    check(drive_protocol_encode_a0(UINT64_C(2), &frame), "A0 reuses previous A1 output");
    golden(&frame, 2U, 1U, a0, "A0 clears previous longer payload");
    check(drive_protocol_encode_a1(UINT64_C(6), UINT64_C(4095), UINT64_C(65535), &frame),
          "Prime maximum A1 before BA reuse");
    check(drive_protocol_encode_ba(UINT64_C(4), UINT64_C(0), &frame), "BA reuses previous A1 output");
    golden(&frame, 4U, 2U, ba_zero, "BA clears previous longer payload");
    check(!drive_protocol_encode_a0(UINT64_C(1), NULL), "A0 NULL output fails safely");
    check(!drive_protocol_encode_a1(UINT64_C(1), UINT64_C(0), UINT64_C(1), NULL),
          "A1 NULL output fails safely");
    check(!drive_protocol_encode_ba(UINT64_C(1), UINT64_C(0), NULL), "BA NULL output fails safely");

    /* The original A0/A1/BA assertions above remain unchanged. */
    aa_tests();

    printf("%s %u checks; %u failures; OFFLINE ONLY; no hardware I/O or transmission; "
           "protocol validity is not motion permission or actual stopping.\n",
           failures ? "FAIL" : "PASS", checks, failures);
    return failures ? 1 : 0;
}
