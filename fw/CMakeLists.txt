#THIS FILE IS AUTO GENERATED FROM THE TEMPLATE! DO NOT CHANGE!
set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_VERSION 1)
cmake_minimum_required(VERSION 3.30)

# specify cross-compilers and tools
set(CMAKE_C_COMPILER arm-none-eabi-gcc)
set(CMAKE_CXX_COMPILER arm-none-eabi-g++)
set(CMAKE_ASM_COMPILER  arm-none-eabi-gcc)
set(CMAKE_AR arm-none-eabi-ar)
set(CMAKE_OBJCOPY arm-none-eabi-objcopy)
set(CMAKE_OBJDUMP arm-none-eabi-objdump)
set(SIZE arm-none-eabi-size)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

# project settings
project(reflex-fw C CXX ASM)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_C_STANDARD 11)

#Uncomment for hardware floating point
add_compile_definitions(ARM_MATH_CM4;ARM_MATH_MATRIX_CHECK;ARM_MATH_ROUNDING)
add_compile_options(-mfloat-abi=hard -mfpu=fpv4-sp-d16)
add_link_options(-mfloat-abi=hard -mfpu=fpv4-sp-d16)

#Uncomment for software floating point
#add_compile_options(-mfloat-abi=soft)

add_compile_options(-mcpu=cortex-m4 -mthumb -mthumb-interwork)
add_compile_options(-ffunction-sections -fdata-sections -fno-common -fmessage-length=0)

# uncomment to mitigate c++17 absolute addresses warnings
#set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wno-register")

# Enable assembler files preprocessing
add_compile_options($<$<COMPILE_LANGUAGE:ASM>:-x$<SEMICOLON>assembler-with-cpp>)

if ("${CMAKE_BUILD_TYPE}" STREQUAL "Release")
    message(STATUS "Maximum optimization for speed")
    add_compile_options(-Ofast)
elseif ("${CMAKE_BUILD_TYPE}" STREQUAL "RelWithDebInfo")
    message(STATUS "Maximum optimization for speed, debug info included")
    add_compile_options(-Ofast -g)
elseif ("${CMAKE_BUILD_TYPE}" STREQUAL "MinSizeRel")
    message(STATUS "Maximum optimization for size")
    add_compile_options(-Os)
else ()
    message(STATUS "Minimal optimization, debug info included")
    add_compile_options(-Og -g)
endif ()

include_directories(Core/Inc Drivers/STM32F4xx_HAL_Driver/Inc Drivers/STM32F4xx_HAL_Driver/Inc/Legacy Middlewares/Third_Party/FreeRTOS/Source/include Middlewares/Third_Party/FreeRTOS/Source/CMSIS_RTOS_V2 Middlewares/Third_Party/FreeRTOS/Source/portable/GCC/ARM_CM4F Drivers/CMSIS/Device/ST/STM32F4xx/Include Drivers/CMSIS/Include)

add_definitions(-DDEBUG -DUSE_HAL_DRIVER -DSTM32F411xE)

file(GLOB_RECURSE SOURCES "Core/*.*" "Middlewares/*.*" "Drivers/*.*")

# WHERE THE APP IS LINKED. The default is the legacy layout -- the app owns
# the whole flash from 0x08000000, no bootloader, no image header -- and the
# scripts/flash.sh flow is untouched by it. The other supported value is the
# bootloader's RUN slot: sector 5 at 0x08020000, image header at +0x200, VTOR
# set, and reflex-fw.bin patched post-build with its length and CRC32
# (scripts/reflex_image.py). See Core/Inc/els_identity.h and
# docs/decisions/els-modbus-register-map.md. Configure a SEPARATE build
# directory per value, the way diagnostic builds are kept apart.
set(REFLEX_APP_BASE "0x08000000" CACHE STRING
    "Flash address the app is linked at: 0x08000000 (legacy, no bootloader) or 0x08020000 (bootloader RUN slot)")
if (REFLEX_APP_BASE STREQUAL "0x08000000")
    set(LINKER_SCRIPT ${CMAKE_SOURCE_DIR}/STM32F411CEUX_FLASH.ld)
    set(REFLEX_SLOTTED OFF)
elseif (REFLEX_APP_BASE STREQUAL "0x08020000")
    set(LINKER_SCRIPT ${CMAKE_SOURCE_DIR}/STM32F411CEUX_APP.ld)
    set(REFLEX_SLOTTED ON)
    add_compile_definitions(REFLEX_APP_BASE=${REFLEX_APP_BASE} REFLEX_IMAGE_HEADER=1)
    find_program(REFLEX_PYTHON3 NAMES python3 python)
    if (NOT REFLEX_PYTHON3)
        message(FATAL_ERROR "REFLEX_APP_BASE=${REFLEX_APP_BASE} needs python3 to patch the image header (scripts/reflex_image.py)")
    endif()
    message(STATUS "Slotted build: app at ${REFLEX_APP_BASE} with image header")
else()
    message(FATAL_ERROR "REFLEX_APP_BASE must be 0x08000000 (legacy) or 0x08020000 (RUN slot), got ${REFLEX_APP_BASE}")
endif()

# Build identity for the Modbus identity window (Core/Inc/els_identity.h):
# regenerated on EVERY build, from git, into the build tree.
set(REFLEX_BUILD_REV_H ${PROJECT_BINARY_DIR}/generated/reflex_build_rev.h)
add_custom_target(reflex_build_rev
    COMMAND ${CMAKE_COMMAND} -DOUT=${REFLEX_BUILD_REV_H} -DSRC=${CMAKE_SOURCE_DIR}
            -P ${CMAKE_SOURCE_DIR}/cmake/BuildRev.cmake
    BYPRODUCTS ${REFLEX_BUILD_REV_H}
    COMMENT "Recording git rev for the identity window")
include_directories(${PROJECT_BINARY_DIR}/generated)

add_link_options(-Wl,-gc-sections,--print-memory-usage,-Map=${PROJECT_BINARY_DIR}/${PROJECT_NAME}.map)
add_link_options(-mcpu=cortex-m4 -mthumb -mthumb-interwork)
add_link_options(-T ${LINKER_SCRIPT})

add_executable(${PROJECT_NAME}.elf ${SOURCES} ${LINKER_SCRIPT})
add_dependencies(${PROJECT_NAME}.elf reflex_build_rev)

set(HEX_FILE ${PROJECT_BINARY_DIR}/${PROJECT_NAME}.hex)
set(BIN_FILE ${PROJECT_BINARY_DIR}/${PROJECT_NAME}.bin)

if (REFLEX_SLOTTED)
    # The .bin is the image: header patched in place, then re-read and
    # validated by the script. The .hex is made FROM the patched .bin so it
    # carries the same header; the ELF still holds the zero placeholders and
    # must never be what gets programmed into the RUN slot.
    add_custom_command(TARGET ${PROJECT_NAME}.elf POST_BUILD
            COMMAND ${CMAKE_OBJCOPY} -Obinary $<TARGET_FILE:${PROJECT_NAME}.elf> ${BIN_FILE}
            COMMAND ${REFLEX_PYTHON3} ${CMAKE_SOURCE_DIR}/scripts/reflex_image.py patch ${BIN_FILE}
            COMMAND ${CMAKE_OBJCOPY} -I binary -O ihex --change-addresses ${REFLEX_APP_BASE} ${BIN_FILE} ${HEX_FILE}
            COMMENT "Building ${BIN_FILE} with image header, ${HEX_FILE} from it")
else()
    add_custom_command(TARGET ${PROJECT_NAME}.elf POST_BUILD
            COMMAND ${CMAKE_OBJCOPY} -Oihex $<TARGET_FILE:${PROJECT_NAME}.elf> ${HEX_FILE}
            COMMAND ${CMAKE_OBJCOPY} -Obinary $<TARGET_FILE:${PROJECT_NAME}.elf> ${BIN_FILE}
            COMMENT "Building ${HEX_FILE}
Building ${BIN_FILE}")
endif()
