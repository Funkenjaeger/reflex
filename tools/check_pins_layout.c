/* Standalone compile check for one board's generated pinout header.
 *
 * Includes the real HAL alongside fw/boards/provvedo/pins.h and references
 * every BOARD_* macro it emits, so a port/pin that does not actually exist in
 * the target device headers (a typo in the export, a stale macro after a
 * board respin) fails to compile here rather than silently linking.
 *
 * Nothing includes pins.h yet -- the per-board firmware build (Open Loops
 * 6aa6c3b5) is what wires Ramps.h to consume it -- so this file, like its
 * sibling tools/check_generated_layout.c for the register map, is the only
 * place today that proves the header is real against the actual device
 * headers rather than merely self-consistent. tools/test_genpins_seen_red.py
 * exercises the same compile with the current toolchain (cross if available,
 * else host gcc, see its docstring) and is what actually runs in CI; this
 * file is for compiling by hand against a specific board when working on the
 * generator.
 *
 * PROVVEDO-SPECIFIC: hand-lists provvedo's macros, so it needs updating if
 * fw/boards/provvedo/pins.h's named nets change. tools/test_genpins_seen_red.py
 * does not have this problem -- it derives the macro list from pins.json.
 *
 * Built for arm-none-eabi normally; falls back to host gcc, which agrees for
 * these scalar/pointer macros even though it is not the ABI that ships.
 */
#include "stm32f4xx_hal.h"
#include "../fw/boards/provvedo/pins.h"

int main(void) {
  unsigned long x = 0;
  x += (unsigned long)BOARD_OSCIN_PORT      + BOARD_OSCIN_PIN      + BOARD_OSCIN_PIN_NUM;
  x += (unsigned long)BOARD_OSCOUT_PORT     + BOARD_OSCOUT_PIN     + BOARD_OSCOUT_PIN_NUM;
  x += (unsigned long)BOARD_MTR_STEP_PORT   + BOARD_MTR_STEP_PIN   + BOARD_MTR_STEP_PIN_NUM;
  x += (unsigned long)BOARD_SPARE_1_PORT    + BOARD_SPARE_1_PIN    + BOARD_SPARE_1_PIN_NUM;
  x += (unsigned long)BOARD_SPARE_2_PORT    + BOARD_SPARE_2_PIN    + BOARD_SPARE_2_PIN_NUM;
  x += (unsigned long)BOARD_SPARE_3_PORT    + BOARD_SPARE_3_PIN    + BOARD_SPARE_3_PIN_NUM;
  x += (unsigned long)BOARD_ENC2A_PORT      + BOARD_ENC2A_PIN      + BOARD_ENC2A_PIN_NUM;
  x += (unsigned long)BOARD_ENC3A_PORT      + BOARD_ENC3A_PIN      + BOARD_ENC3A_PIN_NUM;
  x += (unsigned long)BOARD_ENC3B_PORT      + BOARD_ENC3B_PIN      + BOARD_ENC3B_PIN_NUM;
  x += (unsigned long)BOARD_SPARE_4_PORT    + BOARD_SPARE_4_PIN    + BOARD_SPARE_4_PIN_NUM;
  x += (unsigned long)BOARD_USR_LED_PORT    + BOARD_USR_LED_PIN    + BOARD_USR_LED_PIN_NUM;
  x += (unsigned long)BOARD_MTR_DIR_PORT    + BOARD_MTR_DIR_PIN    + BOARD_MTR_DIR_PIN_NUM;
  x += (unsigned long)BOARD_MTR_ENA_PORT    + BOARD_MTR_ENA_PIN    + BOARD_MTR_ENA_PIN_NUM;
  x += (unsigned long)BOARD_ENC1A_PORT      + BOARD_ENC1A_PIN      + BOARD_ENC1A_PIN_NUM;
  x += (unsigned long)BOARD_ENC1B_PORT      + BOARD_ENC1B_PIN      + BOARD_ENC1B_PIN_NUM;
  x += (unsigned long)BOARD_RXD_PORT        + BOARD_RXD_PIN        + BOARD_RXD_PIN_NUM;
  x += (unsigned long)BOARD_TMS_SWDIO_PORT  + BOARD_TMS_SWDIO_PIN  + BOARD_TMS_SWDIO_PIN_NUM;
  x += (unsigned long)BOARD_TCLK_SWCLK_PORT + BOARD_TCLK_SWCLK_PIN + BOARD_TCLK_SWCLK_PIN_NUM;
  x += (unsigned long)BOARD_TXD_PORT        + BOARD_TXD_PIN        + BOARD_TXD_PIN_NUM;
  x += (unsigned long)BOARD_ENC2B_PORT      + BOARD_ENC2B_PIN      + BOARD_ENC2B_PIN_NUM;
  x += (unsigned long)BOARD_ENC4A_PORT      + BOARD_ENC4A_PIN      + BOARD_ENC4A_PIN_NUM;
  x += (unsigned long)BOARD_ENC4B_PORT      + BOARD_ENC4B_PIN      + BOARD_ENC4B_PIN_NUM;
  return (int)x;
}
