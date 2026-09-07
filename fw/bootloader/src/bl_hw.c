/*
 * bl_hw.c -- the STM32F411CE underneath the bootloader core: bl_port.h for
 * the core, plus the UART byte driver, the watchdog, and the jump.
 *
 * Bare registers, no HAL, no interrupts. The chip runs on the 16 MHz HSI it
 * woke up with; nothing in RCC is changed except peripheral clock enables,
 * and those are undone before the jump, so the app starts from what is for
 * every practical purpose the reset state (VTOR aside, which it sets itself).
 *
 * NO DIRECTION PIN. The RS-485 driver enable is derived from TXD in hardware
 * on this board (Ramps.c EN_Port = NULL); the UART is simply written.
 */
#include <stdint.h>
#include "stm32f4xx.h"
#include "bl_port.h"
#include "bl_hw.h"
#include "bl_rxring.h"
#include "els_identity.h"

/* HSI is 16 MHz, APB2 prescaler 1 at reset -> 115200 needs USARTDIV 8.6875:
 * mantissa 8, fraction 11/16 -> 0x8B (115108 baud, -0.08%). */
#define BL_UART_BRR      0x8Bu

/* ---- CRC unit ---------------------------------------------------------- */

void blPortCrcReset(void)
{
  CRC->CR = CRC_CR_RESET;
}

void blPortCrcFeed(uint32_t word)
{
  CRC->DR = word;
}

uint32_t blPortCrcValue(void)
{
  return CRC->DR;
}

/* ---- flash ------------------------------------------------------------- */

#define FLASH_ERR_MASK (FLASH_SR_WRPERR | FLASH_SR_PGAERR | FLASH_SR_PGPERR | \
                        FLASH_SR_PGSERR | FLASH_SR_RDERR | FLASH_SR_SOP)

static void flashWaitIdle(void)
{
  while (FLASH->SR & FLASH_SR_BSY) { }
}

static void flashUnlock(void)
{
  if (FLASH->CR & FLASH_CR_LOCK) {
    FLASH->KEYR = 0x45670123u;
    FLASH->KEYR = 0xCDEF89ABu;
  }
}

static void flashLock(void)
{
  FLASH->CR |= FLASH_CR_LOCK;
}

static int flashErrors(void)
{
  uint32_t sr = FLASH->SR & FLASH_ERR_MASK;
  if (sr) FLASH->SR = sr;       /* write-1-to-clear */
  return sr != 0u;
}

static uint32_t sectorOf(uint32_t base)
{
  switch (base) {
    case ELS_BL_SECTOR_BASE:    return ELS_BL_SECTOR;
    case ELS_STATE_SECTOR_BASE: return ELS_STATE_SECTOR;
    case ELS_RUN_SLOT_BASE:     return ELS_RUN_SECTOR;
    case ELS_STAGING_SLOT_BASE: return ELS_STAGING_SECTOR;
    case ELS_BACKUP_SLOT_BASE:  return ELS_BACKUP_SECTOR;
    default:                    return 0xFFu;
  }
}

int blPortErase(uint32_t sectorBase)
{
  uint32_t sector = sectorOf(sectorBase);
  if (sector == 0xFFu || sector == ELS_BL_SECTOR) return -1;   /* never our own sector */
  flashWaitIdle();
  (void)flashErrors();
  flashUnlock();
  /* PSIZE x32 (VDD 2.7-3.6 V), sector erase, then STRT. RM0383 3.6.2. */
  FLASH->CR = (FLASH->CR & ~(FLASH_CR_PSIZE | FLASH_CR_SNB | FLASH_CR_PG))
            | FLASH_CR_PSIZE_1 | FLASH_CR_SER | (sector << FLASH_CR_SNB_Pos);
  FLASH->CR |= FLASH_CR_STRT;
  flashWaitIdle();                 /* the 1-4 s stall: CPU fetches stall here too */
  FLASH->CR &= ~FLASH_CR_SER;
  flashLock();
  return flashErrors() ? -1 : 0;
}

int blPortProgram(uint32_t addr, const uint32_t *words, uint32_t nwords)
{
  if ((addr & 3u) != 0u) return -1;
  if (addr < ELS_STATE_SECTOR_BASE) return -1;              /* never our own sector */
  flashWaitIdle();
  (void)flashErrors();
  flashUnlock();
  FLASH->CR = (FLASH->CR & ~(FLASH_CR_PSIZE | FLASH_CR_SER)) | FLASH_CR_PSIZE_1 | FLASH_CR_PG;
  for (uint32_t i = 0; i < nwords; i++) {
    *(volatile uint32_t *)(addr + 4u * i) = words[i];
    flashWaitIdle();
    if (FLASH->SR & FLASH_ERR_MASK) break;
  }
  FLASH->CR &= ~FLASH_CR_PG;
  flashLock();
  return flashErrors() ? -1 : 0;
}

const uint8_t *blPortMap(uint32_t addr)
{
  return (const uint8_t *)addr;
}

/* ---- backup registers -------------------------------------------------- */

static void bkpUnlock(void)
{
  RCC->APB1ENR |= RCC_APB1ENR_PWREN;
  (void)RCC->APB1ENR;
  PWR->CR |= PWR_CR_DBP;
}

uint32_t blPortBkpRead(uint32_t idx)
{
  volatile uint32_t *bkp = &RTC->BKP0R;
  bkpUnlock();
  return bkp[idx];
}

void blPortBkpWrite(uint32_t idx, uint32_t value)
{
  volatile uint32_t *bkp = &RTC->BKP0R;
  bkpUnlock();
  bkp[idx] = value;
}

/* ---- watchdog ---------------------------------------------------------- */

void blPortWatchdogKick(void)
{
  IWDG->KR = 0xAAAAu;
}

void blHwArmWatchdog(void)
{
  /* LSI ~32 kHz / 256 / 4096 = ~32.8 s (22-60 s over LSI tolerance). Starting
   * the IWDG forces LSI on. Cannot be stopped afterwards: the app must kick
   * it (els_boot.h), and so must we while resident. Frozen while a debugger
   * halts the core, so an SWD session does not get reset mid-operation. */
  /* ORDER IS LOAD-BEARING, and getting it wrong hangs forever rather than
   * failing (found on the chip, 2026-09-07 bring-up: the bootloader spun in a
   * two-instruction loop here, so it never reached its Modbus slave and never
   * jumped -- nothing answered on the bus at all). SR's PVU/RVU bits are
   * cleared by the IWDG's own LSI clock domain, and the LSI only runs once the
   * IWDG has been STARTED. Waiting on SR before the 0xCCCC start is therefore
   * a wait on a clock that is off. Start, unlock, write, wait, reload -- the
   * RM0383 sequence. */
  DBGMCU->APB1FZ |= DBGMCU_APB1_FZ_DBG_IWDG_STOP;
  IWDG->KR  = 0xCCCCu;
  IWDG->KR  = 0x5555u;
  IWDG->PR  = 6u;
  IWDG->RLR = 0xFFFu;
  while (IWDG->SR) { }
  IWDG->KR  = 0xAAAAu;
}

/* ---- UART -------------------------------------------------------------- */

/*
 * RECEIVE IS DMA, NOT POLLED BYTES, and that is the whole point of this
 * section. The receiver this replaced read at most one byte per main-loop
 * iteration, so every byte that arrived while the loop was somewhere else was
 * simply gone: inside blHwUartSend's blocking TC wait, and above all during
 * flash programming, which stalls instruction fetch on this single-bank F411
 * because this code executes from the same flash it is writing. Measured on
 * the board 2026-09-07 against that receiver: 31 of 222 chunk frames lost
 * (14.0%) at 200 bytes of payload, 38 of 220 (17.3%) at 40 bytes. Loss that
 * does not scale with frame length is a receiver missing TIME, not bytes.
 *
 * DMA2 keeps filling SRAM through both stalls; the main loop only has to
 * notice afterwards that a frame happened. Still no interrupts anywhere in
 * this program -- the IDLE flag and the DMA remaining-count register are both
 * polled from the same loop, and the IDLE flag LATCHES, which is why a stall
 * that outlasts a whole frame still ends with a frame in hand.
 *
 * USART1_RX is DMA2, channel 4, on stream 2 or stream 5 (RM0383 "DMA2 request
 * mapping"). Stream 2 here; nothing else in this program uses DMA at all, so
 * there is nothing to collide with.
 *
 * FIFO OFF -- direct mode, FCR = 0 -- is load-bearing, not tidiness. The frame
 * length below is a difference of NDTR, and in FIFO mode NDTR counts bytes
 * pulled out of DR into the FIFO rather than bytes landed in SRAM, so the
 * length would run ahead of the data.
 */
#define BL_RX_DMA_STREAM DMA2_Stream2
#define BL_RX_DMA_CHSEL  4u
#define BL_RX_DMA_FLAGS  (DMA_LIFCR_CTCIF2 | DMA_LIFCR_CHTIF2 | DMA_LIFCR_CTEIF2 | \
                          DMA_LIFCR_CDMEIF2 | DMA_LIFCR_CFEIF2)
#define BL_RX_DMA_ERRS   (DMA_LISR_TEIF2 | DMA_LISR_DMEIF2 | DMA_LISR_FEIF2)

static volatile uint8_t rxRing[BL_RX_RING_SIZE];
static uint32_t         rxTail;

/* (Re)arm the stream from index 0. Safe to call at any time: an enabled
 * stream must be disabled and SEEN disabled before it is reconfigured. */
static void rxDmaStart(void)
{
  BL_RX_DMA_STREAM->CR &= ~DMA_SxCR_EN;
  while (BL_RX_DMA_STREAM->CR & DMA_SxCR_EN) { }
  DMA2->LIFCR = BL_RX_DMA_FLAGS;

  BL_RX_DMA_STREAM->PAR  = (uint32_t)&USART1->DR;
  BL_RX_DMA_STREAM->M0AR = (uint32_t)rxRing;
  BL_RX_DMA_STREAM->NDTR = BL_RX_RING_SIZE;
  BL_RX_DMA_STREAM->FCR  = 0u;                  /* direct mode, see above */
  /* Everything not named is zero and meant to be: DIR = peripheral-to-memory,
   * PSIZE = MSIZE = byte, PINC off, double-buffer off, no interrupt enables. */
  BL_RX_DMA_STREAM->CR   = (BL_RX_DMA_CHSEL << DMA_SxCR_CHSEL_Pos)
                         | DMA_SxCR_PL_1        /* priority high */
                         | DMA_SxCR_MINC
                         | DMA_SxCR_CIRC;
  BL_RX_DMA_STREAM->CR  |= DMA_SxCR_EN;
  rxTail = 0u;
}

void blHwInit(void)
{
  /* Clocks: GPIOA, CRC, DMA2, USART1, PWR. */
  RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_CRCEN | RCC_AHB1ENR_DMA2EN;
  RCC->APB2ENR |= RCC_APB2ENR_USART1EN;
  RCC->APB1ENR |= RCC_APB1ENR_PWREN;
  (void)RCC->APB1ENR;

  /* PA10 = USART1_RX, PA15 = USART1_TX, both AF7 push-pull, no pull. */
  GPIOA->AFR[1] = (GPIOA->AFR[1] & ~((0xFu << 8) | (0xFu << 28))) | (7u << 8) | (7u << 28);
  GPIOA->MODER  = (GPIOA->MODER & ~((3u << 20) | (3u << 30))) | (2u << 20) | (2u << 30);
  GPIOA->PUPDR &= ~((3u << 20) | (3u << 30));
  GPIOA->OSPEEDR |= (3u << 30);

  /* 8N1, oversampling 16, TX + RX. */
  USART1->CR1 = 0u;
  USART1->CR2 = 0u;
  USART1->CR3 = 0u;
  USART1->BRR = BL_UART_BRR;
  USART1->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;

  /* Empty DR and disarm any IDLE the line already latched before the DMA
   * takes over. On this part IDLE is cleared ONLY by a read of SR followed by
   * a read of DR -- there is no write-1-to-clear -- and a stale IDLE would
   * make the very first poll report a frame that never arrived. */
  (void)USART1->SR;
  (void)USART1->DR;

  rxDmaStart();
  USART1->CR3 |= USART_CR3_DMAR;      /* stream armed first, then requests */
}

uint32_t blHwUartPoll(uint8_t *frame)
{
  uint32_t sr = USART1->SR;

  if (sr & (USART_SR_ORE | USART_SR_FE | USART_SR_NE)) {
    /* ORE MEANS SOMETHING ELSE NOW. With the byte-polled receiver it meant
     * "software was too slow", which was the normal case and the bug. With
     * the DMA draining DR within a few cycles of every RXNE, one 115200
     * stream cannot outrun it, so ORE now means the stream itself stopped --
     * a transfer error, or an unarmed stream -- and that is worth restarting
     * rather than just noting. FE/NE still mean a mangled character on the
     * wire, which the DMA stores as garbage like any other byte.
     *
     * All three are cleared by the same SR-then-DR read that clears IDLE; sr
     * above was the SR half. Either way the run sitting in the ring is
     * suspect, so drop it and resynchronize -- the client retries. */
    (void)USART1->DR;
    if ((DMA2->LISR & BL_RX_DMA_ERRS) != 0u ||
        (BL_RX_DMA_STREAM->CR & DMA_SxCR_EN) == 0u) {
      rxDmaStart();                          /* clears the flags, tail to 0 */
    } else {
      rxTail = blRxRingHead(BL_RX_DMA_STREAM->NDTR);
    }
    return 0u;
  }

  if (sr & USART_SR_IDLE) {
    /* The frame boundary. Clearing IDLE needs SR read then DR read, and the
     * DR read is the trap once the receiver is on DMA: a software read of DR
     * steals the byte the DMA controller was about to fetch. So re-read SR
     * and only clear while RXNE is down. The line has just been idle for a
     * full character time, so RXNE down means DR is empty and the next start
     * bit is at least a character away; if RXNE is up, leave IDLE latched and
     * come back for it -- the flag does not expire.
     *
     * NDTR is read after the clear so the length covers everything received
     * up to this instant, and the length is that count's movement since the
     * last frame. */
    if (USART1->SR & USART_SR_RXNE) return 0u;
    (void)USART1->DR;
    return blRxRingTake(rxRing, BL_RX_DMA_STREAM->NDTR, &rxTail,
                        frame, BL_HW_FRAME_MAX);
  }

  return 0u;
}

void blHwUartSend(const uint8_t *data, uint32_t len)
{
  for (uint32_t i = 0; i < len; i++) {
    while (!(USART1->SR & USART_SR_TXE)) { }
    USART1->DR = data[i];
  }
  while (!(USART1->SR & USART_SR_TC)) { }
}

/* ---- the jump ---------------------------------------------------------- */

void blHwJump(uint32_t appBase)
{
  uint32_t msp   = *(volatile uint32_t *)appBase;
  uint32_t reset = *(volatile uint32_t *)(appBase + 4u);

  __disable_irq();

  /* Receive DMA off BEFORE the UART, so no request is left outstanding and
   * nothing is still writing into rxRing when the app takes the SRAM back.
   * Then UART off and reset; drain nothing -- the last reply was waited to
   * TC. DMA2 goes back through its peripheral reset with its clock off,
   * because the app configures no DMA at all (Core/Src/Ramps.c sets
   * xTypeHW = USART_HW, so its Modbus receives one byte per USART interrupt)
   * and would therefore never undo anything left behind here. */
  USART1->CR3 &= ~USART_CR3_DMAR;
  DMA2_Stream2->CR &= ~DMA_SxCR_EN;
  while (DMA2_Stream2->CR & DMA_SxCR_EN) { }

  USART1->CR1 = 0u;
  RCC->APB2RSTR |=  RCC_APB2RSTR_USART1RST;
  RCC->APB2RSTR &= ~RCC_APB2RSTR_USART1RST;
  RCC->APB2ENR  &= ~RCC_APB2ENR_USART1EN;
  RCC->AHB1RSTR |=  RCC_AHB1RSTR_DMA2RST;
  RCC->AHB1RSTR &= ~RCC_AHB1RSTR_DMA2RST;

  /* GPIOA back to its reset image (RM0383 8.4: MODER 0xA8000000,
   * OSPEEDR 0x0C000000, PUPDR 0x64000000, OTYPER/AFR 0). */
  GPIOA->MODER   = 0xA8000000u;
  GPIOA->OSPEEDR = 0x0C000000u;
  GPIOA->PUPDR   = 0x64000000u;
  GPIOA->OTYPER  = 0u;
  GPIOA->AFR[0]  = 0u;
  GPIOA->AFR[1]  = 0u;

  CRC->CR = CRC_CR_RESET;
  PWR->CR &= ~PWR_CR_DBP;
  RCC->AHB1ENR &= ~(RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_CRCEN | RCC_AHB1ENR_DMA2EN);
  RCC->APB1ENR &= ~RCC_APB1ENR_PWREN;

  /* Nothing here touches DWT or DEMCR any more: the cycle counter existed
   * only for the old inter-frame gap timer, which the IDLE flag replaced.
   * The APP still enables both for its own ISR timing (Core/Src/Ramps.c);
   * that is its business and it does it after this jump. */

  FLASH->CR |= FLASH_CR_LOCK;

  /* No interrupt was ever enabled here, but leave nothing to chance: SysTick
   * off, every NVIC line disabled and unpended, so nothing can fire into the
   * app's table before the app has installed its handlers. */
  SysTick->CTRL = 0u;
  SysTick->LOAD = 0u;
  SysTick->VAL  = 0u;
  for (uint32_t i = 0; i < 8u; i++) {
    NVIC->ICER[i] = 0xFFFFFFFFu;
    NVIC->ICPR[i] = 0xFFFFFFFFu;
  }
  SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk;

  SCB->VTOR = appBase;
  __DSB();
  __ISB();

  /* MSP from vector[0], then branch to vector[1] with interrupts re-enabled
   * (reset state). One asm block so no C spill can touch the new stack. */
  __asm volatile(
      "msr msp, %0   \n"
      "cpsie i       \n"
      "bx   %1       \n"
      : : "r"(msp), "r"(reset) : "memory");
  for (;;) { }
}
