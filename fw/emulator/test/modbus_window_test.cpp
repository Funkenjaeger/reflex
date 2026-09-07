/*
 * modbus_window_test.cpp -- Core/Inc/modbus_window.h, the address resolver
 * both stages share. Pins the contract the decision record relies on:
 *
 *   - the main map and each window are served only for ranges WHOLLY inside
 *     one of them; the gap between them and any straddle is exception 2;
 *   - a write to a read-only window is exception 2, a read is served;
 *   - the app's view (main map + identity window, no bootloader window)
 *     answers exception 2 at ELS_BL_BASE -- "this is the app";
 *   - the bootloader's view (no main map) answers exception 2 at 0.
 *
 * MUTATIONS (seen red 2026-09-06): `end <= wend` -> `end < wend` fails the
 * "last register of the window" case; dropping the readOnly test fails
 * "write to the identity window".
 */
#include <cstdio>
#include <cstdint>

extern "C" {
#include "modbus_window.h"
#include "els_identity.h"
}

/* The app's protocolVersion as the identity window would carry it; the
 * resolver does not care about the value, only the layout. */
#define ELS_PROTOCOL_VERSION_TEST 8

static int failures = 0;
static void check(bool ok, const char *label) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) failures++;
}

int main() {
    uint16_t mainRegs[232];          /* the app's ~464-byte struct */
    uint16_t idRegs[ELS_ID_SIZE] = ELS_ID_WINDOW_INIT(ELS_ID_STAGE_APP, ELS_PROTOCOL_VERSION_TEST);
    uint16_t blRegs[ELS_BL_SIZE];
    mbWindow_t appWins[1] = { { ELS_ID_BASE, ELS_ID_SIZE, idRegs, 1 } };
    mbWindow_t blWins[2]  = { { ELS_ID_BASE, ELS_ID_SIZE, idRegs, 1 },
                              { ELS_BL_BASE, ELS_BL_SIZE, blRegs, 0 } };
    uint16_t *p = nullptr;
    const unsigned mainSize = 232;

    /* --- the app's view --- */
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, 0, 1, 0, &p) == 0 && p == &mainRegs[0],
          "app: register 0 is the struct");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, mainSize - 1, 1, 1, &p) == 0 && p == &mainRegs[mainSize - 1],
          "app: last struct register, writable");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, mainSize, 1, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "app: one past the struct is exception 2 (the old > check let this through)");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, mainSize - 1, 2, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "app: a read straddling the struct end is exception 2");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, 0, 125, 0, &p) == 0,
          "app: a 125-register read from 0 is served");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, ELS_ID_BASE, ELS_ID_SIZE, 0, &p) == 0 && p == &idRegs[0],
          "app: the whole identity window reads");
    check(p[ELS_ID_MAGIC_OFF] == ELS_ID_MAGIC && p[ELS_ID_STAGE_OFF] == ELS_ID_STAGE_APP,
          "app: identity window carries magic and stage 2");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, ELS_ID_BASE + ELS_ID_SIZE - 1, 1, 0, &p) == 0,
          "app: last register of the window is served");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, ELS_ID_BASE + ELS_ID_SIZE, 1, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "app: one past the window is exception 2");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, ELS_ID_BASE - 1, 2, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "app: straddling into the window is exception 2");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, ELS_ID_BASE, 1, 1, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "app: write to the identity window is exception 2 (read-only)");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, 1000, 8, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "app: the gap between struct and window is exception 2");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, ELS_BL_BASE, 4, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "app: the bootloader window is exception 2 -- the 'this is the app' signal");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, 0, 0, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "count 0 is refused");
    check(mbResolveRange(mainRegs, mainSize, appWins, 1, 65535, 125, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "addr + count past 65535 does not wrap into the struct");

    /* --- the bootloader's view --- */
    check(mbResolveRange(nullptr, 0, blWins, 2, 0, 1, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "bootloader: register 0 is exception 2 (no main map)");
    check(mbResolveRange(nullptr, 0, blWins, 2, ELS_BL_BASE, ELS_BL_SIZE, 1, &p) == 0 && p == &blRegs[0],
          "bootloader: the whole control window is writable");
    check(mbResolveRange(nullptr, 0, blWins, 2, ELS_BL_BASE + ELS_BL_COMMAND, 113, 1, &p) == 0 && p == &blRegs[ELS_BL_COMMAND],
          "bootloader: one FC16 from blCommand through the end of blData (113 regs) is served");
    check(mbResolveRange(nullptr, 0, blWins, 2, ELS_BL_BASE + ELS_BL_SIZE, 1, 0, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "bootloader: one past blData is exception 2");
    check(mbResolveRange(nullptr, 0, blWins, 2, ELS_ID_BASE, ELS_ID_SIZE, 1, &p) == MB_EXC_ILLEGAL_ADDRESS,
          "bootloader: identity window is read-only here too");

    /* --- the layout facts the record states --- */
    check(ELS_BL_SEQ < ELS_BL_RESULT, "blSeq sits at a LOWER address than blResult (seq-before-payload)");
    check(ELS_BL_SEQ < ELS_BL_DATA && ELS_BL_SEQ < ELS_BL_COPY_STATE && ELS_BL_SEQ < ELS_BL_RUN_VALID,
          "blSeq is below every other outcome register");
    check(ELS_BL_BASE > ELS_ID_BASE + ELS_ID_SIZE, "control window is above the identity window");
    check(ELS_ID_BASE >= 4 * mainSize, "identity base is well clear of the struct (>= 4x today's size)");
    check(ELS_BL_SIZE == 116 && ELS_BL_DATA == 16, "control window: 16 head registers + 100 data");

    printf("%s\n", failures ? "FAILURES" : "all passed");
    return failures ? 1 : 0;
}
