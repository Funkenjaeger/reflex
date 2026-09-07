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
#include "els_identity.h"

/* HSI is 16 MHz, APB2 prescaler 1 at reset -> 115200 needs USARTDIV 8.6875:
 * mantissa 8, fraction 11/16 -> 0x8B (115108 baud, -0.08%). */
#define BL_UART_BRR      0x8Bu
/* Inter-frame silence that ends a frame: 1.5 ms at 16 MHz. Modbus asks for
 * 3.5 characters (~300 us at 115200); the client waits for our reply before
 * sending again, so a longer gap costs only latency. */
#define BL_FRAME_GAP_CYC (16000u * 15u / 10u)

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

static uint8_t  rxBuf[BL_HW_FRAME_MAX];
static uint32_t rxLen;
static uint32_t rxLastCyc;

void blHwInit(void)
{
  /* Clocks: GPIOA, CRC, USART1, PWR. */
  RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_CRCEN;
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

  /* Cycle counter for frame-gap timing. */
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0u;
  DWT->CTRL  |= DWT_CTRL_CYCCNTENA_Msk;

  rxLen = 0u;
  rxLastCyc = DWT->CYCCNT;
}

uint32_t blHwUartPoll(uint8_t *frame)
{
  uint32_t sr = USART1->SR;
  if (sr & (USART_SR_ORE | USART_SR_FE | USART_SR_NE)) {
    /* Cleared by SR read followed by DR read. Whatever we were collecting is
     * now suspect: drop it, the client will time out and retry. */
    (void)USART1->DR;
    rxLen = 0u;
    rxLastCyc = DWT->CYCCNT;
    return 0u;
  }
  if (sr & USART_SR_RXNE) {
    uint8_t b = (uint8_t)USART1->DR;
    if (rxLen < BL_HW_FRAME_MAX) rxBuf[rxLen++] = b;
    rxLastCyc = DWT->CYCCNT;
    return 0u;
  }
  if (rxLen != 0u && (uint32_t)(DWT->CYCCNT - rxLastCyc) > BL_FRAME_GAP_CYC) {
    uint32_t n = rxLen;
    for (uint32_t i = 0; i < n; i++) frame[i] = rxBuf[i];
    rxLen = 0u;
    return n;
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

  /* UART off and reset; drain nothing -- the last reply was waited to TC. */
  USART1->CR1 = 0u;
  RCC->APB2RSTR |=  RCC_APB2RSTR_USART1RST;
  RCC->APB2RSTR &= ~RCC_APB2RSTR_USART1RST;
  RCC->APB2ENR  &= ~RCC_APB2ENR_USART1EN;

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
  RCC->AHB1ENR &= ~(RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_CRCEN);
  RCC->APB1ENR &= ~RCC_APB1ENR_PWREN;

  DWT->CTRL &= ~DWT_CTRL_CYCCNTENA_Msk;
  CoreDebug->DEMCR &= ~CoreDebug_DEMCR_TRCENA_Msk;

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
