"""Game Boy DMG palette. Five steps from the lit green down to the shell."""

SCREEN = "#9bbc0f"
SCREEN_DIM = "#8bac0f"
MID = "#306230"
SHELL = "#0f380f"

SPRITE = (
    (0x9B, 0xBC, 0x0F),
    (0x6B, 0x8F, 0x1F),
    (0x30, 0x62, 0x30),
    (0x1F, 0x4A, 0x1F),
    (0x0F, 0x38, 0x0F),
)

BUTTON = {
    "fg_color": MID,
    "hover_color": SHELL,
    "text_color": SCREEN,
}

SEGMENTED = {
    "fg_color": SHELL,
    "selected_color": MID,
    "selected_hover_color": SHELL,
    "unselected_color": SHELL,
    "unselected_hover_color": MID,
    "text_color": SCREEN,
}

MENU = {
    "fg_color": MID,
    "button_color": MID,
    "button_hover_color": SHELL,
    "text_color": SCREEN,
    "dropdown_fg_color": SHELL,
    "dropdown_hover_color": MID,
    "dropdown_text_color": SCREEN,
}

SLIDER = {
    "fg_color": MID,
    "progress_color": SCREEN,
    "button_color": SCREEN,
    "button_hover_color": SCREEN_DIM,
}

CHECK = {
    "fg_color": SCREEN,
    "hover_color": SCREEN_DIM,
    "border_color": SCREEN,
    "checkmark_color": SHELL,
    "text_color": SCREEN,
}
