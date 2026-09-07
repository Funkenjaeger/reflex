/*
 * els_identity.h -- THE ONE MAP the bootloader and the application share.
 *
 * Everything in this header is a wire contract or a flash-geometry contract
 * between three parties that are built and flashed at different times: the
 * write-protected bootloader in sector 0, the application in the RUN slot, and
 * the host client (scripts/modbus-flash.py). It is the C rendering of
 * docs/decisions/els-modbus-register-map.md; that record's "Implemented"
 * section is written FROM this file, so change them together.
 *
 * Both stages include this header and nothing else of each other's. The
 * bootloader never sees Ramps.h; the app never sees the bootloader's sources.
 *
 * Never renumber anything here. Append.
 */
#ifndef ELS_IDENTITY_H
#define ELS_IDENTITY_H

#include <stdint.h>

/* -------------------------------------------------------------------------
 * Build identity, injected at build time by cmake/BuildRev.cmake into a
 * generated reflex_build_rev.h. A build without the generated header (a bare
 * compile of one translation unit, an old build tree) reads rev 0, DIRTY 1:
 * "unknown" must never masquerade as a clean, identifiable build.
 * ---------------------------------------------------------------------- */
#if defined(__has_include)
#  if __has_include("reflex_build_rev.h")
#    include "reflex_build_rev.h"
#  endif
#endif
#ifndef REFLEX_BUILD_REV
#  define REFLEX_BUILD_REV   0u
#endif
#ifndef REFLEX_BUILD_DIRTY
#  define REFLEX_BUILD_DIRTY 1u
#endif

/* -------------------------------------------------------------------------
 * IDENTITY WINDOW, ELS_ID_BASE = 2048 (0x0800). Read-only, 8 registers,
 * implemented IDENTICALLY by both stages. A client reads this FIRST, always.
 * ---------------------------------------------------------------------- */
#define ELS_ID_BASE             2048u
#define ELS_ID_SIZE             8u
#define ELS_ID_MAGIC            0x454Cu   /* "EL" */
#define ELS_ID_WINDOW_VERSION   1u
#define ELS_ID_STAGE_BOOTLOADER 1u
#define ELS_ID_STAGE_APP        2u

enum {
  ELS_ID_MAGIC_OFF = 0,          /* 0x454C, proves the window exists       */
  ELS_ID_STAGE_OFF,              /* 1 = bootloader, 2 = application        */
  ELS_ID_WINDOW_VERSION_OFF,     /* layout version of THIS window          */
  ELS_ID_BUILD_REV_LO_OFF,       /* git short rev, low 16 bits             */
  ELS_ID_BUILD_REV_HI_OFF,       /* git short rev, high 16 (top 4 are 0)   */
  ELS_ID_BUILD_DIRTY_OFF,        /* 1 if built from a dirty tree           */
  ELS_ID_APP_PROTOCOL_OFF,       /* the app's protocolVersion; 0 in the BL */
  ELS_ID_RESERVED_OFF
};

/* Static initializer for the window. `stage` is ELS_ID_STAGE_*, `appProto`
 * is ELS_PROTOCOL_VERSION in the app and 0 in the bootloader. */
#define ELS_ID_WINDOW_INIT(stage, appProto) { \
  (uint16_t)ELS_ID_MAGIC, (uint16_t)(stage), (uint16_t)ELS_ID_WINDOW_VERSION, \
  (uint16_t)((REFLEX_BUILD_REV) & 0xFFFFu), \
  (uint16_t)(((REFLEX_BUILD_REV) >> 16) & 0xFFFFu), \
  (uint16_t)((REFLEX_BUILD_DIRTY) ? 1u : 0u), (uint16_t)(appProto), 0u }

/* -------------------------------------------------------------------------
 * BOOTLOADER CONTROL WINDOW, ELS_BL_BASE = 2304 (0x0900). Bootloader only;
 * the app answers an illegal-data-address exception (2) here.
 *
 * ORDERING: blSeq sits at a LOWER address than blResult, the outcome it
 * counts, per the calSeq invariant in Ramps.h (FC3 copies registers in
 * ascending address order, so a torn read comes out as (stale seq, new
 * payload), which edge detection harmlessly re-reads). The decision record's
 * draft table had result at +1 and seq at +2, the inverted order; it is
 * corrected here and in the record's Implemented section.
 * ---------------------------------------------------------------------- */
#define ELS_BL_BASE       2304u
#define ELS_BL_DATA_REGS  100u
#define ELS_BL_SIZE       (16u + ELS_BL_DATA_REGS)   /* 116 registers */

enum {
  ELS_BL_STATUS = 0,        /* RO  ELS_BL_STATUS_*                             */
  ELS_BL_SEQ,               /* RO  increments once per completed operation     */
  ELS_BL_RESULT,            /* RO  ELS_BL_OK / ELS_BL_ERR_* of that operation  */
  ELS_BL_COMMAND,           /* RW  ELS_BL_CMD_*; FIRMWARE CLEARS ON CONSUME    */
  ELS_BL_TARGET_ADDR_LO,    /* RW  32-bit flash address for the next WRITE     */
  ELS_BL_TARGET_ADDR_HI,
  ELS_BL_IMAGE_LEN_LO,      /* RW  host's image length; VERIFY cross-checks it */
  ELS_BL_IMAGE_LEN_HI,
  ELS_BL_IMAGE_CRC_LO,      /* RW  host's image CRC32; VERIFY cross-checks it  */
  ELS_BL_IMAGE_CRC_HI,
  ELS_BL_SLOT,              /* RW  slot ERASE/WRITE/VERIFY act on: STAGING only */
  ELS_BL_ACTIVE_SLOT,       /* RO  slot JUMP boots: always RUN                 */
  ELS_BL_WRITE_LEN,         /* RW  bytes of blData a WRITE programs (4..200, x4) */
  ELS_BL_ATTEMPTS,          /* RO  boot-attempt counter as read at this boot   */
  ELS_BL_COPY_STATE,        /* RO  ELS_BL_STATE_* from the flash journal       */
  ELS_BL_RUN_VALID,         /* RO  1 if the RUN slot passes header + CRC       */
  ELS_BL_DATA = 16          /* RW  blData[100], written with FC 0x10           */
};

/* 32-bit values span two registers, LOW word at the lower address -- the
 * same convention the app's uint32 struct members already have on the wire. */

/* blCommand */
#define ELS_BL_CMD_NONE    0u
#define ELS_BL_CMD_ERASE   1u   /* erase blSlot (STAGING)                         */
#define ELS_BL_CMD_WRITE   2u   /* program blWriteLen bytes of blData at blTargetAddr */
#define ELS_BL_CMD_VERIFY  3u   /* validate the STAGING image header + CRC        */
#define ELS_BL_CMD_APPLY   4u   /* back up RUN, copy STAGING -> RUN, enter TRIAL  */
#define ELS_BL_CMD_JUMP    5u   /* count an attempt, arm IWDG, boot RUN           */
#define ELS_BL_CMD_STAY    6u   /* no-op that acks: stay resident                 */

/* blStatus */
#define ELS_BL_STATUS_IDLE          0u  /* resident, nothing in flight          */
#define ELS_BL_STATUS_ERASING       1u
#define ELS_BL_STATUS_WRITING       2u
#define ELS_BL_STATUS_VERIFYING     3u
#define ELS_BL_STATUS_BAD_IMAGE     4u  /* last VERIFY of STAGING failed        */
#define ELS_BL_STATUS_STAGED        5u  /* STAGING verified; APPLY is allowed   */
#define ELS_BL_STATUS_APPLYING      6u
#define ELS_BL_STATUS_READY_TO_JUMP 7u  /* APPLY done; RUN holds the new image  */
#define ELS_BL_STATUS_STRUCK_OUT    8u  /* 3 attempts failed, nothing to revert to */

/* blResult. 0 is success; every distinguishable failure has its own code,
 * per the ELS_CAL_* precedent. Never a binary fault flag. */
#define ELS_BL_OK                  0u
#define ELS_BL_ERR_BAD_COMMAND     1u   /* unknown blCommand value               */
#define ELS_BL_ERR_SLOT            2u   /* blSlot is not STAGING                 */
#define ELS_BL_ERR_ADDR_RANGE      3u   /* WRITE target outside STAGING / unaligned */
#define ELS_BL_ERR_WRITE_LEN       4u   /* blWriteLen 0, > 200, or not x4        */
#define ELS_BL_ERR_FLASH_ERASE     5u   /* flash controller reported an erase error */
#define ELS_BL_ERR_FLASH_PROG      6u   /* flash controller reported a program error */
#define ELS_BL_ERR_FLASH_VERIFY    7u   /* readback after program did not match  */
#define ELS_BL_ERR_HDR_MAGIC       8u   /* no image header magic in the slot     */
#define ELS_BL_ERR_HDR_VERSION     9u   /* header version this bootloader cannot read */
#define ELS_BL_ERR_HDR_LENGTH     10u   /* header length out of range / not x4   */
#define ELS_BL_ERR_HDR_CRC        11u   /* computed CRC32 != header CRC32        */
#define ELS_BL_ERR_HOST_LEN       12u   /* header length != blImageLen           */
#define ELS_BL_ERR_HOST_CRC       13u   /* header CRC32 != blImageCrc            */
#define ELS_BL_ERR_NOT_STAGED     14u   /* APPLY without a passed VERIFY         */
#define ELS_BL_ERR_NO_RUN_IMAGE   15u   /* JUMP with an invalid RUN slot         */
#define ELS_BL_ERR_VECTORS        16u   /* RUN's MSP / reset vector are implausible */
#define ELS_BL_ERR_JOURNAL        17u   /* could not append to the state journal */
#define ELS_BL_ERR_BACKUP_FAILED  18u   /* RUN -> BACKUP copy did not verify     */
#define ELS_BL_ERR_COPY_FAILED    19u   /* STAGING -> RUN copy did not verify    */

/* Slots */
#define ELS_BL_SLOT_RUN     0u
#define ELS_BL_SLOT_STAGING 1u
#define ELS_BL_SLOT_BACKUP  2u

/* Copy-state machine (blCopyState, and the record kept in the flash journal).
 * The journal is what makes a power loss mid-copy recoverable: on the next
 * boot the bootloader reads the last state and resumes or aborts the copy
 * without ever jumping into a torn image. */
#define ELS_BL_STATE_IDLE     0u  /* nothing pending                                    */
#define ELS_BL_STATE_BACKUP   1u  /* RUN -> BACKUP in progress (BACKUP may be torn)     */
#define ELS_BL_STATE_COPY     2u  /* STAGING -> RUN in progress (RUN may be torn)       */
#define ELS_BL_STATE_TRIAL    3u  /* RUN holds a new image the app has not confirmed    */
#define ELS_BL_STATE_REVERT   4u  /* BACKUP -> RUN in progress (RUN may be torn)        */
#define ELS_BL_STATE_REVERTED 5u  /* fell back to BACKUP; a second strike-out stays resident */

/* -------------------------------------------------------------------------
 * FLASH GEOMETRY, STM32F411CE, 512 KB single bank (RM0383 Table 5).
 * ---------------------------------------------------------------------- */
#define ELS_FLASH_BASE        0x08000000u
#define ELS_BL_SECTOR         0u   /* bootloader, 16 KB, write-protected (WRP)  */
#define ELS_BL_SECTOR_BASE    0x08000000u
#define ELS_BL_SECTOR_SIZE    0x4000u
#define ELS_STATE_SECTOR      1u   /* copy-state journal, 16 KB                 */
#define ELS_STATE_SECTOR_BASE 0x08004000u
#define ELS_STATE_SECTOR_SIZE 0x4000u
#define ELS_RUN_SECTOR        5u   /* the one address the app is linked at      */
#define ELS_RUN_SLOT_BASE     0x08020000u
#define ELS_STAGING_SECTOR    6u   /* the host writes here                      */
#define ELS_STAGING_SLOT_BASE 0x08040000u
#define ELS_BACKUP_SECTOR     7u   /* previous RUN image, for the swap-back     */
#define ELS_BACKUP_SLOT_BASE  0x08060000u
#define ELS_SLOT_SIZE         0x20000u   /* 128 KB, every slot                  */

/* -------------------------------------------------------------------------
 * IMAGE HEADER. Lives INSIDE the image at a fixed offset from the slot base,
 * after the vector table, so the vector table stays at the slot base and
 * VTOR needs no alignment gymnastics (the F411 table is 102 words = 0x198
 * bytes; 0x200 clears it, and the app linker script ASSERTs that).
 *
 * The CRC covers every word of [0, imageLength) with the crc32 field itself
 * taken as 0. It is the STM32 CRC unit's variant: poly 0x04C11DB7, init
 * 0xFFFFFFFF, words fed as little-endian uint32, no reflection, no final
 * XOR. NOT zlib. scripts/reflex_image.py is the host-side implementation.
 * ---------------------------------------------------------------------- */
#define ELS_IMAGE_HEADER_OFFSET  0x200u
#define ELS_IMAGE_HEADER_SIZE    32u
#define ELS_IMAGE_MAGIC          0x584C4652u   /* "RFLX" as little-endian bytes */
#define ELS_IMAGE_HEADER_VERSION 1u
#define ELS_IMAGE_FLAG_DIRTY     0x0001u
#define ELS_IMAGE_MIN_LENGTH     (ELS_IMAGE_HEADER_OFFSET + ELS_IMAGE_HEADER_SIZE)
#define ELS_IMAGE_MAX_LENGTH     ELS_SLOT_SIZE

typedef struct {
  uint32_t magic;          /* ELS_IMAGE_MAGIC                                   */
  uint16_t headerVersion;  /* ELS_IMAGE_HEADER_VERSION                          */
  uint16_t flags;          /* ELS_IMAGE_FLAG_*                                  */
  uint32_t imageLength;    /* bytes from slot base, x4, header included         */
  uint32_t crc32;          /* over [0, imageLength) with this word as 0         */
  uint32_t buildRev;       /* same 28-bit value idBuildRevLo/Hi publish         */
  uint32_t reserved[3];
} elsImageHeader_t;

/* -------------------------------------------------------------------------
 * RTC BACKUP REGISTERS (RTC->BKP0R..). Survive every reset EXCEPT loss of
 * VDD, because this board has no VBAT: a power cycle zeroes them. That is
 * acceptable for the boot-attempt counter (a power cycle grants a fresh
 * three attempts) and for the stay-resident request; the copy-state marker
 * is in flash precisely because it must not have this property.
 * ---------------------------------------------------------------------- */
#define ELS_BKP_ATTEMPTS_IDX  0u
#define ELS_BKP_REQUEST_IDX   1u
/* BKP0R = (TAG << 16) | count. A missing tag means "power cycled since the
 * bootloader last wrote here": count unknown, treated as 0 and NOT as an
 * app confirmation. TAG with count 0 can only be written by the app (the
 * bootloader always writes count >= 1 before jumping), so it IS the app's
 * "Modbus is live" confirmation. */
#define ELS_BOOT_ATTEMPT_TAG   0xB007u
#define ELS_BOOT_MAX_ATTEMPTS  3u
#define ELS_BOOT_ATTEMPTS_WORD(count) ((((uint32_t)ELS_BOOT_ATTEMPT_TAG) << 16) | ((uint32_t)(count) & 0xFFFFu))
/* BKP1R: the app writes this and resets to ask the bootloader to stay
 * resident. The bootloader clears it on consume. */
#define ELS_BOOT_REQ_STAY      0x53544159u   /* "STAY" */

/* The app-side command register (elsStop.bootCommand, protocolVersion 8). */
#define ELS_BOOT_CMD_NONE        0u
#define ELS_BOOT_CMD_BOOTLOADER  1u  /* reboot into the bootloader and stay resident */
#define ELS_BOOT_CMD_RESET       2u  /* plain reboot; the bootloader boots RUN as usual */

#endif /* ELS_IDENTITY_H */
