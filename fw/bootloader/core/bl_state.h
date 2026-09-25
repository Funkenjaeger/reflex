/*
 * bl_state.h -- the copy-state JOURNAL in flash sector 1.
 *
 * WHY FLASH AND NOT A BACKUP REGISTER: the backup domain on this board dies
 * with VDD (no VBAT). A power loss mid-copy must be recoverable on the next
 * boot, so the marker that says "RUN may be torn, STAGING is good, resume
 * or revert" has to survive exactly that event.
 *
 * WHY A JOURNAL AND NOT A SLOT: flash erases are the hazard (a 16 KB erase
 * is itself a multi-ms window in which the marker does not exist), so the
 * state is APPENDED as 16-byte records into an erased sector and the sector
 * is only erased -- compacted -- while no copy is in flight and there are
 * fewer than the records one apply needs. A record is {MAGIC, state,
 * ~state, SEAL}; a torn record fails the complement check and is skipped; the
 * current state is the last valid record before the first blank one, and
 * no record at all means IDLE. The fourth word is a SEAL written last, so
 * a torn record is a prefix without it. 1024 records per erase, three or four per
 * update: the sector outlives the board.
 */
#ifndef BL_STATE_H
#define BL_STATE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BL_STATE_RECORD_BYTES 16u
#define BL_STATE_RECORDS      (0x4000u / BL_STATE_RECORD_BYTES)   /* 1024 */
#define BL_STATE_MAGIC        0x4C425354u                          /* "TSBL" */
#define BL_STATE_SEAL         0xB3BDACABu                          /* ~MAGIC, last word */
/* Records one apply can append (BACKUP, COPY, TRIAL/REVERT, REVERTED/IDLE)
 * plus one spare, so compaction never has to happen mid-sequence. */
#define BL_STATE_APPLY_RECORDS 5u

/* Current state, ELS_BL_STATE_*; IDLE when the journal is empty/erased. */
uint8_t blStateRead(void);

/* Append a record. Returns 0, or ELS_BL_ERR_JOURNAL if it did not land
 * (program error, or a readback that does not decode to `state`). */
int blStateWrite(uint8_t state);

/* Number of blank records remaining. */
uint32_t blStateFree(void);

/* Make room for at least `records` appends: no-op if there is room, else
 * erase the sector and re-append the current state. ONLY call while no copy
 * is in flight (bl_core does, at the start of an apply). 0 or ELS_BL_ERR_JOURNAL. */
int blStateEnsureRoom(uint32_t records);

#ifdef __cplusplus
}
#endif

#endif /* BL_STATE_H */
