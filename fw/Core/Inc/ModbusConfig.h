/*
 * ModbusConfig.h
 *
 *  Created on: Apr 28, 2021
 *      Author: Alejandro Mera
 *
 *  This is a template for the Modbus library configuration.
 *  Every project needs a tailored copy of this file renamed to ModbusConfig.h, and added to the include path.
 */
/*
 * From Alejandro Mera's Modbus-STM32-HAL-FreeRTOS library
 * (https://github.com/alejoseb/Modbus-STM32-HAL-FreeRTOS), itself adapted from
 * https://github.com/smarmengol/Modbus-Master-Slave-for-Arduino. Both are
 * licensed under the GNU Lesser General Public License version 2.1, and so is
 * this file; the full text is fw/Modbus-LGPL-2.1.txt. The rest of the firmware
 * is MIT (fw/LICENSE).
 *
 * Tailored from upstream's ModbusConfigTemplate.h in rotary-controller-f4 (2022-2024)
 * and Reflex (2026).
 * The git history of this repository records each change and its date.
 */

#ifndef THIRD_PARTY_MODBUS_LIB_CONFIG_MODBUSCONFIG_H_
#define THIRD_PARTY_MODBUS_LIB_CONFIG_MODBUSCONFIG_H_



/* Uncomment the following line to enable support for Modbus RTU over USB CDC profile. Only tested for BluePill f103 board. */
//#define ENABLE_USB_CDC 1

/* Uncomment the following line to enable support for Modbus TCP. Only tested for Nucleo144-F429ZI. */
//#define ENABLE_TCP 1

/* Uncomment the following line to enable support for Modbus RTU USART DMA mode. Only tested for Nucleo144-F429ZI.  */
#define ENABLE_USART_DMA 1


/* T35: end-of-frame silence, in FreeRTOS ticks (configTICK_RATE_HZ 1000, so
 * ms). It is a software timer reset from the RX interrupt on every received
 * byte (UARTCallback.c, USART_HW mode), and FreeRTOS dates that reset to the
 * tick count at the moment of the reset, so the frame is declared over
 * between T35-1 and T35 ms after its last byte. 2 therefore means at least
 * 1 ms of silence, about 11.5 character times at 115200 baud. 1 would allow
 * a silence of almost nothing and must not be used.
 *
 * This is shorter than the 1.75 ms the Modbus RTU specification sets above
 * 19200 baud. The cost is that a pause of 1-2 ms INSIDE a request can now
 * split it, and the pieces fail their size or CRC check and are dropped,
 * where 5 tolerated a pause of up to 4 ms. The gain is that every reply
 * starts 3 ms sooner: at 5, the reply waited 4-5 ms after each request.
 *
 * Only the application uses this; the bootloader detects frames from the
 * USART's IDLE flag and does not include this file. The emulator compiles
 * Modbus.c with this header, so it runs at 2 ms as well. */
#define T35  2              // Timer T35 period (in ticks) for end frame detection.
#define MAX_BUFFER  256	    // Maximum size for the communication buffer in bytes.
#define MB_MAX_WINDOWS 2    // Auxiliary register windows per handler (modbus_window.h).
#define TIMEOUT_MODBUS 1000 // Timeout for master query (in ticks)
#define MAX_M_HANDLERS 2    //Maximum number of modbus handlers that can work concurrently
#define MAX_TELEGRAMS 2     //Max number of Telegrams in master queue

#if ENABLE_TCP == 1
#define NUMBERTCPCONN   4   // Maximum number of simultaneous client connections, it should be equal or less than LWIP configuration
#define TCPAGINGCYCLES	1000 // Number of times the server will check for a incoming request before closing the connection for inactivity
/* Note: the total aging time for a connection is approximately NUMBERTCPCONN*TCPAGINGCYCLES*u16timeOut ticks
*/
#endif




#endif /* THIRD_PARTY_MODBUS_LIB_CONFIG_MODBUSCONFIG_H_ */
