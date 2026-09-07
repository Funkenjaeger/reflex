/*
 * bl_rxring_test.cpp -- the framing arithmetic of the bootloader's DMA
 * receiver (bootloader/core/bl_rxring.c).
 *
 * WHAT THIS DOES NOT TEST, said plainly: the DMA controller. Nothing here
 * proves that DMA2 stream 2 channel 4 is the right request mapping, that
 * direct mode was selected, that the USART IDLE flag is cleared by the
 * SR-then-DR read, or that a byte arriving during a flash erase lands in
 * SRAM. Those are register behaviors of a real STM32F411 and they are
 * settled on the chip, not here (fw/todo.md, "NOT proven on hardware").
 *
 * What IS testable natively is the part that turns "the remaining-count
 * register moved" into "here is a frame of N bytes" -- modular arithmetic
 * over a wrapping buffer, which is exactly the kind of thing that is wrong
 * by one for months. So the hardware layer holds only register writes and
 * this holds the arithmetic, driven by a simulated circular stream.
 *
 * MUTATIONS (seen red 2026-09-07, 16 checks):
 *   - moving `*tail = head` in blRxRingTake to after the oversize return, so
 *     an over-long run is left in the ring instead of dropped: 2 red, both of
 *     them the resynchronization checks and nothing else, which is the point.
 *   - blRxRingHead returning `ndtr` instead of `BL_RX_RING_SIZE - ndtr`:
 *     12 red.
 */
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>

extern "C" {
#include "bl_rxring.h"
}

static int failures = 0;

static void check(bool ok, const char *label) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) failures++;
}

/* A circular DMA stream over rxRing: writes at BL_RX_RING_SIZE - ndtr,
 * decrements ndtr per byte, reloads to BL_RX_RING_SIZE at zero. */
struct Stream {
    uint8_t  ring[BL_RX_RING_SIZE];
    uint32_t ndtr = BL_RX_RING_SIZE;

    Stream() { memset(ring, 0, sizeof ring); }

    void receive(const uint8_t *data, uint32_t n) {
        for (uint32_t i = 0; i < n; i++) {
            uint32_t idx = BL_RX_RING_SIZE - ndtr;
            ring[idx] = data[i];
            ndtr--;
            if (ndtr == 0u) ndtr = BL_RX_RING_SIZE;   /* circular reload */
        }
    }
    void receive(const std::vector<uint8_t> &v) { receive(v.data(), (uint32_t)v.size()); }
};

static std::vector<uint8_t> pattern(uint32_t n, uint8_t seed) {
    std::vector<uint8_t> v(n);
    for (uint32_t i = 0; i < n; i++) v[i] = (uint8_t)(seed + i * 7u);
    return v;
}

int main() {
    const uint32_t FRAME_MAX = 256u;
    uint8_t frame[BL_RX_RING_SIZE + 64u];   /* room for the deliberate over-reads */

    /* --- one frame, no wrap ------------------------------------------- */
    {
        Stream s;
        uint32_t tail = 0;
        auto req = pattern(235, 0x11);          /* the real FC16 chunk size */
        s.receive(req);
        uint32_t n = blRxRingTake(s.ring, s.ndtr, &tail, frame, FRAME_MAX);
        check(n == 235u, "a 235-byte frame comes out as 235 bytes");
        check(memcmp(frame, req.data(), 235) == 0, "...with the bytes in order");
        check(tail == 235u, "...and the tail sits on the write head");

        /* Nothing new since: an IDLE with no bytes must not fabricate one. */
        check(blRxRingTake(s.ring, s.ndtr, &tail, frame, FRAME_MAX) == 0u,
              "a second take with no new bytes yields nothing");
    }

    /* --- a frame that straddles the end of the ring --------------------- */
    {
        Stream s;
        uint32_t tail = 0;
        /* Park the head 40 bytes from the end, consume that, then send 200. */
        auto filler = pattern(BL_RX_RING_SIZE - 40u, 0x40);
        s.receive(filler);
        (void)blRxRingTake(s.ring, s.ndtr, &tail, frame, BL_RX_RING_SIZE);

        auto req = pattern(200, 0xA3);
        s.receive(req);
        uint32_t n = blRxRingTake(s.ring, s.ndtr, &tail, frame, FRAME_MAX);
        check(n == 200u, "a frame straddling the ring end still measures 200");
        check(memcmp(frame, req.data(), 200) == 0,
              "...and reassembles across the wrap in order");
        check(tail == 160u, "...leaving the tail past the wrap");
    }

    /* --- the stall case: a whole frame arrives while nobody polls ------- */
    {
        Stream s;
        uint32_t tail = 0;
        auto req = pattern(75, 0x5A);           /* the 40-byte-payload frame */
        s.receive(req);                          /* landed during an erase */
        /* Many polls later, one latched IDLE. */
        uint32_t n = blRxRingTake(s.ring, s.ndtr, &tail, frame, FRAME_MAX);
        check(n == 75u && memcmp(frame, req.data(), 75) == 0,
              "a frame that landed during a stall is intact afterwards");
    }

    /* --- resynchronization on an oversized run -------------------------- */
    {
        Stream s;
        uint32_t tail = 0;
        /* Stalled through the gap between a request and the client's retry:
         * two 235-byte frames, one latched IDLE, 470 bytes in the ring. */
        s.receive(pattern(235, 0x11));
        s.receive(pattern(235, 0x11));
        uint32_t n = blRxRingTake(s.ring, s.ndtr, &tail, frame, FRAME_MAX);
        check(n == 0u, "470 bytes under one IDLE is not a frame");
        check(tail == 470u, "...and the run is dropped, not carried forward");

        auto next = pattern(235, 0xC0);
        s.receive(next);
        n = blRxRingTake(s.ring, s.ndtr, &tail, frame, FRAME_MAX);
        check(n == 235u && memcmp(frame, next.data(), 235) == 0,
              "resync: the frame after an oversized run comes out intact");
    }

    /* --- boundaries ---------------------------------------------------- */
    {
        Stream s;
        uint32_t tail = 0;
        s.receive(pattern(FRAME_MAX, 0x77));
        check(blRxRingTake(s.ring, s.ndtr, &tail, frame, FRAME_MAX) == FRAME_MAX,
              "a run of exactly frameMax is delivered, not dropped");

        check(blRxRingHead(0u) == 0u,
              "ndtr 0 is the reload instant, i.e. write index 0");
        check(blRxRingHead(BL_RX_RING_SIZE) == 0u, "a full ring is write index 0");
        check(blRxRingHead(BL_RX_RING_SIZE + 1u) == 0u,
              "an out-of-range count is index 0, not a huge run");
    }

    /* --- many frames, several laps of the ring -------------------------- */
    {
        Stream s;
        uint32_t tail = 0;
        bool ok = true;
        for (int i = 0; i < 40; i++) {
            auto req = pattern(97, (uint8_t)i);
            s.receive(req);
            uint32_t n = blRxRingTake(s.ring, s.ndtr, &tail, frame, FRAME_MAX);
            if (n != 97u || memcmp(frame, req.data(), 97) != 0) { ok = false; break; }
        }
        check(ok, "40 frames of 97 bytes (3.8 laps of the ring) all intact");
    }

    printf("%s\n", failures ? "FAILURES" : "all passed");
    return failures ? 1 : 0;
}
