/*
 * bl_state_test.cpp -- the copy-state journal (bootloader/core/bl_state.c)
 * over the mock flash: append, read-latest, torn-record skipping, the full
 * sector, compaction, and the room guarantee.
 *
 * MUTATIONS (seen red 2026-09-06): recordValid without the seal check fails
 * "record torn before the seal is skipped" (and that case was ALSO red
 * against the first draft of bl_state.c, which had no seal -- the test
 * found a real hole); recordValid without the complement check fails "torn
 * record is skipped"; blStateEnsureRoom without the re-append fails
 * "compaction preserves TRIAL".
 */
#include <cstdio>
#include "bl_mock_port.h"

extern "C" {
#include "bl_state.h"
}

static int failures = 0;
static void check(bool ok, const char *label) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) failures++;
}

static uint8_t *rec(uint32_t idx) {
    return mock::flash + (ELS_STATE_SECTOR_BASE - ELS_FLASH_BASE) + idx * BL_STATE_RECORD_BYTES;
}

int main() {
    mock::reset();
    check(blStateRead() == ELS_BL_STATE_IDLE, "erased journal reads IDLE");
    check(blStateFree() == BL_STATE_RECORDS, "erased journal has every record free");

    check(blStateWrite(ELS_BL_STATE_BACKUP) == 0, "append BACKUP");
    check(blStateRead() == ELS_BL_STATE_BACKUP, "reads BACKUP");
    check(blStateWrite(ELS_BL_STATE_COPY) == 0, "append COPY");
    check(blStateWrite(ELS_BL_STATE_TRIAL) == 0, "append TRIAL");
    check(blStateRead() == ELS_BL_STATE_TRIAL, "latest record wins");
    check(blStateFree() == BL_STATE_RECORDS - 3, "three records used");
    check(mock::eraseCount == 0, "no erase for ordinary appends");

    /* A torn record (power lost mid-append) after TRIAL: magic landed,
     * complement did not. Must be skipped, and the next append must land
     * AFTER it, not on top of it. */
    {
        uint32_t magic = BL_STATE_MAGIC, st = ELS_BL_STATE_IDLE;
        memcpy(rec(3), &magic, 4);
        memcpy(rec(3) + 4, &st, 4);
        /* stateInv, reserved left 0xFF: complement check fails */
    }
    check(blStateRead() == ELS_BL_STATE_TRIAL, "torn record is skipped; TRIAL still current");
    /* The nastier tear: magic, state AND complement landed, seal did not.
     * Before the seal existed, state 0 with a blank complement decoded as a
     * valid IDLE (0 ^ 0xFFFFFFFF == 0xFFFFFFFF) -- found by this test. */
    {
        uint32_t st = ELS_BL_STATE_IDLE, inv = ~st;
        memcpy(rec(3) + 8, &inv, 4);
    }
    check(blStateRead() == ELS_BL_STATE_TRIAL, "record torn before the seal is skipped even with a consistent complement");
    check(blStateFree() == BL_STATE_RECORDS - 4, "torn record consumes its slot");
    check(blStateWrite(ELS_BL_STATE_IDLE) == 0, "append after a torn record");
    check(blStateRead() == ELS_BL_STATE_IDLE, "...and it is the current state");
    check(memcmp(rec(4), rec(5), 4) != 0 && rec(4)[0] != 0xFF, "...and it landed in slot 4, not on the torn slot");

    /* A SEALED record whose state word lost a bit after the fact (flash
     * disturbance, not a tear): the seal is present, so only the complement
     * check can refuse it. Without that check the corrupted value would be
     * read as a state. */
    {
        uint32_t st = ELS_BL_STATE_TRIAL | 0x10u, inv = ~(uint32_t)ELS_BL_STATE_TRIAL, seal = BL_STATE_SEAL, magic = BL_STATE_MAGIC;
        memcpy(rec(5), &magic, 4); memcpy(rec(5) + 4, &st, 4); memcpy(rec(5) + 8, &inv, 4); memcpy(rec(5) + 12, &seal, 4);
    }
    check(blStateRead() == ELS_BL_STATE_IDLE, "sealed record with state != ~inv (bit flip) is skipped");

    /* A record with a foreign magic (garbage from a torn erase) is skipped. */
    {
        uint32_t junk[4] = { 0xDEADBEEFu, 3u, ~3u, BL_STATE_SEAL };
        memcpy(rec(6), junk, 16);
    }
    check(blStateRead() == ELS_BL_STATE_IDLE, "foreign-magic record is skipped");

    /* Fill the sector, then one more: compaction erases and re-appends. */
    mock::reset();
    for (uint32_t i = 0; i < BL_STATE_RECORDS; i++) {
        if (blStateWrite((i & 1) ? ELS_BL_STATE_TRIAL : ELS_BL_STATE_IDLE) != 0) { failures++; break; }
    }
    check(blStateFree() == 0, "sector full after 1024 appends");
    check(blStateRead() == ELS_BL_STATE_TRIAL, "last of 1024 is current");
    unsigned erasesBefore = mock::eraseCount;
    check(blStateWrite(ELS_BL_STATE_REVERTED) == 0, "append into a full sector succeeds");
    check(mock::eraseCount == erasesBefore + 1, "...by erasing once");
    check(blStateRead() == ELS_BL_STATE_REVERTED, "...and the new state is current");
    check(blStateFree() == BL_STATE_RECORDS - 1, "...with the sector otherwise empty");

    /* EnsureRoom: no-op with room, compacts without losing the state when
     * there is not. */
    mock::reset();
    blStateWrite(ELS_BL_STATE_TRIAL);
    erasesBefore = mock::eraseCount;
    check(blStateEnsureRoom(BL_STATE_APPLY_RECORDS) == 0 && mock::eraseCount == erasesBefore,
          "EnsureRoom with room is a no-op");
    for (uint32_t i = 1; i < BL_STATE_RECORDS - 2; i++) blStateWrite(ELS_BL_STATE_TRIAL);
    check(blStateFree() == 2, "two records left");
    check(blStateEnsureRoom(BL_STATE_APPLY_RECORDS) == 0, "EnsureRoom compacts");
    check(mock::eraseCount == erasesBefore + 1, "...with one erase");
    check(blStateRead() == ELS_BL_STATE_TRIAL, "compaction preserves TRIAL");
    check(blStateFree() == BL_STATE_RECORDS - 1, "...and frees the sector");

    /* Compacting an IDLE journal leaves it erased: erased IS idle. */
    mock::reset();
    for (uint32_t i = 0; i < BL_STATE_RECORDS - 1; i++) blStateWrite(ELS_BL_STATE_IDLE);
    check(blStateEnsureRoom(BL_STATE_APPLY_RECORDS) == 0 && blStateFree() == BL_STATE_RECORDS,
          "compacting an IDLE journal writes nothing back");

    /* The record does not straddle: 16 bytes, 4 words, x4-aligned base. */
    check((ELS_STATE_SECTOR_BASE % 4) == 0 && BL_STATE_RECORDS * BL_STATE_RECORD_BYTES == ELS_STATE_SECTOR_SIZE,
          "journal geometry fills sector 1 exactly");

    printf("%s\n", failures ? "FAILURES" : "all passed");
    return failures ? 1 : 0;
}
