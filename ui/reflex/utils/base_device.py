import struct
from typing import Optional, List, Any

from keke import ktrace, kev

from pydantic import BaseModel
from kivy.logger import Logger

from reflex.utils.port_owner import Batch, PortOwner, Read, ReadSpan, Write
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


def _is_structure(type_definition: TypeDefinition) -> bool:
    """True for a struct member (its read function is a BaseDevice subclass),
    False for a scalar register field."""
    rf = type_definition.read_function
    return isinstance(rf, type) and issubclass(rf, BaseDevice)


class _PlainWrite:
    """One write to a dict-shaped double: no wire, so it cannot fail.

    Lets a Batch be built over the doubles the suite uses without every one of
    them having to grow a register layer. A double that wants a write REFUSED
    implements write_command itself and returns something whose execute() says
    so.
    """

    def __init__(self, device, key, value):
        self.device, self.key, self.value = device, key, value

    def execute(self, cm):
        self.device[self.key] = self.value
        return True


def write_command(device, key, value):
    """The command one write becomes, for collecting several into a Batch."""
    builder = getattr(device, "write_command", None)
    if builder is None:
        return _PlainWrite(device, key, value)
    return builder(key, value)


def write_confirmed(device, key, value) -> bool:
    """Write `key` on `device` and return the controller's acknowledgement.

    Use this, not `device[key] = value`, wherever the answer GATES something:
    the cut arming, the disengage, the stop offset. A plain assignment is fire
    and forget and tells the caller nothing.

    Falls back to an assignment for the dict-shaped doubles that stand in for a
    device across the suite. A dict write cannot fail, so True is the honest
    answer for one -- and a double that wants to REFUSE a write says so by
    implementing write_confirmed itself, which is how the nack-injection tests
    reach these paths.
    """
    confirm = getattr(device, "write_confirmed", None)
    if confirm is None:
        device[key] = value
        return True
    return bool(confirm(key, value))


class BaseDevice:
    definition = ""
    root_structure = False

    # Registers per FC3 request in refresh(). Raised 32 -> 64 on 2026-08-23.
    #
    # WHAT THE FIRMWARE CAN SURVIVE. An FC3 response is 5 + 2N bytes (address,
    # function, byte-count, 2N of data, 2 of CRC) and it is assembled in a
    # 256-byte buffer, MB_RTU_MAX_FRAME in fw/modbus_rtu/mb_rtu.h. That puts
    # the ceiling at N = 125 (5 + 250 = 255), and the firmware's Modbus core
    # refuses a count outside 1..125 with exception 3. The slave it replaced
    # had no such check: it overran its buffer or wrapped its one-byte length
    # field past 125, silently. Either way 125 is the cliff, so the only safe
    # move is to stay well short of it.
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
    MAX_REGISTERS_PER_READ = 73  # protocolVersion 13: elsStop is 146 regs; two requests need >= 73, the 60%-of-125 rule caps at 75 (72 since 2026-09-07, for 140 regs)

    # The largest single request read_span() will issue, and a separate number
    # from the refresh() chunk above on purpose. The one span past that chunk
    # is the board tick (devices.Global.read_tick): fastData plus the elsStop
    # hot group, 30 + 56 = 86 registers, a 177-byte reply. That is 69% of the
    # firmware's 125-register FC3 ceiling, past the 60% this file otherwise
    # keeps to, and accepted as such: it makes the tick ONE request instead of
    # two. Set to exactly the tick's size so the tick is the largest request
    # anything makes, and a hot group that grows refuses loudly here (and in
    # tests/fsms/test_els_stop_snapshot.py) instead of creeping toward the
    # ceiling, where every failure is silent.
    MAX_REGISTERS_PER_SPAN = 86

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

    def _port(self) -> PortOwner:
        """The owner every exchange of this device goes through."""
        return PortOwner.of(self.dm)

    def __getitem__(self, key):
        var = self._variable_index[key]
        address = var.address + self.base_address

        if _is_structure(var.type):
            # A sub-structure (servo, scales[i], elsStop): building the view is
            # not an exchange, so it does not go through the port owner.
            if var.count > 1:
                return [var.type.read_function(self.dm, address + var.type.length * i)
                        for i in range(var.count)]
            return var.type.read_function(self.dm, address)

        # A LIVE read, one exchange per element. An array's elements are one
        # Batch so they reach the wire back to back.
        if var.count > 1:
            return self._port().call(Batch(tuple(
                Read(var.type.read_function, address + var.type.length * i)
                for i in range(var.count))))
        return self._port().call(Read(var.type.read_function, address))

    def write_command(self, key, value):
        """The Write (or Batch of them) one assignment to `key` becomes.

        Public so a caller can COLLECT several into one Batch -- the arming
        sequence does, because those writes must reach the controller back to
        back with no tick scan between them, and must come back with a verdict
        per write rather than one for the lot.
        """
        var = self._variable_index[key]
        if var.count > 1 and isinstance(value, (list, tuple)):
            # Symmetric with __getitem__'s array read: element-wise at
            # type-length strides. Without this, assigning a list handed the
            # WHOLE list to the scalar write function, whose int() coercion
            # raised, was swallowed by the write path's except, and surfaced
            # only as connected=False -- a silent no-op write. Needed by the
            # calMeasured round-trip (the calibration legs the take-up gate
            # derives its threshold from, restored at connect).
            return Batch(tuple(
                Write(var.type.write_function,
                      var.address + self.base_address + var.type.length * i,
                      v, f"{key}[{i}]")
                for i, v in enumerate(value[:var.count])))
        return Write(var.type.write_function,
                     var.address + self.base_address, value, key)

    def __setitem__(self, key, value):
        """FIRE AND FORGET: queue the write and return without waiting.

        Most writes do not gate anything -- servo speeds, directions, sync
        ratios, cal limits, the settings pushed at start-up. Waiting for each
        one bought nothing and cost the UI the round trip: a Cut press runs
        about 14 writes and a jog start 6, which at ~8.7 ms an exchange is the
        120-220 ms freeze this step exists to remove. A failure still surfaces
        the way it always did, through the write helper's log line and the
        connection verdict.

        ORDER IS UNCHANGED. The owner's queue is FIFO, so these reach the wire
        in the order they were issued, and a later read or confirmed write
        queued behind them still sees their effect.

        A WRITE THAT GATES A TRANSITION DOES NOT BELONG HERE -- use
        write_confirmed(), which returns the controller's acknowledgement.
        """
        self._port().submit(self.write_command(key, value))

    def write_confirmed(self, key, value) -> bool:
        """Write and WAIT, returning whether the controller acknowledged it.

        The replacement for reading `dm.connected` after a write. That only
        ever meant "did the last exchange succeed", so any later exchange --
        another write, or a tick scan on the owner thread -- could set it True
        over a write that failed and mask it. A verdict that can be masked by
        an unrelated success is not a verdict, and the cut arming rests on it.

        The answer here is THIS write's own, carried back on its future, and
        nothing can overwrite it. For an array every element must be
        acknowledged.
        """
        try:
            result = self._port().call(self.write_command(key, value))
        except Exception as e:                           # noqa: BLE001
            # Includes the proxy's TimeoutError. An answer that never came is
            # not an acknowledgement, and the whole point of this call is that
            # the caller can trust a True.
            log.error(f"write_confirmed({key}): no acknowledgement: {e}")
            return False
        if isinstance(result, list):
            return all(bool(r) for r in result)
        return bool(result)

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
    def read_span(self, first_register, register_count):
        """Read a contiguous span of this device's registers as ONE FC3 request.

        The counterpart to refresh(), which reads the WHOLE device and splits it
        into ceil(size / MAX_REGISTERS_PER_READ) requests. A span that is known
        to fit in one request -- the hot register group -- has no reason to pay
        for the second, and REQUESTS are the quantity that fails (see the note
        on MAX_REGISTERS_PER_READ above).

        Refuses rather than silently splitting: a caller asking for a span too
        large has lost the property it came here for, and quietly issuing two
        requests would hide that until it showed up as a comms timeout during a
        cut. The generated group sizes are asserted against the same budget at
        generation time, so this should be unreachable from generated callers --
        which is exactly why it must be loud if it ever is not.

        The budget is MAX_REGISTERS_PER_SPAN, not the refresh() chunk: the
        board tick's merged read is the one span larger than a chunk.
        """
        if register_count > self.MAX_REGISTERS_PER_SPAN:
            raise ValueError(
                f"span of {register_count} registers exceeds the "
                f"{self.MAX_REGISTERS_PER_SPAN}-register single-request budget; "
                f"a one-request read is the whole point of asking for a span")
        return self._port().call(
            ReadSpan(self.base_address + first_register, register_count))

    @ktrace()
    def refresh(self):
        """Read the whole device, in chunks of MAX_REGISTERS_PER_READ, as one
        Batch: the chunks reach the wire back to back. A failed chunk raises
        and the chunks after it are not requested."""
        remaining_size = self.size
        max_size = self.MAX_REGISTERS_PER_READ
        remaining_address = self.base_address
        requests = []
        while remaining_size > max_size:
            requests.append(ReadSpan(remaining_address, max_size))
            remaining_size -= max_size
            remaining_address += max_size
        if remaining_size > 0:
            requests.append(ReadSpan(remaining_address, remaining_size))

        with kev("read_registers"):
            parts = self._port().call(Batch(tuple(requests)))
        raw_data = [register for part in parts for register in part]

        return self.decode(raw_data)

    def decode(self, raw_data):
        """Unpack this device's registers, read by whatever request, into the
        same dict refresh() returns. Split out so a caller that read the block
        as part of a larger span (devices.Global.read_tick) decodes it exactly
        as refresh() would."""
        with kev("struct"):
            raw_bytes = struct.pack("<" + "H" * self.size, *raw_data)
            values = list(struct.unpack("<" + self.struct_unpack_string, raw_bytes))
        with kev("set_fast_data"):
            return self.set_fast_data(values)
