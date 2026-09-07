# reflex ELS Modbus field bootloader -- its own CMake project, built apart
# from the app so neither can pick up the other's flags by accident.
#
#   cmake -S bootloader -B bootloader/build -DCMAKE_BUILD_TYPE=MinSizeRel
#   cmake --build bootloader/build
#
# Outputs bootloader/build/reflex-bl.elf / .bin / .hex, linked into sector 0
# (16 KB). The link FAILS if the image does not fit; that is the budget.
#
# Bare metal: no HAL, no FreeRTOS, soft float (the FPU is off at reset and
# this program never turns it on). Shares with the app exactly two headers,
# Core/Inc/els_identity.h and Core/Inc/modbus_window.h, plus the CMSIS
# device headers and the startup file.
set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_VERSION 1)
cmake_minimum_required(VERSION 3.30)

set(CMAKE_C_COMPILER arm-none-eabi-gcc)
set(CMAKE_ASM_COMPILER arm-none-eabi-gcc)
set(CMAKE_OBJCOPY arm-none-eabi-objcopy)
set(CMAKE_SIZE arm-none-eabi-size)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

project(reflex-bl C ASM)
set(CMAKE_C_STANDARD 11)

set(FW_DIR ${CMAKE_SOURCE_DIR}/..)

add_compile_options(-mcpu=cortex-m4 -mthumb -mfloat-abi=soft)
add_compile_options(-ffunction-sections -fdata-sections -fno-common -fmessage-length=0)
add_compile_options(-Wall -Wextra -Werror=implicit-function-declaration)
add_compile_options($<$<COMPILE_LANGUAGE:ASM>:-x$<SEMICOLON>assembler-with-cpp>)

if (NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE MinSizeRel)
endif()
if ("${CMAKE_BUILD_TYPE}" STREQUAL "Debug")
    add_compile_options(-Og -g)
else()
    add_compile_options(-Os)
endif()

add_definitions(-DSTM32F411xE -DBL_TARGET=1)

include_directories(
    ${CMAKE_SOURCE_DIR}/core
    ${CMAKE_SOURCE_DIR}/src
    ${FW_DIR}/Core/Inc
    ${FW_DIR}/Drivers/CMSIS/Device/ST/STM32F4xx/Include
    ${FW_DIR}/Drivers/CMSIS/Include
    ${PROJECT_BINARY_DIR}/generated
)

# Build identity for the identity window, same generator as the app.
set(REFLEX_BUILD_REV_H ${PROJECT_BINARY_DIR}/generated/reflex_build_rev.h)
add_custom_target(reflex_bl_build_rev
    COMMAND ${CMAKE_COMMAND} -DOUT=${REFLEX_BUILD_REV_H} -DSRC=${FW_DIR}
            -P ${FW_DIR}/cmake/BuildRev.cmake
    BYPRODUCTS ${REFLEX_BUILD_REV_H}
    COMMENT "Recording git rev for the bootloader identity window")

set(LINKER_SCRIPT ${CMAKE_SOURCE_DIR}/STM32F411CEUX_BL.ld)

add_link_options(-mcpu=cortex-m4 -mthumb -mfloat-abi=soft)
add_link_options(-Wl,--gc-sections,--print-memory-usage,-Map=${PROJECT_BINARY_DIR}/${PROJECT_NAME}.map)
add_link_options(--specs=nano.specs --specs=nosys.specs)
add_link_options(-T ${LINKER_SCRIPT})

add_executable(${PROJECT_NAME}.elf
    src/main.c
    src/bl_hw.c
    core/bl_core.c
    core/bl_image.c
    core/bl_state.c
    core/bl_modbus.c
    core/bl_rxring.c
    ${FW_DIR}/Core/Startup/startup_stm32f411ceux.s
    ${LINKER_SCRIPT})
add_dependencies(${PROJECT_NAME}.elf reflex_bl_build_rev)

set(HEX_FILE ${PROJECT_BINARY_DIR}/${PROJECT_NAME}.hex)
set(BIN_FILE ${PROJECT_BINARY_DIR}/${PROJECT_NAME}.bin)
add_custom_command(TARGET ${PROJECT_NAME}.elf POST_BUILD
        COMMAND ${CMAKE_OBJCOPY} -Oihex $<TARGET_FILE:${PROJECT_NAME}.elf> ${HEX_FILE}
        COMMAND ${CMAKE_OBJCOPY} -Obinary $<TARGET_FILE:${PROJECT_NAME}.elf> ${BIN_FILE}
        COMMAND ${CMAKE_SIZE} $<TARGET_FILE:${PROJECT_NAME}.elf>
        COMMENT "Building ${HEX_FILE} and ${BIN_FILE}")
