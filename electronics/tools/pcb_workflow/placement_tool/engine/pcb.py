class Component:
    def __init__(self, name, footprint_name='', value='', width=10.0, height=10.0):
        self.name = name
        self.footprint_name = footprint_name
        self.value = value
        self.width = width
        self.height = height
        self.ctr_x = 0.0
        self.ctr_y = 0.0
        self.rotation = 0.0
        self.fixed = False
        
        # Semantic attributes
        self.semantic_class = None
        self.interface_type = None
        self.placement_type = None
        self.anchor_parent = None

    def translate_to(self, x, y):
        """Move component center to (x, y)."""
        self.ctr_x = x
        self.ctr_y = y

    def get_x_length(self):
        if int(round(self.rotation)) % 180 != 0:
            return self.height
        return self.width

    def get_y_length(self):
        if int(round(self.rotation)) % 180 != 0:
            return self.width
        return self.height

class Net:
    def __init__(self, name):
        self.name = name
        self.pins = [] # List of (component_name, pin_name)

class PCB:
    def __init__(self, width=None, height=None):
        self.components = {} # refdes -> Component
        self.nets = {}       # net_name -> Net
        self.holes = []      # List of mounting hole dicts: {"x":, "y":, "radius":}
        self.keepouts = []   # List of keepout zone dicts: {"x":, "y":, "w":, "h":}
        self.corner_exclusion = 10.0  # Default corner exclusion zone in mm
        if width is not None and height is not None:
            self.x_range = [0.0, float(width)]
            self.y_range = [0.0, float(height)]
        else:
            self.x_range = [float("inf"), float("-inf")]
            self.y_range = [float("inf"), float("-inf")]

    def add_component(self, component):
        self.components[component.name] = component

    def add_net(self, net):
        self.nets[net.name] = net

    def add_hole(self, x, y, radius=3.0):
        """Add a mounting hole at (x, y) with given radius."""
        self.holes.append({"x": x, "y": y, "radius": radius})

    def add_keepout(self, x, y, w, h):
        """Add a keepout zone at (x, y) with width w and height h."""
        self.keepouts.append({"x": x, "y": y, "w": w, "h": h})

    def get_hole_exclusion_zones(self, padding=2.0):
        """
        Get exclusion zones around mounting holes.
        Returns list of (x_min, y_min, x_max, y_max) tuples.
        """
        zones = []
        for hole in self.holes:
            radius = hole.get("radius")
            if radius is None:
                radius = hole.get("diameter", 6.0) / 2.0
            r = radius + padding
            zones.append((
                hole["x"] - r,
                hole["y"] - r,
                hole["x"] + r,
                hole["y"] + r
            ))
        return zones
