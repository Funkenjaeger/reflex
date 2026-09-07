/*
 * bl_core.c -- see bl_core.h for the boot sequence; the copy/swap state
 * machine is documented inline at doApply().
 */
#include <string.h>
#include "bl_core.h"
#include "bl_image.h"
#include "bl_state.h"
#include "bl_port.h"

#define COPY_CHUNK_WORDS 64u

uint32_t blCoreReg32(const blCore_t *c, unsigned lo)
{
  return (uint32_t)c->regs[lo] | ((uint32_t)c->regs[lo + 1u] << 16);
}

void blCoreSetReg32(blCore_t *c, unsigned lo, uint32_t v)
{
  c->regs[lo]      = (uint16_t)(v & 0xFFFFu);
  c->regs[lo + 1u] = (uint16_t)(v >> 16);
}

static int slotValid(uint32_t base, elsImageHeader_t *hdr)
{
  return blImageValidate(base, hdr) == ELS_BL_OK;
}

static int runUsable(void)
{
  return blImageValidate(ELS_RUN_SLOT_BASE, (elsImageHeader_t *)0) == ELS_BL_OK &&
         blImageVectorsPlausible(ELS_RUN_SLOT_BASE) == ELS_BL_OK;
}

/* Erase dst's sector and copy `length` bytes from src, verifying by readback
 * and by re-validating the header at dst. Returns ELS_BL_OK or a code. */
static uint16_t copySlot(uint32_t dst, uint32_t src, uint32_t length, uint16_t failCode)
{
  uint32_t words[COPY_CHUNK_WORDS];
  blPortWatchdogKick();
  if (blPortErase(dst) != 0) return ELS_BL_ERR_FLASH_ERASE;
  blPortWatchdogKick();
  for (uint32_t off = 0; off < length; off += COPY_CHUNK_WORDS * 4u) {
    uint32_t n = (length - off) / 4u;
    if (n > COPY_CHUNK_WORDS) n = COPY_CHUNK_WORDS;
    memcpy(words, blPortMap(src + off), n * 4u);
    if (blPortProgram(dst + off, words, n) != 0) return ELS_BL_ERR_FLASH_PROG;
  }
  blPortWatchdogKick();
  if (memcmp(blPortMap(dst), blPortMap(src), length) != 0) return failCode;
  if (!slotValid(dst, (elsImageHeader_t *)0)) return failCode;
  return ELS_BL_OK;
}

static int journal(blCore_t *c, uint8_t state)
{
  if (blStateWrite(state) != 0) return 0;
  c->copyState = state;
  return 1;
}

/* THE COPY/SWAP STATE MACHINE. Entered at `from`, which is the state the
 * journal already holds (an apply writes BACKUP first; a boot-time resume
 * passes whatever it found). Every transition is journaled BEFORE the flash
 * work it describes, so a power loss at any point leaves a record naming
 * the slot that may be torn and the slot that is known good:
 *
 *   BACKUP: RUN -> BACKUP.   RUN intact, STAGING intact, BACKUP may be torn.
 *   COPY:   STAGING -> RUN.  STAGING intact, BACKUP intact, RUN may be torn.
 *   TRIAL:  RUN = new image, unconfirmed. Nothing torn.
 *   REVERT: BACKUP -> RUN.   BACKUP intact, RUN may be torn.
 *   REVERTED / IDLE: nothing in flight.
 *
 * A step that cannot complete falls to the next-safest state rather than
 * leaving the machine in a state whose repair it cannot perform. Returns the
 * final state and stores the blResult code in *result. */
static uint8_t doApply(blCore_t *c, uint8_t from, uint16_t *result)
{
  elsImageHeader_t hdr;
  uint8_t state = from;
  *result = ELS_BL_OK;

  if (state == ELS_BL_STATE_BACKUP) {
    if (slotValid(ELS_RUN_SLOT_BASE, &hdr)) {
      uint16_t r = copySlot(ELS_BACKUP_SLOT_BASE, ELS_RUN_SLOT_BASE, hdr.imageLength,
                            ELS_BL_ERR_BACKUP_FAILED);
      if (r != ELS_BL_OK) {
        /* RUN is untouched, so the safe exit is to abandon the apply. */
        *result = r;
        journal(c, ELS_BL_STATE_IDLE);
        return ELS_BL_STATE_IDLE;
      }
    }
    if (!journal(c, ELS_BL_STATE_COPY)) { *result = ELS_BL_ERR_JOURNAL; return state; }
    state = ELS_BL_STATE_COPY;
  }

  if (state == ELS_BL_STATE_COPY) {
    uint16_t r = ELS_BL_ERR_NOT_STAGED;
    if (slotValid(ELS_STAGING_SLOT_BASE, &hdr)) {
      r = copySlot(ELS_RUN_SLOT_BASE, ELS_STAGING_SLOT_BASE, hdr.imageLength,
                   ELS_BL_ERR_COPY_FAILED);
      if (r == ELS_BL_OK) {
        if (!journal(c, ELS_BL_STATE_TRIAL)) { *result = ELS_BL_ERR_JOURNAL; return state; }
        return ELS_BL_STATE_TRIAL;
      }
    }
    /* STAGING unusable or the copy did not verify: RUN may be torn. Put the
     * previous image back if there is one. */
    *result = r;
    if (slotValid(ELS_BACKUP_SLOT_BASE, (elsImageHeader_t *)0)) {
      if (!journal(c, ELS_BL_STATE_REVERT)) { *result = ELS_BL_ERR_JOURNAL; return state; }
      state = ELS_BL_STATE_REVERT;
    } else {
      journal(c, ELS_BL_STATE_IDLE);
      return ELS_BL_STATE_IDLE;     /* RUN may be torn: runValid decides the jump */
    }
  }

  if (state == ELS_BL_STATE_REVERT) {
    if (slotValid(ELS_BACKUP_SLOT_BASE, &hdr)) {
      uint16_t r = copySlot(ELS_RUN_SLOT_BASE, ELS_BACKUP_SLOT_BASE, hdr.imageLength,
                            ELS_BL_ERR_COPY_FAILED);
      if (r == ELS_BL_OK) {
        if (!journal(c, ELS_BL_STATE_REVERTED)) { *result = ELS_BL_ERR_JOURNAL; return state; }
        return ELS_BL_STATE_REVERTED;
      }
      if (*result == ELS_BL_OK) *result = r;
    }
    journal(c, ELS_BL_STATE_IDLE);
    return ELS_BL_STATE_IDLE;
  }

  return state;
}

/* Does BACKUP hold a valid image that is NOT the one in RUN? Reverting to an
 * identical image would loop forever. */
static int backupIsDifferentAndValid(void)
{
  elsImageHeader_t b, r;
  if (!slotValid(ELS_BACKUP_SLOT_BASE, &b)) return 0;
  if (!slotValid(ELS_RUN_SLOT_BASE, &r)) return 1;
  return b.crc32 != r.crc32 || b.imageLength != r.imageLength;
}

void blCoreInit(blCore_t *c)
{
  memset(c, 0, sizeof *c);
  c->regs[ELS_BL_SLOT] = ELS_BL_SLOT_STAGING;
  blCorePublish(c);
}

void blCorePublish(blCore_t *c)
{
  c->regs[ELS_BL_ACTIVE_SLOT] = ELS_BL_SLOT_RUN;
  c->regs[ELS_BL_ATTEMPTS]    = c->attempts;
  c->regs[ELS_BL_COPY_STATE]  = c->copyState;
  c->regs[ELS_BL_RUN_VALID]   = c->runValid ? 1u : 0u;
}

blBootDecision_t blCoreBoot(blCore_t *c)
{
  uint16_t result = ELS_BL_OK;
  uint32_t req, att;
  int tagged, confirmed;

  c->copyState = blStateRead();

  req = blPortBkpRead(ELS_BKP_REQUEST_IDX);
  if (req == ELS_BOOT_REQ_STAY) {
    blPortBkpWrite(ELS_BKP_REQUEST_IDX, 0u);
    c->stayRequested = 1;
  }

  att = blPortBkpRead(ELS_BKP_ATTEMPTS_IDX);
  tagged = ((att >> 16) == ELS_BOOT_ATTEMPT_TAG);
  c->attempts = tagged ? (uint16_t)(att & 0xFFFFu) : 0u;
  confirmed = tagged && c->attempts == 0u;

  /* 2. finish or abort a copy that was in flight. */
  switch (c->copyState) {
    case ELS_BL_STATE_BACKUP:
    case ELS_BL_STATE_COPY:
    case ELS_BL_STATE_REVERT:
      c->copyState = doApply(c, c->copyState, &result);
      break;
    default:
      break;
  }

  /* 3. the app confirmed the image it was running. */
  if (confirmed && (c->copyState == ELS_BL_STATE_TRIAL || c->copyState == ELS_BL_STATE_REVERTED)) {
    journal(c, ELS_BL_STATE_IDLE);
  }

  c->runValid = runUsable() ? 1u : 0u;

  /* 4. strike-out. */
  if (c->attempts >= ELS_BOOT_MAX_ATTEMPTS) {
    if (c->copyState != ELS_BL_STATE_REVERTED && backupIsDifferentAndValid() &&
        journal(c, ELS_BL_STATE_REVERT)) {
      c->copyState = doApply(c, ELS_BL_STATE_REVERT, &result);
      c->runValid = runUsable() ? 1u : 0u;
      if (c->copyState == ELS_BL_STATE_REVERTED && c->runValid) {
        c->attempts = 0u;           /* the reverted image gets its own three */
      } else {
        c->struckOut = 1;
      }
    } else {
      c->struckOut = 1;
    }
  }

  c->regs[ELS_BL_RESULT] = result;
  if (c->struckOut)                                   c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_STRUCK_OUT;
  else if (c->copyState == ELS_BL_STATE_TRIAL && c->runValid) c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_READY_TO_JUMP;
  else                                                c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_IDLE;
  blCorePublish(c);

  /* 5. */
  if (c->stayRequested || !c->runValid || c->struckOut) return BL_BOOT_STAY;
  return BL_BOOT_JUMP;
}

void blCoreCountAttempt(blCore_t *c)
{
  c->attempts++;
  blPortBkpWrite(ELS_BKP_ATTEMPTS_IDX, ELS_BOOT_ATTEMPTS_WORD(c->attempts));
  blCorePublish(c);
}

/* ---- commands ---------------------------------------------------------- */

static uint16_t cmdErase(blCore_t *c)
{
  if (c->regs[ELS_BL_SLOT] != ELS_BL_SLOT_STAGING) return ELS_BL_ERR_SLOT;
  c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_ERASING;
  c->stagedOk = 0;
  blPortWatchdogKick();
  if (blPortErase(ELS_STAGING_SLOT_BASE) != 0) return ELS_BL_ERR_FLASH_ERASE;
  blPortWatchdogKick();
  return ELS_BL_OK;
}

static uint16_t cmdWrite(blCore_t *c)
{
  uint32_t addr = blCoreReg32(c, ELS_BL_TARGET_ADDR_LO);
  uint32_t len  = c->regs[ELS_BL_WRITE_LEN];
  uint32_t words[ELS_BL_DATA_REGS / 2u];
  if (c->regs[ELS_BL_SLOT] != ELS_BL_SLOT_STAGING) return ELS_BL_ERR_SLOT;
  if (len == 0u || len > ELS_BL_DATA_REGS * 2u || (len & 3u) != 0u) return ELS_BL_ERR_WRITE_LEN;
  if ((addr & 3u) != 0u || addr < ELS_STAGING_SLOT_BASE ||
      addr + len > ELS_STAGING_SLOT_BASE + ELS_SLOT_SIZE) return ELS_BL_ERR_ADDR_RANGE;
  c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_WRITING;
  c->stagedOk = 0;
  /* blData is little-endian register pairs: reg[2i] is the low half of word i. */
  for (uint32_t i = 0; i < len / 4u; i++) {
    words[i] = (uint32_t)c->regs[ELS_BL_DATA + 2u * i] |
               ((uint32_t)c->regs[ELS_BL_DATA + 2u * i + 1u] << 16);
  }
  if (blPortProgram(addr, words, len / 4u) != 0) return ELS_BL_ERR_FLASH_PROG;
  if (memcmp(blPortMap(addr), words, len) != 0) return ELS_BL_ERR_FLASH_VERIFY;
  return ELS_BL_OK;
}

static uint16_t cmdVerify(blCore_t *c)
{
  elsImageHeader_t hdr;
  uint16_t r;
  if (c->regs[ELS_BL_SLOT] != ELS_BL_SLOT_STAGING) return ELS_BL_ERR_SLOT;
  c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_VERIFYING;
  c->stagedOk = 0;
  blPortWatchdogKick();
  r = blImageValidate(ELS_STAGING_SLOT_BASE, &hdr);
  if (r != ELS_BL_OK) return r;
  if (hdr.imageLength != blCoreReg32(c, ELS_BL_IMAGE_LEN_LO)) return ELS_BL_ERR_HOST_LEN;
  if (hdr.crc32 != blCoreReg32(c, ELS_BL_IMAGE_CRC_LO))       return ELS_BL_ERR_HOST_CRC;
  c->stagedOk = 1;
  return ELS_BL_OK;
}

static uint16_t cmdApply(blCore_t *c)
{
  uint16_t result;
  if (!c->stagedOk) return ELS_BL_ERR_NOT_STAGED;
  c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_APPLYING;
  if (blStateEnsureRoom(BL_STATE_APPLY_RECORDS) != 0) return ELS_BL_ERR_JOURNAL;
  if (!journal(c, ELS_BL_STATE_BACKUP)) return ELS_BL_ERR_JOURNAL;
  c->copyState = doApply(c, ELS_BL_STATE_BACKUP, &result);
  c->runValid = runUsable() ? 1u : 0u;
  c->stagedOk = 0;
  if (result == ELS_BL_OK && c->copyState != ELS_BL_STATE_TRIAL) result = ELS_BL_ERR_COPY_FAILED;
  return result;
}

static uint16_t cmdJump(blCore_t *c)
{
  c->runValid = runUsable() ? 1u : 0u;
  if (!c->runValid) return ELS_BL_ERR_NO_RUN_IMAGE;
  /* An explicit JUMP from the host is a fresh start for the counter. */
  c->attempts = 0u;
  c->struckOut = 0;
  c->jumpPending = 1;
  return ELS_BL_OK;
}

void blCoreService(blCore_t *c)
{
  uint16_t cmd = c->regs[ELS_BL_COMMAND];
  uint16_t result;
  if (cmd == ELS_BL_CMD_NONE) return;
  c->regs[ELS_BL_COMMAND] = ELS_BL_CMD_NONE;      /* consumed */

  switch (cmd) {
    case ELS_BL_CMD_ERASE:  result = cmdErase(c);  break;
    case ELS_BL_CMD_WRITE:  result = cmdWrite(c);  break;
    case ELS_BL_CMD_VERIFY: result = cmdVerify(c); break;
    case ELS_BL_CMD_APPLY:  result = cmdApply(c);  break;
    case ELS_BL_CMD_JUMP:   result = cmdJump(c);   break;
    case ELS_BL_CMD_STAY:   result = ELS_BL_OK;    break;
    default:                result = ELS_BL_ERR_BAD_COMMAND; break;
  }

  /* Settle blStatus from the outcome. */
  if (c->struckOut)                                  c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_STRUCK_OUT;
  else if (cmd == ELS_BL_CMD_VERIFY && result != ELS_BL_OK) c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_BAD_IMAGE;
  else if (c->stagedOk)                              c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_STAGED;
  else if (c->copyState == ELS_BL_STATE_TRIAL && c->runValid) c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_READY_TO_JUMP;
  else                                               c->regs[ELS_BL_STATUS] = ELS_BL_STATUS_IDLE;

  /* RESULT BEFORE SEQ. The host edge-detects blSeq and then reads blResult;
   * writing them in this order (and blSeq sitting at the lower address)
   * is what makes a torn FC3 read come out as (stale seq, new result). */
  c->regs[ELS_BL_RESULT] = result;
  c->regs[ELS_BL_SEQ]++;
  blCorePublish(c);
}
