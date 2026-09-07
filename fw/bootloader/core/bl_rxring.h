/*
 * bl_rxring.h -- turning a circular DMA receive buffer into Modbus frames.
 *
 * The hardware half lives in src/bl_hw.c: USART1 receives into rxRing with
 * DMA2 in circular mode, and the USART's IDLE flag says "the line went quiet,
 * a frame just ended". Everything after that is arithmetic on the DMA's
 * remaining-count register, and arithmetic is the part that can be wrong in
 * ways a chip is expensive to ask about -- so it lives here, portable, with
 * a native test (emulator/test/bl_rxring_test.cpp).
 *
 * No hardware in this file. `ndtr` is whatever DMA_SxNDTR read as; the caller
 * owns the read and its ordering against the IDLE clear.
 */
#ifndef BL_RXRING_H
#define BL_RXRING_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 1 KB for a 256-byte protocol, and the slack is deliberate. A flash erase
 * stalls this core for 1-4 s, during which the client can time out and retry
 * a 235-byte request two or three times; the DMA lands every one of them. The
 * ring has to hold that whole burst, because a burst that laps the ring would
 * come out the far side looking like a plausible short frame instead of an
 * obvious oversized run -- and an oversized run is the case blRxRingTake
 * resynchronizes on. Must be a power of two. */
#define BL_RX_RING_SIZE 1024u

/* The index the DMA has written up to, from the stream's remaining count.
 * A count of 0 is the instant of the circular reload and means index 0; a
 * count larger than the ring means the stream is not configured the way this
 * module assumes, and is reported as index 0 rather than as a huge run. */
uint32_t blRxRingHead(uint32_t ndtr);

/* Take everything the DMA has landed since the last call.
 *
 *   ring     the circular destination, BL_RX_RING_SIZE bytes
 *   ndtr     the stream's remaining count, read after the IDLE was observed
 *   tail     in/out: index of the first byte not yet consumed
 *   frame    destination for the frame, at least frameMax bytes
 *   frameMax the caller's frame buffer size
 *
 * Returns the frame length, or 0 when there was nothing new or too much.
 *
 * `*tail` jumps to the write head in EVERY case, including the too-much case.
 * That is the resynchronization: if this core was stalled through the gap
 * between two frames -- a long erase, or a client that retried -- the run
 * spans both and is not a frame at all. Dropping it whole gets the next
 * frame's boundary right; keeping the excess would poison it.
 *
 * The one blind spot is a run of exactly BL_RX_RING_SIZE bytes, which lands
 * the head back on the tail and reads as "nothing new". That needs 1024 bytes
 * -- 89 ms of continuous 115200 traffic -- to arrive between two polls with
 * no earlier IDLE seen, and it degrades to a dropped frame the client retries,
 * not to a mis-framed one. Growing the ring is the lever if it ever matters. */
uint32_t blRxRingTake(const volatile uint8_t *ring, uint32_t ndtr,
                      uint32_t *tail, uint8_t *frame, uint32_t frameMax);

#ifdef __cplusplus
}
#endif

#endif /* BL_RXRING_H */
