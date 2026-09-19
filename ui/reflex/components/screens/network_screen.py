"""Network Settings screen.

NETWORK MANAGEMENT MUST NEVER TAKE DOWN THE CONTROLLER. Found on the lathe
2026-09-13, on the first card where the UI runs as an unprivileged service
user: ``__init__`` mirrored the radio state with ``self.wifi_enabled =
nmcli.radio().wifi``, that property write dispatched ``on_wifi_enabled``, and
the handler called ``nmcli.radio.wifi_on()`` unguarded. polkit refused
("Not authorized to perform this operation", surfaced as
``nmcli._exception.UnspecifiedException``), the exception left
``Manager.__init__`` and ``App.build()`` died -- so a machine controller that
was otherwise perfectly able to cut metal would not start because it could not
manage wifi.

Two things follow, and both are load-bearing:

* Construction reflects the radio state without COMMANDING the radio. See
  ``_syncing_radio_state``.
* Every nmcli call is caught. ``NMCLI_AVAILABLE`` already covered nmcli being
  missing; the instance flag ``nmcli_usable`` covers nmcli being present and
  refusing, which is the same degradation arriving by a different route. The
  degradation is shown through the two surfaces this screen already has -- a
  ``log.warning`` (which is what the missing-nmcli path emits, and what the in-app
  Log Viewer reads) and ``self.log()``, which appends to ``status_text`` and so
  into the "Status" box at the bottom of ``network_screen.kv``.
"""
import asyncio
import shutil

import nmcli

from kivy.clock import Clock
from kivy.properties import StringProperty, ListProperty, BooleanProperty, ObjectProperty
from kivy.logger import Logger
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import Screen

from reflex.utils.kv_loader import load_kv

log = Logger.getChild(__name__)

NMCLI_AVAILABLE = shutil.which("nmcli") is not None
if NMCLI_AVAILABLE:
    try:
        nmcli.disable_use_sudo()
    except Exception as e:  # pragma: no cover - a raise here would kill import
        log.warning(f"nmcli setup failed — network management unavailable: {e}")
        NMCLI_AVAILABLE = False

load_kv(__file__)


class NetworkScreen(Screen):
    setup_popup = ObjectProperty()
    networks = ListProperty(["Loading"])
    connection = StringProperty("")
    devices = ListProperty(["Loading"])
    device = StringProperty("")
    state = StringProperty("")
    address = StringProperty("")
    netmask = StringProperty("")
    gateway = StringProperty("")
    dns = StringProperty("")
    password = StringProperty("")
    key_mgmt = StringProperty("")
    hwaddr = StringProperty("")
    wireless_auth_alg = StringProperty("")
    wireless_mode = StringProperty("")
    lock = BooleanProperty(True)

    status_text = StringProperty("")
    wifi_enabled = BooleanProperty(False)

    # Plain class attributes, deliberately NOT Kivy properties: they are read
    # by guards that can run during kv rule application, so they must already
    # have a value no matter how a subclass or a kv rule orders its writes.
    nmcli_usable = NMCLI_AVAILABLE
    _syncing_radio_state = False

    def __init__(self, **kv):
        # Before super(): applying this screen's kv rules can dispatch
        # wifi_enabled, and the guards below are what keep that from issuing a
        # radio command.
        self.nmcli_usable = NMCLI_AVAILABLE
        self._syncing_radio_state = False
        super().__init__(**kv)
        self.ids['grid_layout'].bind(minimum_height=self.ids['grid_layout'].setter('height'))
        self.status_update_task = None

        if not NMCLI_AVAILABLE:
            log.warning("nmcli not found — network management unavailable")
            return

        try:
            radio_wifi = nmcli.radio().wifi
        except Exception as e:
            # An unprivileged user gets here, not to a stack trace out of
            # App.build(). The screen stays constructed and inert.
            self._nmcli_failed("radio query", e)
            return

        # Mirror the radio state WITHOUT re-commanding the radio.
        #
        # Kivy auto-binds ``on_<property>`` methods in
        # ``EventDispatcher.__init__``, and a Kivy property has no "quiet"
        # write -- any value change dispatches. So the side effect is
        # suppressed at the handler, with an explicit flag, rather than by
        # writing the property before ``super().__init__()`` (which works only
        # because of Kivy's internal bind ORDER, invisible from the handler) or
        # by unbinding and rebinding ``on_wifi_enabled`` (which silently
        # swallows a real user toggle that lands in the window). The flag is
        # the one mechanism a reader of ``on_wifi_enabled`` can see.
        self._syncing_radio_state = True
        try:
            self.wifi_enabled = radio_wifi
        finally:
            self._syncing_radio_state = False

        Clock.schedule_once(lambda dt: asyncio.ensure_future(self.refresh()))
        self.status_update_task = Clock.schedule_interval(lambda dt: asyncio.ensure_future(self.status_update()), timeout=1)

    # ── nmcli availability ─────────────────────────────────────────────

    def _nmcli_ok(self) -> bool:
        """True while nmcli is installed AND has not refused a call yet."""
        return NMCLI_AVAILABLE and self.nmcli_usable

    def _nmcli_failed(self, action: str, exc: BaseException):
        """Degrade this screen instead of propagating an nmcli failure.

        The usual cause is polkit refusing the operation for the service user,
        which is permanent for the life of the process -- so the flag latches
        rather than being retried once a second by ``status_update``.
        """
        self.nmcli_usable = False
        log.warning(f"nmcli {action} failed — network management unavailable: {exc}")
        self.log(f"Network management unavailable ({action}): {exc}")

    def log(self, message: str):
        log.info(message)
        self.status_text += f"{message}\n"

    async def status_update(self):
        if not self._nmcli_ok() or self.device == "":
            return
        try:
            data = await asyncio.to_thread(nmcli.device.show, self.device)
        except Exception as e:
            self._nmcli_failed("device status query", e)
            return
        new_state = data.get("GENERAL.STATE")
        if self.state != new_state:
            self.log("State Changed, refreshing properties")
            await self.refresh()

    async def refresh(self):
        if not self._nmcli_ok():
            return
        log.debug("Refresh properties invoked")

        try:
            # Scan Devices
            all_devices = await asyncio.to_thread(nmcli.device)
            self.devices = [item.device for item in all_devices if item.device_type in ["wifi"]]
            if len(self.devices) > 0:
                self.device = self.devices[0]
            else:
                self.device = ""

            await asyncio.sleep(0.5)

            # If we have a device, read the current settings
            if self.device != "":
                data = await asyncio.to_thread(nmcli.device.show, self.device)

                self.device = data.get("GENERAL.DEVICE") or self.device
                self.hwaddr = data.get("GENERAL.HWADDR") or self.hwaddr
                self.state = data.get("GENERAL.STATE") or self.state
                self.address = data.get("IP4.ADDRESS[1]") or ""
                self.gateway = data.get("IP4.GATEWAY") or ""
                self.dns = data.get("IP4.DNS[1]") or ""
                self.connection = data.get("GENERAL.CONNECTION") or ""

                if data.get("GENERAL.CONNECTION", None) is not None:
                    try:
                        conn = await asyncio.to_thread(nmcli.connection.show, name=self.connection, show_secrets=True)
                        self.key_mgmt = conn.get('802-11-wireless-security.key-mgmt') or ""
                        self.wireless_mode = conn.get('802-11-wireless.mode') or ""
                        self.wireless_auth_alg = conn.get('802-11-wireless-security.auth-alg') or ""
                        self.password = conn.get('802-11-wireless-security.psk') or ""
                    except Exception as e:
                        # One connection's details being unreadable is not the
                        # whole of network management being unavailable.
                        log.error(str(e))
                else:
                    self.key_mgmt = ""
                    self.wireless_mode = ""
                    self.wireless_auth_alg = ""
                    self.password = ""
        except Exception as e:
            self._nmcli_failed("device query", e)
            return

        # Only reached when the read succeeded: `lock` stays set while network
        # management is unavailable, exactly as on the missing-nmcli path.
        self.lock = False

    async def connect(self):
        if not self._nmcli_ok():
            return
        self.log("Request Wifi Connection")

        try:
            connections_dict = await asyncio.to_thread(nmcli.connection)
        except Exception as e:
            self.log(f"Unable to list connections: {str(e)}")
            return

        if self.connection in [item.name for item in connections_dict]:
            connection = self.connection
            self.log(f"Updating the password for connection: {self.connection}")
            new_options = {
                '802-11-wireless-security.psk': self.password
            }
            try:
                await asyncio.to_thread(nmcli.device.show, self.device)
                await asyncio.sleep(5)
                await asyncio.to_thread(nmcli.connection.modify, name=connection, options=new_options)
                await asyncio.sleep(5)
                await asyncio.to_thread(nmcli.connection.up, name=connection)
                await asyncio.sleep(5)
            except Exception as e:
                self.log(f"Unable to edit connection: {str(e)}")
        else:
            self.log(f"Creating a new connection profile for {self.connection} with device: {self.device}")
            try:
                await asyncio.to_thread(
                    nmcli.device.wifi_connect,
                    ssid=self.connection,
                    password=self.password,
                    ifname=self.device
                )
            except Exception as e:
                self.log(f"Unable to connect: {str(e)}")

    def on_wifi_enabled(self, instance, value):
        if self._syncing_radio_state:
            # Construction is mirroring the radio's CURRENT state. Commanding
            # the radio from here is what killed App.build() on 2026-09-13.
            return
        if not self._nmcli_ok():
            return
        try:
            if self.wifi_enabled:
                self.log("Enable Wifi Connections")
                nmcli.radio.wifi_on()
                self.log("Run Scan to find the available access points")
            else:
                self.log("Disable Wifi Connections")
                nmcli.radio.wifi_off()
        except Exception as e:
            self._nmcli_failed("radio toggle", e)

    def apply(self):
        self.lock = True
        Clock.schedule_once(lambda dt: asyncio.ensure_future(self.connect()))

    def select_network(self, selected_network):
        log.info(f"Selected network: {selected_network}")
        self.connection = selected_network

    def on_dismiss(self):
        log.debug("Dismiss signal received, stopping status_update_task")
        if self.status_update_task is not None:
            self.status_update_task.cancel()
