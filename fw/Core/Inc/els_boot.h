/*
 * els_boot.h -- the APPLICATION's side of the bootloader hand-off.
 *
 * Three things the app owes the bootloader (docs/decisions/
 * els-modbus-register-map.md, Implemented section):
 *
 *   1. elsBootAttemptsClear()  -- once Modbus is live, write the boot-attempt
 *      counter to (TAG, 0). The bootloader increments it before every jump;
 *      three jumps that never reach this call are a strike-out and trigger
 *      the swap-back to the previous image.
 *   2. elsBootWatchdogKick()   -- the bootloader arms the IWDG (~32 s) before
 *      jumping and the IWDG cannot be stopped, so the app must refresh it
 *      forever. Called from a task every 50 ms: it proves the scheduler runs,
 *      not that Modbus does. A hung app is reset and counted as a strike.
 *   3. elsBootRequestStayAndReset() -- elspi cannot power-cycle the board,
 *      so "reboot into the bootloader and stay resident" is a software path:
 *      leave the request magic in a backup register and reset.
 *
 * All three touch registers the emulator has no model of (RTC backup domain,
 * IWDG, SCB AIRCR). Under EMULATOR_BUILD they are WEAK no-ops, so every test
 * target that links Ramps.c keeps linking unchanged and a test that wants to
 * observe a call defines a strong version of the same symbol. The hardware
 * implementations are in Core/Src/els_boot.c.
 *
 * Both configurations of the app carry these calls. In the legacy build
 * (linked at 0x08000000, no bootloader) they are harmless: the IWDG refresh
 * key does nothing to a watchdog that was never started, and the backup
 * registers are written by nobody else.
 */
#ifndef ELS_BOOT_H
#define ELS_BOOT_H

#include <stdint.h>
#include "els_identity.h"

#ifdef __cplusplus
extern "C" {
#endif

void elsBootAttemptsClear(void);
void elsBootWatchdogKick(void);
void elsBootRequestStayAndReset(void);
void elsBootRequestReset(void);

#ifdef EMULATOR_BUILD
__attribute__((weak)) void elsBootAttemptsClear(void) {}
__attribute__((weak)) void elsBootWatchdogKick(void) {}
__attribute__((weak)) void elsBootRequestStayAndReset(void) {}
__attribute__((weak)) void elsBootRequestReset(void) {}
#endif

#ifdef __cplusplus
}
#endif

#endif /* ELS_BOOT_H */
