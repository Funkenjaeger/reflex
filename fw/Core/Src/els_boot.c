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

void elsBootRequestStayAndReset(void)
{
  bkpWrite(ELS_BKP_REQUEST_IDX, ELS_BOOT_REQ_STAY);
  NVIC_SystemReset();
}

void elsBootRequestReset(void)
{
  NVIC_SystemReset();
}

#endif /* EMULATOR_BUILD */
