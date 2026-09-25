/*
 * els_boot.c -- hardware implementation of the app's bootloader hand-off.
 * See els_boot.h for what each call owes the bootloader and why.
 *
 * Not compiled in the emulator: els_boot.h supplies weak no-ops there.
 */
#ifndef EMULATOR_BUILD

#include "els_boot.h"
#include "stm32f4xx.h"

/* The backup domain is write-protected after reset (RM0383 5.1.2): enable
 * the PWR interface clock and set DBP. Reads need only the clock. Left
 * enabled afterwards; nothing else in the app touches the backup domain. */
static void bkpUnlock(void)
{
  RCC->APB1ENR |= RCC_APB1ENR_PWREN;
  (void)RCC->APB1ENR;
  PWR->CR |= PWR_CR_DBP;
}

static void bkpWrite(uint32_t idx, uint32_t value)
{
  volatile uint32_t *bkp = &RTC->BKP0R;
  bkpUnlock();
  bkp[idx] = value;
}

void elsBootAttemptsClear(void)
{
  bkpWrite(ELS_BKP_ATTEMPTS_IDX, ELS_BOOT_ATTEMPTS_WORD(0u));
}

void elsBootWatchdogKick(void)
{
  /* Refresh key. A no-op when the IWDG was never started (legacy build). */
  IWDG->KR = 0xAAAAu;
}

/*
 * ENTERING THE BOOTLOADER IS A JUMP, NOT A RESET (2026-09-12).
 *
 * BOOT0 is not connected on the V1.2 controller (kicad/reflex
 * docs/els-respin-interconnect.md, 3.1), so every reset samples a floating
 * pin. On 2026-09-12, 3 of 7 reset-into-bootloader cycles came up in the ST
 * ROM bootloader instead: pc 0x1fff..., the bus answering garbage, MTR_STEP
 * and MTR_ENA floating, until a power cycle. A jump samples nothing.
 *
 * It has to hand the bootloader a chip that looks, to every register the
 * bootloader reads, like it just left reset. That is what bootloader/src/
 * bl_hw.c and the CMSIS startup assume: 16 MHz HSI as SYSCLK, prescalers 1,
 * no interrupts enabled or pending, its own USART1/DMA2/GPIOA setup ORed
 * onto reset-state registers, PWR clock off until it turns it on. The
 * sequence below is blHwJump's, which does the same thing in the other
 * direction, plus the app's own extras (FreeRTOS, PLL from HSE, the DWT
 * cycle counter, the FPU).
 *
 * Runs from the ramps task: thread mode, privileged (configENABLE_MPU is
 * 0), on the process stack. Nothing here returns.
 *
 * What a jump cannot undo: the IWDG (fine: the bootloader re-arms and kicks
 * it within microseconds of Reset_Handler) and the reset flags in RCC->CSR
 * (nobody reads them). What it does not cover: elsBootRequestReset and a
 * power-on still sample BOOT0. Not visible to the native tests -- every
 * mistake here is a hang, not a wrong answer -- so the proof is the bench
 * count in the task that introduced it.
 */
static void enterBootloader(void)
{
  const uint32_t base  = ELS_BL_SECTOR_BASE;
  uint32_t       msp   = *(volatile uint32_t *)base;
  uint32_t       reset = *(volatile uint32_t *)(base + 4u);

  __disable_irq();

  /* Core: SysTick off, every NVIC line disabled and unpended, PendSV and
   * SysTick unpended. FreeRTOS never runs again. */
  SysTick->CTRL = 0u;
  SysTick->LOAD = 0u;
  SysTick->VAL  = 0u;
  for (uint32_t i = 0; i < 8u; i++) {
    NVIC->ICER[i] = 0xFFFFFFFFu;
    NVIC->ICPR[i] = 0xFFFFFFFFu;
  }
  SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk;

  /* The cycle counter Ramps.c enables for ISR timing, back off. */
  DWT->CTRL = 0u;
  CoreDebug->DEMCR &= ~CoreDebug_DEMCR_TRCENA_Msk;

  /* Every peripheral on all three buses through its reset line: the four
   * timers, USART1, the GPIO ports, DMA, CRC, SYSCFG, PWR. The same
   * all-ones write the HAL's __HAL_RCC_xxx_FORCE_RESET macros do. The
   * backup registers survive the PWR reset -- only BDCR.BDRST clears the
   * backup domain -- so the STAY request written before this call is
   * still there for blCoreBoot. Clock enables back to their reset value
   * afterwards; the bootloader enables its own. */
  RCC->AHB1RSTR = 0xFFFFFFFFu;
  RCC->AHB1RSTR = 0u;
  RCC->APB1RSTR = 0xFFFFFFFFu;
  RCC->APB1RSTR = 0u;
  RCC->APB2RSTR = 0xFFFFFFFFu;
  RCC->APB2RSTR = 0u;
  RCC->AHB1ENR = 0u;
  RCC->AHB2ENR = 0u;
  RCC->APB1ENR = 0u;
  RCC->APB2ENR = 0u;

  /* Clock tree back to reset, in RM0383 6.3 order: HSI on and selected with
   * prescalers 1 (CFGR reset image is 0), then PLL and HSE off, PLLCFGR at
   * its reset image, interrupts off, and flash latency 0 last -- legal
   * only once SYSCLK is back at 16 MHz. HAL_RCC_DeInit is an empty weak
   * stub in this HAL, so this is by hand. */
  RCC->CR |= RCC_CR_HSION;
  while ((RCC->CR & RCC_CR_HSIRDY) == 0u) { }
  RCC->CFGR = 0u;
  while ((RCC->CFGR & RCC_CFGR_SWS) != 0u) { }
  RCC->CR &= ~(RCC_CR_PLLON | RCC_CR_HSEON | RCC_CR_CSSON | RCC_CR_HSEBYP);
  while ((RCC->CR & RCC_CR_PLLRDY) != 0u) { }
  RCC->PLLCFGR = 0x24003010u;
  RCC->CIR = 0u;
  /* ART accelerator back to reset, CONTENTS included. Disabling the
   * caches (ACR = 0) leaves their lines valid, and the lines hold THIS
   * image's instructions at RUN-slot addresses. The bootloader is about
   * to write a different image there and jump into it, and that image
   * re-enables the caches in HAL_Init: a stale hit would execute the old
   * image's words inside the new one. A real reset clears the cache; a
   * jump must do it by hand (RM0383 3.4.3: ICRST/DCRST, written with the
   * cache disabled). Added 2026-09-13 after the first in-app update to a
   * differently-laid-out image answered ~30 s late, one IWDG period. */
  FLASH->ACR &= ~(FLASH_ACR_ICEN | FLASH_ACR_DCEN);
  FLASH->ACR |=  (FLASH_ACR_ICRST | FLASH_ACR_DCRST);
  FLASH->ACR &= ~(FLASH_ACR_ICRST | FLASH_ACR_DCRST);
  FLASH->ACR = 0u;
  FLASH->CR |= FLASH_CR_LOCK;

  /* Reset-state core registers: BASEPRI and FAULTMASK clear (FreeRTOS
   * critical sections use BASEPRI), FPU access off as at reset, and the
   * vector table where the bootloader's already is -- so what 0x00000000
   * aliases to, which is the very thing BOOT0 decides, no longer matters. */
  __set_BASEPRI(0u);
  __set_FAULTMASK(0u);
  SCB->CPACR = 0u;
  SCB->VTOR = base;
  __DSB();
  __ISB();

  /* CONTROL 0 (main stack, privileged, no FP context), MSP from vector[0],
   * then vector[1] with interrupts re-enabled (reset state). One asm block
   * so no C spill can touch the new stack. */
  __asm volatile(
      "msr control, %2 \n"
      "isb             \n"
      "msr msp, %0     \n"
      "cpsie i         \n"
      "bx   %1         \n"
      : : "r"(msp), "r"(reset), "r"(0u) : "memory");
  for (;;) { }
}

void elsBootEnterBootloader(void)
{
  bkpWrite(ELS_BKP_REQUEST_IDX, ELS_BOOT_REQ_STAY);
  enterBootloader();
}

void elsBootRequestReset(void)
{
  NVIC_SystemReset();
}

#endif /* EMULATOR_BUILD */
