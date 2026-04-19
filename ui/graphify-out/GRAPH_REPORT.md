# Graph Report - .  (2026-04-11)

## Corpus Check
- 125 files · ~50,776 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1218 nodes · 2299 edges · 49 communities detected
- Extraction: 62% EXTRACTED · 38% INFERRED · 0% AMBIGUOUS · INFERRED: 868 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]

## God Nodes (most connected - your core abstractions)
1. `ServoDispatcher` - 80 edges
2. `ElsStateMachine` - 79 edges
3. `InputDispatcher` - 73 edges
4. `AxisDispatcher` - 72 edges
5. `AxisTransform` - 72 edges
6. `MainApp` - 67 edges
7. `MockBoard` - 67 edges
8. `MockFormats` - 54 edges
9. `TransformType` - 52 edges
10. `MockOffsetProvider` - 45 edges

## Surprising Connections (you probably didn't know these)
- `Imperial threads use 254/(TPI*10) formula.` --uses--> `FeedConfiguration`  [INFERRED]
  tests/test_feeds.py → rcp/feeds.py
- `Higher TPI = finer thread = smaller pitch ratio.` --uses--> `FeedConfiguration`  [INFERRED]
  tests/test_feeds.py → rcp/feeds.py
- `MM feed ratios should equal the name as a fraction.` --uses--> `FeedConfiguration`  [INFERRED]
  tests/test_feeds.py → rcp/feeds.py
- `Simulates a refresh when experimental is already enabled.` --uses--> `UpdateScreen`  [INFERRED]
  tests/screens/test_update_screen.py → rcp/components/screens/update_screen.py
- `TestIdentityTransform` --uses--> `TransformType`  [INFERRED]
  tests/dispatchers/test_axis_transform.py → rcp/dispatchers/axis_transform.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.04
Nodes (74): Loads the specified help file text from the help files folder., AxisDispatcher, AxisTransform, Defines how an axis value is derived from physical scale inputs.      IDENTITY, Compute axis value from scale positions., TransformType, Board, Add a new axis with the given transform (defaults to identity on first unused in (+66 more)

### Community 1 - "Community 1"
Cohesion: 0.03
Nodes (25): CustomPopup, ElsAdvancedBar, Unified ELS advanced bar — hosts the ElsStateMachine and supports all     three, Bind `target_prop` to an AxisDispatcher's formattedPosition with         strict, No-op kept for backward compatibility.          The former single `btn_value` co, Dispatcher wired to each TextHeaderButton's on_release in the kv.          Only, ElsSettingsPopup, Get available thread types based on metric mode. (+17 more)

### Community 2 - "Community 2"
Cohesion: 0.03
Nodes (29): App, MainApp, CoordBar, Pure UI widget displaying axis state. All logic lives in AxisDispatcher., DroCoordBar, Simplified CoordBar for DRO/ELS modes: no sync ratio Num/Den columns, no sync to, DroModeLayout, DRO mode: simplified DroCoordBars, no bottom bar. (+21 more)

### Community 3 - "Community 3"
Cohesion: 0.04
Nodes (28): AxisDispatcher — abstraction layer between raw encoder inputs and the UI.  An, Return the primary InputDispatcher, or None if out of range., Include transform config in every save., Load transform from persisted YAML (if available)., Persist the transform config to the YAML file., Encoder steps per spindle revolution., Current axis value before axis-level offset (in ratio-units)., Convert input's steps_per_second to display speed. (+20 more)

### Community 4 - "Community 4"
Cohesion: 0.04
Nodes (19): ColorPickerScreen, ElsSetupScreen, _font_display_name(), FontPickerEntry, FontPickerScreen, FormatsScreen, InputScreen, InputsSetupScreen (+11 more)

### Community 5 - "Community 5"
Cohesion: 0.04
Nodes (24): AutoSizeButton, Button that automatically scales font_size down to prevent text wrapping., BeepMixin, Mixin for Kivy button widgets that plays the app beep sound on press., BeepMixin, Button, ButtonBehavior, CirclePopup (+16 more)

### Community 6 - "Community 6"
Cohesion: 0.04
Nodes (20): BooleanItem, BoxLayout, ButtonItem, Default event handler. Can be overridden in KV or Python., ColorItem, CoordsOverlay, DropDownItem, DualNumberItem (+12 more)

### Community 7 - "Community 7"
Cohesion: 0.11
Nodes (27): BaseDevice, refresh(), register_type(), TypeDefinition, VariableDefinition, BaseDevice, BaseModel, Log an error message only if it differs from the last one logged. (+19 more)

### Community 8 - "Community 8"
Cohesion: 0.06
Nodes (14): FloatView, FloatLayout, LinePatternDispatcher, Scene, StencilView, 5 points from 0 to 100 should be at 0, 25, 50, 75, 100., Single hole should be at origin., Two holes should be at start and end. (+6 more)

### Community 9 - "Community 9"
Cohesion: 0.06
Nodes (10): FeedConfiguration, MM feed ratios should equal the name as a fraction., Imperial threads use 254/(TPI*10) formula., Higher TPI = finer thread = smaller pitch ratio., TestFeedConfiguration, TestFeedIN, TestFeedMM, TestTable (+2 more)

### Community 10 - "Community 10"
Cohesion: 0.08
Nodes (15): RectPatternDispatcher, 90° rotation should swap x and y axes., Rotation should not change distances between points., 360° rotation should produce same points as 0°., 3x3 grid should produce 9 points., 3x3 grid with spacing 25 should be centered: -25 to +25., Center of all points should be at origin., Adjacent columns should differ by spacing_x. (+7 more)

### Community 11 - "Community 11"
Cohesion: 0.08
Nodes (14): CirclePatternDispatcher, start_angle=90 should place first point at top., Full circle (360°) should produce exactly holes_count points., All points should be at radius = diameter/2 from origin., First point should be at start_angle (0°) = rightmost., Points should be evenly spaced angularly., Partial arc (not 360°) should have holes_count + 1 points., First point at start_angle, last at end_angle. (+6 more)

### Community 12 - "Community 12"
Cohesion: 0.06
Nodes (6): TestFormatBytes, TestGetBlockSizeBytes, TestGetFilesystemUsage, TestGetRootDevice, TestIsRaspberryPi, TestParseDiskAndPartition

### Community 13 - "Community 13"
Cohesion: 0.06
Nodes (6): TestEncoderTracking, TestInputDispatcherFilename, TestSaveRestore, TestScaledValue, TestSpeed, TestSpindleWrapping

### Community 14 - "Community 14"
Cohesion: 0.06
Nodes (7): TestConfigureLeadScrewRatio, TestGoNextPrevious, TestOnConnected, TestOnIndex, TestScaledPosition, TestToggleEnable, TestUpdatePositions

### Community 15 - "Community 15"
Cohesion: 0.09
Nodes (6): Simulates a refresh when experimental is already enabled., TestAllowExperimental, TestInstallRelease, TestSetReleases, Set the releases list, appending the dev entry if experimental is enabled., UpdateScreen

### Community 16 - "Community 16"
Cohesion: 0.08
Nodes (4): TestBackwardCompatibility, TestIdentityTransform, TestSerialization, TestSumTransform

### Community 17 - "Community 17"
Cohesion: 0.13
Nodes (6): AxesSetupScreen, Build the list of (label, callback) for all items including the Add button., AxisScreen, Remove this axis from the board, clean up this screen, and go back., Sync UI fields from the current axis transform when entering., Build an AxisTransform from the current UI field values and apply it.

### Community 18 - "Community 18"
Cohesion: 0.21
Nodes (9): ConnectionManager, read_float(), read_long(), read_signed(), read_unsigned(), write_float(), write_long(), write_signed() (+1 more)

### Community 19 - "Community 19"
Cohesion: 0.13
Nodes (3): Encoder wraps from max uint32 to small value (forward motion)., Encoder wraps from small value back past zero (backward motion)., TestUint32SubtractToInt32

### Community 20 - "Community 20"
Cohesion: 0.19
Nodes (4): ProfilingPanel, Reset all collected stats., Called every frame to track frame timing., Update displayed FPS stats periodically.

### Community 21 - "Community 21"
Cohesion: 0.14
Nodes (7): load_kv should load a .kv file that exists alongside the .py file., load_kv should return None when no .kv file exists., load_kv should skip a .kv file that was already loaded., get_loaded_kv_files should return a copy, not the internal set., load_kv should track each file independently., Clear the loaded files registry before each test., TestLoadKv

### Community 22 - "Community 22"
Cohesion: 0.15
Nodes (12): format_bytes(), get_block_size_bytes(), get_filesystem_usage(), get_root_device(), is_raspberry_pi(), parse_disk_and_partition(), Check if running on a Raspberry Pi by reading the device-tree model., Get the device backing the root filesystem, e.g. '/dev/mmcblk0p2'. (+4 more)

### Community 23 - "Community 23"
Cohesion: 0.27
Nodes (3): compute_at_position(), Reproduce the at_position logic from FloatView.update_tick()., TestAtPosition

### Community 24 - "Community 24"
Cohesion: 0.28
Nodes (5): from_dict(), identity(), Pure-data axis transform layer.  Two modes: - IDENTITY: axis mirrors a single, sum(), Enum

### Community 25 - "Community 25"
Cohesion: 0.4
Nodes (4): get_loaded_kv_files(), load_kv(), Load the .kv file corresponding to a Python source file.      Resolves the .kv, Return the set of all KV files that have been loaded.

### Community 26 - "Community 26"
Cohesion: 0.5
Nodes (2): Verify all .kv files have valid KV syntax (catches indentation errors, etc.)., test_kv_file_syntax()

### Community 27 - "Community 27"
Cohesion: 0.5
Nodes (0): 

### Community 28 - "Community 28"
Cohesion: 1.0
Nodes (0): 

### Community 29 - "Community 29"
Cohesion: 1.0
Nodes (0): 

### Community 30 - "Community 30"
Cohesion: 1.0
Nodes (0): 

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (0): 

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (0): 

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (0): 

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (0): 

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (0): 

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (0): 

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (0): 

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (0): 

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (0): 

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): The first (or only) contributing scale input index.

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (1): All scale input indices used by this transform.

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (0): 

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (0): 

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (0): 

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (0): 

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (0): 

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (0): 

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **31 isolated node(s):** `Simplified CoordBar for DRO/ELS modes: no sync ratio Num/Den columns, no sync to`, `Pure UI widget displaying servo state. All logic lives in ServoDispatcher.`, `Thread profile types with their calculation formulas.`, `Set the releases list, appending the dev entry if experimental is enabled.`, `Called every frame to track frame timing.` (+26 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 28`** (2 nodes): `uint32_subtract_to_int32()`, `ctype_calc.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (1 nodes): `main.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 31`** (1 nodes): `appsettings.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 34`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `The first (or only) contributing scale input index.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `All scale input indices used by this transform.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MainApp` connect `Community 2` to `Community 0`, `Community 1`, `Community 3`, `Community 4`, `Community 5`, `Community 6`, `Community 8`, `Community 17`?**
  _High betweenness centrality (0.396) - this node is a cross-community bridge._
- **Why does `SavingDispatcher` connect `Community 3` to `Community 0`, `Community 1`, `Community 2`, `Community 8`, `Community 10`, `Community 11`?**
  _High betweenness centrality (0.115) - this node is a cross-community bridge._
- **Why does `ServoDispatcher` connect `Community 0` to `Community 2`, `Community 3`, `Community 14`?**
  _High betweenness centrality (0.112) - this node is a cross-community bridge._
- **Are the 61 inferred relationships involving `ServoDispatcher` (e.g. with `MainApp` and `Loads the specified help file text from the help files folder.`) actually correct?**
  _`ServoDispatcher` has 61 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `ElsStateMachine` (e.g. with `ElsAdvancedBar` and `Unified ELS advanced bar — hosts the ElsStateMachine and supports all     three`) actually correct?**
  _`ElsStateMachine` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 66 inferred relationships involving `InputDispatcher` (e.g. with `MainApp` and `Loads the specified help file text from the help files folder.`) actually correct?**
  _`InputDispatcher` has 66 INFERRED edges - model-reasoned connections that need verification._
- **Are the 54 inferred relationships involving `AxisDispatcher` (e.g. with `MainApp` and `Loads the specified help file text from the help files folder.`) actually correct?**
  _`AxisDispatcher` has 54 INFERRED edges - model-reasoned connections that need verification._