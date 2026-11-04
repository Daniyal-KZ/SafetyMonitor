"""Restrained system-style colours for the desktop application."""

import customtkinter as ctk


BACKGROUND = ("#ECEDEF", "#25272B")
SURFACE = ("#FFFFFF", "#303338")
SURFACE_ALT = ("#F5F5F6", "#35383E")
SIDEBAR = ("#E2E4E7", "#292C31")
HEADER = ("#E8EAED", "#303338")
BORDER = ("#C6CAD0", "#484D54")
TEXT = ("#24282D", "#ECEEF1")
MUTED = ("#69717B", "#A5ABB4")
BUTTON = ("#F4F5F6", "#383D44")
BLUE = ("#3F678D", "#567FA5")
BLUE_HOVER = ("#335775", "#638CB1")
RED = ("#AA3434", "#E28585")
RED_SURFACE = ("#FAEEEE", "#443033")
GREEN = ("#3C7650", "#88BB94")


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    """Use the standard Windows desktop font with Cyrillic support."""
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def button(master, **kwargs):
    defaults = dict(height=30, corner_radius=3, fg_color=SURFACE, text_color=TEXT,
                    border_color=BORDER, border_width=1, hover_color=BORDER, font=font(12))
    return ctk.CTkButton(master, **(defaults | kwargs))


def entry(master, **kwargs):
    defaults = dict(height=30, corner_radius=3, border_width=1, fg_color=SURFACE,
                    border_color=BORDER, text_color=TEXT, font=font())
    return ctk.CTkEntry(master, **(defaults | kwargs))


def option_menu(master, **kwargs):
    defaults = dict(height=30, corner_radius=3, fg_color=SURFACE, button_color=BORDER,
                    button_hover_color=BORDER, text_color=TEXT, font=font(12),
                    dropdown_fg_color=SURFACE, dropdown_text_color=TEXT,
                    dropdown_hover_color=BORDER, dropdown_font=font(12))
    return ctk.CTkOptionMenu(master, **(defaults | kwargs))


def tabview(master, **kwargs):
    defaults = dict(corner_radius=0, border_width=1, border_color=BORDER, fg_color=SURFACE,
                    segmented_button_fg_color=BACKGROUND, segmented_button_selected_color=SURFACE,
                    segmented_button_selected_hover_color=BORDER, segmented_button_unselected_color=BACKGROUND,
                    segmented_button_unselected_hover_color=BORDER, text_color=TEXT, anchor='w')
    return ctk.CTkTabview(master, **(defaults | kwargs))
