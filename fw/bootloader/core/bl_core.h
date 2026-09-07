/*
 * bl_core.h -- the bootloader's decision logic and command handler, with no
 * chip in it. Everything that touches hardware goes through bl_port.h; the
 * Modbus byte layer (bl_modbus.c) and the hardware main (src/main.c) sit on
 * top of this. The boot decision, the copy/swap state machine, the strike
 * counter and the command semantics are all here, which is what lets the
 * native tests run them through a power-loss injector.
 *
 * BOOT SEQUENCE (blCoreBoot):
 *   1. read the flash journal; consume the STAY request and the attempt
 *      counter from the backup registers;
 *   2. finish or abort any copy the journal says was in flight, so a torn
 *      RUN slot is repaired (from STAGING) or replaced (from BACKUP) BEFORE
 *      anything is jumped to;
 *   3. an app confirmation (attempts written to TAG,0) promotes TRIAL or
 *      REVERTED to IDLE;
 *   4. three attempts without a confirmation: revert to BACKUP if it holds a
 *      different valid image and we have not already reverted; otherwise
 *      STRUCK_OUT and stay resident;
 *   5. jump if RUN validates and nobody asked us to stay.
 *
 * The caller counts the attempt (blCoreCountAttempt), arms the IWDG and
 * jumps -- in that order -- when the answer is BL_BOOT_JUMP.
 */
#ifndef BL_CORE_H
#define BL_CORE_H

#include <stdint.h>
#include "els_identity.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { BL_BOOT_STAY = 0, BL_BOOT_JUMP = 1 } blBootDecision_t;

typedef struct {
  uint16_t regs[ELS_BL_SIZE];   /* the control window as the host sees it */
  uint16_t attempts;            /* boot-attempt count read at boot          */
  uint8_t  copyState;           /* ELS_BL_STATE_* from the journal          */
  uint8_t  runValid;            /* RUN passes header + CRC + vectors        */
  uint8_t  stagedOk;            /* the last VERIFY of STAGING passed        */
  uint8_t  stayRequested;       /* the app asked us to stay resident        */
  uint8_t  struckOut;           /* 3 attempts, nothing left to revert to    */
  uint8_t  jumpPending;         /* a JUMP command was accepted; go after the reply */
} blCore_t;

void blCoreInit(blCore_t *c);

/* Steps 1-5 above. Leaves the window published. */
blBootDecision_t blCoreBoot(blCore_t *c);

/* Increment and store the attempt counter. Immediately before a jump. */
void blCoreCountAttempt(blCore_t *c);

/* Consume blCommand if nonzero: run it to completion, set blResult, bump
 * blSeq, clear the command. Sets c->jumpPending when a JUMP was accepted. */
void blCoreService(blCore_t *c);

/* Refresh the read-only registers from the core state. */
void blCorePublish(blCore_t *c);

/* Helpers exposed for the tests. */
uint32_t blCoreReg32(const blCore_t *c, unsigned lo);
void     blCoreSetReg32(blCore_t *c, unsigned lo, uint32_t v);

#ifdef __cplusplus
}
#endif

#endif /* BL_CORE_H */
