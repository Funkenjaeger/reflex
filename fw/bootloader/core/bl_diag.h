/*
 * bl_diag.h -- the one way a receiver diagnostic counter moves.
 *
 * The counters are the blDiag registers (els_identity.h ELS_BL_DIAG,
 * ELS_BL_DG_*), stored in blCore_t.regs[ELS_BL_DIAG ..] and served to the
 * host as a read-only window. Three layers bump them -- the ring (frames
 * taken, overflow drops), the Modbus parser (CRC and other rejects) and the
 * UART driver (error flags, DMA restarts) -- and each is handed a pointer to
 * the first counter rather than the whole core, so the ring and the driver
 * stay ignorant of blCore_t.
 *
 * SATURATING, never wrapping: a counter that wrapped would read as "fewer
 * errors than last time" to a bench comparing two reads, which is exactly
 * the wrong conclusion. 0xFFFF means "at least that many".
 *
 * `diag` may be NULL, in which case nothing is counted; that keeps the
 * portable functions callable from tests that do not care.
 */
#ifndef BL_DIAG_H
#define BL_DIAG_H

#include <stdint.h>
#include "els_identity.h"

static inline void blDiagBump(uint16_t *diag, unsigned idx)
{
  if (diag != (uint16_t *)0 && idx < ELS_BL_DIAG_REGS && diag[idx] != 0xFFFFu) diag[idx]++;
}

#endif /* BL_DIAG_H */
