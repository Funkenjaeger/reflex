/*
 * mb_app_hw.c -- the STM32F411 transport under the application's Modbus
 * slave (mb_app_hw.h): USART1, both directions on DMA2, no interrupts.
 *
 * RECEIVE is the field bootloader's receiver, register for register
 * (bootloader/src/bl_hw.c, "UART"): DMA2 stream 2, channel 4, fills a
 * circular ring in SRAM, the USART's IDLE flag says a frame has ended, and the
 * frame length is the movement of the stream's remaining count
 * (bootloader/core/bl_rxring.c, the same file, natively tested). An interrupt
 * per byte would have 87 us at 115200 baud to empty DR before the next byte
 * overran it. The DMA empties DR within a few bus cycles of every byte no
 * matter what the CPU is doing, and the IDLE flag LATCHES, so a frame that
 * ends while the poller is held off is still there when it gets the CPU back.
 * The only deadline is the host's reply timeout.
 *
 * The rules bl_hw.c documents, with their measurements, hold here:
 *   - FIFO off (direct mode): NDTR must count bytes landed in SRAM;
 *   - IDLE is cleared only by SR-then-DR, and a software DR read while RXNE is
 *     up steals the byte the DMA was about to take, so the DR read waits for
 *     RXNE to be down;
 *   - with DMA, ORE means the stream stopped; a dead stream is re-armed.
 *
 * TRANSMIT is DMA2 stream 7, channel 4, one transfer per reply, so a reply
 * (up to 255 bytes, 22 ms on the wire) costs the CPU nothing while it leaves.
 * Busy until the stream has finished AND the USART's TC says the last stop
 * bit is out.
 *
 * NO DIRECTION PIN. The RS-485 driver enable is derived from TXD in hardware
 * on this board, as the bootloader's comment says; the UART is simply
 * written.
 *
 * HAL's MX_USART1_UART_Init (usart.c) has already set the pins, the baud and
 * UE/TE/RE. Nothing here uses the HAL UART driver after that.
 */
#include "mb_app_hw.h"
#include "stm32f4xx_hal.h"
#include "els_identity.h"
/* The ring arithmetic is the bootloader's own file, compiled into this image
 * from bootloader/core (CMakeLists.txt). Included by path because that
 * directory cannot go on this image's include path: it also holds the
 * bootloader's mb_rtu_port.h. */
#include "../../bootloader/core/bl_rxring.h"

#define MB_RX_DMA_STREAM DMA2_Stream2
#define MB_RX_DMA_FLAGS  (DMA_LIFCR_CTCIF2 | DMA_LIFCR_CHTIF2 | DMA_LIFCR_CTEIF2 | \
                          DMA_LIFCR_CDMEIF2 | DMA_LIFCR_CFEIF2)
#define MB_RX_DMA_ERRS   (DMA_LISR_TEIF2 | DMA_LISR_DMEIF2 | DMA_LISR_FEIF2)

#define MB_TX_DMA_STREAM DMA2_Stream7
#define MB_TX_DMA_FLAGS  (DMA_HIFCR_CTCIF7 | DMA_HIFCR_CHTIF7 | DMA_HIFCR_CTEIF7 | \
                          DMA_HIFCR_CDMEIF7 | DMA_HIFCR_CFEIF7)

/* USART1_RX and USART1_TX are both channel 4 of DMA2 (RM0383, DMA2 request
 * mapping): RX on stream 2 (or 5), TX on stream 7. Nothing else in this
 * image uses DMA. */
#define MB_DMA_CHSEL 4u

static volatile uint8_t rxRing[BL_RX_RING_SIZE];
static uint32_t         rxTail;
static uint16_t        *rxDiag;

static void diagBump(unsigned idx)
{
  if (rxDiag != (uint16_t *)0 && rxDiag[idx] != 0xFFFFu) rxDiag[idx]++;
}

/* (Re)arm the receive stream from index 0. An enabled stream must be
 * disabled and SEEN disabled before it is reconfigured. */
static void rxDmaStart(void)
{
  MB_RX_DMA_STREAM->CR &= ~DMA_SxCR_EN;
  while (MB_RX_DMA_STREAM->CR & DMA_SxCR_EN) { }
  DMA2->LIFCR = MB_RX_DMA_FLAGS;

  MB_RX_DMA_STREAM->PAR  = (uint32_t)&USART1->DR;
  MB_RX_DMA_STREAM->M0AR = (uint32_t)rxRing;
  MB_RX_DMA_STREAM->NDTR = BL_RX_RING_SIZE;
  MB_RX_DMA_STREAM->FCR  = 0u;                  /* direct mode */
  /* Unnamed fields are zero on purpose: peripheral-to-memory, byte to byte,
   * no peripheral increment, no double buffer, no interrupt enables. */
  MB_RX_DMA_STREAM->CR   = (MB_DMA_CHSEL << DMA_SxCR_CHSEL_Pos)
                         | DMA_SxCR_PL_1        /* priority high */
                         | DMA_SxCR_MINC
                         | DMA_SxCR_CIRC;
  MB_RX_DMA_STREAM->CR  |= DMA_SxCR_EN;
  rxTail = 0u;
}

void mbHwInit(uint16_t *diag)
{
  rxDiag = diag;

  /* No USART1 interrupt. usart.c's MspInit enables the NVIC line for
   * the HAL driver; no interrupt enable bit is set in the USART, so it could
   * not fire, but say so in the NVIC as well. */
  HAL_NVIC_DisableIRQ(USART1_IRQn);
  __HAL_RCC_DMA2_CLK_ENABLE();

  /* Empty DR and disarm any IDLE latched before the DMA takes over: a stale
   * IDLE would make the first poll report a frame that never arrived. */
  (void)USART1->SR;
  (void)USART1->DR;

  rxDmaStart();
  USART1->CR3 |= USART_CR3_DMAR | USART_CR3_DMAT;   /* streams armed first, then requests */
}

uint32_t mbHwPoll(uint8_t *frame, uint32_t frameMax)
{
  uint32_t sr = USART1->SR;

  if (sr & (USART_SR_ORE | USART_SR_FE | USART_SR_NE)) {
    /* Cleared by the same SR-then-DR read as IDLE, so the same trap: wait
     * while RXNE is up, unless the stream is dead -- then nothing will ever
     * fetch DR, RXNE stays up forever, and waiting would leave the slave deaf.
     * Decide which BEFORE the RXNE test (bl_hw.c blHwUartPoll). */
    uint32_t dead = (DMA2->LISR & MB_RX_DMA_ERRS) != 0u ||
                    (MB_RX_DMA_STREAM->CR & DMA_SxCR_EN) == 0u;
    sr = USART1->SR;
    if (!dead && (sr & USART_SR_RXNE)) return 0u;
    (void)USART1->DR;
    if (sr & USART_SR_ORE) diagBump(ELS_BL_DG_ERR_ORE);
    if (sr & USART_SR_FE)  diagBump(ELS_BL_DG_ERR_FE);
    if (sr & USART_SR_NE)  diagBump(ELS_BL_DG_ERR_NE);
    /* The run in the ring is suspect either way: drop it and resynchronize;
     * the host retries. */
    if (dead) {
      rxDmaStart();
      diagBump(ELS_BL_DG_DMA_RESTARTS);
    } else {
      rxTail = blRxRingHead(MB_RX_DMA_STREAM->NDTR);
    }
    return 0u;
  }

  if (sr & USART_SR_IDLE) {
    /* The frame boundary. Clear only while RXNE is down (see above); the
     * flag does not expire, so a poll that finds RXNE up comes back for it.
     * NDTR is read after the clear, so the length covers everything received
     * up to this instant. */
    if (USART1->SR & USART_SR_RXNE) return 0u;
    (void)USART1->DR;
    return blRxRingTake(rxRing, MB_RX_DMA_STREAM->NDTR, &rxTail,
                        frame, frameMax, rxDiag);
  }

  return 0u;
}

int mbHwTxBusy(void)
{
  return (MB_TX_DMA_STREAM->CR & DMA_SxCR_EN) != 0u ||
         (USART1->SR & USART_SR_TC) == 0u;
}

void mbHwSend(const uint8_t *data, uint32_t len)
{
  /* The caller waited for mbHwTxBusy() to read 0, so the stream is already
   * disabled; the wait is the reconfiguration rule, not a delay. */
  MB_TX_DMA_STREAM->CR &= ~DMA_SxCR_EN;
  while (MB_TX_DMA_STREAM->CR & DMA_SxCR_EN) { }
  DMA2->HIFCR = MB_TX_DMA_FLAGS;

  MB_TX_DMA_STREAM->PAR  = (uint32_t)&USART1->DR;
  MB_TX_DMA_STREAM->M0AR = (uint32_t)data;
  MB_TX_DMA_STREAM->NDTR = len;
  MB_TX_DMA_STREAM->FCR  = 0u;                  /* direct mode */
  MB_TX_DMA_STREAM->CR   = (MB_DMA_CHSEL << DMA_SxCR_CHSEL_Pos)
                         | DMA_SxCR_PL_0        /* priority medium, below receive */
                         | DMA_SxCR_MINC
                         | DMA_SxCR_DIR_0;      /* memory-to-peripheral */

  /* TC cleared by writing 0 to it, the other bits written 1 (no effect on
   * them; RXNE is rc_w0 too and must not be cleared). RM0383 USART "DMA
   * transmission": clear TC before enabling the stream, so busy-until-TC
   * means this reply's last bit, not the previous one's. */
  USART1->SR = ~USART_SR_TC;
  MB_TX_DMA_STREAM->CR |= DMA_SxCR_EN;
}
