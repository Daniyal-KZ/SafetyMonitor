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
