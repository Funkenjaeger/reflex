/* Standalone compile check for the generated register layout.
 *
 * Includes ONLY the generated header, so nothing else can satisfy its asserts
 * on its behalf. Every _Static_assert in Ramps_generated.h compares the
 * generator's arithmetic against what the compiler actually lays out; if the
 * schema and the ABI disagree about padding, this translation unit fails to
 * compile and names the field that moved.
 *
 * Built for arm-none-eabi, the ABI that ships. A host build agrees for these
 * scalar types but is not the ABI in question.
 */
#include "../fw/Core/Inc/Ramps_generated.h"

int main(void) { return (int)sizeof(elsStop_t); }
