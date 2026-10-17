/*
 * mb_rtu_port.h -- the native test's port of the Modbus RTU core
 * (modbus_rtu/mb_rtu.h). Every hook records what it saw, so
 * mb_rtu_test.cpp can say when the core called it and with what.
 *
 * Windows: the identity window (read-only) and one writable window at
 * MB_TEST_WIN_BASE standing in for the bootloader's control window. The
 * main map, its size and the access table are whatever the test sets.
 */
#ifndef MB_RTU_PORT_H
#define MB_RTU_PORT_H

#include <stdint.h>
#include "els_identity.h"
#include "modbus_window.h"

#define MB_TEST_WIN_BASE 0x0900u
#define MB_TEST_WIN_SIZE 16u

typedef struct {
  uint16_t            *mainRegs;
  uint16_t             mainSize;
  uint16_t             win[MB_TEST_WIN_SIZE];
  const mbRtuAccess_t *access;

  /* What the hooks saw. */
  unsigned       beforeReads;
  unsigned       commands;
  unsigned       drops[2];          /* by MB_RTU_DROP_* */
  const uint8_t *resp;              /* the reply buffer under test */
  int            replyUntouchedAtCommand;
  uint16_t       winAtCommand[MB_TEST_WIN_SIZE];
} mbRtuTestPort_t;

typedef mbRtuTestPort_t mbRtuPort_t;

#define MB_RTU_PORT_ADDRESS 17u
#define MB_RTU_PORT_WINDOWS 2u

/* The test fills the reply buffer with this before every request. */
#define MB_TEST_SENTINEL 0xA5u

static inline uint16_t *mbRtuPortMainRegs(mbRtuTestPort_t *t) { return t->mainRegs; }
static inline uint16_t  mbRtuPortMainSize(mbRtuTestPort_t *t) { return t->mainSize; }

static inline void mbRtuPortWindows(mbRtuTestPort_t *t, const uint16_t *identity,
                                    mbWindow_t *wins)
{
  wins[0].base = ELS_ID_BASE; wins[0].size = ELS_ID_SIZE;
  wins[0].regs = (uint16_t *)identity; wins[0].readOnly = 1;
  wins[1].base = MB_TEST_WIN_BASE; wins[1].size = MB_TEST_WIN_SIZE;
  wins[1].regs = t->win; wins[1].readOnly = 0;
}

static inline const mbRtuAccess_t *mbRtuPortAccess(mbRtuTestPort_t *t) { return t->access; }

static inline void mbRtuPortBeforeRead(mbRtuTestPort_t *t) { t->beforeReads++; }

static inline void mbRtuPortCommand(mbRtuTestPort_t *t)
{
  int untouched = 1;
  for (unsigned i = 0; i < 8u; i++)
    if (t->resp[i] != MB_TEST_SENTINEL) untouched = 0;
  t->replyUntouchedAtCommand = untouched;
  for (unsigned i = 0; i < MB_TEST_WIN_SIZE; i++) t->winAtCommand[i] = t->win[i];
  t->commands++;
}

static inline void mbRtuPortDrop(mbRtuTestPort_t *t, unsigned why) { t->drops[why]++; }

#endif /* MB_RTU_PORT_H */
