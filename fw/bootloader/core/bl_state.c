/*
 * bl_state.c -- see bl_state.h.
 */
#include <string.h>
#include "bl_state.h"
#include "bl_port.h"
#include "els_identity.h"

/* Four words, programmed in ascending order, so a record interrupted by a
 * power loss is a PREFIX: the seal in the last word is only ever present on
 * a record whose first three words landed. Without the seal, a record torn
 * after `state` and before `stateInv` (state 0, stateInv still 0xFFFFFFFF)
 * would pass the complement check by arithmetic accident -- bl_state_test
 * caught exactly that on 2026-09-06. */
typedef struct {
  uint32_t magic;      /* BL_STATE_MAGIC */
  uint32_t state;      /* ELS_BL_STATE_* */
  uint32_t stateInv;   /* ~state */
  uint32_t seal;       /* BL_STATE_SEAL, written last */
} blStateRecord_t;

static void readRecord(uint32_t idx, blStateRecord_t *r)
{
  memcpy(r, blPortMap(ELS_STATE_SECTOR_BASE + idx * BL_STATE_RECORD_BYTES), sizeof *r);
}

static int recordBlank(const blStateRecord_t *r)
{
  return r->magic == 0xFFFFFFFFu && r->state == 0xFFFFFFFFu &&
         r->stateInv == 0xFFFFFFFFu && r->seal == 0xFFFFFFFFu;
}

static int recordValid(const blStateRecord_t *r)
{
  return r->magic == BL_STATE_MAGIC && r->seal == BL_STATE_SEAL &&
         (r->state ^ r->stateInv) == 0xFFFFFFFFu && r->state <= 0xFFu;
}

/* Scan: index of the first blank record (== BL_STATE_RECORDS if full) and
 * the state of the last valid record before it. */
static uint32_t scan(uint8_t *stateOut)
{
  uint8_t state = ELS_BL_STATE_IDLE;
  uint32_t i;
  for (i = 0; i < BL_STATE_RECORDS; i++) {
    blStateRecord_t r;
    readRecord(i, &r);
    if (recordBlank(&r)) break;
    if (recordValid(&r)) state = (uint8_t)r.state;
    /* a torn or foreign record is skipped, not trusted */
  }
  if (stateOut) *stateOut = state;
  return i;
}

uint8_t blStateRead(void)
{
  uint8_t s;
  (void)scan(&s);
  return s;
}

uint32_t blStateFree(void)
{
  return BL_STATE_RECORDS - scan((uint8_t *)0);
}

static int append(uint32_t idx, uint8_t state)
{
  blStateRecord_t r, back;
  uint32_t words[4];
  r.magic = BL_STATE_MAGIC;
  r.state = state;
  r.stateInv = ~(uint32_t)state;
  r.seal = BL_STATE_SEAL;
  memcpy(words, &r, sizeof words);
  if (blPortProgram(ELS_STATE_SECTOR_BASE + idx * BL_STATE_RECORD_BYTES, words, 4u) != 0)
    return (int)ELS_BL_ERR_JOURNAL;
  /* Gate on the signal: the record must read back as itself. */
  readRecord(idx, &back);
  if (!recordValid(&back) || back.state != state) return (int)ELS_BL_ERR_JOURNAL;
  return 0;
}

int blStateWrite(uint8_t state)
{
  uint32_t idx = scan((uint8_t *)0);
  if (idx >= BL_STATE_RECORDS) {
    /* Full. Compact: the window between the erase and the re-append reads
     * as IDLE, which is only safe while no copy is in flight -- bl_core
     * guarantees that by calling blStateEnsureRoom before an apply. */
    if (blPortErase(ELS_STATE_SECTOR_BASE) != 0) return (int)ELS_BL_ERR_JOURNAL;
    idx = 0;
  }
  return append(idx, state);
}

int blStateEnsureRoom(uint32_t records)
{
  uint8_t current;
  uint32_t used = scan(&current);
  if (BL_STATE_RECORDS - used >= records) return 0;
  if (blPortErase(ELS_STATE_SECTOR_BASE) != 0) return (int)ELS_BL_ERR_JOURNAL;
  if (current == ELS_BL_STATE_IDLE) return 0;      /* erased == IDLE already */
  return append(0u, current);
}
