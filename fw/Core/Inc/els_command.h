/* The ELS command channel (protocolVersion 13): elsStop.command,
 * elsStop.commandSeq, elsStop.commandResult.
 *
 * The calCommand idiom generalized to the registers that move the carriage
 * (docs/design/els-command-channel.md). The host writes one command id; the
 * firmware consumes and clears it in servoEnableTask, carries it out, writes
 * commandResult, and bumps commandSeq LAST. One command in flight: a command
 * written while another is still waiting for its ack stays in the register
 * and is consumed after that ack.
 *
 * COMMAND AND RESULT VALUES ARE A WIRE CONTRACT once a build carrying them is
 * flashed: append, never renumber. reflex-ui mirrors them in
 * ui/reflex/fsms/els_command.py, and ui/tests/test_registry_contract.py
 * compares the two copies in both directions.
 */
#ifndef ELS_COMMAND_H
#define ELS_COMMAND_H

#include <stdint.h>

/* Command ids, written by the host to elsStop.command. */
#define ELS_CMD_NONE        0u   /* nothing requested; the consumed value */
#define ELS_CMD_DISENGAGE   1u   /* make the machine inert: sync off on every
                                  * scale, servoMode 0, enable 0, the job's
                                  * pending motion cancelled. Legal from
                                  * every mode, never refused */

/* Outcomes, written to elsStop.commandResult before the commandSeq bump.
 * Values below 0x100 are successes; 0x100 and up are refusals, so a host can
 * classify a code it does not know by its range. */
#define ELS_CMD_RESULT_OK              0u
#define ELS_CMD_RESULT_OK_CAL_ABORTED  1u    /* DISENGAGE from CAL: the run
                                              * ended with calResult =
                                              * ELS_CAL_ERR_ABORTED on its
                                              * own calSeq */
#define ELS_CMD_RESULT_ERR_UNKNOWN     256u  /* not a command this firmware
                                              * knows; nothing was done */

#endif /* ELS_COMMAND_H */
