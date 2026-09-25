/*
 * bl_crc32_test.cpp -- the bootloader's software CRC-32 (STM32 CRC-unit
 * variant) against vectors computed INDEPENDENTLY in Python
 * (scripts/reflex_image.py, table-driven, itself checked against its own
 * bit-serial twin and against the published CRC-unit result for one zero
 * word, 0xC704DD7B).
 *
 * Vectors generated 2026-09-06 with:
 *   python3 -c "import reflex_image as r; print(hex(r.stm32_crc32_words([...])))"
 *
 * MUTATION (seen red 2026-09-06): poly 0x04C11DB7 -> 0x04C11DB5 fails every
 * vector but the all-ones word (which is 0 for any polynomial, since
 * init ^ word == 0 shifts to 0); init 0xFFFFFFFF -> 0 fails all five.
 */
#include <cstdio>
#include <cstdint>
#include <cstring>

extern "C" {
#include "bl_port.h"
}

static int failures = 0;

static void check(bool ok, const char *label) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) failures++;
}

static uint32_t crcOf(const uint32_t *w, unsigned n) {
    blPortCrcReset();
    for (unsigned i = 0; i < n; i++) blPortCrcFeed(w[i]);
    return blPortCrcValue();
}

int main() {
    const uint32_t zero[] = { 0x00000000u };
    const uint32_t ones[] = { 0xFFFFFFFFu };
    const uint32_t seq4[] = { 1u, 2u, 3u, 4u };
    const uint32_t abcd[] = { 0x64636261u, 0x68676665u };   /* "abcdefgh" little-endian */
    const uint32_t r8[]   = { 0x57124242u, 0xb1fee08fu, 0x59a54a7bu, 0x98289fcdu,
                              0x7f26144bu, 0x9474031bu, 0xcc011cddu, 0x74c9df6au };

    check(crcOf(zero, 1) == 0xC704DD7Bu, "one zero word -> 0xC704DD7B (published STM32 CRC-unit value)");
    check(crcOf(ones, 1) == 0x00000000u, "one all-ones word -> 0 (init ^ word == 0)");
    check(crcOf(seq4, 4) == 0x955AE3FDu, "1,2,3,4 -> 0x955AE3FD (Python vector)");
    check(crcOf(abcd, 2) == 0x18C4859Cu, "'abcdefgh' -> 0x18C4859C (Python vector)");
    check(crcOf(r8, 8)   == 0x6D0E925Cu, "8 random words -> 0x6D0E925C (Python vector)");

    /* Reset really resets: feeding the same words twice without a reset must
     * not reproduce the single-pass value. */
    blPortCrcReset();
    for (unsigned i = 0; i < 4; i++) blPortCrcFeed(seq4[i]);
    uint32_t once = blPortCrcValue();
    for (unsigned i = 0; i < 4; i++) blPortCrcFeed(seq4[i]);
    check(blPortCrcValue() != once, "state carries across feeds until reset");
    check(crcOf(seq4, 4) == once, "reset restores the single-pass value");

    printf("%s\n", failures ? "FAILURES" : "all passed");
    return failures ? 1 : 0;
}
