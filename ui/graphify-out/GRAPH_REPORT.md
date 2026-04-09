# Graph Report - .  (2026-04-09)

## Corpus Check
- Corpus is ~37,402 words - fits in a single context window. You may not need a graph.

## Summary
- 1269 nodes · 2248 edges · 82 communities detected
- Extraction: 63% EXTRACTED · 37% INFERRED · 0% AMBIGUOUS · INFERRED: 833 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `ServoDispatcher` - 80 edges
2. `InputDispatcher` - 73 edges
3. `AxisTransform` - 72 edges
4. `AxisDispatcher` - 72 edges
5. `MockBoard` - 67 edges
6. `MainApp` - 64 edges
7. `MockFormats` - 54 edges
8. `TransformType` - 52 edges
9. `MockOffsetProvider` - 45 edges
10. `SavingDispatcher` - 38 edges

## Surprising Connections (you probably didn't know these)
- `Communication Layer Functions Should Be Methods (TODO)` --references--> `ConnectionManager`  [EXTRACTED]
  todo.md → rcp/utils/communication.py
- `Imperial threads use 254/(TPI*10) formula.` --uses--> `FeedConfiguration`  [INFERRED]
  tests/test_feeds.py → rcp/feeds.py
- `Higher TPI = finer thread = smaller pitch ratio.` --uses--> `FeedConfiguration`  [INFERRED]
  tests/test_feeds.py → rcp/feeds.py
- `MM feed ratios should equal the name as a fraction.` --uses--> `FeedConfiguration`  [INFERRED]
  tests/test_feeds.py → rcp/feeds.py
- `Simulates a refresh when experimental is already enabled.` --uses--> `UpdateScreen`  [INFERRED]
  tests/screens/test_update_screen.py → rcp/components/screens/update_screen.py

## Hyperedges (group relationships)
- **Manager Screen Registration** — manager_Manager, home_screen_HomePage, setup_screen_SetupScreen, network_screen_NetworkScreen, color_picker_screen_ColorPickerScreen, font_picker_screen_FontPickerScreen, inputs_setup_screen_InputsSetupScreen, input_screen_InputScreen, axes_setup_screen_AxesSetupScreen, els_setup_screen_ElsSetupScreen, update_screen_UpdateScreen, system_screen_SystemScreen, profiling_screen_ProfilingScreen [EXTRACTED 1.00]
- **MainApp Owns Dispatchers** — app_MainApp, dispatchers_FormatsDispatcher, dispatchers_Board, dispatchers_ElsDispatcher, dispatchers_ServoDispatcher, dispatchers_AxisDispatcher, dispatchers_InputDispatcher [EXTRACTED 1.00]
- **Feed Table Registry Groups All Feed Configs** — feeds_table, feeds_THREAD_MM, feeds_THREAD_IN, feeds_FEED_IN, feeds_FEED_MM [EXTRACTED 1.00]
- **Plot Subsystem Components** — plot_plotscreen, plot_floatview, plot_plottoolbar, plot_scene, plot_coordsoverlay, plot_circlepopup, plot_linepopup, plot_rectpopup [INFERRED 0.90]
- **Toolbar Buttons with BeepMixin** — toolbars_ledbutton, toolbars_twostatebutton, toolbars_toolbarbutton, widgets_beepmixin [EXTRACTED 1.00]
- **Home Mode Layout Variants** — home_dromodelayout, home_jogmodelayout, home_modelayout [EXTRACTED 1.00]
- **FloatView Pattern Dispatchers** — plot_floatview, dispatchers_circlepattern, dispatchers_linepattern, dispatchers_rectpattern [EXTRACTED 1.00]
- **Home Mode Layout Hierarchy** — mode_layout_ModeLayout, els_mode_layout_ElsModeLayout, index_mode_layout_IndexModeLayout [EXTRACTED 1.00]
- **ELS Feed Management Components** — elsbar_ElsBar, feeds_table_popup_FeedsTablePopup, feeds_module [INFERRED 0.85]
- **Home Screen Bar Widgets** — home_toolbar_HomeToolbar, coordbar_CoordBar, servobar_ServoBar, jogbar_JogBar, statusbar_StatusBar, elsbar_ElsBar, els_advbar_ElsAdvancedBar [INFERRED 0.80]
- **Widget Components Using HelpPopup** — widgets_BooleanItem, widgets_DropDownItem, widgets_ButtonItem, help_popup_HelpPopup [EXTRACTED 1.00]
- **ELS Mode Layout Components** — els_mode_layout_ElsModeLayout, els_mode_layout_ElsSpindleInfo, elsbar_ElsBar, els_advbar_ElsAdvancedBar [EXTRACTED 1.00]
- **BeepMixin Button Widgets** — widgets_auto_size_button, widgets_keypad_button, widgets_keypad_icon_button, components_beep_mixin [EXTRACTED 1.00]
- **SavingDispatcher Subclasses** — dispatchers_saving_dispatcher, dispatchers_formats, dispatchers_circle_pattern, dispatchers_rect_pattern, dispatchers_line_pattern, dispatchers_axis, dispatchers_servo, dispatchers_input, dispatchers_els [EXTRACTED 1.00]
- **Pattern Dispatchers** — dispatchers_circle_pattern, dispatchers_rect_pattern, dispatchers_line_pattern [INFERRED 0.85]
- **Board Orchestrates Core Dispatchers** — dispatchers_board, dispatchers_servo, dispatchers_input, dispatchers_axis, dispatchers_formats, dispatchers_axis_transform [EXTRACTED 1.00]
- **Widgets with HelpPopup Support** — widgets_font_item, widgets_string_item, widgets_dual_number_item, widgets_number_item, widgets_color_item, popups_help_popup [EXTRACTED 1.00]
- **Modbus Register Read/Write Functions** — communication_read_float, communication_write_float, communication_read_long, communication_write_long, communication_read_unsigned, communication_write_unsigned, communication_read_signed, communication_write_signed, communication_connectionmanager [EXTRACTED 1.00]
- **Device Type Class Hierarchy** — base_device_basedevice, devices_servo, devices_scale, devices_fastdata, devices_global [EXTRACTED 1.00]
- **C Typedef Parsing Pipeline** — base_device_register_type, base_device_parse_addresses, base_device_typedefinition, base_device_variabledefinition, communication_load_structures [EXTRACTED 0.95]
- **ELS Configuration Settings Group** — els_mode_help, els_axis_roles, lead_screw_pitch, spindle_mode, units_per_turn, servo_gearing [INFERRED 0.90]
- **Servo Motion Control Settings Group** — servo_max_speed, servo_acceleration, servo_gearing, position_tolerance, sync_ratio [INFERRED 0.85]
- **Axis Transform Configuration Group** — transform_type, scale_input, scales, weights, angle_degrees [INFERRED 0.90]
- **Display Appearance Settings Group** — font_size, display_font, colors_help, format_digits, hide_mouse_cursor [INFERRED 0.85]
- **Threading Direction Icons** — thread_rh_icon, thread_lh_icon, threading_feature [INFERRED 0.88]
- **DRO Action Icons** — zero_icon, origin_icon, half_icon, tool_offset_icon, dro_coordinate_feature [INFERRED 0.85]
- **Pattern Feature Icons** — circular_pattern_icon, rectangular_pattern_icon, circle_pattern_feature [INFERRED 0.82]

## Communities

### Community 0 - "Axis Control & Transform"
Cohesion: 0.04
Nodes (74): Loads the specified help file text from the help files folder., AxisDispatcher, AxisTransform, Defines how an axis value is derived from physical scale inputs.      IDENTITY:, Compute axis value from scale positions., TransformType, Board, Add a new axis with the given transform (defaults to identity on first unused in (+66 more)

### Community 1 - "Application Core"
Cohesion: 0.03
Nodes (46): MainApp, BoxLayout, CoordBar, Pure UI widget displaying axis state. All logic lives in AxisDispatcher., AxisDispatcher, AxisTransform, TransformType, DroCoordBar (+38 more)

### Community 2 - "Axis Dispatcher Methods"
Cohesion: 0.03
Nodes (33): AxisDispatcher — abstraction layer between raw encoder inputs and the UI.  An ax, Return the primary InputDispatcher, or None if out of range., Include transform config in every save., Load transform from persisted YAML (if available)., Persist the transform config to the YAML file., Encoder steps per spindle revolution., Current axis value before axis-level offset (in ratio-units)., Convert input's steps_per_second to display speed. (+25 more)

### Community 3 - "Setup Screens"
Cohesion: 0.04
Nodes (19): ColorPickerScreen, ElsSetupScreen, _font_display_name(), FontPickerEntry, FontPickerScreen, FormatsScreen, InputScreen, InputsSetupScreen (+11 more)

### Community 4 - "UI Widget Components"
Cohesion: 0.04
Nodes (21): AutoSizeButton, Button that automatically scales font_size down to prevent text wrapping.      S, BeepMixin, Mixin for Kivy button widgets that plays the app beep sound on press., BeepMixin, Button, ButtonBehavior, CirclePopup (+13 more)

### Community 5 - "Hardware Device Abstraction"
Cohesion: 0.11
Nodes (29): BaseDevice, refresh(), register_type(), BaseDevice.set_fast_data, TypeDefinition, VariableDefinition, BaseDevice, BaseModel (+21 more)

### Community 6 - "App Wiring & Screen Nav"
Cohesion: 0.06
Nodes (49): MainApp, App ConfigParser, AxesSetupScreen, ColorPickerScreen, CoordBar, AxisDispatcher, Board Dispatcher, ElsDispatcher (+41 more)

### Community 7 - "Plot Float View"
Cohesion: 0.06
Nodes (14): FloatView, FloatLayout, LinePatternDispatcher, Scene, StencilView, 5 points from 0 to 100 should be at 0, 25, 50, 75, 100., Single hole should be at origin., Two holes should be at start and end. (+6 more)

### Community 8 - "Form Widgets"
Cohesion: 0.05
Nodes (13): BooleanItem, ButtonItem, Default event handler. Can be overridden in KV or Python., ColorItem, DropDownItem, DualNumberItem, FontItem, HelpPopup (+5 more)

### Community 9 - "Feed Configuration"
Cohesion: 0.06
Nodes (10): FeedConfiguration, MM feed ratios should equal the name as a fraction., Imperial threads use 254/(TPI*10) formula., Higher TPI = finer thread = smaller pitch ratio., TestFeedConfiguration, TestFeedIN, TestFeedMM, TestTable (+2 more)

### Community 10 - "Rectangle Pattern"
Cohesion: 0.08
Nodes (15): RectPatternDispatcher, 90° rotation should swap x and y axes., Rotation should not change distances between points., 360° rotation should produce same points as 0°., 3x3 grid should produce 9 points., 3x3 grid with spacing 25 should be centered: -25 to +25., Center of all points should be at origin., Adjacent columns should differ by spacing_x. (+7 more)

### Community 11 - "Circle Pattern"
Cohesion: 0.08
Nodes (14): CirclePatternDispatcher, start_angle=90 should place first point at top., Full circle (360°) should produce exactly holes_count points., All points should be at radius = diameter/2 from origin., First point should be at start_angle (0°) = rightmost., Points should be evenly spaced angularly., Partial arc (not 360°) should have holes_count + 1 points., First point at start_angle, last at end_angle. (+6 more)

### Community 12 - "Platform Tests"
Cohesion: 0.06
Nodes (6): TestFormatBytes, TestGetBlockSizeBytes, TestGetFilesystemUsage, TestGetRootDevice, TestIsRaspberryPi, TestParseDiskAndPartition

### Community 13 - "Input Dispatcher Tests"
Cohesion: 0.06
Nodes (6): TestEncoderTracking, TestInputDispatcherFilename, TestSaveRestore, TestScaledValue, TestSpeed, TestSpindleWrapping

### Community 14 - "Servo Dispatcher Tests"
Cohesion: 0.06
Nodes (7): TestConfigureLeadScrewRatio, TestGoNextPrevious, TestOnConnected, TestOnIndex, TestScaledPosition, TestToggleEnable, TestUpdatePositions

### Community 15 - "Update Screen Tests"
Cohesion: 0.09
Nodes (6): Simulates a refresh when experimental is already enabled., TestAllowExperimental, TestInstallRelease, TestSetReleases, Set the releases list, appending the dev entry if experimental is enabled., UpdateScreen

### Community 16 - "Axis Transform Tests"
Cohesion: 0.08
Nodes (4): TestBackwardCompatibility, TestIdentityTransform, TestSerialization, TestSumTransform

### Community 17 - "Axes Setup Screen"
Cohesion: 0.13
Nodes (6): AxesSetupScreen, Build the list of (label, callback) for all items including the Add button., AxisScreen, Remove this axis from the board, clean up this screen, and go back., Sync UI fields from the current axis transform when entering., Build an AxisTransform from the current UI field values and apply it.

### Community 18 - "Modbus Communication"
Cohesion: 0.22
Nodes (11): ConnectionManager, read_float(), read_long(), read_signed(), read_unsigned(), write_float(), write_long(), write_signed() (+3 more)

### Community 19 - "Dispatcher Registry"
Cohesion: 0.22
Nodes (16): App Settings Config, AxisDispatcher, AxisTransform, Board Dispatcher, CirclePatternDispatcher, ElsDispatcher, FormatsDispatcher, InputDispatcher (+8 more)

### Community 20 - "CType Calc Tests"
Cohesion: 0.13
Nodes (3): Encoder wraps from max uint32 to small value (forward motion)., Encoder wraps from small value back past zero (backward motion)., TestUint32SubtractToInt32

### Community 21 - "KV Loader Tests"
Cohesion: 0.14
Nodes (7): load_kv should load a .kv file that exists alongside the .py file., load_kv should return None when no .kv file exists., load_kv should skip a .kv file that was already loaded., get_loaded_kv_files should return a copy, not the internal set., load_kv should track each file independently., Clear the loaded files registry before each test., TestLoadKv

### Community 22 - "Performance Profiling"
Cohesion: 0.19
Nodes (4): ProfilingPanel, Reset all collected stats., Called every frame to track frame timing., Update displayed FPS stats periodically.

### Community 23 - "Position Tests"
Cohesion: 0.27
Nodes (3): compute_at_position(), Reproduce the at_position logic from FloatView.update_tick()., TestAtPosition

### Community 24 - "Platform Utilities"
Cohesion: 0.15
Nodes (12): format_bytes(), get_block_size_bytes(), get_filesystem_usage(), get_root_device(), is_raspberry_pi(), parse_disk_and_partition(), Check if running on a Raspberry Pi by reading the device-tree model., Get the device backing the root filesystem, e.g. '/dev/mmcblk0p2'. (+4 more)

### Community 25 - "Cross-Cutting UI Utilities"
Cohesion: 0.24
Nodes (12): BeepMixin, HelpPopup, KV Loader Utility, AutoSizeButton Widget, ColorItem Widget, DualNumberItem Widget, FontItem Widget, KeypadButton Widget (+4 more)

### Community 26 - "ELS Documentation"
Cohesion: 0.23
Nodes (12): Electronic Lead Screw (ELS), Gearing Ratio Calculation, Microstepping Configuration, Spindle Encoder, ELS Axis Roles Help, ELS Mode Help, Lead Screw Pitch Help, Motor Gears Icon (+4 more)

### Community 27 - "Transform & Scale Docs"
Cohesion: 0.2
Nodes (10): Angle Degrees Help, Linear Scale Encoder Input, Axis Transform Pipeline, Rationale: Angle Cos/Sin Transforms for Compound Slide, Rationale: Sync Ratio Unit Normalization, Scale Input Help, Scale Ratio Configuration Help, Sync Ratio Help (+2 more)

### Community 28 - "Logging Panel"
Cohesion: 0.48
Nodes (2): get_log_dir(), LogsPanel

### Community 29 - "Coordinates Overlay"
Cohesion: 0.38
Nodes (1): CoordsOverlay

### Community 30 - "Plot Pattern Selection"
Cohesion: 0.38
Nodes (7): CirclePatternDispatcher, LinePatternDispatcher, RectPatternDispatcher, CirclePopup, FloatView, PlotToolbar, Scene

### Community 31 - "KV File Loading"
Cohesion: 0.33
Nodes (5): KV File Loading Pattern, get_loaded_kv_files(), load_kv(), Load the .kv file corresponding to a Python source file.      Resolves the .kv p, Return the set of all KV files that have been loaded.

### Community 32 - "Feed Table Data"
Cohesion: 0.53
Nodes (6): FEED_IN Feed Table, FEED_MM Feed Table, FeedConfiguration, THREAD_IN Feed Table, THREAD_MM Feed Table, Feed Table Registry

### Community 33 - "DRO Action Icons"
Cohesion: 0.4
Nodes (5): DRO Coordinate Position Feature, Half Midpoint Icon, Origin Home Icon, Tool Offset Icon, Zero Reset Icon

### Community 34 - "KV Syntax Tests"
Cohesion: 0.5
Nodes (2): Verify all .kv files have valid KV syntax (catches indentation errors, etc.)., test_kv_file_syntax()

### Community 35 - "Board Tests"
Cohesion: 0.5
Nodes (0): 

### Community 36 - "Toolbar Buttons"
Cohesion: 0.5
Nodes (4): LedButton, ToolbarButton, TwoStateButton, BeepMixin

### Community 37 - "Project Overview Docs"
Cohesion: 0.5
Nodes (4): Firmware Naming Parity Convention, OSPI OS Project, RCP Project Overview, rotary-controller-f4 Firmware

### Community 38 - "Display Appearance Settings"
Cohesion: 0.5
Nodes (4): Color Settings Help, DRO Display Settings, Display Font Help, Default Font Size Help

### Community 39 - "Servo Motion Help"
Cohesion: 0.5
Nodes (4): Servo Motor Control, Trapezoidal Motion Profile, Servo Acceleration Help, Servo Maximum Speed Help

### Community 40 - "WiFi Network"
Cohesion: 0.67
Nodes (3): nmcli Network Configuration, WiFi and Network Management, Enable WiFi Help

### Community 41 - "Error Telemetry"
Cohesion: 0.67
Nodes (3): Sentry Error Telemetry, Error Reporting Help, Rationale: Anonymous Error Reporting Privacy

### Community 42 - "Threading Icons"
Cohesion: 1.0
Nodes (3): Left-Hand Thread Icon, Right-Hand Thread Icon, Threading Feature (ELS)

### Community 43 - "Pattern Icons"
Cohesion: 1.0
Nodes (3): Circle Bolt Hole Pattern Feature, Circular Pattern Bolt Circle Icon, Rectangular Pattern Icon

### Community 44 - "CType Arithmetic"
Cohesion: 1.0
Nodes (0): 

### Community 45 - "Circular Import Handling"
Cohesion: 1.0
Nodes (2): Deferred Import Pattern for Circular Dependencies, Circular Import Dependencies (TODO)

### Community 46 - "OTA Software Update"
Cohesion: 1.0
Nodes (2): GitHub Releases for OTA Updates, Software Update Help

### Community 47 - "Position Tolerance"
Cohesion: 1.0
Nodes (2): Position Tolerance Help, Rationale: Position Tolerance Trade-off

### Community 48 - "Turning Direction Icons"
Cohesion: 1.0
Nodes (2): Turn Inward Icon, Turn Outward Icon

### Community 49 - "Module Init"
Cohesion: 1.0
Nodes (0): 

### Community 50 - "Entry Point"
Cohesion: 1.0
Nodes (0): 

### Community 51 - "App Settings"
Cohesion: 1.0
Nodes (0): 

### Community 52 - "AxisTransform Rationale A"
Cohesion: 1.0
Nodes (1): The first (or only) contributing scale input index.

### Community 53 - "AxisTransform Rationale B"
Cohesion: 1.0
Nodes (1): All scale input indices used by this transform.

### Community 54 - "Logs Screen"
Cohesion: 1.0
Nodes (1): LogsScreen

### Community 55 - "Formats Screen"
Cohesion: 1.0
Nodes (1): FormatsScreen

### Community 56 - "Log Viewer Screen"
Cohesion: 1.0
Nodes (1): LogViewerScreen

### Community 57 - "Rect Popup"
Cohesion: 1.0
Nodes (1): RectPopup

### Community 58 - "Line Popup"
Cohesion: 1.0
Nodes (1): LinePopup

### Community 59 - "ELS Feed Mode"
Cohesion: 1.0
Nodes (1): FeedMode

### Community 60 - "Title Item Widget"
Cohesion: 1.0
Nodes (1): TitleItem

### Community 61 - "MainApp Alt Reference"
Cohesion: 1.0
Nodes (1): MainApp

### Community 62 - "BaseDevice Address Parser"
Cohesion: 1.0
Nodes (1): BaseDevice.parse_addresses_from_definition

### Community 63 - "ELS Mode Doc"
Cohesion: 1.0
Nodes (1): Electronic Lead Screw (ELS) Mode

### Community 64 - "Sync Mode Doc"
Cohesion: 1.0
Nodes (1): Sync Mode with Gear Ratios

### Community 65 - "Circle Pattern Doc"
Cohesion: 1.0
Nodes (1): Circle Pattern Calculator

### Community 66 - "PCB Hardware Doc"
Cohesion: 1.0
Nodes (1): rotary-controller-pcb Hardware

### Community 67 - "Component Pattern Doc"
Cohesion: 1.0
Nodes (1): UI Component Pattern

### Community 68 - "SavingDispatcher Pattern Doc"
Cohesion: 1.0
Nodes (1): SavingDispatcher Auto-Persistence Pattern

### Community 69 - "Axis Name Help"
Cohesion: 1.0
Nodes (1): Axis Name Help

### Community 70 - "Network Connection Help"
Cohesion: 1.0
Nodes (1): Network Connection Help

### Community 71 - "Format Digits Help"
Cohesion: 1.0
Nodes (1): Display Format Digits Help

### Community 72 - "Hide Mouse Cursor Help"
Cohesion: 1.0
Nodes (1): Hide Mouse Cursor Help

### Community 73 - "System Storage Help"
Cohesion: 1.0
Nodes (1): System Storage Help

### Community 74 - "Volume Help"
Cohesion: 1.0
Nodes (1): Sound Volume Help

### Community 75 - "Icon Risorsa 10"
Cohesion: 1.0
Nodes (1): Risorsa 10 Icon

### Community 76 - "Icon Risorsa 8"
Cohesion: 1.0
Nodes (1): Risorsa 8 Icon

### Community 77 - "Icon Risorsa 11"
Cohesion: 1.0
Nodes (1): Risorsa 11 Icon

### Community 78 - "Icon Risorsa 7"
Cohesion: 1.0
Nodes (1): Risorsa 7 Icon

### Community 79 - "Icon Risorsa 12"
Cohesion: 1.0
Nodes (1): Risorsa 12 Icon

### Community 80 - "Icon Risorsa 9"
Cohesion: 1.0
Nodes (1): Risorsa 9 Icon

### Community 81 - "Fonts Test Image"
Cohesion: 1.0
Nodes (1): Fonts Test Image

## Knowledge Gaps
- **129 isolated node(s):** `Clear the loaded files registry before each test.`, `load_kv should load a .kv file that exists alongside the .py file.`, `load_kv should return None when no .kv file exists.`, `load_kv should skip a .kv file that was already loaded.`, `get_loaded_kv_files should return a copy, not the internal set.` (+124 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `CType Arithmetic`** (2 nodes): `ctype_calc.py`, `uint32_subtract_to_int32()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Circular Import Handling`** (2 nodes): `Deferred Import Pattern for Circular Dependencies`, `Circular Import Dependencies (TODO)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `OTA Software Update`** (2 nodes): `GitHub Releases for OTA Updates`, `Software Update Help`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Position Tolerance`** (2 nodes): `Position Tolerance Help`, `Rationale: Position Tolerance Trade-off`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Turning Direction Icons`** (2 nodes): `Turn Inward Icon`, `Turn Outward Icon`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Module Init`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Entry Point`** (1 nodes): `main.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `App Settings`** (1 nodes): `appsettings.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `AxisTransform Rationale A`** (1 nodes): `The first (or only) contributing scale input index.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `AxisTransform Rationale B`** (1 nodes): `All scale input indices used by this transform.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Logs Screen`** (1 nodes): `LogsScreen`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Formats Screen`** (1 nodes): `FormatsScreen`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Log Viewer Screen`** (1 nodes): `LogViewerScreen`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Rect Popup`** (1 nodes): `RectPopup`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Line Popup`** (1 nodes): `LinePopup`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `ELS Feed Mode`** (1 nodes): `FeedMode`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Title Item Widget`** (1 nodes): `TitleItem`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `MainApp Alt Reference`** (1 nodes): `MainApp`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `BaseDevice Address Parser`** (1 nodes): `BaseDevice.parse_addresses_from_definition`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `ELS Mode Doc`** (1 nodes): `Electronic Lead Screw (ELS) Mode`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Sync Mode Doc`** (1 nodes): `Sync Mode with Gear Ratios`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Circle Pattern Doc`** (1 nodes): `Circle Pattern Calculator`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `PCB Hardware Doc`** (1 nodes): `rotary-controller-pcb Hardware`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Component Pattern Doc`** (1 nodes): `UI Component Pattern`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `SavingDispatcher Pattern Doc`** (1 nodes): `SavingDispatcher Auto-Persistence Pattern`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Axis Name Help`** (1 nodes): `Axis Name Help`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Network Connection Help`** (1 nodes): `Network Connection Help`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Format Digits Help`** (1 nodes): `Display Format Digits Help`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Hide Mouse Cursor Help`** (1 nodes): `Hide Mouse Cursor Help`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `System Storage Help`** (1 nodes): `System Storage Help`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Volume Help`** (1 nodes): `Sound Volume Help`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Icon Risorsa 10`** (1 nodes): `Risorsa 10 Icon`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Icon Risorsa 8`** (1 nodes): `Risorsa 8 Icon`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Icon Risorsa 11`** (1 nodes): `Risorsa 11 Icon`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Icon Risorsa 7`** (1 nodes): `Risorsa 7 Icon`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Icon Risorsa 12`** (1 nodes): `Risorsa 12 Icon`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Icon Risorsa 9`** (1 nodes): `Risorsa 9 Icon`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Fonts Test Image`** (1 nodes): `Fonts Test Image`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MainApp` connect `Application Core` to `Axis Control & Transform`, `Axis Dispatcher Methods`, `Setup Screens`, `UI Widget Components`, `Plot Float View`, `Form Widgets`, `Axes Setup Screen`, `Dispatcher Registry`, `Logging Panel`, `Coordinates Overlay`, `Plot Pattern Selection`?**
  _High betweenness centrality (0.312) - this node is a cross-community bridge._
- **Why does `ServoDispatcher` connect `Axis Control & Transform` to `Application Core`, `Axis Dispatcher Methods`, `Servo Dispatcher Tests`?**
  _High betweenness centrality (0.092) - this node is a cross-community bridge._
- **Why does `SavingDispatcher` connect `Axis Dispatcher Methods` to `Axis Control & Transform`, `Application Core`, `Plot Float View`, `Rectangle Pattern`, `Circle Pattern`?**
  _High betweenness centrality (0.086) - this node is a cross-community bridge._
- **Are the 61 inferred relationships involving `ServoDispatcher` (e.g. with `TestServoDispatcherFilename` and `TestUpdatePositions`) actually correct?**
  _`ServoDispatcher` has 61 INFERRED edges - model-reasoned connections that need verification._
- **Are the 66 inferred relationships involving `InputDispatcher` (e.g. with `TestInputDispatcherFilename` and `TestEncoderTracking`) actually correct?**
  _`InputDispatcher` has 66 INFERRED edges - model-reasoned connections that need verification._
- **Are the 68 inferred relationships involving `AxisTransform` (e.g. with `TestIdentityTransform` and `TestSumTransform`) actually correct?**
  _`AxisTransform` has 68 INFERRED edges - model-reasoned connections that need verification._
- **Are the 54 inferred relationships involving `AxisDispatcher` (e.g. with `TestAxisDispatcherFilename` and `TestIdentityAxisMirrorsInput`) actually correct?**
  _`AxisDispatcher` has 54 INFERRED edges - model-reasoned connections that need verification._