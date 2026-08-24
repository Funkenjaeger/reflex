import struct
from typing import Optional, List, Any

from keke import ktrace, kev

from pydantic import BaseModel
from kivy.logger import Logger
log = Logger.getChild(__name__)


class TypeDefinition(BaseModel):
    name: str
    length: int
    read_function: Any
    write_function: Optional[Any]
    struct_unpack_string: str


class VariableDefinition(BaseModel):
    name: str
    address: int
    type: TypeDefinition
    count: int = 1


class BaseDevice:
    definition = ""
    root_structure = False

    # Registers per FC3 request in refresh(). Raised 32 -> 64 on 2026-08-23.
    #
    # WHAT THE FIRMWARE CAN SURVIVE. An FC3 response is 5 + 2N bytes (address,
    # function, byte-count, 2N of data, 2 of CRC) and it is assembled in a
    # 256-byte buffer -- `uint8_t u8Buffer[MAX_BUFFER]`, MAX_BUFFER 256, in
    # fw/Core/Inc/Modbus.h and ModbusConfig.h. That alone puts the ceiling at
    # N = 125 (5 + 250 = 255). Two details in `process_FC3`
    # (fw/Core/Src/Modbus.c) say do not go anywhere near it:
    #
    #   * the copy loop writes `u8Buffer[u8BufferSize++]` twice per register
    #     with NO bounds check at all, so overrunning the buffer corrupts
    #     whatever follows it in RAM rather than returning an error; and
    #   * `u8BufferSize` is itself a `uint8_t`, so the response length wraps
    #     silently one byte past 255 -- and the byte-count field the firmware
    #     writes into `u8Buffer[2]` is a single byte too, wrapping at N = 128.
    #     `u8regsno` truncates the REQUESTED count to a uint8_t before any of
    #     this, so a too-large request is not even reliably too-large.
    #
    # N = 125 therefore lands exactly on both edges with zero slack, and every
    # way of exceeding it fails silently. Nothing here may change the firmware,
    # so the only safe move is to stay well short of it.
    #
    # WHY 64. It is half the ceiling to within the rounding: 5 + 2*64 = 133
    # bytes, leaving 123 of the 256-byte buffer unused, 122 counts before the
    # length field wraps, and 61 registers clear of the cliff at 125. It is
    # also where the win is. The two blocks actually read are fastData (30
    # registers, already one request at 32) and elsStop (122 registers, four
    # requests at 32 and two at 64). Going higher buys nothing: 96 and 122
    # still need two requests for elsStop, and each of them spends headroom
    # for it. Going below 61 puts elsStop back to three.
    #
    # THE QUANTITY BEING MINIMISED IS REQUESTS, NOT BYTES. Each request is an
    # independent chance for the firmware to miss its answering window while
    # the 100 kHz ISR is saturated -- which is how six of six cuts lost comms
    # on 2026-08-23, every drop a timeout at the transition into `cutting` and
    # not one a corrupted frame. This trades more bytes for fewer exchanges on
    # purpose.
    MAX_REGISTERS_PER_READ = 64

    def __init__(self, connection_manager, base_address=0):
        from reflex.utils.communication import ConnectionManager
        self.base_address = base_address
        self.size = 0
        self.struct_unpack_string = ""
        self.fast_data = dict()
        self.dm: ConnectionManager = connection_manager
        self.variables: List[VariableDefinition or BaseDevice] = []
        self._variable_index: dict[str, VariableDefinition] = {}
        self._sub_device_cache: dict[tuple, "BaseDevice"] = {}
        self.parse_addresses_from_definition()

    def __getitem__(self, key):
        var = self._variable_index[key]

        if var.count > 1:
            list_type = list()
            for i in range(var.count):
                list_type.append(
                    var.type.read_function(self.dm, var.address + self.base_address + var.type.length * i)
                )
            return list_type
        else:
            return var.type.read_function(self.dm, var.address + self.base_address)

    def __setitem__(self, key, value):
        var = self._variable_index[key]
        var.type.write_function(self.dm, var.address + self.base_address, value, key)
        return

    @classmethod
    def register_type(cls, variable_definitions) -> TypeDefinition:
        current_address = 0
        size = 0
        name = None
        struct_unpack_string = ""
        for line in cls.definition.split(sep="\n"):
            tokens = [item for item in line.split(" ") if len(item) > 0]
            tokens = [item.replace(";", "") for item in tokens]
            tokens = [item.replace("*", "") for item in tokens]

            # Skip lines that don't represent a type definition
            if "typedef" in tokens:
                continue
            if "}" in tokens:
                name = tokens[1]
                continue
            if "{" in tokens:
                continue
            if len(tokens) == 0:
                continue

            # Find type match
            identified_type = tokens[0]
            identified_name = "".join(tokens[1:])

            matching_type = [
                item
                for item in variable_definitions
                if item.name == identified_type
            ][0]

            # Handle multi var definition separated by comma
            # `size` MUST be updated in every branch, not just the scalar one
            # below. It is the reported length of the type, and until 2026-08-14
            # the comma and array branches advanced `current_address` and fell
            # through without touching it -- so a struct whose LAST member was an
            # array under-reported its own size by exactly that array.
            #
            # Nothing caught it because every struct here happened to end on a
            # scalar, which set `size` to the correct running total on the way
            # past. elsStop_t's diagnostic scratchpad ends with two arrays and
            # took the reported size of rampsSharedData_t from 432 to 320,
            # silently truncating the register map by 112 bytes.
            if "," in identified_name:
                for name in identified_name.replace(" ", "").split(","):
                    current_address = current_address + matching_type.length
                    struct_unpack_string += matching_type.struct_unpack_string
                    size = current_address
                continue

            # Handle array definition
            if "[" in identified_name:
                name, count = identified_name.split("[")
                count, _ = count.split("]")
                count = int(count)

                current_address += matching_type.length * count
                struct_unpack_string += matching_type.struct_unpack_string * count
                size = current_address
                continue

            current_address = current_address + matching_type.length
            struct_unpack_string += matching_type.struct_unpack_string
            size = current_address

        if name is None:
            raise ValueError("Unable to identify the typedef name from the provided definition")

        return TypeDefinition(
            name=name,
            length=size,
            struct_unpack_string=struct_unpack_string,
            read_function=cls,
            write_function=cls,
        )

    def parse_addresses_from_definition(self):
        current_address = 0
        self.struct_unpack_string = ""
        self.variables = []
        for line in self.definition.split(sep="\n"):
            tokens = [item for item in line.split(" ") if len(item) > 0]
            tokens = [item.replace(";", "") for item in tokens]
            tokens = [item.replace("*", "") for item in tokens]

            # Skip lines that don't represent a type definition
            if "typedef" in tokens:
                continue
            if "}" in tokens:
                continue
            if "{" in tokens:
                continue
            if len(tokens) == 0:
                continue

            # Find type match
            try:
                identified_type = tokens[0]
                identified_name = "".join(tokens[1:])

                matching_type = [
                    item
                    for item in self.dm.definitions
                    if item.name == identified_type
                ][0]

                # Handle multi var definition separated by comma
                if "," in identified_name:
                    for name in identified_name.replace(" ", "").split(","):
                        self.variables.append(VariableDefinition(
                            name=name,
                            address=current_address,
                            type=matching_type
                        ))
                        current_address += matching_type.length
                        self.struct_unpack_string += matching_type.struct_unpack_string
                    continue

                # Handle array definition
                if "[" in identified_name:
                    name, count = identified_name.split("[")
                    count, _ = count.split("]")
                    count = int(count)

                    self.variables.append(VariableDefinition(
                        name=name,
                        address=current_address,
                        type=matching_type,
                        count=count
                    ))
                    current_address += matching_type.length * count
                    self.struct_unpack_string += matching_type.struct_unpack_string * count
                    continue

                self.variables.append(VariableDefinition(
                    name=identified_name,
                    address=current_address,
                    type=matching_type
                ))
                current_address += matching_type.length
                self.struct_unpack_string += matching_type.struct_unpack_string

            except Exception as e:
                raise ValueError(f"Unable to find a matching type for: {tokens[0]}: {str(e)}") from e

        self._variable_index = {v.name: v for v in self.variables}
        self.size = current_address

    def _get_sub_device(self, device_class, address: int) -> "BaseDevice":
        cache_key = (device_class, address)
        if cache_key not in self._sub_device_cache:
            self._sub_device_cache[cache_key] = device_class(self.dm, address)
        return self._sub_device_cache[cache_key]

    def set_fast_data(self, values: List):
        self.fast_data = dict()
        sorted_keys: List[VariableDefinition] = sorted(self.variables, key=lambda v: v.address)
        for item in sorted_keys:
            if hasattr(item.type.read_function, "set_fast_data"):
                if item.count > 1:
                    fd_list = list()
                    for i in range(item.count):
                        sub = self._get_sub_device(item.type.read_function, item.address + item.type.length * i)
                        fd_list.append(sub.set_fast_data(values))
                    self.fast_data[item.name] = fd_list
                else:
                    sub = self._get_sub_device(item.type.read_function, item.address)
                    self.fast_data[item.name] = sub.set_fast_data(values)
            else:
                if item.count > 1:
                    fd_list = list()
                    for i in range(item.count):
                        fd_list.append(values.pop(0))

                    self.fast_data[item.name] = fd_list
                else:
                    self.fast_data[item.name] = values.pop(0)

        return self.fast_data

    @ktrace()
    def refresh(self):
        remaining_size = self.size
        max_size = self.MAX_REGISTERS_PER_READ
        raw_data = []
        remaining_address = self.base_address
        with kev("read_registers"):
            while remaining_size > max_size:
                part_data = self.dm.device.read_registers(
                    registeraddress=remaining_address,
                    number_of_registers=max_size
                )
                remaining_size -= max_size
                remaining_address += max_size
                raw_data += part_data

            if remaining_size > 0:
                part_data = self.dm.device.read_registers(
                    registeraddress=remaining_address,
                    number_of_registers=remaining_size
                )
                remaining_address += remaining_size
                raw_data += part_data

        with kev("struct"):
            raw_bytes = struct.pack("<" + "H" * self.size, *raw_data)
            values = list(struct.unpack("<" + self.struct_unpack_string, raw_bytes))
        with kev("set_fast_data"):
            return self.set_fast_data(values)
