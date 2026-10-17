/*
 * mb_rtu_port.h -- the bootloader's port of the Modbus RTU core
 * (modbus_rtu/mb_rtu.h), which defines the contract.
 *
 * The windows are the identity window (read-only) and the bootloader
 * control window, served as TWO windows: the writable head + blData below
 * ELS_BL_DIAG, and blDiag above it read-only, so the host can read the
 * counters but never write them (els_identity.h, blDiag). Every address
 * below ELS_BL_DIAG resolves exactly as it did when that was the whole
 * window. No main map, and no access table: the control window has no
 * firmware-owned register below ELS_BL_DIAG.
 *
 * The command hook is blCoreService(), so a write that leaves blCommand
 * nonzero is executed to completion before the reply is sent and the host
 * never has a request in flight while the flash interface is stalled by an
 * erase. Dropped frames are counted in blDiag, CRC errors apart from every
 * other reject (bl_diag.h).
 */
#ifndef MB_RTU_PORT_H
#define MB_RTU_PORT_H

#include <stdint.h>
#include "bl_core.h"
#include "bl_diag.h"
#include "modbus_window.h"

typedef blCore_t mbRtuPort_t;

#define MB_RTU_PORT_ADDRESS 17u   /* the app's address, Ramps.h MODBUS_ADDRESS */
#define MB_RTU_PORT_WINDOWS 3u

static inline uint16_t *mbRtuPortMainRegs(blCore_t *core)
{
  (void)core;
  return (uint16_t *)0;
}

static inline uint16_t mbRtuPortMainSize(blCore_t *core)
{
  (void)core;
  return 0u;
}

static inline void mbRtuPortWindows(blCore_t *core, const uint16_t *identity,
                                    mbWindow_t *wins)
{
  wins[0].base = ELS_ID_BASE; wins[0].size = ELS_ID_SIZE;
  wins[0].regs = (uint16_t *)identity; wins[0].readOnly = 1;
  wins[1].base = ELS_BL_BASE; wins[1].size = ELS_BL_DIAG;
  wins[1].regs = core->regs;  wins[1].readOnly = 0;
  wins[2].base = (uint16_t)(ELS_BL_BASE + ELS_BL_DIAG); wins[2].size = ELS_BL_DIAG_REGS;
  wins[2].regs = &core->regs[ELS_BL_DIAG]; wins[2].readOnly = 1;
}

static inline const mbRtuAccess_t *mbRtuPortAccess(blCore_t *core)
{
  (void)core;
  return (const mbRtuAccess_t *)0;
}

static inline void mbRtuPortBeforeRead(blCore_t *core)
{
  blCorePublish(core);
}

static inline void mbRtuPortCommand(blCore_t *core)
{
  blCoreService(core);
}

static inline void mbRtuPortDrop(blCore_t *core, unsigned why)
{
  blDiagBump(&core->regs[ELS_BL_DIAG],
             why == MB_RTU_DROP_CRC ? ELS_BL_DG_CRC_ERRORS : ELS_BL_DG_BAD_FRAMES);
}

#endif /* MB_RTU_PORT_H */
