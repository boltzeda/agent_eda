from enum import Enum, auto

# =============================================================================
# 1. Component Classification Constants
# =============================================================================

class ComponentClass(Enum):
    CONNECTOR = auto()
    ANTENNA = auto()
    POWER_ENTRY = auto()
    PROTECTION = auto()
    MCU_FPGA = auto()
    MEMORY = auto()
    ANALOG_IC = auto()
    PASSIVE = auto()
    USER_INTERFACE = auto()
    SAFETY = auto()
    UNKNOWN = auto()

class InterfaceType(Enum):
    POWER = auto()
    RF = auto()
    IO = auto()
    USER = auto()
    SIGNAL = auto()
    SAFETY = auto()
    NONE = auto()

class PlacementType(Enum):
    FIXED = auto()      # Never moves (absolute constraint)
    ANCHORED = auto()   # Relative to parent (semantic constraint)
    FREE = auto()       # Fully optimizable

# Regex patterns for classification (Name based + RefDes based)
# Format: (Class, Interface, Placement, Pattern List)
# Patterns are checked against: Footprint Name, RefDes, Value
CLASSIFICATION_RULES = [
    (ComponentClass.ANTENNA, InterfaceType.RF, PlacementType.FIXED,
     # \b on ANT so word-internal hits (GIANT, ABUNDANT) can't fire; SMA needs a
     # non-LL follower so common values like "C_Small"/"R_Small" don't match.
     [r"\bANT(ENNA)?", r"SMA(?!LL)", r"U\.FL", r"IPEX", r"RF_SHIELD"]),
    
    (ComponentClass.POWER_ENTRY, InterfaceType.POWER, PlacementType.FIXED, 
     [r"BARREL", r"DC_JACK", r"TERMINAL_BLOCK", r"SCREW_TERMINAL", r"VIN"]),
     
    (ComponentClass.PROTECTION, InterfaceType.SAFETY, PlacementType.FIXED, 
     [r"FUSE", r"TVS", r"MOV", r"VARISTOR", r"PTC"]),
     
    (ComponentClass.MCU_FPGA, InterfaceType.SIGNAL, PlacementType.FREE, 
     [r"QFN", r"BGA", r"LQFP", r"TQFP", r"STM32", r"ESP32", r"ATMEGA", r"FPGA"]),
     
    (ComponentClass.MEMORY, InterfaceType.SIGNAL, PlacementType.FREE, 
     [r"DDR", r"SDRAM", r"FLASH", r"EMMC", r"SO-DIMM"]),
     
    (ComponentClass.ANALOG_IC, InterfaceType.SIGNAL, PlacementType.FREE, 
     [r"OPAMP", r"ADC", r"DAC", r"LDO", r"REGULATOR"]),
     
    (ComponentClass.USER_INTERFACE, InterfaceType.USER, PlacementType.FREE, 
     [r"SW_", r"BUTTON", r"LED", r"DISPLAY", r"LCD", r"OLED"]),
     
    (ComponentClass.SAFETY, InterfaceType.SAFETY, PlacementType.FIXED, 
     [r"ISOLATION", r"OPTO", r"RELAY", r"TRANSFORMER"]),

    (ComponentClass.CONNECTOR, InterfaceType.IO, PlacementType.FIXED, 
     [r"USB", r"HDMI", r"RJ45", r"ETHERNET", r"SATA", r"PCIE", r"M\.2", r"CONNECTOR", r"HEADER", r"^J\d+"]),
     
    (ComponentClass.PASSIVE, InterfaceType.NONE, PlacementType.FREE, 
     [r"^R\d+", r"^C\d+", r"^L\d+", r"^D\d+", r"RES", r"CAP", r"IND", r"DIODE"])
]

# =============================================================================
# 2. Board Type & Archetype Detection
# =============================================================================

class BoardType(Enum):
    POWER = auto()
    RF = auto()
    ANALOG = auto()
    HIGH_SPEED = auto()
    MIXED = auto()

class BoardArchetype(Enum):
    MODULE = auto()
    CONTROLLER = auto()
    SENSOR_NODE = auto()
    CARRIER = auto()
    INDUSTRIAL = auto()

# Packing Density Rules (Area utilization)
DENSITY_RULES = {
    BoardType.POWER: 0.35,
    BoardType.MIXED: 0.45,
    BoardType.HIGH_SPEED: 0.55,
    BoardType.RF: 0.40,
    BoardType.ANALOG: 0.50
}

# =============================================================================
# 3. Edge Roles & Constraints
# =============================================================================

class EdgeRole(Enum):
    POWER_EDGE = auto()
    RF_EDGE = auto()
    IO_EDGE = auto()
    USER_EDGE = auto()
    SIGNAL_EDGE = auto()
    SAFETY_EDGE = auto()
    NONE = auto()

# Hard Constraints: Forbidden Adjacency
FORBIDDEN_ADJACENCY = {
    EdgeRole.POWER_EDGE: {EdgeRole.RF_EDGE, EdgeRole.SIGNAL_EDGE},
    EdgeRole.RF_EDGE: {EdgeRole.POWER_EDGE, EdgeRole.IO_EDGE, EdgeRole.USER_EDGE, EdgeRole.SAFETY_EDGE},
    EdgeRole.SAFETY_EDGE: {EdgeRole.USER_EDGE, EdgeRole.IO_EDGE, EdgeRole.RF_EDGE},
    EdgeRole.SIGNAL_EDGE: {EdgeRole.POWER_EDGE, EdgeRole.IO_EDGE},
    EdgeRole.IO_EDGE: {EdgeRole.RF_EDGE, EdgeRole.SAFETY_EDGE, EdgeRole.SIGNAL_EDGE}
}

# Soft Constraints (Allowed but penalized)
SOFT_CONSTRAINTS = {
    (EdgeRole.POWER_EDGE, EdgeRole.IO_EDGE): "Low speed only",
    (EdgeRole.USER_EDGE, EdgeRole.POWER_EDGE): "No HV",
    (EdgeRole.SIGNAL_EDGE, EdgeRole.RF_EDGE): "Shielded"
}

# Edge Directions
DIRECTIONS = ["NORTH", "EAST", "SOUTH", "WEST"]

# =============================================================================
# 4. Anchoring Rules
# =============================================================================

# Parent -> Child relationships
# Key: Child Type (e.g., Capacitor), Value: Parent Type (e.g., MCU)
ANCHOR_RULES = {
    ComponentClass.PASSIVE: [ComponentClass.MCU_FPGA, ComponentClass.MEMORY, ComponentClass.ANALOG_IC, ComponentClass.POWER_ENTRY],
    # Crystal -> MCU handled separately in logic
}
