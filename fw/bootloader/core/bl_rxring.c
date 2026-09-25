/*
 * bl_rxring.c -- see bl_rxring.h.
 */
#include "bl_rxring.h"
#include "bl_diag.h"

#define RING_MASK (BL_RX_RING_SIZE - 1u)

uint32_t blRxRingHead(uint32_t ndtr)
{
  if (ndtr == 0u || ndtr > BL_RX_RING_SIZE) return 0u;
  return BL_RX_RING_SIZE - ndtr;
}

uint32_t blRxRingTake(const volatile uint8_t *ring, uint32_t ndtr,
                      uint32_t *tail, uint8_t *frame, uint32_t frameMax,
                      uint16_t *diag)
{
  uint32_t head  = blRxRingHead(ndtr);
  uint32_t t     = *tail & RING_MASK;
  uint32_t avail = (head + BL_RX_RING_SIZE - t) & RING_MASK;

  /* Advance before the length test: an oversized run is dropped, not kept. */
  *tail = head;

  if (avail == 0u) return 0u;
  if (avail > frameMax) {
    blDiagBump(diag, ELS_BL_DG_OVERFLOW_DROPS);
    return 0u;
  }

  for (uint32_t i = 0; i < avail; i++) {
    frame[i] = ring[(t + i) & RING_MASK];
  }
  blDiagBump(diag, ELS_BL_DG_FRAMES_TAKEN);
  return avail;
}
