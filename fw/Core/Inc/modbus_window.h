/*
 * modbus_window.h -- one answer to "which memory backs holding register N".
 *
 * The app's map is rampsSharedData_t cast wholesale into registers 0..N.
 * The identity window (els_identity.h) lives OUTSIDE that struct at a fixed
 * base, and the bootloader's control window likewise. This resolver is the
 * single place both stages decide whether a request's address range is
 * served, and from where -- the app's Modbus.c and the bootloader's own
 * slave call the same function, so they cannot disagree about a gap.
 *
 * Header-only and dependency-free on purpose: it is compiled into a
 * FreeRTOS firmware, a bare-metal bootloader, and native tests.
 *
 * A range is served only if it lies WHOLLY inside one region. A range that
 * straddles the main map and a window, or falls in the gap between them,
 * is an illegal data address (exception 2), which is the signal the decision
 * record relies on: a client probing the bootloader window on the app gets
 * exception 2, not zeros.
 */
#ifndef MODBUS_WINDOW_H
#define MODBUS_WINDOW_H

#include <stdint.h>

#define MB_EXC_ILLEGAL_ADDRESS 2u

typedef struct {
  uint16_t  base;      /* first register address of the window */
  uint16_t  size;      /* registers in the window */
  uint16_t *regs;      /* backing store, size entries */
  uint8_t   readOnly;  /* writes answer exception 2 */
} mbWindow_t;

/* Resolve addr..addr+count-1. On success returns 0 and stores the backing
 * pointer for `addr` in *out (contiguous for `count` registers). Otherwise
 * returns MB_EXC_ILLEGAL_ADDRESS and leaves *out untouched. `mainRegs` may
 * be NULL with mainSize 0 (the bootloader has no main map). */
static inline uint8_t mbResolveRange(uint16_t *mainRegs, uint16_t mainSize,
                                     const mbWindow_t *wins, unsigned nwins,
                                     uint16_t addr, uint16_t count,
                                     int forWrite, uint16_t **out)
{
  uint32_t end = (uint32_t)addr + (uint32_t)count;   /* exclusive; no uint16 wrap */
  if (count == 0u) return MB_EXC_ILLEGAL_ADDRESS;
  if (mainRegs != (uint16_t *)0 && end <= (uint32_t)mainSize) {
    *out = &mainRegs[addr];
    return 0u;
  }
  for (unsigned i = 0; i < nwins; i++) {
    const mbWindow_t *w = &wins[i];
    uint32_t wend = (uint32_t)w->base + (uint32_t)w->size;
    if (addr >= w->base && end <= wend) {
      if (forWrite && w->readOnly) return MB_EXC_ILLEGAL_ADDRESS;
      *out = &w->regs[addr - w->base];
      return 0u;
    }
  }
  return MB_EXC_ILLEGAL_ADDRESS;
}

#endif /* MODBUS_WINDOW_H */
