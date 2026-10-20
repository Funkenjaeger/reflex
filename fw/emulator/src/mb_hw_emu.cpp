/*
 * mb_hw_emu.cpp -- the lathe-emulator's transport under the application's
 * Modbus slave (Core/Inc/mb_app_hw.h).
 *
 * Models the chip's receiver closely enough to run its arithmetic: bytes from
 * the PTY/TCP transport land in a circular ring at a write index that a
 * remaining count, the DMA's NDTR, counts down and reloads, and the transport
 * raises an IDLE flag once the line has been quiet. mbHwPoll hands the ring,
 * that count and the slave's tail to the bootloader's own blRxRingTake
 * (bootloader/core/bl_rxring.c), exactly as Core/Src/mb_app_hw.c does on the
 * chip. What is NOT modeled: the USART error flags and a DMA that stops.
 *
 * Threads: the transport's poll thread writes (mbEmuRxByte, mbEmuRxIdle); the
 * thread polling the slave reads. The count is published with release order
 * after each byte, so a reader that sees it also sees the byte.
 *
 * Transmit is synchronous: the reply is handed to the transport before
 * mbHwSend returns, so the transmitter is never busy.
 */
#include <atomic>
#include <cstdint>

extern "C" {
#include "mb_app_hw.h"
#include "emulator_state.h"
#include "../../bootloader/core/bl_rxring.h"
}

#include "mb_hw_emu.h"

static volatile uint8_t      rxRing[BL_RX_RING_SIZE];
static std::atomic<uint32_t> rxNdtr{BL_RX_RING_SIZE};
static std::atomic<bool>     rxIdle{false};
static uint32_t              rxTail;
static uint16_t             *rxDiag;

void mbEmuRxByte(uint8_t byte)
{
    uint32_t n = rxNdtr.load(std::memory_order_relaxed);
    rxRing[blRxRingHead(n)] = byte;
    /* Circular mode: the count reloads the instant it would reach 0. */
    rxNdtr.store(n == 1u ? BL_RX_RING_SIZE : n - 1u, std::memory_order_release);
}

void mbEmuRxIdle(void)
{
    rxIdle.store(true, std::memory_order_release);
}

extern "C" void mbHwInit(uint16_t *diag)
{
    rxDiag = diag;
    rxTail = blRxRingHead(rxNdtr.load(std::memory_order_acquire));
    rxIdle.store(false);
}

extern "C" uint32_t mbHwPoll(uint8_t *frame, uint32_t frameMax)
{
    if (!rxIdle.exchange(false, std::memory_order_acq_rel)) return 0u;
    return blRxRingTake(rxRing, rxNdtr.load(std::memory_order_acquire), &rxTail,
                        frame, frameMax, rxDiag);
}

extern "C" int mbHwTxBusy(void)
{
    return 0;
}

extern "C" void mbHwSend(const uint8_t *data, uint32_t len)
{
    if (emu_hw.on_uart_tx) emu_hw.on_uart_tx(data, (uint16_t)len);
}
