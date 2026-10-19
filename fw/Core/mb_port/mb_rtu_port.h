/*
 * mb_rtu_port.h -- the application's port of the Modbus RTU core
 * (modbus_rtu/mb_rtu.h), which defines the contract.
 *
 * IN ITS OWN DIRECTORY ON PURPOSE. mb_rtu.h finds its port by name on the
 * image's include path, and every native test puts Core/Inc on its path
 * ahead of its own port directory; a port in Core/Inc would win over the
 * test's and the bootloader's. Only the app and the lathe-emulator put this
 * directory on their path.
 *
 * The main map is rampsSharedData_t, cast wholesale into registers
 * 0..size-1, and the one window is the identity window at ELS_ID_BASE,
 * read-only. The bootloader control window is deliberately not served: the
 * app answers exception 2 there, which is the host's "this is the app"
 * signal alongside idStage.
 *
 * The access table is Ramps_access_generated.h: a write, FC 6 or FC 16,
 * touching any register the schema marks `access: ro_firmware` is answered
 * with exception 2 and writes nothing (mb_rtu.h).
 *
 * NO COMMAND HOOK. Every command register in the map is consumed by
 * servoEnableTask or the ISR, and a write is acknowledged as soon as it
 * lands. BeforeRead does nothing either: FC 3 copies the live registers one
 * at a time, ascending, while the ISR runs, and the seq-before-payload rule
 * in registers/els_stop.yaml is built on exactly that.
 *
 * Dropped frames are counted in the port's receiver counters, which use the
 * bootloader's blDiag layout (els_identity.h ELS_BL_DG_*) but are not served:
 * readable over SWD only, so they stay out of the register contract.
 */
#ifndef MB_RTU_PORT_H
#define MB_RTU_PORT_H

#include <stdint.h>
#include "els_identity.h"
#include "modbus_window.h"
#include "mb_app.h"

typedef struct {
  uint16_t            *regs;      /* rampsSharedData_t as registers */
  uint16_t             size;      /* sizeof(rampsSharedData_t) / 2 */
  const mbRtuAccess_t *access;    /* ro_firmware table */
  uint16_t            *diag;      /* ELS_BL_DIAG_REGS receiver counters */
} mbAppPort_t;

typedef mbAppPort_t mbRtuPort_t;

#define MB_RTU_PORT_ADDRESS MB_APP_ADDRESS
#define MB_RTU_PORT_WINDOWS 1u

static inline uint16_t *mbRtuPortMainRegs(mbAppPort_t *port) { return port->regs; }
static inline uint16_t  mbRtuPortMainSize(mbAppPort_t *port) { return port->size; }

static inline void mbRtuPortWindows(mbAppPort_t *port, const uint16_t *identity,
                                    mbWindow_t *wins)
{
  (void)port;
  wins[0].base = ELS_ID_BASE; wins[0].size = ELS_ID_SIZE;
  wins[0].regs = (uint16_t *)identity; wins[0].readOnly = 1;
}

static inline const mbRtuAccess_t *mbRtuPortAccess(mbAppPort_t *port) { return port->access; }

static inline void mbRtuPortBeforeRead(mbAppPort_t *port) { (void)port; }

static inline void mbRtuPortCommand(mbAppPort_t *port) { (void)port; }

static inline void mbRtuPortDrop(mbAppPort_t *port, unsigned why)
{
  unsigned idx = (why == MB_RTU_DROP_CRC) ? ELS_BL_DG_CRC_ERRORS : ELS_BL_DG_BAD_FRAMES;
  if (port->diag != (uint16_t *)0 && port->diag[idx] != 0xFFFFu) port->diag[idx]++;
}

#endif /* MB_RTU_PORT_H */
