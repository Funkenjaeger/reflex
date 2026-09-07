/*
 * image_header.c -- the application image header the bootloader validates.
 *
 * Only present in a slotted build (REFLEX_APP_BASE != 0x08000000, which
 * defines REFLEX_IMAGE_HEADER); the legacy build has no bootloader to read
 * it and no .image_header output section to put it in. The APP linker
 * script pins the section at ELS_IMAGE_HEADER_OFFSET from the slot base.
 *
 * imageLength and crc32 are PLACEHOLDERS here. They depend on the final
 * binary, so scripts/reflex_image.py patches them into reflex-fw.bin as a
 * post-build step and re-reads the result to prove the patch landed. The
 * ELF is therefore NOT a valid image; flash the .bin (or the .hex made from
 * it), never the ELF, into the RUN slot.
 */
#ifdef REFLEX_IMAGE_HEADER

#include "els_identity.h"

__attribute__((section(".image_header"), used))
const elsImageHeader_t reflexImageHeader = {
  .magic         = ELS_IMAGE_MAGIC,
  .headerVersion = ELS_IMAGE_HEADER_VERSION,
  .flags         = (REFLEX_BUILD_DIRTY) ? ELS_IMAGE_FLAG_DIRTY : 0u,
  .imageLength   = 0u,      /* patched by scripts/reflex_image.py */
  .crc32         = 0u,      /* patched by scripts/reflex_image.py */
  .buildRev      = REFLEX_BUILD_REV,
  .reserved      = { 0u, 0u, 0u },
};

#endif /* REFLEX_IMAGE_HEADER */
