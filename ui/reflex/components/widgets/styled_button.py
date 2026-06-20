from kivy.app import App
from kivy.uix.button import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.graphics import Rectangle, Ellipse, Color, Line, LinearGradient
from kivy.core.text import Label as CoreLabel
from kivy.properties import BooleanProperty, StringProperty, ListProperty, NumericProperty
from kivy.metrics import dp

class StyledButton(ButtonBehavior, FloatLayout):
    '''
    A custom button supporting brushed metal (standard) and glowing (highlighted) styles.
    '''
    
    # Properties
    text = StringProperty('Button')
    is_highlighted = BooleanProperty(False)
    font_size = NumericProperty(24)
    
    # Colors
    # Standard Metal Gradient: Top-Left to Bottom-Right
    metal_color_start = ListProperty([0.7, 0.7, 0.7, 1]) # Light Silver
    metal_color_mid = ListProperty([0.9, 0.9, 0.9, 1])  # Bright Silver
    metal_color_end = ListProperty([0.4, 0.4, 0.4, 1])  # Dark Grey
    
    # Highlight Colors
    highlight_border_color = ListProperty([0.0, 1.0, 1.0, 1]) # Cyan
    highlight_bg_color = ListProperty([0.1, 0.15, 0.15, 1])  # Dark Teal
    highlight_text_color = ListProperty([0.0, 1.0, 1.0, 1])  # Cyan
    
    # Standard Colors
    standard_border_color = ListProperty([0.1, 0.1, 0.1, 1]) # Dark border
    standard_text_color = ListProperty([0.8, 0.8, 0.8, 1])  # Light grey text

    def __init__(self, **kwargs):
        super(StyledButton, self).__init__(**kwargs)
        self.radius = dp(15) # Rounded corner radius
        
        # Bind events to redraw when properties change
        self.bind(is_highlighted=self._update_canvas)
        self.bind(size=self._update_canvas)
        self.bind(pos=self._update_canvas)
        self.bind(text=self._update_canvas)
        
        # Initial draw
        self._update_canvas()

    def _update_canvas(self, *args):
        # Clear existing canvas
        self.canvas.clear()
        
        # 1. Draw the Outer Border (The "Bevel" or Glow)
        with self.canvas.before:
            # Set Border Color
            if self.is_highlighted:
                Color(*self.highlight_border_color)
            else:
                Color(*self.standard_border_color)
            
            # Draw Rounded Rect Outline
            # We use a Line instruction with 'rectangle' mode to draw the outline
            Line(
                rectangle=(self.x, self.y, self.width, self.height),
                radius=self.radius,
                width=dp(2) if self.is_highlighted else dp(1)
            )

        # 2. Draw the Background
        with self.canvas.before:
            if self.is_highlighted:
                # --- HIGHLIGHTED STYLE ---
                Color(*self.highlight_bg_color)
                self._draw_rounded_rect_fill(self.x, self.y, self.width, self.height, self.radius)
                
            else:
                # --- BRUSHED METAL STYLE ---
                # We use a LinearGradient to simulate the metallic sheen
                # (x0, y0, x1, y1) defines the direction of the gradient
                gradient = LinearGradient(
                    orientation='angle',
                    angle=135, # Diagonal top-left to bottom-right
                    start=self.metal_color_start,
                    mid=self.metal_color_mid,
                    end=self.metal_color_end
                )
                self._draw_rounded_rect_fill(self.x, self.y, self.width, self.height, self.radius)
                # Remove gradient from canvas after use to prevent memory leaks if not stored
                # (Kivy handles this automatically in most cases, but good to be aware)

        # 3. Draw the Inner Bevel (Optional, adds 3D depth)
        # A lighter line on top-left, darker on bottom-right
        with self.canvas.before:
            # Inner Highlight (Top/Left)
            Color(1, 1, 1, 0.3)
            Line(
                rectangle=(self.x + dp(1), self.y + dp(1), self.width - dp(2), self.height - dp(2)),
                radius=self.radius,
                width=dp(1)
            )

        # 4. Draw the Text
        with self.canvas:
            # Set Text Color
            if self.is_highlighted:
                Color(*self.highlight_text_color)
            else:
                Color(*self.standard_text_color)
            
            # Create a CoreLabel for the text
            # We need to update the text position manually relative to the widget
            lbl = CoreLabel(
                text=self.text,
                font_size=self.font_size,
                font_name='Roboto', # Or your preferred font
                bold=True
            )
            lbl.refresh()
            
            # Center the text
            text_x = self.x + (self.width - lbl.texture_size[0]) / 2
            text_y = self.y + (self.height - lbl.texture_size[1]) / 2
            
            Rectangle(
                texture=lbl.texture,
                pos=(text_x, text_y),
                size=lbl.texture_size
            )

    def _draw_rounded_rect_fill(self, x, y, w, h, r):
        """Helper to draw a filled rounded rectangle using Kivy graphics instructions."""
        # Top Left Ellipse
        Ellipse(pos=(x, y), size=(2*r, 2*r))
        # Top Right Ellipse
        Ellipse(pos=(x + w - 2*r, y), size=(2*r, 2*r))
        # Bottom Left Ellipse
        Ellipse(pos=(x, y + h - 2*r), size=(2*r, 2*r))
        # Bottom Right Ellipse
        Ellipse(pos=(x + w - 2*r, y + h - 2*r), size=(2*r, 2*r))
        
        # Middle Rectangles
        Rectangle(pos=(x + r, y), size=(w - 2*r, h))
        Rectangle(pos=(x, y + r), size=(w, h - 2*r))