/*
 * bl_mock_port.h -- the bootloader's bl_port.h over a byte array, for the
 * native tests. Include ONCE per test binary.
 *
 * Flash semantics that matter for the state machine are modelled: a program
 * can only clear bits (1 -> 0), an erase sets the sector to 0xFF, and every
 * program WORD and every erase is one "operation" the POWER-LOSS INJECTOR
 * can interrupt. When the countdown reaches zero the operation is applied
 * PARTIALLY (an erase leaves the sector half erased and half garbage, a
 * program leaves a random subset of the word's bits cleared) and control
 * longjmps back to the test, which then "reboots" by calling blCoreBoot()
 * on the same flash. That is what lets a test walk every operation boundary
 * of an apply and assert the torn-image invariant at each one.
 *
 * CRC is the software port from bootloader/core/bl_crc32_sw.c, linked
 * separately.
 */
#ifndef BL_MOCK_PORT_H
#define BL_MOCK_PORT_H

#include <cstdint>
#include <cstring>
#include <csetjmp>
#include <cstdlib>

extern "C" {
#include "els_identity.h"
#include "bl_port.h"
}

namespace mock {

static uint8_t  flash[512 * 1024];
static uint32_t bkp[4];
static long     powerLossIn = -1;      /* ops until the lights go out; -1 = never */
static jmp_buf  powerLossJmp;
static unsigned opsCount = 0;          /* flash ops performed since reset() */
static unsigned eraseCount = 0;
static unsigned wdKicks = 0;
static unsigned rngState = 12345u;
/* A STUCK BIT: a word address whose bit 0 cannot be cleared. Programs to it
 * report success (the controller does not know), which is the failure the
 * readback verify exists to catch. 0 = no stuck bit. */
static uint32_t stuckAddr = 0;

static unsigned rnd() {
    rngState = rngState * 1103515245u + 12345u;
    return (rngState >> 8) & 0xFFFFFFu;
}

static void reset() {
    memset(flash, 0xFF, sizeof flash);
    memset(bkp, 0, sizeof bkp);
    powerLossIn = -1;
    opsCount = 0;
    eraseCount = 0;
    wdKicks = 0;
    stuckAddr = 0;
}

static uint32_t sectorSize(uint32_t base) {
    if (base < 0x08010000u) return 0x4000u;
    if (base < 0x08020000u) return 0x10000u;
    return 0x20000u;
}

static uint32_t sectorBaseOf(uint32_t addr) {
    uint32_t off = addr - ELS_FLASH_BASE;
    if (off < 0x10000u) return ELS_FLASH_BASE + (off & ~0x3FFFu);
    if (off < 0x20000u) return 0x08010000u;
    return ELS_FLASH_BASE + (off & ~0x1FFFFu);
}

/* Returns true when this op is the one that loses power. */
static bool tick() {
    opsCount++;
    if (powerLossIn < 0) return false;
    if (powerLossIn == 0) return true;
    powerLossIn--;
    return false;
}

} // namespace mock

extern "C" {

int blPortErase(uint32_t sectorBase) {
    if (sectorBase < ELS_FLASH_BASE || sectorBase >= ELS_FLASH_BASE + sizeof mock::flash) return -1;
    if (mock::sectorBaseOf(sectorBase) != sectorBase) return -1;
    if (sectorBase == ELS_BL_SECTOR_BASE) return -1;
    uint8_t *p = mock::flash + (sectorBase - ELS_FLASH_BASE);
    uint32_t n = mock::sectorSize(sectorBase);
    mock::eraseCount++;
    if (mock::tick()) {
        /* torn erase: first half erased, second half scribbled */
        memset(p, 0xFF, n / 2);
        for (uint32_t i = n / 2; i < n; i += 4) p[i] &= (uint8_t)mock::rnd();
        longjmp(mock::powerLossJmp, 1);
    }
    memset(p, 0xFF, n);
    return 0;
}

int blPortProgram(uint32_t addr, const uint32_t *words, uint32_t nwords) {
    if ((addr & 3u) != 0u) return -1;
    if (addr < ELS_STATE_SECTOR_BASE) return -1;
    if (addr + nwords * 4u > ELS_FLASH_BASE + sizeof mock::flash) return -1;
    for (uint32_t i = 0; i < nwords; i++) {
        uint8_t *p = mock::flash + (addr + 4u * i - ELS_FLASH_BASE);
        uint32_t old; memcpy(&old, p, 4);
        uint32_t nw = old & words[i];          /* 1 -> 0 only */
        if (mock::stuckAddr != 0 && addr + 4u * i == mock::stuckAddr) nw |= 1u;   /* silently */
        if (mock::tick()) {
            uint32_t partial = old & (words[i] | (uint32_t)mock::rnd() | ((uint32_t)mock::rnd() << 8));
            memcpy(p, &partial, 4);
            longjmp(mock::powerLossJmp, 1);
        }
        memcpy(p, &nw, 4);
    }
    return 0;
}

const uint8_t *blPortMap(uint32_t addr) {
    return mock::flash + (addr - ELS_FLASH_BASE);
}

uint32_t blPortBkpRead(uint32_t idx) { return mock::bkp[idx]; }
void     blPortBkpWrite(uint32_t idx, uint32_t v) { mock::bkp[idx] = v; }
void     blPortWatchdogKick(void) { mock::wdKicks++; }

} // extern "C"

/* ---- image builder ------------------------------------------------------ */

namespace mock {

/* Build a syntactically valid image of `length` bytes at `base`, filled
 * from `seed`, with the header patched exactly as scripts/reflex_image.py
 * does. Returns the header CRC. */
/* `vectorBase` is the slot the image is LINKED for -- its reset vector
 * points there. A real image is always linked for RUN; a test that builds
 * an image directly in another slot to validate it passes that slot. */
static inline uint32_t writeImage(uint32_t base, uint32_t length, uint32_t seed, uint32_t buildRev = 0x1234567u,
                           bool validVectors = true, uint32_t vectorBase = 0) {
    uint8_t *p = flash + (base - ELS_FLASH_BASE);
    if (vectorBase == 0) vectorBase = base;
    rngState = seed;
    for (uint32_t i = 0; i < length; i++) p[i] = (uint8_t)rnd();
    uint32_t msp = validVectors ? 0x20020000u : 0x08000000u;
    uint32_t rst = validVectors ? (vectorBase + 0x401u) : (vectorBase + 0x400u);
    memcpy(p + 0, &msp, 4);
    memcpy(p + 4, &rst, 4);
    elsImageHeader_t h;
    memset(&h, 0, sizeof h);
    h.magic = ELS_IMAGE_MAGIC;
    h.headerVersion = ELS_IMAGE_HEADER_VERSION;
    h.flags = 0;
    h.imageLength = length;
    h.crc32 = 0;
    h.buildRev = buildRev;
    memcpy(p + ELS_IMAGE_HEADER_OFFSET, &h, sizeof h);
    /* CRC with the crc word as 0, via the same port the bootloader uses. */
    blPortCrcReset();
    for (uint32_t i = 0; i < length / 4; i++) {
        uint32_t w; memcpy(&w, p + 4 * i, 4);
        if (i == (ELS_IMAGE_HEADER_OFFSET + 12) / 4) w = 0;
        blPortCrcFeed(w);
    }
    h.crc32 = blPortCrcValue();
    memcpy(p + ELS_IMAGE_HEADER_OFFSET + 12, &h.crc32, 4);
    return h.crc32;
}

static inline uint32_t slotCrc(uint32_t base) {
    uint32_t c; memcpy(&c, flash + (base - ELS_FLASH_BASE) + ELS_IMAGE_HEADER_OFFSET + 12, 4);
    return c;
}

} // namespace mock

#endif /* BL_MOCK_PORT_H */
